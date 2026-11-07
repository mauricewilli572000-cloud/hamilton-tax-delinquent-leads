"""
Property type from the Ohio Auditor land-use (class) code, with the CAGIS
existing-land-use code (EXLUCODE) as a fallback.

Returns (category, label). Categories used on the dashboard / GHL:
  Single Family, Multi-Family (2-4), Apartments (5+), Condo, Mobile/Manufactured,
  Vacant Land, Commercial, Industrial, Agricultural, Exempt/Government, Other
"""
from __future__ import annotations

import re

LABELS = {
    # residential 500s
    "500": ("Vacant Land", "Residential vacant land"),
    "501": ("Vacant Land", "Residential vacant land (<10 ac)"),
    "502": ("Vacant Land", "Residential vacant land (10-20 ac)"),
    "503": ("Vacant Land", "Residential vacant land (20+ ac)"),
    "510": ("Single Family", "Single family dwelling"),
    "520": ("Multi-Family (2-4)", "Two family dwelling"),
    "530": ("Multi-Family (2-4)", "Three family dwelling"),
    "550": ("Condo", "Condominium unit"),
    "551": ("Condo", "Boataminium"),
    "552": ("Other", "Condo / PUD garage"),
    "555": ("Condo", "Landominium"),
    "560": ("Mobile/Manufactured", "Manufactured home"),
    "569": ("Multi-Family (2-4)", "LIHTC residential"),
    "599": ("Other", "Other residential structure"),
    # commercial 400s
    "400": ("Vacant Land", "Commercial vacant land"),
    "401": ("Apartments (5+)", "Apartments 4-19 units"),
    "402": ("Apartments (5+)", "Apartments 20-39 units"),
    "403": ("Apartments (5+)", "Apartments 40+ units"),
    "404": ("Apartments (5+)", "Retirement / senior housing"),
    "410": ("Commercial", "Motel"),
    "411": ("Commercial", "Hotel"),
    "415": ("Mobile/Manufactured", "Mobile home / trailer park"),
    "419": ("Apartments (5+)", "Other commercial housing"),
    "420": ("Commercial", "Small retail (<10,000 sq ft)"),
    "421": ("Commercial", "Supermarket"),
    "425": ("Commercial", "Neighborhood shopping center"),
    "429": ("Commercial", "Other retail"),
    "430": ("Commercial", "Restaurant / cafeteria / bar"),
    "431": ("Commercial", "Office / apartment over (mixed use)"),
    "435": ("Commercial", "Drive-in restaurant"),
    "441": ("Commercial", "Funeral home"),
    "442": ("Commercial", "Medical clinic / office"),
    "444": ("Commercial", "Full-service bank"),
    "447": ("Commercial", "Office building (1-2 story)"),
    "448": ("Commercial", "Office building (3+ story)"),
    "449": ("Commercial", "Other commercial office"),
    "450": ("Commercial", "Auto sales / service"),
    "452": ("Commercial", "Auto service station"),
    "453": ("Commercial", "Car wash"),
    "454": ("Commercial", "Auto dealer"),
    "455": ("Commercial", "Commercial garage"),
    "456": ("Commercial", "Parking garage / lot"),
    "480": ("Commercial", "Commercial warehouse"),
    "499": ("Commercial", "Other commercial"),
    # industrial / agricultural / exempt
    "300": ("Vacant Land", "Industrial vacant land"),
    "100": ("Vacant Land", "Agricultural vacant land"),
    "645": ("Exempt/Government", "Metropolitan housing authority"),
}

RANGES = [  # (low, high, category, label) for codes not listed above
    (511, 516, "Single Family", "Single family dwelling (acreage)"),
    (521, 526, "Multi-Family (2-4)", "Two family dwelling (acreage)"),
    (531, 536, "Multi-Family (2-4)", "Three family dwelling (acreage)"),
    (540, 546, "Mobile/Manufactured", "Mobile home"),
    (504, 509, "Vacant Land", "Residential vacant land"),
    (500, 599, "Other", "Other residential"),
    (400, 499, "Commercial", "Commercial"),
    (300, 399, "Industrial", "Industrial"),
    (100, 199, "Agricultural", "Agricultural"),
    (600, 699, "Exempt/Government", "Exempt / government"),
    (700, 899, "Other", "Utility / other"),
]

EXLU = {
    "SF": ("Single Family", "Single family"), "TF": ("Multi-Family (2-4)", "Two family"),
    "MF": ("Apartments (5+)", "Multi family"), "MH": ("Mobile/Manufactured", "Mobile home"),
    "VA": ("Vacant Land", "Vacant"), "C": ("Commercial", "Commercial"), "O": ("Commercial", "Office"),
    "MU": ("Commercial", "Mixed use"), "LI": ("Industrial", "Light industrial"),
    "HI": ("Industrial", "Heavy industrial"), "AG": ("Agricultural", "Agriculture"),
    "IN": ("Exempt/Government", "Institutional"), "ED": ("Exempt/Government", "Educational"),
    "PR": ("Exempt/Government", "Parks / recreation"), "PS": ("Exempt/Government", "Public service"),
    "PU": ("Other", "Public utility"), "CH": ("Apartments (5+)", "Congregate housing"),
}

HOUSE_CATEGORIES = {"Single Family", "Multi-Family (2-4)", "Condo", "Mobile/Manufactured"}


def classify(land_use: str, exlucode: str = "") -> tuple[str, str]:
    code = re.sub(r"\D", "", land_use or "")[:3]
    if code in LABELS:
        return LABELS[code]
    if code:
        n = int(code)
        for lo, hi, cat, label in RANGES:
            if lo <= n <= hi:
                return cat, f"{label} ({code})"
    if (exlucode or "").upper() in EXLU:
        return EXLU[exlucode.upper()]
    return "Other", f"Unknown ({code})" if code else "Unknown"
