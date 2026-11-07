"""
Header detection + column mapping for the Auditor's delinquent XLSX files.

The Auditor's files have no published layout we could read, and the layout can
change year to year, so instead of hard-coding column letters we:
  1. find the header row (the first row with several recognisable header words),
  2. map each header to a canonical field using ordered alias rules,
  3. let config.COLUMN_OVERRIDES pin any column by its exact header text.
"""
from __future__ import annotations

import re

# Canonical field -> ordered list of (match_type, normalized_alias).
#   "eq"  = normalized header equals alias
#   "in"  = alias is a substring of the normalized header
# Earlier rules win. Normalized = lowercase, letters+digits only.
FIELD_RULES: dict[str, list[tuple[str, str]]] = {
    "parcel": [
        ("eq", "parcel"), ("eq", "parcelid"), ("eq", "parcelnumber"), ("eq", "parcelno"),
        ("eq", "parcelnum"), ("eq", "ppn"), ("eq", "pin"), ("eq", "propertynumber"),
        ("in", "parcel"), ("in", "propertynumber"),
    ],
    "owner_name_2": [
        ("eq", "owner2"), ("eq", "ownername2"), ("eq", "name2"), ("eq", "owner2name"),
        ("in", "ownername2"), ("in", "secondowner"),
    ],
    "owner_name": [
        ("eq", "owner"), ("eq", "ownername"), ("eq", "owner1"), ("eq", "ownername1"),
        ("eq", "name"), ("eq", "name1"), ("eq", "taxpayer"), ("eq", "taxpayername"),
        ("in", "ownername"), ("in", "owner"), ("in", "taxpayer"),
    ],
    # --- mailing ---
    "mail_city_state_zip": [
        ("in", "mailcitystatezip"), ("in", "mailingcitystatezip"), ("in", "citystatezip"),
        ("in", "cityzip"),
    ],
    "mail_city": [("eq", "mailcity"), ("eq", "mailingcity"), ("in", "mailcity"), ("in", "mailingcity"),
                  ("in", "billcity")],
    "mail_state": [("eq", "mailstate"), ("eq", "mailingstate"), ("in", "mailstate"),
                   ("in", "mailingstate"), ("in", "billstate"), ("eq", "state"), ("eq", "st")],
    "mail_zip": [("eq", "mailzip"), ("eq", "mailingzip"), ("in", "mailzip"), ("in", "mailingzip"),
                 ("in", "billzip")],
    # mailing street lines - may be several (Mail Addr 1/2/3) -> collected in order
    "mail_line": [
        ("in", "owneraddress"), ("in", "owneraddr"),
        ("in", "mailingaddress"), ("in", "mailaddress"), ("in", "mailaddr"), ("in", "mailline"),
        ("in", "billingaddress"), ("in", "billaddr"), ("in", "taxbill"), ("in", "mailing"),
        ("eq", "mailname"),
    ],
    # --- property / location ---
    "prop_house_no": [("eq", "housenumber"), ("eq", "houseno"), ("eq", "streetnumber"),
                      ("eq", "stno"), ("eq", "addrno"), ("eq", "locationnumber"), ("in", "housenum")],
    "prop_street": [("eq", "streetname"), ("eq", "street"), ("eq", "stname"),
                    ("eq", "locationstreet"), ("in", "streetname")],
    "prop_suffix": [("eq", "suffix"), ("eq", "streetsuffix"), ("eq", "sttype")],
    "prop_address": [
        ("in", "propertyaddress"), ("in", "propertylocation"), ("in", "locationaddress"),
        ("in", "situs"), ("in", "siteaddress"), ("in", "propaddr"), ("eq", "location"),
        ("eq", "address"), ("eq", "propertyaddr"),
    ],
    "prop_description": [("in", "propertydescription"), ("in", "legaldescription"), ("eq", "description")],
    "prop_city": [("in", "propertycity"), ("in", "locationcity"), ("in", "situscity"),
                  ("eq", "city"), ("in", "municipality"), ("eq", "taxdistrictname")],
    "prop_zip": [("in", "propertyzip"), ("in", "locationzip"), ("in", "situszip"), ("eq", "zip"),
                 ("eq", "zipcode")],
    # --- money ---
    "delinquent_amount": [
        ("eq", "priordelinquent"), ("eq", "delinquent"), ("eq", "totaldelinquent"),
        ("in", "delinquentbalance"), ("in", "delinquentamount"), ("in", "delinquentdue"),
        ("in", "priordelq"), ("in", "delqamt"), ("in", "delinquenttax"),
    ],
    "amount_owed": [
        ("eq", "totaldue"), ("eq", "totalowed"), ("eq", "amountdue"), ("eq", "balancedue"),
        ("eq", "totalunpaid"), ("eq", "unpaid"), ("eq", "unpaidbalance"), ("eq", "balance"),
        ("eq", "total"), ("eq", "amount"), ("in", "totaldue"), ("in", "totalowed"),
        ("in", "amountdue"), ("in", "balance"), ("in", "unpaid"), ("in", "total"),
    ],
    # --- other ---
    "tax_year": [("in", "certifieddelinquentyear"), ("in", "certyear"), ("in", "firstdelinquent"),
                 ("eq", "taxyear"), ("eq", "year"), ("in", "taxyear"), ("in", "delqyear")],
    "certified_date": [("in", "certifieddate"), ("in", "certdate"), ("in", "delinquentdate"),
                       ("in", "advertiseddate")],
    "contract": [("in", "contractyn"), ("in", "contract"), ("in", "paymentplan")],
    "land_use": [("eq", "landuse"), ("eq", "landusecode"), ("eq", "class"), ("eq", "propertyclass"),
                 ("in", "landuse"), ("in", "usecode"), ("in", "propertyclass")],
}

