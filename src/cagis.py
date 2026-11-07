"""
Property address + value for every Hamilton County parcel, from the CAGIS
quarterly open-data parcel CSV (one bulk download - no per-parcel page views):

    https://cagis.org/Opendata/Quarterly_GIS_Data/
      "Hamilton County Parcels July 2026.csv"   (name changes each quarter)
    Field list: https://cagis.org/Opendata/Quarterly_GIS_Data/Parcel_DataDictionary.txt

Fields used:
    PROPTYID / PARCELID + MLTOWN  -> 13-digit Auditor parcel number
    ADDRNO, ADDRST, ADDRSF, UNIT  -> property address
    MKTLND + MKTIMP               -> market value
    CLASS, EXLUCODE               -> land use (EXLUCODE VA = vacant)
    FORECL_FLAG                   -> foreclosure flag
    SALDAT (MDDYY), SALAMT        -> last sale date / amount
    DELIQ_TAXES                   -> delinquent tax amount (CAGIS copy)

Data credit: Cincinnati Area Geographic Information System (CAGIS).
"""
from __future__ import annotations

import csv
import datetime as dt
import logging
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urljoin

import requests

import config

log = logging.getLogger(__name__)
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
NAME_RE = re.compile(r"Hamilton[ _%20]*County[ _%20]*Parcels[ _%20]*([A-Za-z]+)[ _%20]*(\d{4})\.csv", re.I)


def _file_date(name: str) -> tuple[int, int]:
    m = NAME_RE.search(unquote(name))
    if not m:
        return (0, 0)
    return (int(m.group(2)), MONTHS.get(m.group(1).lower(), 0))


def find_url(session: requests.Session) -> str:
    """Newest 'Hamilton County Parcels <Month> <Year>.csv' in the CAGIS open-data folder."""
    try:
        html = session.get(config.CAGIS_FOLDER, timeout=60).text
        hrefs = re.findall(r'href=["\']([^"\']+\.csv)["\']', html, re.I)
        names = [h for h in hrefs if NAME_RE.search(unquote(h))]
        if names:
            best = max(names, key=_file_date)
            return urljoin(config.CAGIS_FOLDER, best)
        log.info("No parcel CSV link found in the CAGIS folder listing - trying recent month names.")
    except requests.RequestException as e:
        log.info("CAGIS folder listing not readable (%s) - trying recent month names.", e)
    today = dt.date.today()
    for back in range(0, 15):
        y, m = today.year, today.month - back
        while m <= 0:
            y, m = y - 1, m + 12
        name = f"Hamilton County Parcels {list(MONTHS)[m - 1].title()} {y}.csv"
        url = config.CAGIS_FOLDER + quote(name)
        try:
            if session.head(url, timeout=60).status_code == 200:
                return url
        except requests.RequestException:
            continue
    raise RuntimeError("Could not find a 'Hamilton County Parcels <Month> <Year>.csv' file at CAGIS")


def latest_name() -> tuple[str, requests.Session]:
    s = requests.Session()
    s.headers.update(config.HTTP_HEADERS)
    return find_url(s), s


def download(dest_dir: Path, url: str | None = None, session: requests.Session | None = None) -> Path:
    if session is None or url is None:
        url, session = latest_name()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "cagis_parcels.csv"
    log.info("Downloading CAGIS parcels: %s", unquote(url))
    with session.get(url, stream=True, timeout=900) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    log.info("  %.0f MB", dest.stat().st_size / 1e6)
    return dest


def _num(v: str) -> float | None:
    v = re.sub(r"[^0-9.\-]", "", v or "")
    try:
        return float(v) if v not in ("", "-", ".") else None
    except ValueError:
        return None


