"""
Hamilton County, OH - Tax Delinquent motivated-seller lead pipeline.

  python run_pipeline.py                 # download from the Auditor site and process
  python run_pipeline.py --force         # reprocess even if unpaid.xlsx is unchanged
  python run_pipeline.py --local-unpaid raw/unpaid.xlsx --local-advertised raw/Advertised*.xlsx
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import logging
import re
import sys
from pathlib import Path

import config
from src import downloader, store
from src.parser import parse_file
from src.schema import LeadRecord
from src.scoring import score

log = logging.getLogger("hamilton_tax_delinquent")
EXCLUDE_RE = re.compile("|".join(config.EXCLUDE_OWNER_PATTERNS))


def load_state() -> dict:
    if config.STATE_JSON.exists():
        return json.loads(config.STATE_JSON.read_text(encoding="utf-8"))
    return {}


def keep_row(r: dict) -> bool:
    amt = r["amount_owed"] or 0
    if amt < config.MIN_AMOUNT_OWED:
        return False
    if config.REQUIRE_DELINQUENT_BALANCE and r["has_delinquent_column"] and (r["delinquent_amount"] or 0) <= 0:
        return False
    if EXCLUDE_RE.search(f'{r["owner_name"]} {r["owner_name_2"]}'):
        return False
    return True


def year_of(url_or_path: str) -> str:
    m = re.search(r"(20\d{2})", Path(url_or_path).name)
    return m.group(1) if m else ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--local-unpaid", help="use a local unpaid.xlsx instead of downloading")
    ap.add_argument("--local-advertised", nargs="*", default=[], help="local Advertised*.xlsx files (globs ok)")
    ap.add_argument("--as-of", help="snapshot date YYYY-MM-DD (default: date on the Auditor page, else today)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    state = load_state()
    today = dt.date.today().isoformat()

    # ------------------------------------------------------------ 1. get files
    if args.local_unpaid:
        import hashlib
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
        # no state write here, so unchanged days produce no git commit
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

    # ------------------------------------------------------------ 3. merge
    leads = store.load(config.RECORDS_JSON)
    leads.update(store.load(config.PENDING_JSON))
    prev_active = sum(1 for l in leads.values() if l.status == "active")
    if prev_active > 200 and len(current) < 0.3 * prev_active:
        log.error("Only %d parcels parsed vs %d active before - file layout probably changed. "
                  "Aborting so leads are not wrongly marked removed. Run discover_hamilton_delinquent.py.",
                  len(current), prev_active)
        return 2

    added = updated = 0
    for digits, r in current.items():
        adv = advertised.get(digits)
        if adv:  # fill blanks from the advertised list (it often has mailing info)
            for k in ("owner_name", "owner_name_2", "mailing_address", "mailing_city", "mailing_state",
                      "mailing_zip", "property_address", "property_city", "property_zip", "land_use"):
                if not r.get(k) and adv["row"].get(k):
                    r[k] = adv["row"][k]
        key = r["parcel_id"]
        lead = leads.get(key)
        if lead is None:
            lead = LeadRecord(doc_num=key, date_added=today)
            leads[key] = lead
            added += 1
        else:
            updated += 1
            if lead.amount_owed != r["amount_owed"]:
                lead.amount_history.append({"date": snapshot, "amount": r["amount_owed"]})
        for k in ("owner_name", "owner_name_2", "mailing_address", "mailing_city", "mailing_state", "mailing_zip",
                  "property_address", "property_city", "property_zip", "amount_owed", "delinquent_amount",
                  "tax_year", "land_use", "source_url"):
            v = r.get(k)
            if v not in (None, ""):
                setattr(lead, k, v)
        lead.parcel_id = key
        lead.lead_type = config.LEAD_TYPE + (" - Advertised" if adv else "")
        lead.advertised_years = sorted(adv["years"], reverse=True) if adv else []
        lead.filing_date = r.get("certified_date") or lead.filing_date
        lead.auditor_url = config.AUDITOR_PARCEL_URL.format(parcel_digits=digits, year=snapshot[:4])
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

    for lead in leads.values():
        score(lead)

    # ------------------------------------------------------------ 4. save
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
        "added_this_run": added,
    }
    store.save(config.RECORDS_JSON, with_addr, meta)
    store.save(config.PENDING_JSON, pending, {"note": "active parcels with no address - retried each run"})
    n_csv = store.export_ghl(config.GHL_CSV, with_addr)
    store.export_dashboard(config.DASHBOARD_JSON, with_addr, meta)

    state.update({
        "unpaid_sha256": manifest["unpaid"]["sha256"],
        "snapshot": snapshot,
        "last_processed": today,
        "advertised_files": [f["url"] for f in manifest["advertised"]],
    })
    config.STATE_JSON.write_text(json.dumps(state, indent=2), encoding="utf-8")

    log.info("Done. snapshot=%s  new=%d  updated=%d  removed=%d  active=%d (hot %d / warm %d)  "
             "pending=%d  GHL rows=%d", snapshot, added, updated, removed, meta["active"],
             meta["hot"], meta["warm"], len(pending), n_csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