# Order in which fields claim columns (specific before generic).
CLAIM_ORDER = [
    "parcel", "owner_name_2", "owner_name",
    "mail_city_state_zip", "mail_city", "mail_state", "mail_zip", "mail_line",
    "prop_house_no", "prop_street", "prop_suffix", "prop_address", "prop_description", "prop_city", "prop_zip",
    "delinquent_amount", "tax_year", "certified_date", "amount_owed", "contract", "land_use",
]
MULTI_FIELDS = {"mail_line"}  # may map to several columns

# Headers containing these fragments are never used for the field.
EXCLUDE: dict[str, tuple[str, ...]] = {
    "amount_owed": ("paid", "value", "rate", "credit", "acre", "assess", "market", "appraise"),
    "delinquent_amount": ("paid", "year", "date", "flag", "plan"),
    "owner_name": ("mail", "addr", "city", "zip"),
    "mail_line": ("city", "state", "zip"),
    "tax_year": ("amount", "due", "paid"),
}


def _excluded(field_name: str, header_norm: str) -> bool:
    for frag in EXCLUDE.get(field_name, ()):
        if frag in header_norm and not (frag == "paid" and "unpaid" in header_norm):
            return True
    return False

HEADER_HINTS = ("parcel", "owner", "name", "address", "addr", "mail", "city", "zip",
                "due", "delinq", "total", "balance", "amount", "tax", "year", "location")


def norm(h) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def find_header_row(rows: list[list], scan: int = 25) -> int:
    """Return index of the most header-like row in the first `scan` rows."""
    best_idx, best_score = 0, -1
    for i, row in enumerate(rows[:scan]):
        cells = [norm(c) for c in row if c not in (None, "")]
        if len(cells) < 3:
            continue
        hits = sum(1 for c in cells if any(h in c for h in HEADER_HINTS))
        # header rows are mostly text, not numbers
        texty = sum(1 for c in row if isinstance(c, str))
        score = hits * 3 + texty
        if hits >= 2 and score > best_score:
            best_idx, best_score = i, score
    return best_idx


def map_columns(headers: list, overrides: dict[str, str] | None = None) -> dict[str, list[int]]:
    """Map canonical field -> list of column indexes."""
    normed = [norm(h) for h in headers]
    used: set[int] = set()
    mapping: dict[str, list[int]] = {}

    # 1. explicit overrides by exact (case-insensitive) header text
    for field_name, header_text in (overrides or {}).items():
        target = norm(header_text)
        idxs = [i for i, h in enumerate(normed) if h == target]
        if idxs:
            mapping.setdefault(field_name, []).extend(idxs)
            used.update(idxs)

    # 2. alias rules
    for field_name in CLAIM_ORDER:
        if field_name in mapping:
            continue
        for mode, alias in FIELD_RULES[field_name]:
            hits = [
                i for i, h in enumerate(normed)
                if i not in used and h and not _excluded(field_name, h) and ((mode == "eq" and h == alias) or (mode == "in" and alias in h))
            ]
            if not hits:
                continue
            if field_name in MULTI_FIELDS:
                mapping[field_name] = hits
                used.update(hits)
                break
            mapping[field_name] = [hits[0]]
            used.add(hits[0])
            break

    # "state" alone is only a mailing state if we also found mailing columns
    if "mail_state" in mapping and not any(k in mapping for k in ("mail_line", "mail_city", "mail_zip")):
        h = normed[mapping["mail_state"][0]]
        if h in ("state", "st"):
            mapping.pop("mail_state")
    return mapping


def describe_mapping(headers: list, mapping: dict[str, list[int]]) -> str:
    lines = []
    for f in CLAIM_ORDER:
        if f in mapping:
            cols = ", ".join(f"'{headers[i]}'" for i in mapping[f])
            lines.append(f"  {f:<20} <- {cols}")
    unmapped = [str(h) for i, h in enumerate(headers)
                if h not in (None, "") and not any(i in v for v in mapping.values())]
    if unmapped:
        lines.append(f"  (unused columns: {', '.join(unmapped)})")
    return "\n".join(lines)
