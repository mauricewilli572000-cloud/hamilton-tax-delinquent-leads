# Hamilton County, OH — Tax Delinquent Lead Scraper

Motivated-seller leads from the Hamilton County Auditor's delinquent tax files
(https://www.hamiltoncountyauditor.org/tax_delinquent.asp). Built the same way as
the Franklin / Cuyahoga foreclosure scrapers: nightly GitHub Actions run →
`data/records.json` (accumulates across runs) → GHL CSV → GitHub Pages dashboard.

## What it pulls
| File | Used for |
|---|---|
| `unpaid.xlsx` (Current Unpaid Accounts, updated ~monthly) | **The lead list.** One lead per parcel with a delinquent balance. |
| `AdvertisedDelinquentFile{year}.xlsx` / `AdvertisedRe2ndDelinquentFile{year}.xlsx` (newest 4 years) | Flags parcels that were publicly advertised as delinquent (closer to tax foreclosure) → higher score. Also fills in missing owner/mailing info. |

The Forfeited Land Sale PDFs are not used (sale is every other year, next in 2027).

## How it works
1. Reads the Auditor page, finds the `.xlsx` links and the "current as of" date.
2. Downloads `unpaid.xlsx`. **If it's identical to last run, it stops** (no commit). So it's safe to run daily.
3. Finds the header row and maps columns by name (`src/columns.py`). The layout isn't published, so the parser doesn't depend on fixed column positions.
4. Filters: owed ≥ `$250`, must have a prior-year delinquent balance (if the file has that column), skips government/land-bank owners. All in `config.py`.
5. Merges into `data/records.json` keyed by parcel (`doc_num`):
   - new parcels get an **Added** date
   - parcels that drop off the list are kept but marked **removed** (paid) with a date
   - amount changes are kept in `amount_history`
   - parcels with no address go to `data/pending_records.json` and are retried each run
   - safety stop: if a new file parses to < 30% of the current active leads, it aborts instead of mass-marking leads removed
6. Scores 0–100 → Hot (55+) / Warm (32+) / Cold. Points for amount owed, advertised-delinquent (more for multiple years), delinquent 3+ years, absentee owner, out-of-state owner, estate/heirs, residential land use; minus for LLC/corporate owners.
7. **Property address lookup.** `unpaid.xlsx` only has the owner's *mailing* address
   (`owner_address_1` / `owner_address_2`). The property's own address comes from the
   Auditor property page (`wedge.hcauditor.org/view/re/{parcel}/{year}/summary`), up to
   4,000 per run, highest-scoring leads first, saved to `data/property_cache.json` so each
   parcel is looked up only once. On days the county file is unchanged, the run still
   continues these lookups until they're all done. Advertised files also supply a street
   address when their legal description starts with a house number.
8. Writes `data/ghl_hamilton_tax_delinquent.csv` (GoHighLevel import, active leads only) and `docs/hamilton_tax_delinquent.json` (dashboard).

## Column check
Confirmed against the real files on 2026-11-06:
- `unpaid.xlsx`: parcel_number, owner_name_1, owner_name_2, owner_address_1, owner_address_2, property_class, unpaid_amount, contract_yn, tax_district
- `Advertised*.xlsx`: owner_name, property_description, property_number, assessed_value, delinquent amount, year

If the county changes the layout later, run:

```powershell
cd hamilton-tax-delinquent-leads
pip install -r requirements.txt
python discover_hamilton_delinquent.py
```

Check the "Column mapping" section in the output. If parcel, owner, property address, mailing address and amount look right, you're done. If not, send `discover_output.txt` back, or pin the columns in `config.py`:

```python
COLUMN_OVERRIDES = {"amount_owed": "Total Due", "owner_name": "Owner Name 1"}
```

Then do a full local run: `python run_pipeline.py` and open `data/ghl_hamilton_tax_delinquent.csv`.

## Setup on GitHub (own repo, like Summit)
```powershell
cd hamilton-tax-delinquent-leads
git init
git add .
git commit -m "Hamilton County tax delinquent scraper"
git branch -M main
git remote add origin https://github.com/<your-account>/hamilton-tax-delinquent-leads.git
git push -u origin main
```
1. Repo → **Settings → Actions → General → Workflow permissions** → *Read and write*.
2. Repo → **Settings → Pages** → Source: *Deploy from a branch*, `main`, folder `/docs`.
3. **Actions → Hamilton Tax Delinquent Leads → Run workflow** (tick *force* the first time).

Dashboard: `https://<your-account>.github.io/hamilton-tax-delinquent-leads/`

To add it as a tab on the combined dashboard, point the tab at this repo's
`docs/hamilton_tax_delinquent.json` (same core field names as the foreclosure scrapers).

## Files
```
config.py                         settings, filters, column overrides
run_pipeline.py                   main entry point
discover_hamilton_delinquent.py   one-time column check (run locally)
src/downloader.py                 page + xlsx download (retries)
src/columns.py                    header-row detection and column mapping
src/parser.py                     xlsx → normalized parcel rows
src/scoring.py                    motivated-seller score + flags
src/store.py                      merge/accumulate, GHL CSV, dashboard JSON
src/schema.py                     LeadRecord (shared core fields)
docs/index.html                   dashboard (GitHub Pages)
tests/test_pipeline.py            offline tests with synthetic files
.github/workflows/hamilton-tax-delinquent.yml   daily 7:17am ET
```

## Useful commands
```powershell
python run_pipeline.py --force                                     # rebuild even if unchanged
python run_pipeline.py --local-unpaid raw\unpaid.xlsx --local-advertised "raw\Advertised*.xlsx"
python -m pytest -q
```