def parse_saldat(v: str) -> str:
    """CAGIS SALDAT is MDDYY / MMDDYY ('11567' = Jan 15 1967). Returns YYYY-MM-DD or ''."""
    d = re.sub(r"\D", "", v or "")
    if len(d) not in (5, 6) or int(d) == 0:
        return ""
    d = d.zfill(6)
    mm, dd, yy = int(d[:2]), int(d[2:4]), int(d[4:])
    year = 2000 + yy if yy <= dt.date.today().year % 100 else 1900 + yy
    try:
        return dt.date(year, mm, dd).isoformat()
    except ValueError:
        return ""


def keys_for(row: dict) -> list[str]:
    """Possible 13-digit Auditor parcel numbers for a CAGIS row."""
    out = []
    p = re.sub(r"\D", "", row.get("PROPTYID") or "")
    if len(p) == 13:
        out.append(p)
    base = re.sub(r"\D", "", row.get("PARCELID") or row.get("NAME") or "")
    if len(base) == 11:
        ml = re.sub(r"\D", "", row.get("MLTOWN") or "0")
        out.append(base + ml.zfill(2)[-2:])
        out.append(base + "00")
    return out


def read_parcels(path: Path, wanted: set[str]) -> dict[str, dict]:
    """Stream the CSV and return info for the wanted 13-digit parcels only."""
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    out: dict[str, dict] = {}
    by_base: dict[str, dict] = {}
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [(h or "").strip().upper() for h in (reader.fieldnames or [])]
        missing = {"ADDRNO", "ADDRST"} - set(reader.fieldnames)
        if missing or not ({"PROPTYID", "PARCELID", "NAME"} & set(reader.fieldnames)):
            raise RuntimeError(f"CAGIS CSV columns not recognised: {reader.fieldnames[:40]}")
        wanted_base = {w[:11] for w in wanted}
        rows = 0
        for row in reader:
            rows += 1
            keys = keys_for(row)
            hit = next((k for k in keys if k in wanted), None)
            if hit is None and not any(k[:11] in wanted_base for k in keys):
                continue
            g = lambda k: (row.get(k) or "").strip()
            no, st, sf, unit = g("ADDRNO"), g("ADDRST"), g("ADDRSF"), g("UNIT")
            addr = " ".join(x for x in (no if no not in ("0", "") else "", st, sf) if x)
            if unit and addr:
                addr += f" UNIT {unit}"
            land, imp = _num(g("MKTLND")), _num(g("MKTIMP"))
            info = {
                "address": re.sub(r"\s+", " ", addr).upper(),
                "market_value": (land or 0) + (imp or 0) if (land or imp) else None,
                "land_use": g("CLASS"),
                "exlucode": g("EXLUCODE").upper(),
                "foreclosure": g("FORECL_FLAG").upper() in ("Y", "YES", "1", "T", "TRUE"),
                "last_sale_date": parse_saldat(g("SALDAT")),
                "last_sale_amount": _num(g("SALAMT")),
                "cagis_delinquent": _num(g("DELIQ_TAXES")),
            }
            if hit:
                if hit not in out or (not out[hit]["address"] and info["address"]):
                    out[hit] = info
            else:
                for k in keys:
                    by_base.setdefault(k[:11], info)
    # fallback: same book/page/parcel, different multi-owner suffix
    for w in wanted - set(out):
        if w[:11] in by_base:
            out[w] = by_base[w[:11]]
    log.info("CAGIS CSV: %d rows read, %d of %d leads matched (%d with an address)",
             rows, len(out), len(wanted), sum(1 for v in out.values() if v["address"]))
    return out


def apply(leads: dict, parcels: dict[str, dict]) -> int:
    n = 0
    for lead in leads.values():
        p = parcels.get(lead.parcel_id.replace("-", ""))
        if not p:
            continue
        n += 1
        if p["address"]:
            lead.property_address = p["address"]
        lead.market_value = p["market_value"]
        lead.foreclosure_flag = p["foreclosure"]
        lead.vacant = p["exlucode"] == "VA"
        lead.last_sale_date = p["last_sale_date"]
        lead.last_sale_amount = p["last_sale_amount"]
        if p["land_use"]:
            lead.land_use = p["land_use"]
    return n
