"""
Hamilton County, OH - Tax Delinquent lead scraper configuration.

Source page: https://www.hamiltoncountyauditor.org/tax_delinquent.asp
"""
from pathlib import Path

COUNTY = "Hamilton"
STATE = "OH"
LEAD_TYPE = "Tax Delinquent"

# --------------------------------------------------------------------------
# Source URLs
# --------------------------------------------------------------------------
BASE_URL = "https://www.hamiltoncountyauditor.org"
SOURCE_PAGE = f"{BASE_URL}/tax_delinquent.asp"
UNPAID_URL = f"{BASE_URL}/download/Delinquent/unpaid.xlsx"

# Fallback list if the page can't be scraped for links. The pipeline normally
# discovers every *.xlsx link on SOURCE_PAGE automatically.
ADVERTISED_FALLBACK_URLS = [
    f"{BASE_URL}/download/Delinquent/AdvertisedDelinquentFile2025.xlsx",
    f"{BASE_URL}/download/Delinquent/AdvertisedRe2ndDelinquentFile2024.xlsx",
    f"{BASE_URL}/download/Delinquent/AdvertisedRe2ndDelinquentFile2023.xlsx",
]
# How many years of "Advertised" files to pull (newest first). They are used to
# flag parcels that have been certified/advertised delinquent, which raises
# their score. Older lists add little.
ADVERTISED_YEARS_BACK = 4

# Auditor property page for each parcel: dashboard link + property address lookup.
# {year} is the tax year, e.g. 2025 for "2025 Payable 2026".
WEDGE_URL = "https://wedge.hcauditor.org/view/re/{parcel_digits}/{year}/summary"
AUDITOR_PARCEL_URL = WEDGE_URL

# Property address lookups (unpaid.xlsx has only the owner's MAILING address).
# Cached forever in data/property_cache.json; each run does up to this many,
# highest-score leads first, so a big first batch fills in over a few days.
ENRICH_MAX_PER_RUN = 4000
ENRICH_TIME_BUDGET_SEC = 35 * 60
ENRICH_WORKERS = 2
ENRICH_DELAY = 0.6            # seconds between requests (auto-slows if the site throttles)

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "*/*",
}
HTTP_TIMEOUT = 120
HTTP_RETRIES = 4

# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------
# Ignore tiny balances (rounding, a single special assessment, etc.).
MIN_AMOUNT_OWED = 250.00
# Only keep rows that actually have a DELINQUENT (prior-year) balance when the
# file has a separate delinquent column. If the file only has a total, all
# rows above MIN_AMOUNT_OWED are kept.
REQUIRE_DELINQUENT_BALANCE = True
# Skip government / exempt owners - not sellers.
EXCLUDE_OWNER_PATTERNS = [
    r"\bCITY OF\b", r"\bVILLAGE OF\b", r"\bTOWNSHIP\b", r"\bBOARD OF\b",
    r"\bCOUNTY COMM", r"\bHAMILTON COUNTY\b", r"\bSTATE OF OHIO\b",
    r"\bUNITED STATES\b", r"\bLAND REUTILIZATION\b", r"\bLAND BANK\b",
    r"\bPORT AUTHORITY\b", r"\bMETROPOLITAN HOUSING\b", r"\bSCHOOL DIST",
    r"\bRAILROAD\b", r"\bRAILWAY\b",
]

# --------------------------------------------------------------------------
# Column mapping
# --------------------------------------------------------------------------
# The parser auto-detects columns from header names (see src/columns.py).
# If a column is detected wrong, run `python discover_hamilton_delinquent.py`
# and pin it here using the EXACT header text from the file, e.g.:
#   COLUMN_OVERRIDES = {"amount_owed": "Total Due", "owner_name": "Owner Name 1"}
COLUMN_OVERRIDES: dict[str, str] = {}

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "raw"                      # downloaded xlsx (gitignored)
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
RECORDS_JSON = DATA_DIR / "records.json"
PENDING_JSON = DATA_DIR / "pending_records.json"   # leads missing an address
STATE_JSON = DATA_DIR / "state.json"
PROPERTY_CACHE_JSON = DATA_DIR / "property_cache.json"
GHL_CSV = DATA_DIR / "ghl_hamilton_tax_delinquent.csv"
DASHBOARD_JSON = DOCS_DIR / "hamilton_tax_delinquent.json"
