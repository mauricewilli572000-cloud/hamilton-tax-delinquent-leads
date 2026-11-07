"""Accumulate leads across runs (merge by doc_num = parcel) and export."""
from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path

from .schema import LeadRecord

log = logging.getLogger(__name__)
BUSINESS_RE = re.compile(r"\b(LLC|INC|CORP|CO|LTD|LP|TRUST|ESTATE|BANK|CHURCH|ASSN|ASSOCIATION|HEIRS)\b")


def load(path: Path) -> dict[str, LeadRecord]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data["records"] if isinstance(data, dict) else data
    return {d["doc_num"]: LeadRecord.from_dict(d) for d in items}


def save(path: Path, leads: dict[str, LeadRecord], meta: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(leads.values(), key=lambda l: (l.status != "active", -l.score, -(l.amount_owed or 0)))
    payload = {"meta": meta or {}, "records": [l.to_dict() for l in rows]}
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")


def split_name(owner: str) -> tuple[str, str]:
    """Auditor names are 'LAST FIRST M' (e.g. 'SHATZER REGINA'). Returns (first, last)."""
    name = (owner or "").strip()
    if not name or BUSINESS_RE.search(name):
        return "", name.title()
    name = re.split(r"\s+(?:&|AND)\s+", name)[0]       # first owner only
    parts = name.replace(",", " ").split()
    if len(parts) == 1:
        return "", parts[0].title()
    return " ".join(parts[1:]).title(), parts[0].title()


GHL_COLUMNS = [
    "First Name", "Last Name", "Full Name", "Owner 2", "Address", "City", "State", "Postal Code",
    "Property Address", "Property City", "Property Zip", "Lead Type", "Amount Owed",
    "Delinquent Amount", "Market Value", "Last Sale Date", "Score", "Tier", "Parcel", "Advertised Years", "Absentee", "Date Added",
    "Auditor Link", "Tags",
]


def export_ghl(path: Path, leads: dict[str, LeadRecord]) -> int:
    rows = [l for l in leads.values() if l.status == "active" and l.has_address()]
    rows.sort(key=lambda l: -l.score)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(GHL_COLUMNS)
        for l in rows:
            first, last = split_name(l.owner_name)
            # fall back to property address if the owner has no mailing address
            addr = l.mailing_address or l.property_address
            city = l.mailing_city if l.mailing_address else l.property_city
            state = l.mailing_state if l.mailing_address else "OH"
            zipc = l.mailing_zip if l.mailing_address else l.property_zip
            tags = ["tax delinquent", "hamilton county", l.tier.lower()]
            if l.advertised_years:
                tags.append("advertised delinquent")
            if l.absentee_owner:
                tags.append("absentee")
            if l.foreclosure_flag:
                tags.append("foreclosure flag")
            w.writerow([
                first, last, l.owner_name.title(), l.owner_name_2.title(), addr, city.title(), state, zipc,
                l.property_address, l.property_city.title(), l.property_zip, l.lead_type,
                f"{l.amount_owed:.2f}" if l.amount_owed is not None else "",
                f"{l.delinquent_amount:.2f}" if l.delinquent_amount is not None else "",
                f"{l.market_value:.0f}" if l.market_value else "", l.last_sale_date,
                l.score, l.tier, l.parcel_id, " ".join(l.advertised_years),
                "Yes" if l.absentee_owner else "No", l.date_added, l.auditor_url, ",".join(tags),
            ])
    return len(rows)


def export_dashboard(path: Path, leads: dict[str, LeadRecord], meta: dict) -> None:
    """Compact JSON for the GitHub Pages dashboard (active leads + recently removed)."""
    keep = ("doc_num", "owner_name", "owner_name_2", "mailing_address", "mailing_city", "mailing_state",
            "mailing_zip", "property_address", "property_city", "property_zip", "amount_owed",
            "delinquent_amount", "tax_year", "advertised_years", "absentee_owner", "on_contract", "market_value",
            "foreclosure_flag", "vacant", "last_sale_date", "last_sale_amount", "land_use", "flags", "score",
            "tier", "auditor_url", "date_added", "last_seen", "status", "removed_date")
    rows = [
        {k: getattr(l, k) for k in keep}
        for l in sorted(leads.values(), key=lambda l: -l.score)
        if l.has_address()
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"meta": meta, "records": rows}, separators=(",", ":")), encoding="utf-8")
