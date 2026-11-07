"""Download the delinquent files from the Hamilton County Auditor site."""
from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

import config

log = logging.getLogger(__name__)
AS_OF_RE = re.compile(r"current\s+as\s+of\s+(\d{1,2})/(\d{1,2})/(\d{4})", re.I)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(config.HTTP_HEADERS)
    return s


def _get(session: requests.Session, url: str, **kw) -> requests.Response:
    last = None
    for attempt in range(1, config.HTTP_RETRIES + 1):
        try:
            r = session.get(url, timeout=config.HTTP_TIMEOUT, **kw)
            if r.status_code == 404:
                r.raise_for_status()
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(f"{r.status_code} from {url}")
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                raise
            last = e
        except requests.RequestException as e:
            last = e
        wait = 5 * attempt
        log.warning("GET %s failed (%s) - retry %d in %ds", url, last, attempt, wait)
        time.sleep(wait)
    raise RuntimeError(f"Could not download {url}: {last}")


def read_source_page(session: requests.Session) -> tuple[str, list[str]]:
    """Return (snapshot_date 'YYYY-MM-DD' or '', list of xlsx URLs on the page)."""
    try:
        html = _get(session, config.SOURCE_PAGE).text
    except Exception as e:  # page is nice-to-have; the files are what matter
        log.warning("Could not read source page: %s", e)
        return "", []
    m = AS_OF_RE.search(html)
    as_of = f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else ""
    links = re.findall(r'href=["\']([^"\']+\.xlsx)["\']', html, re.I)
    urls = sorted({urljoin(config.SOURCE_PAGE, l) for l in links})
    log.info("Source page: as-of=%s, %d xlsx links", as_of or "?", len(urls))
    return as_of, urls


def pick_advertised(urls: list[str]) -> list[str]:
    """Newest ADVERTISED_YEARS_BACK advertised files (both naming styles)."""
    adv = [u for u in urls if "advertised" in u.lower()]
    if not adv:
        adv = list(config.ADVERTISED_FALLBACK_URLS)

    def year(u: str) -> int:
        m = re.search(r"(20\d{2})", u)
        return int(m.group(1)) if m else 0

    years = sorted({year(u) for u in adv}, reverse=True)[: config.ADVERTISED_YEARS_BACK]
    return sorted([u for u in adv if year(u) in years], key=year, reverse=True)


def download(session: requests.Session, url: str, dest_dir: Path) -> tuple[Path, str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / url.rsplit("/", 1)[-1]
    r = _get(session, url)
    content = r.content
    if not content.startswith(b"PK"):
        raise RuntimeError(f"{url} did not return an XLSX file (got {content[:80]!r})")
    dest.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()
    log.info("Downloaded %s (%.1f MB)", dest.name, len(content) / 1e6)
    return dest, sha


def fetch_all(dest_dir: Path) -> dict:
    """Download unpaid + advertised files. Returns manifest dict."""
    s = _session()
    as_of, urls = read_source_page(s)
    unpaid_url = next((u for u in urls if u.lower().endswith("/unpaid.xlsx")), config.UNPAID_URL)
    unpaid_path, unpaid_sha = download(s, unpaid_url, dest_dir)

    advertised = []
    for u in pick_advertised(urls):
        try:
            p, _ = download(s, u, dest_dir)
            advertised.append({"url": u, "path": str(p)})
        except Exception as e:
            log.warning("Skipping advertised file %s: %s", u, e)
    return {
        "as_of": as_of,
        "unpaid": {"url": unpaid_url, "path": str(unpaid_path), "sha256": unpaid_sha},
        "advertised": advertised,
    }
