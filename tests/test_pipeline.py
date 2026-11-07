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
    for name in ("RECORDS_JSON", "PENDING_JSON", "STATE_JSON", "GHL_CSV", "DASHBOARD_JSON"):
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
                              "--as-of", "2026-10-31"]) == 0

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
    assert run_pipeline.main(["--local-unpaid", str(unpaid2), "--as-of", "2026-11-30"]) == 0
    recs = {r["doc_num"]: r for r in json.loads(config.RECORDS_JSON.read_text())["records"]}
    assert recs["247-0001-0003-00"]["status"] == "removed"
    assert recs["247-0001-0003-00"]["removed_date"] == "2026-11-30"
    assert [h["amount"] for h in recs["073-0003-0193-00"]["amount_history"]] == [1210.0, 1800.0]

    # unchanged file -> no-op
    assert run_pipeline.main(["--local-unpaid", str(unpaid2)]) == 0
