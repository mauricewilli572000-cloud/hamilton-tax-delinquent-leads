"""
Property addresses for every Hamilton County parcel, from the CAGIS open-data
Auditor parcel file (one bulk download - no per-parcel page views):

    https://www.cagis.org/Opendata/Auditor/Parcels{YEAR}.gdb.zip
    layer  HAM_PARCELS_MERGED_W_CONDOS (or similar)
    AUDPTYID = Auditor parcel number, ADDRNO / ADDRST / ADDRSF = property address,
    CLASS = land use, MKT_TOTAL_VAL = market value, HMSD_FLAG = homestead (owner-occupied),
    RENT_REG_FLAG = rental registration, ACREDEED = acres

CAGIS asks users of its data to credit "CAGIS" and accept its disclaimer:
https://cagismaps.hamilton-co.org/cagisportal/mapdata/download
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import shutil
import zipfile
from pathlib import Path

import requests

import config

log = logging.getLogger(__name__)

WANTED = {
    "parcel": ("AUDPTYID", "PARCELID", "PARCEL_ID", "PARCELNO"),
    "addr_no": ("ADDRNO", "HNUM", "HOUSENO"),
    "addr_st": ("ADDRST", "STREET", "STNAME"),
    "addr_sf": ("ADDRSF", "SUFFIX", "STSUFFIX"),
    "unit": ("UNIT",),
    "zip": ("ZIPCODE", "ADDRZIP", "ZIP", "ZIP5"),
    "city": ("ADDRCITY", "CITY", "MUNI", "MUNICIPALITY"),
    "land_use": ("CLASS", "LUC", "LANDUSE"),
    "market_value": ("MKT_TOTAL_VAL", "MKTTOTAL", "MARKET_TOTAL"),
    "homestead": ("HMSD_FLAG", "HOMESTEAD"),
    "rental": ("RENT_REG_FLAG", "RENTAL_REG"),
    "acres": ("ACREDEED", "ACRES"),
}


def find_url(session: requests.Session) -> str:
    """Newest Parcels{YEAR}.gdb.zip: from the CAGIS download page, else by trying years."""
    try:
        html = session.get(config.CAGIS_DOWNLOAD_PAGE, timeout=60).text
        links = re.findall(r'href=["\']([^"\']*Parcels(\d{4})\.gdb\.zip)["\']', html, re.I)
        if links:
            href, _ = max(links, key=lambda t: int(t[1]))
            return requests.compat.urljoin(config.CAGIS_DOWNLOAD_PAGE, href)
    except requests.RequestException as e:
        log.info("CAGIS download page not readable (%s) - trying file names", e)
    year = dt.date.today().year
    for y in range(year + 1, year - 4, -1):
        url = config.CAGIS_PARCELS_URL.format(year=y)
        try:
            r = session.head(url, timeout=60, allow_redirects=True)
            if r.status_code == 200:
                return url
        except requests.RequestException:
            continue
    raise RuntimeError("Could not find a CAGIS Parcels{year}.gdb.zip file")


def download(dest_dir: Path) -> Path:
    """Download + unzip the parcel geodatabase. Returns the .gdb folder."""
    s = requests.Session()
    s.headers.update(config.HTTP_HEADERS)
    url = find_url(s)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zpath = dest_dir / url.rsplit("/", 1)[-1]
    log.info("Downloading CAGIS parcels: %s", url)
    with s.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with zpath.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    log.info("  %.0f MB", zpath.stat().st_size / 1e6)
    out = dest_dir / "cagis"
    shutil.rmtree(out, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(out)
    gdbs = list(out.rglob("*.gdb"))
    if not gdbs:
        raise RuntimeError(f"No .gdb folder inside {zpath.name}")
    return gdbs[0]


def _pick(fields: list[str], names: tuple[str, ...]) -> str | None:
    up = {f.upper(): f for f in fields}
    for n in names:
        if n in up:
            return up[n]
    return None


def read_parcels(gdb: Path) -> dict[str, dict]:
    """Return {13-digit parcel: info} from the geodatabase (attributes only)."""
    import pyogrio

    layers = [l[0] for l in pyogrio.list_layers(gdb)]
    best, best_fields = None, None
    for name in layers:
        fields = list(pyogrio.read_info(gdb, layer=name)["fields"])
        if _pick(fields, WANTED["parcel"]) and _pick(fields, WANTED["addr_st"]):
            # prefer the merged-with-condos layer, else the biggest match
            if best is None or "MERGED" in name.upper():
                best, best_fields = name, fields
    if best is None:
        raise RuntimeError(f"No parcel layer with address fields in {gdb.name}; layers={layers}")
    cols = {k: _pick(best_fields, v) for k, v in WANTED.items()}
    use = [c for c in cols.values() if c]
    log.info("CAGIS layer %s, fields: %s", best, {k: v for k, v in cols.items() if v})
    # raw read = plain numpy arrays, no geopandas needed
    meta, _fids, _geom, data = pyogrio.raw.read(gdb, layer=best, columns=use, read_geometry=False)
    names = list(meta["fields"])
    arrays = dict(zip(names, data))
    nrows = len(data[0]) if data else 0

    def val(row, key):
        c = cols.get(key)
        if not c:
            return ""
        v = row[c]
        if hasattr(v, "item"):
            v = v.item()          # numpy scalar -> python
        if v is None or (isinstance(v, float) and v != v):
            return ""
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return str(v).strip()

    out: dict[str, dict] = {}
    for i in range(nrows):
        row = {n: arrays[n][i] for n in names}
        digits = re.sub(r"\D", "", val(row, "parcel"))
        if not digits:
            continue
        digits = digits.zfill(13) if len(digits) < 13 else digits[:13]
        addr = " ".join(x for x in (val(row, "addr_no"), val(row, "addr_st"), val(row, "addr_sf")) if x and x != "0")
        unit = val(row, "unit")
        if unit and addr:
            addr = f"{addr} UNIT {unit}"
        mv = val(row, "market_value")
        info = {
            "address": addr.upper(),
            "zip": (re.search(r"\d{5}", val(row, "zip")) or [""])[0] if val(row, "zip") else "",
            "city": val(row, "city").upper(),
            "land_use": val(row, "land_use"),
            "market_value": float(mv) if mv.replace(".", "", 1).isdigit() else None,
            "homestead": val(row, "homestead").upper() == "Y",
            "rental": val(row, "rental").upper() == "Y",
        }
        cur = out.get(digits)
        if cur is None or (not cur["address"] and info["address"]):
            out[digits] = info
    log.info("CAGIS parcels read: %d (%d with an address)", len(out), sum(1 for v in out.values() if v["address"]))
    return out


def apply(leads: dict, parcels: dict[str, dict]) -> int:
    """Fill property address + extra fields on leads. Returns # matched."""
    n = 0
    for lead in leads.values():
        p = parcels.get(lead.parcel_id.replace("-", ""))
        if not p:
            continue
        n += 1
        if p["address"]:
            lead.property_address = p["address"]
        lead.property_zip = p["zip"] or lead.property_zip
        lead.property_city = p["city"] or lead.property_city
        lead.market_value = p["market_value"]
        lead.homestead = p["homestead"]
        lead.rental_registered = p["rental"]
        if not lead.land_use and p["land_use"]:
            lead.land_use = p["land_use"]
    log.info("CAGIS matched %d of %d leads", n, len(leads))
    return n
