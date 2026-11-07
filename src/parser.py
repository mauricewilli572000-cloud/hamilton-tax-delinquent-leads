"""Parse the Hamilton County Auditor delinquent XLSX files into normalized rows."""
from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path

from openpyxl import load_workbook

from .columns import describe_mapping, find_header_row, map_columns

log = logging.getLogger(__name__)

CSZ_RE = re.compile(r"^(?P<city>.*?)[,\s]+(?P<state>[A-Z]{2})[,\s]+(?P<zip>\d{5})(?:-?\d{4})?$")
ZIP_RE = re.compile(r"\b(\d{5})(?:-?\d{4})?\b")


# ---------------------------------------------------------------- helpers
def clean(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    return re.sub(r"\s+", " ", str(v)).strip()


def parse_money(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = str(v).strip()
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s or s in ("-", "."):
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return round(-val if neg else val, 2)


def normalize_parcel(v) -> tuple[str, str]:
    """Return (hyphenated, digits). Hamilton parcels: 247-0001-0003-00 (3-4-4-2)."""
    if v is None:
        return "", ""
    if isinstance(v, float):
        v = int(v)
    raw = str(v).strip().upper()
    raw = re.sub(r"^(PPN|PARCEL)[:#\s]*", "", raw)
    digits = re.sub(r"[^0-9]", "", raw)
    if not digits:
        return "", ""
    if 10 <= len(digits) < 13:            # Excel stripped leading zeros
        digits = digits.zfill(13)
    if len(digits) == 13:
        return f"{digits[0:3]}-{digits[3:7]}-{digits[7:11]}-{digits[11:13]}", digits
    return raw, digits


def split_mailing(lines: list[str]) -> tuple[str, str, str, str]:
    """Split mailing lines into (street, city, state, zip)."""
    lines = [l for l in (clean(x) for x in lines) if l]
    if not lines:
        return "", "", "", ""
    city = state = zipc = ""
    street_lines = list(lines)
    for i in range(len(lines) - 1, -1, -1):
        m = CSZ_RE.match(lines[i].upper())
        if m:
            city, state, zipc = m.group("city").strip(" ,"), m.group("state"), m.group("zip")
            street_lines = lines[:i]
            break
    # prefer the line(s) that look like a street / PO box
    streetish = [l for l in street_lines if re.match(r"^(\d|P\.?\s?O\.?\s*BOX|BOX\s|RR\s|C/O)", l.upper())]
    if streetish:
        street = " ".join(l for l in streetish if not l.upper().startswith("C/O")) or streetish[-1]
    else:
        street = street_lines[-1] if street_lines else ""
    return street, city, state, zipc


def _cell(row: list, idxs: list[int] | None) -> str:
    if not idxs:
        return ""
    return clean(row[idxs[0]]) if idxs[0] < len(row) else ""


# ---------------------------------------------------------------- main
def read_rows(path: Path) -> tuple[list, list[list]]:
    """Return (headers, data_rows) from the biggest sheet in the workbook."""
    wb = load_workbook(path, read_only=True, data_only=True)
    best = None
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(c not in (None, "") for c in r)]
        if best is None or len(rows) > len(best):
            best = rows
    wb.close()
    if not best:
        return [], []
    h = find_header_row(best)
    headers = [clean(c) for c in best[h]]
    return headers, best[h + 1:]


def parse_file(path: Path, overrides: dict[str, str] | None = None, source_url: str = "") -> list[dict]:
    headers, rows = read_rows(path)
    if not headers:
        log.warning("%s: empty workbook", path.name)
        return []
    mapping = map_columns(headers, overrides)
    log.info("%s: %d rows, column mapping:\n%s", path.name, len(rows), describe_mapping(headers, mapping))
    if "parcel" not in mapping:
        raise ValueError(
            f"{path.name}: could not find a parcel column. Headers were: {headers}. "
            "Run discover_hamilton_delinquent.py and set COLUMN_OVERRIDES in config.py."
        )

    out = []
    for row in rows:
        parcel, digits = normalize_parcel(row[mapping["parcel"][0]] if mapping["parcel"][0] < len(row) else None)
        if not digits or len(digits) < 8:
            continue  # totals / footer rows

        # mailing
        mail_lines = [clean(row[i]) for i in mapping.get("mail_line", []) if i < len(row)]
        if "mail_city_state_zip" in mapping:
            mail_lines.append(_cell(row, mapping["mail_city_state_zip"]))
        street, mcity, mstate, mzip = split_mailing(mail_lines)
        mcity = _cell(row, mapping.get("mail_city")) or mcity
        mstate = (_cell(row, mapping.get("mail_state")) or mstate).upper()[:2]
        mzip_raw = _cell(row, mapping.get("mail_zip")) or mzip
        zm = ZIP_RE.search(mzip_raw.zfill(5) if mzip_raw.isdigit() else mzip_raw)
        mzip = zm.group(1) if zm else ""

        # property
        prop = _cell(row, mapping.get("prop_address"))
        if not prop and ("prop_street" in mapping):
            prop = " ".join(x for x in (
                _cell(row, mapping.get("prop_house_no")),
                _cell(row, mapping.get("prop_street")),
                _cell(row, mapping.get("prop_suffix")),
            ) if x and x != "0")
        pzip = _cell(row, mapping.get("prop_zip"))
        pzm = ZIP_RE.search(pzip)

        amount = parse_money(row[mapping["amount_owed"][0]]) if "amount_owed" in mapping else None
        delinquent = parse_money(row[mapping["delinquent_amount"][0]]) if "delinquent_amount" in mapping else None
        if amount is None and delinquent is not None:
            amount = delinquent

        out.append({
            "parcel_id": parcel,
            "parcel_digits": digits,
            "owner_name": _cell(row, mapping.get("owner_name")).upper(),
            "owner_name_2": _cell(row, mapping.get("owner_name_2")).upper(),
            "mailing_address": street.upper(),
            "mailing_city": mcity.upper(),
            "mailing_state": mstate,
            "mailing_zip": mzip,
            "property_address": prop.upper(),
            "property_city": _cell(row, mapping.get("prop_city")).upper(),
            "property_zip": pzm.group(1) if pzm else "",
            "amount_owed": amount,
            "delinquent_amount": delinquent,
            "has_delinquent_column": "delinquent_amount" in mapping,
            "tax_year": _cell(row, mapping.get("tax_year")),
            "certified_date": _cell(row, mapping.get("certified_date")),
            "land_use": _cell(row, mapping.get("land_use")),
            "source_url": source_url,
        })
    # one row per parcel: some files list a row per tax year -> sum amounts
    merged: dict[str, dict] = {}
    for r in out:
        cur = merged.get(r["parcel_digits"])
        if cur is None:
            merged[r["parcel_digits"]] = r
            continue
        for k in ("amount_owed", "delinquent_amount"):
            if r[k] is not None:
                cur[k] = round((cur[k] or 0) + r[k], 2)
        for k, v in r.items():
            if not cur.get(k) and v:
                cur[k] = v
        if r["tax_year"] and (not cur["tax_year"] or r["tax_year"] < cur["tax_year"]):
            cur["tax_year"] = r["tax_year"]   # keep the oldest year
    log.info("%s: %d parcels parsed", path.name, len(merged))
    return list(merged.values())
