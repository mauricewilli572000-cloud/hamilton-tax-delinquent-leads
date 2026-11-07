"""Motivated-seller scoring for tax delinquent parcels (0-100)."""
from __future__ import annotations

import datetime as dt
import re

from .schema import LeadRecord

# "(?<!REAL )" so "TI REAL ESTATE SERVICES LLC" is not treated as a deceased owner's estate
ESTATE_RE = re.compile(r"(?<!REAL )\b(ESTATE|EST OF|HEIRS|DECEASED|DECD|UNKNOWN HEIRS|SURVIVING)\b")
TRUST_RE = re.compile(r"\b(TRUST|TRUSTEE|TR)\b")
BUSINESS_RE = re.compile(r"\b(LLC|L L C|INC|CORP|CORPORATION|COMPANY|CO|LTD|LP|LLP|PARTNERSHIP|BANK|ASSOCIATION|ASSN|CHURCH|MINISTRIES)\b")
RESIDENTIAL_CODES = re.compile(r"^5\d\d$")   # Ohio land-use 500-599 = residential


def _norm_street(s: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", "", (s or "").upper())
    s = re.sub(r"\b(STREET|ST|AVENUE|AVE|ROAD|RD|DRIVE|DR|LANE|LN|COURT|CT|PLACE|PL|BOULEVARD|BLVD)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def score(lead: LeadRecord) -> LeadRecord:
    pts = 0
    flags: list[str] = []
    amt = lead.amount_owed or 0

    if amt >= 15000:
        pts += 30; flags.append("$15k+ owed")
    elif amt >= 7500:
        pts += 24; flags.append("$7.5k+ owed")
    elif amt >= 3000:
        pts += 16; flags.append("$3k+ owed")
    elif amt >= 1000:
        pts += 9

    if lead.advertised_years:
        pts += 18
        flags.append("Advertised delinquent " + ",".join(lead.advertised_years))
        pts += min(4 * (len(lead.advertised_years) - 1), 12)
        if len(lead.advertised_years) > 1:
            flags.append(f"{len(lead.advertised_years)} yrs advertised")

    if lead.tax_year.isdigit():
        ref_year = int((lead.last_seen or dt.date.today().isoformat())[:4])
        yrs = max(0, ref_year - int(lead.tax_year))
        if yrs >= 3:
            pts += 8; flags.append(f"Delinquent since {lead.tax_year}")

    # absentee owner: mailing street differs from property street
    if lead.mailing_address and lead.property_address:
        lead.absentee_owner = (_norm_street(lead.mailing_address) != _norm_street(lead.property_address)
                               and not lead.homestead)
    if lead.absentee_owner:
        pts += 14; flags.append("Absentee owner")
    lead.out_of_state = bool(lead.mailing_state) and lead.mailing_state != "OH"
    if lead.out_of_state:
        pts += 6; flags.append(f"Out of state ({lead.mailing_state})")

    owner = f"{lead.owner_name} {lead.owner_name_2}"
    if ESTATE_RE.search(owner):
        pts += 14; flags.append("Estate / heirs")
    elif TRUST_RE.search(owner):
        pts += 4; flags.append("Trust")
    if BUSINESS_RE.search(owner):
        pts -= 6; flags.append("Business owner")

    if lead.foreclosure_flag:
        pts += 15; flags.append("Foreclosure flag")
    if lead.rental_registered:
        pts += 6; flags.append("Registered rental")
    mv = lead.market_value or 0
    if mv >= 10000 and amt:
        ratio = amt / mv
        if ratio >= 0.10:
            pts += 8; flags.append(f"Owes {ratio:.0%} of value")
        elif ratio >= 0.05:
            pts += 4
    if lead.last_sale_date[:4].isdigit():
        held = dt.date.today().year - int(lead.last_sale_date[:4])
        if held >= 15:
            pts += 6; flags.append(f"Owned since {lead.last_sale_date[:4]}")
    code3 = re.sub(r"\D", "", lead.land_use)[:3]
    if lead.vacant or code3 in ("300", "400", "500"):
        flags.append("Vacant land")

    if lead.on_contract:
        pts -= 8; flags.append("On payment plan")

    code = re.sub(r"\D", "", lead.land_use)[:3]
    if code and RESIDENTIAL_CODES.match(code):
        pts += 5; flags.append("Residential")

    lead.score = max(0, min(100, pts))
    lead.tier = "Hot" if lead.score >= 55 else "Warm" if lead.score >= 32 else "Cold"
    lead.flags = flags
    return lead
