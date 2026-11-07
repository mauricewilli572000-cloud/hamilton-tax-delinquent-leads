"""
Offline tests with synthetic workbooks in two different plausible layouts.
Run:  python -m pytest -q
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

import config
import run_pipeline
from src.parser import normalize_parcel, parse_file, split_mailing
from src.store import split_name


def make_xlsx(path: Path, rows: list[list]) -> Path:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


# Layout A: title rows, Mail Addr 1/2/3 lines, single Total Due column
UNPAID_A = [
    ["Hamilton County Auditor - Unpaid Accounts"],
    ["File current as of 10/31/2026"],
    [],
    ["Parcel Number", "Owner Name", "Owner Name 2", "Property Location", "Mail Addr 1", "Mail Addr 2",
     "Mail Addr 3", "Land Use", "Prior Delinquent", "Total Due"],
    ["247-0001-0003-00", "SMITH JOHN A", "", "6436 HAMILTON AVE", "6436 HAMILTON AVE", "",
     "CINCINNATI OH 45224", "510", "$8,200.10", "$9,904.55"],
    [2060009005300, "JONES MARY ESTATE OF", "", "2280 HARRISON AVE", "C/O BOB JONES", "PO BOX 44",
     "ATLANTA, GA 30301-1234", "510", 15000, 17321.4],
    ["073-0003-0193-00", "ACME HOLDINGS LLC", "", "ELSINORE PL", "100 MAIN ST STE 4", "",
     "MASON OH 45040", "500", 900.0, 1210.0],
    ["590-0382-0084-00", "CITY OF CINCINNATI", "", "10025 HAMILTON AVE", "801 PLUM ST", "",
     "CINCINNATI OH 45202", "640", 5000, 6000],
    ["241-0001-0127-00", "WAY IMOGENE & WILLIAM E", "", "105 W SIXTY-NINTH ST", "105 W SIXTY-NINTH ST", "",
     "CINCINNATI OH 45216", "510", 0, 860.82],          # current only -> filtered
    ["111-0001-0001-00", "NOADDR PERSON", "", "", "", "", "", "510", 700, 800],   # no address -> pending
    ["", "TOTAL", "", "", "", "", "", "", 1, 1],
]

# Layout B: separate house/street + separate mailing city/state/zip, one row per year
UNPAID_B = [
    ["PARCEL_ID", "OWNER1", "HOUSE_NO", "STREET_NAME", "SUFFIX", "MAIL_ADDRESS", "MAIL_CITY", "MAIL_STATE",
     "MAIL_ZIP", "TAX_YEAR", "DELQ_AMT", "BALANCE_DUE"],
    ["5900382008400", "BROWN TOM", "10025", "HAMILTON", "AVE", "77 OAK LN", "DAYTON", "OH", "45402",
     "2021", 1200, 1200],
    ["5900382008400", "BROWN TOM", "10025", "HAMILTON", "AVE", "77 OAK LN", "DAYTON", "OH", "45402",
     "2023", 900, 900],
]

ADVERTISED = [
    ["PARCEL", "OWNER", "ADDRESS", "AMOUNT"],
    ["2470001000300", "SMITH JOHN A", "6436 HAMILTON AVE", 8200.10],
]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    for name in ("RECORDS_JSON", "PENDING_JSON", "STATE_JSON", "GHL_CSV", "DASHBOARD_JSON", "PROPERTY_CACHE_JSON"):
        monkeypatch.setattr(config, name, tmp_path / Path(getattr(config, name)).name)
    monkeypatch.setattr(config, "MIN_AMOUNT_OWED", 250.0)
    return tmp_path


def test_helpers():
    assert normalize_parcel(2470001000300) == ("247-0001-0003-00", "2470001000300")
    assert normalize_parcel(206000900530.0)[0] == "020-6000-9005-30"   # leading-zero restore
    assert split_mailing(["C/O BOB", "PO BOX 44", "ATLANTA, GA 30301-1234"]) == ("PO BOX 44", "ATLANTA", "GA", "30301")
    assert split_name("SMITH JOHN A") == ("John A", "Smith")
    assert split_name("ACME HOLDINGS LLC") == ("", "Acme Holdings Llc")


def test_layout_b_sums_years(tmp_path):
    rows = parse_file(make_xlsx(tmp_path / "b.xlsx", UNPAID_B))
    assert len(rows) == 1
    r = rows[0]
    assert r["parcel_id"] == "590-0382-0084-00"
    assert r["property_address"] == "10025 HAMILTON AVE"
    assert (r["mailing_city"], r["mailing_state"], r["mailing_zip"]) == ("DAYTON", "OH", "45402")
    assert r["amount_owed"] == 2100 and r["delinquent_amount"] == 2100
    assert r["tax_year"] == "2021"


def test_full_pipeline_two_runs(sandbox):
    unpaid = make_xlsx(sandbox / "unpaid.xlsx", UNPAID_A)
    adv = make_xlsx(sandbox / "AdvertisedDelinquentFile2025.xlsx", ADVERTISED)
    assert run_pipeline.main(["--local-unpaid", str(unpaid), "--local-advertised", str(adv),
                              "--as-of", "2026-10-31", "--no-enrich", "--no-cagis"]) == 0

    data = json.loads(config.RECORDS_JSON.read_text())
    recs = {r["doc_num"]: r for r in data["records"]}
    assert set(recs) == {"247-0001-0003-00", "206-0009-0053-00", "073-0003-0193-00"}
    smith = recs["247-0001-0003-00"]
    assert smith["advertised_years"] == ["2025"] and smith["lead_type"] == "Tax Delinquent - Advertised"
    assert smith["absentee_owner"] is False
    jones = recs["206-0009-0053-00"]
    assert jones["out_of_state"] and jones["absentee_owner"] and jones["tier"] == "Hot"
    assert jones["mailing_address"] == "PO BOX 44"
    pending = json.loads(config.PENDING_JSON.read_text())["records"]
    assert [p["doc_num"] for p in pending] == ["111-0001-0001-00"]
    with config.GHL_CSV.open(encoding="utf-8-sig") as f:
        ghl = list(csv.DictReader(f))
    assert len(ghl) == 3 and ghl[0]["Full Name"] == "Jones Mary Estate Of"

    # second snapshot: Smith paid off, Acme amount changed
    rows2 = [r for r in UNPAID_A if not (r and r[0] == "247-0001-0003-00")]
    rows2 = [r if not (r and r[0] == "073-0003-0193-00") else r[:8] + [1500, 1800] for r in rows2]
    unpaid2 = make_xlsx(sandbox / "unpaid2.xlsx", rows2)
    assert run_pipeline.main(["--local-unpaid", str(unpaid2), "--as-of", "2026-11-30", "--no-enrich", "--no-cagis"]) == 0
    recs = {r["doc_num"]: r for r in json.loads(config.RECORDS_JSON.read_text())["records"]}
    assert recs["247-0001-0003-00"]["status"] == "removed"
    assert recs["247-0001-0003-00"]["removed_date"] == "2026-11-30"
    assert [h["amount"] for h in recs["073-0003-0193-00"]["amount_history"]] == [1210.0, 1800.0]

    # unchanged file -> no-op
    assert run_pipeline.main(["--local-unpaid", str(unpaid2), "--no-enrich", "--no-cagis"]) == 0


# ---------------------------------------------------------------------------
# REAL layouts, copied from discover_output.txt (2026-11-06)
# ---------------------------------------------------------------------------
REAL_UNPAID = [
    ["parcel_number", "owner_name_1", "owner_name_2", "owner_address_1", "owner_address_2", "property_class",
     "unpaid_amount", "contract_yn", "tax_district"],
    ["0010002031600", "CHELSEA MOORE INC", None, "5531 FIRETHORN CT ", "CINCINNATI,OH  45242", 400, 0.91, "N", "001"],
    ["0010004008700", "SCHMIDT WILLIAM A", "& PAULA", "6587 RIPPLEWOOD LN ", "CINCINNATI,OH  452301469", 510, 5543.79, "N", "001"],
    ["0010004026300", "BASHIR SAAD &", "KAISERA SAAD BASHIR", "6596 SPINDLEWICK LN ", "CINCINNATI,OH  452302433", 510, 3428, "Y", "001"],
    ["0020002012800", "EVANS JEFFREY & JENNIFER", None, "PO BOX 9", "MASON,OH  45040", 510, 3713.39, "N", "001"],
    ["0060001001400", "FARES J RADEL", "FUNERAL HOMES INC", "822 YORK ST ", "NEWPORT,KY  41071", 441, 809.19, "N", "001"],
    ["2180057010600", "TI REAL ESTATE SERVICES LLC", None, "1 MAIN ST", "CINCINNATI,OH  45202", 500, 17921.39, "N", "001"],
]
REAL_ADV = [
    ["owner_name", "property_description", "property_number", "assessed_value", "delinquent amount", "year",
     None, None, None, None],
    ["EVANS JEFFREY & JENNIFER", "6313 CAMBRIDGE AVE       216.46 X 526.34 IRR      PT LOT 12 J GERRARDS SUB",
     "0020002012800", 317950, 3713.39, 2024, None, None, None, None],
    ["ARIF NARGIS", "RAINBOW LN 54 X 170.51   IRR LOT 63 RAINBOW HILL  SUB SEC B-2", "0010001023600",
     165430, 2011.94, 2024, None, None, None, None],
]


def test_real_unpaid_layout(tmp_path):
    rows = {r["parcel_id"]: r for r in parse_file(make_xlsx(tmp_path / "unpaid.xlsx", REAL_UNPAID))}
    s = rows["001-0004-0087-00"]
    assert (s["mailing_address"], s["mailing_city"], s["mailing_state"], s["mailing_zip"]) == \
        ("6587 RIPPLEWOOD LN", "CINCINNATI", "OH", "45230")
    assert s["owner_name_2"] == "& PAULA" and s["amount_owed"] == 5543.79 and s["land_use"] == "510"
    assert rows["001-0004-0263-00"]["on_contract"] is True
    assert rows["006-0001-0014-00"]["mailing_state"] == "KY"
    assert rows["002-0002-0128-00"]["mailing_address"] == "PO BOX 9"


def test_real_advertised_layout(tmp_path):
    rows = {r["parcel_id"]: r for r in parse_file(make_xlsx(tmp_path / "AdvertisedDelinquentFile2025.xlsx", REAL_ADV))}
    assert rows["002-0002-0128-00"]["property_address"] == "6313 CAMBRIDGE AVE"
    assert rows["001-0001-0236-00"]["property_address"] == ""          # no house number -> leave blank
    assert rows["002-0002-0128-00"]["tax_year"] == "2024"


def test_real_pipeline(sandbox):
    unpaid = make_xlsx(sandbox / "unpaid.xlsx", REAL_UNPAID)
    adv = make_xlsx(sandbox / "AdvertisedDelinquentFile2025.xlsx", REAL_ADV)
    # pre-seed the property cache so no network is needed
    config.PROPERTY_CACHE_JSON.write_text(json.dumps({
        "0010004008700": {"status": "ok", "address": "6587 RIPPLEWOOD LN", "date": "2026-11-06"},
    }))
    assert run_pipeline.main(["--local-unpaid", str(unpaid), "--local-advertised", str(adv),
                              "--as-of", "2026-10-31", "--no-enrich", "--no-cagis"]) == 0
    recs = {r["doc_num"]: r for r in json.loads(config.RECORDS_JSON.read_text())["records"]}
    assert "001-0002-0316-00" not in recs                     # $0.91 filtered
    assert len(recs) == 5                                      # all have a mailing address now
    s = recs["001-0004-0087-00"]
    assert s["property_address"] == "6587 RIPPLEWOOD LN" and s["property_zip"] == "45230"
    assert s["absentee_owner"] is False
    e = recs["002-0002-0128-00"]
    assert e["advertised_years"] == ["2025"] and e["property_address"] == "6313 CAMBRIDGE AVE"
    assert e["absentee_owner"] is True
    assert "Estate / heirs" not in recs["218-0057-0106-00"]["flags"]
    assert "On payment plan" in recs["001-0004-0263-00"]["flags"]
    assert "Out of state (KY)" in recs["006-0001-0014-00"]["flags"]
    assert recs["001-0004-0087-00"]["auditor_url"].startswith("https://wedge.hcauditor.org/view/re/0010004008700/")


def test_parse_wedge_header():
    from src.enrich import parse_address
    html = """<html><head><script>var x='Address foo';</script></head><body>
      <div class="parcel"><span>Parcel ID</span><span>206-0009-0053-00</span>
      <span>Address</span><span>2280 HARRISON AVE</span><span>Index Order</span><span>Parcel Number</span></div>
      <div>Tax Bill Mail Address</div><div>3 E FOURTH ST</div></body></html>"""
    assert parse_address(html) == "2280 HARRISON AVE"
    doubled = "Parcel ID 247-0001-0003-00 247-0001-0003-00 Address 6436 HAMILTON AVE 6436 HAMILTON AVE Index Order"
    assert parse_address(doubled) == "6436 HAMILTON AVE"
    assert parse_address("<p>nothing here</p>") == ""


def test_wedge_page_check_and_cache_migration(tmp_path):
    from src import enrich
    real = "<span>Parcel ID</span><span>206-0009-0053-00</span><span>Address</span><span>2280 HARRISON AVE</span><span>Index Order</span>"
    assert enrich.is_property_page(real, "2060009005300")
    assert not enrich.is_property_page("<h1>Too many requests</h1>", "2060009005300")
    p = tmp_path / "cache.json"
    p.write_text(json.dumps({
        "a": {"status": "ok", "address": "1 MAIN ST"},          # v1 ok -> kept
        "b": {"status": "none", "address": ""},                  # v1 none -> dropped (retry)
        "c": {"status": "none", "address": "", "v": 2},          # v2 none -> kept
    }))
    assert set(enrich.load_cache(p)) == {"a", "c"}


def test_cagis_csv(sandbox):
    from src.cagis import parse_saldat
    assert parse_saldat("11567") == "1967-01-15" and parse_saldat("0") == ""
    hdr = ["BOOK", "PAGE", "PARCEL", "MLTOWN", "PROPTYID", "OWNNM1", "MKTLND", "MKTIMP", "CLASS", "ADDRNO",
           "ADDRST", "ADDRSF", "SALDAT", "SALAMT", "PARCELID", "UNIT", "FORECL_FLAG", "DELIQ_TAXES", "EXLUCODE"]
    rows = [
        ["001", "0004", "0087", "0", "0010004008700", "SCHMIDT", "50000", "200000", "510", "6587", "RIPPLEWOOD",
         "LN", "60104", "150000", "00100040087", "", "N", "5543.79", "SF"],
        # PROPTYID blank -> matched via PARCELID + MLTOWN
        ["002", "0002", "0128", "0", "", "EVANS", "10000", "20000", "510", "6313", "CAMBRIDGE", "AVE",
         "31599", "40000", "00200020128", "", "Y", "3713.39", "SF"],
        ["218", "0057", "0106", "0", "2180057010600", "TI", "15000", "0", "500", "0", "MAIN", "ST",
         "0", "0", "21800570106", "", "N", "", "VA"],
        ["999", "0001", "0001", "0", "9990001000100", "OTHER", "1", "1", "510", "1", "X", "ST", "0", "0",
         "99900010001", "", "N", "", "SF"],
    ]
    path = sandbox / "Hamilton County Parcels July 2026.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(hdr); w.writerows(rows)
    unpaid = make_xlsx(sandbox / "unpaid.xlsx", REAL_UNPAID)
    assert run_pipeline.main(["--local-unpaid", str(unpaid), "--as-of", "2026-10-31", "--no-enrich",
                              "--cagis-csv", str(path)]) == 0
    recs = {r["doc_num"]: r for r in json.loads(config.RECORDS_JSON.read_text())["records"]}
    s = recs["001-0004-0087-00"]
    assert s["property_address"] == "6587 RIPPLEWOOD LN" and s["market_value"] == 250000
    assert not s["absentee_owner"] and s["property_zip"] == "45230" and s["last_sale_date"] == "2004-06-01"
    e = recs["002-0002-0128-00"]
    assert e["property_address"] == "6313 CAMBRIDGE AVE" and e["absentee_owner"] and e["foreclosure_flag"]
    assert "Foreclosure flag" in e["flags"] and "Owes 12% of value" in e["flags"] and "Owned since 1999" in e["flags"]
    v = recs["218-0057-0106-00"]
    assert v["property_address"] == "MAIN ST" and "Vacant land" in v["flags"]
    st = json.loads(config.STATE_JSON.read_text())
    assert st["cagis_snapshot"] == "2026-10-31" and st["cagis_file"] == path.name
