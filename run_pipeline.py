"""
Hamilton County, OH - Tax Delinquent motivated-seller lead pipeline.

  python run_pipeline.py                 # download from the Auditor site and process
  python run_pipeline.py --force         # reprocess even if unpaid.xlsx is unchanged
  python run_pipeline.py --no-enrich     # skip property-address lookups
  python run_pipeline.py --local-unpaid raw/unpaid.xlsx --local-advertised raw/Advertised*.xlsx
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import logging
import re
import sys
from pathlib import Path

import config
from src import downloader, enrich, store
from src.parser import parse_file
from src.schema import LeadRecord
from src.scoring import score

log = logging.getLogger("hamilton_tax_delinquent")
EXCLUDE_RE = re.compile("|".join(config.EXCLUDE_OWNER_PATTERNS))
MERGE_FIELDS = ("owner_name", "owner_name_2", "mailing_address", "mailing_city", "mailing_state", "mailing_zip",
                "amount_owed", "delinquent_amount", "land_use", "source_url")
FILL_FIELDS = ("property_address", "property_city", "property_zip", "tax_year")   # only fill blanks


def load_state() -> dict:
    if config.STATE_JSON.exists():
        return json.loads(config.STATE_JSON.read_text(encoding="utf-8"))
    return {}


def keep_row(r: dict) -> bool:
    if (r["amount_owed"] or 0) < config.MIN_AMOUNT_OWED:
        return False
    if config.REQUIRE_DELINQUENT_BALANCE and r["has_delinquent_column"] and (r["delinquent_amount"] or 0) <= 0:
        return False
    if EXCLUDE_RE.search(f'{r["owner_name"]} {r["owner_name_2"]}'):
        return False
    return True


def year_of(url_or_path: str) -> str:
    m = re.search(r"(20\d{2})", Path(url_or_path).name)
    return m.group(1) if m else ""


def load_leads() -> dict[str, LeadRecord]:
    leads = store.load(config.RECORDS_JSON)
    leads.update(store.load(config.PENDING_JSON))
    return leads


def finish(leads: dict[str, LeadRecord], snapshot: str, added: int, do_enrich: bool) -> dict:
    """Property lookups, scoring and all outputs. Returns meta."""
    for lead in leads.values():
        score(lead)                       # preliminary score -> lookup order
    if do_enrich:
        enrich.enrich(leads, config.PROPERTY_CACHE_JSON)
    else:
        enrich.apply_cache(leads, enrich.load_cache(config.PROPERTY_CACHE_JSON))
    for lead in leads.values():
        enrich.fill_property_city_zip(lead)
        score(lead)

    with_addr = {k: l for k, l in leads.items() if l.has_address()}
    pending = {k: l for k, l in leads.items() if not l.has_address() and l.status == "active"}
    active = [l for l in with_addr.values() if l.status == "active"]
    meta = {
        "county": config.COUNTY,
        "lead_type": config.LEAD_TYPE,
        "source": config.SOURCE_PAGE,
        "snapshot": snapshot,
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "active": len(active),
        "hot": sum(1 for l in active if l.tier == "Hot"),
        "warm": sum(1 for l in active if l.tier == "Warm"),
        "removed": sum(1 for l in with_addr.values() if l.status == "removed"),
        "pending_no_address": len(pending),
        "property_address_found": sum(1 for l in active if l.property_address),
        "added_this_run": added,
    }
    store.save(config.RECORDS_JSON, with_addr, meta)
    store.save(config.PENDING_JSON, pending, {"note": "active parcels with no address - retried each run"})
    n_csv = store.export_ghl(config.GHL_CSV, with_addr)
    store.export_dashboard(config.DASHBOARD_JSON, with_addr, meta)
    log.info("Saved: active=%d (hot %d / warm %d), property address %d/%d, pending=%d, GHL rows=%d",
             meta["active"], meta["hot"], meta["warm"], meta["property_address_found"], meta["active"],
             len(pending), n_csv)
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-enrich", action="store_true", help="skip property-address lookups")
    ap.add_argument("--local-unpaid", help="use a local unpaid.xlsx instead of downloading")
    ap.add_argument("--local-advertised", nargs="*", default=[], help="local Advertised*.xlsx files (globs ok)")
    ap.add_argument("--as-of", help="snapshot date YYYY-MM-DD (default: date on the Auditor page, else today)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    do_enrich = not args.no_enrich

    state = load_state()
    today = dt.date.today().isoformat()

    # ------------------------------------------------------------ 1. get files
    if args.local_unpaid:
        p = Path(args.local_unpaid)
        manifest = {
            "as_of": args.as_of or "",
            "unpaid": {"url": config.UNPAID_URL, "path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()},
            "advertised": [{"url": f, "path": f} for g in args.local_advertised for f in sorted(glob.glob(g))],
        }
    else:
        manifest = downloader.fetch_all(config.RAW_DIR)
    snapshot = args.as_of or manifest["as_of"] or today

    if manifest["unpaid"]["sha256"] == state.get("unpaid_sha256") and not args.force:
        leads = load_leads()
        if do_enrich and enrich.needs_lookup(leads):
            log.info("unpaid.xlsx unchanged - continuing property address lookups only.")
            finish(leads, state.get("snapshot", snapshot), 0, True)
        else:
            # nothing written -> no git commit on quiet days
            log.info("unpaid.xlsx unchanged since %s - nothing to do (use --force to rebuild).", state.get("snapshot"))
        return 0

    # ------------------------------------------------------------ 2. parse
    ov = config.COLUMN_OVERRIDES
    unpaid_rows = parse_file(Path(manifest["unpaid"]["path"]), ov, manifest["unpaid"]["url"])
    current = {r["parcel_digits"]: r for r in unpaid_rows if keep_row(r)}
    log.info("Unpaid file: %d parcels, %d kept after filters", len(unpaid_rows), len(current))

    advertised: dict[str, dict] = {}   # digits -> {"years": set, "row": first row seen}
    for f in manifest["advertised"]:
        yr = year_of(f["url"])
        try:
            rows = parse_file(Path(f["path"]), ov, f["url"])
        except Exception as e:
            log.warning("Could not parse %s: %s", f["path"], e)
            continue
        for r in rows:
            a = advertised.setdefault(r["parcel_digits"], {"years": set(), "row": r})
            if yr:
                a["years"].add(yr)
    log.info("Advertised lists: %d parcels; %d of the current leads appear on them",
             len(advertised), sum(1 for d in current if d in advertised))

    # ------------------------------------------------------------ 3. merge
    leads = load_leads()
    prev_active = sum(1 for l in leads.values() if l.status == "active")
    if prev_active > 200 and len(current) < 0.3 * prev_active:
        log.error("Only %d parcels parsed vs %d active before - file layout probably changed. "
                  "Aborting so leads are not wrongly marked removed. Run discover_hamilton_delinquent.py.",
                  len(current), prev_active)
        return 2

    added = 0
    for digits, r in current.items():
        adv = advertised.get(digits)
        if adv:  # fill blanks from the advertised list
            for k in ("owner_name", "owner_name_2", "mailing_address", "mailing_city", "mailing_state",
                      "mailing_zip", "property_address", "property_city", "property_zip", "land_use", "tax_year"):
                if not r.get(k) and adv["row"].get(k):
                    r[k] = adv["row"][k]
        key = r["parcel_id"]
        lead = leads.get(key)
        if lead is None:
            lead = LeadRecord(doc_num=key, date_added=today)
            leads[key] = lead
            added += 1
        elif lead.amount_owed != r["amount_owed"]:
            lead.amount_history.append({"date": snapshot, "amount": r["amount_owed"]})
        for k in MERGE_FIELDS:
            v = r.get(k)
            if v not in (None, ""):
                setattr(lead, k, v)
        for k in FILL_FIELDS:
            if r.get(k) and not getattr(lead, k):
                setattr(lead, k, r[k])
        lead.on_contract = bool(r.get("on_contract"))
        lead.parcel_id = key
        lead.lead_type = config.LEAD_TYPE + (" - Advertised" if adv else "")
        lead.advertised_years = sorted(adv["years"], reverse=True) if adv else []
        lead.filing_date = r.get("certified_date") or lead.filing_date
        lead.auditor_url = config.AUDITOR_PARCEL_URL.format(parcel_digits=digits, year=dt.date.today().year - 1)
        if not lead.amount_history:
            lead.amount_history.append({"date": snapshot, "amount": r["amount_owed"]})
        lead.last_seen = snapshot
        lead.status = "active"
        lead.removed_date = ""

    removed = 0
    current_keys = {r["parcel_id"] for r in current.values()}
    for key, lead in leads.items():
        if key not in current_keys and lead.status == "active":
            lead.status = "removed"
            lead.removed_date = snapshot
            removed += 1
    log.info("Merge: new=%d removed=%d", added, removed)

    # ------------------------------------------------------------ 4. enrich + save
    finish(leads, snapshot, added, do_enrich)
    state.update({
        "unpaid_sha256": manifest["unpaid"]["sha256"],
        "snapshot": snapshot,
        "last_processed": today,
        "advertised_files": [f["url"] for f in manifest["advertised"]],
    })
    config.STATE_JSON.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
