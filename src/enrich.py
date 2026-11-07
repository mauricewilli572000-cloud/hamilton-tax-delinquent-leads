"""
Property (situs) address lookup from the Hamilton County Auditor property pages.

unpaid.xlsx only has the OWNER's mailing address. The property's own address is
shown in the header of every Auditor property page:

    https://wedge.hcauditor.org/view/re/{parcel_digits}/{tax_year}/summary
      Parcel ID  206-0009-0053-00
      Address    2280 HARRISON AVE
      Index Order ...

Lookups are cached in data/property_cache.json (an address never changes), done
highest-score first, and capped per run so a big first batch fills in over a
few daily runs instead of one huge run.
"""
from __future__ import annotations

import datetime as dt
import html as htmllib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

import config

log = logging.getLogger(__name__)
_local = threading.local()

ADDR_RES = [
    re.compile(r"Parcel ID\s+[\d-]+(?:\s+[\d-]+)?\s+Address\s+(.+?)\s+(?:Index Order|Parcel Number|Tax Year)", re.I),
    re.compile(r"(?<!Mail )\bAddress\s+(.+?)\s+Index Order", re.I),
]


def page_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", htmllib.unescape(text)).strip()


def parse_address(html: str) -> str:
    text = page_text(html)
    for rx in ADDR_RES:
        m = rx.search(text)
        if m:
            val = m.group(1).strip(" :|")
            toks = val.split()
            # header often prints the value twice ("6436 HAMILTON AVE 6436 HAMILTON AVE")
            if len(toks) % 2 == 0 and toks[: len(toks) // 2] == toks[len(toks) // 2:]:
                val = " ".join(toks[: len(toks) // 2])
            if 2 <= len(val) <= 80:
                return val.upper()
    return ""


def _session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update(config.HTTP_HEADERS)
        _local.s = s
    return s


def lookup(digits: str, year: int) -> tuple[str, str]:
    """Return (status, address). status: ok | none | error."""
    url = config.WEDGE_URL.format(parcel_digits=digits, year=year)
    for attempt in range(3):
        try:
            r = _session().get(url, timeout=30)
            if r.status_code == 200:
                addr = parse_address(r.text)
                return ("ok", addr) if addr else ("none", "")
            if r.status_code == 404:
                return "none", ""
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    return "error", ""


def load_cache(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_cache(path: Path, cache: dict) -> None:
    path.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def apply_cache(leads: dict, cache: dict) -> None:
    for lead in leads.values():
        c = cache.get(lead.parcel_id.replace("-", ""))
        if c and c.get("status") in ("ok", "none"):
            lead.property_lookup = c["status"]
            if c.get("address") and not lead.property_address:
                lead.property_address = c["address"]


def needs_lookup(leads: dict) -> list:
    return [l for l in leads.values()
            if l.status == "active" and not l.property_address and l.property_lookup not in ("ok", "none")]


def enrich(leads: dict, cache_path: Path) -> int:
    """Look up property addresses for active leads that lack one. Returns # looked up."""
    cache = load_cache(cache_path)
    apply_cache(leads, cache)
    todo = sorted(needs_lookup(leads), key=lambda l: (-l.score, -(l.amount_owed or 0)))
    todo = todo[: config.ENRICH_MAX_PER_RUN]
    if not todo:
        return 0
    year = dt.date.today().year - 1          # e.g. "2025 Payable 2026"
    log.info("Property address lookup: %d parcels this run (%d still missing overall)",
             len(todo), len(needs_lookup(leads)))
    start, done, errors, ok = time.time(), 0, 0, 0
    today = dt.date.today().isoformat()
    batch = 200
    with ThreadPoolExecutor(max_workers=config.ENRICH_WORKERS) as pool:
        for b in range(0, len(todo), batch):
            if time.time() - start > config.ENRICH_TIME_BUDGET_SEC:
                log.info("Lookup time budget reached - the rest continue next run.")
                break
            futures = {}
            for lead in todo[b:b + batch]:
                futures[pool.submit(lookup, lead.parcel_id.replace("-", ""), year)] = lead
                time.sleep(config.ENRICH_DELAY)
            b_err = 0
            for fut in as_completed(futures):
                lead = futures[fut]
                status, addr = fut.result()
                done += 1
                if status == "error":
                    errors += 1; b_err += 1
                    continue
                cache[lead.parcel_id.replace("-", "")] = {"status": status, "address": addr, "date": today}
                lead.property_lookup = status
                if addr:
                    lead.property_address = addr
                    ok += 1
            save_cache(cache_path, cache)
            log.info("  ... %d/%d looked up (%d found, %d errors)", done, len(todo), ok, errors)
            if b_err == len(futures):
                log.warning("A whole batch failed - stopping lookups for this run.")
                break
    save_cache(cache_path, cache)
    if errors and errors == done:
        log.warning("Every property lookup failed - wedge.hcauditor.org may be blocking this runner.")
    log.info("Property lookup done: %d found, %d no address, %d errors (retried next run)",
             ok, done - ok - errors, errors)
    return done


def fill_property_city_zip(lead) -> None:
    """If the owner lives at the property, the mailing city/zip is the property city/zip."""
    def n(s):
        return re.sub(r"[^A-Z0-9]", "", (s or "").upper())
    if lead.property_address and n(lead.property_address) == n(lead.mailing_address):
        lead.property_city = lead.property_city or lead.mailing_city
        lead.property_zip = lead.property_zip or lead.mailing_zip
