"""Lead record schema - same core fields as the Franklin / Cuyahoga scrapers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class LeadRecord:
    # --- shared core schema (same names as the foreclosure scrapers) ---
    doc_num: str                     # unique key -> parcel number (hyphenated)
    owner_name: str = ""
    mailing_address: str = ""
    mailing_city: str = ""
    mailing_state: str = ""
    mailing_zip: str = ""
    property_address: str = ""
    property_city: str = ""
    property_zip: str = ""
    lead_type: str = "Tax Delinquent"
    case_number: str = ""            # not applicable for tax delinquency
    filing_date: str = ""            # date first certified/advertised, if known
    amount_owed: float | None = None
    interest_rate: str = ""          # n/a
    loan_type: str = ""              # n/a
    county: str = "Hamilton"
    source_url: str = ""

    # --- tax-delinquent specific ---
    parcel_id: str = ""
    owner_name_2: str = ""
    delinquent_amount: float | None = None
    tax_year: str = ""
    land_use: str = ""
    on_contract: bool = False        # contract_yn = Y -> on a delinquent payment plan
    market_value: float | None = None   # CAGIS / Auditor market total value
    foreclosure_flag: bool = False   # CAGIS FORECL_FLAG
    vacant: bool = False             # CAGIS EXLUCODE = VA
    last_sale_date: str = ""         # CAGIS SALDAT
    last_sale_amount: float | None = None
    property_source: str = ""        # cagis | advertised | wedge
    property_lookup: str = ""        # "" | ok | none | error  (Auditor property page lookup)
    advertised_years: list[str] = field(default_factory=list)
    absentee_owner: bool = False
    out_of_state: bool = False
    flags: list[str] = field(default_factory=list)
    score: int = 0
    tier: str = "Cold"
    auditor_url: str = ""

    # --- tracking across runs ---
    date_added: str = ""             # "Added" column on dashboard
    last_seen: str = ""              # snapshot date the parcel was last listed
    status: str = "active"           # active | removed (paid / dropped off list)
    removed_date: str = ""
    amount_history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LeadRecord":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def has_address(self) -> bool:
        return bool(self.mailing_address.strip() or self.property_address.strip())
