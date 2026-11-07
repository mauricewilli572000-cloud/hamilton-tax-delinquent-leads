"""
Run this ONCE on your own PC to confirm the real column layout of the
Hamilton County Auditor delinquent files:

    python discover_hamilton_delinquent.py

It downloads unpaid.xlsx (+ the newest Advertised file), prints the raw top
rows, the detected header row, how each column was mapped, and 5 parsed
sample leads. Everything is also saved to discover_output.txt - paste or
upload that file back to Claude if anything looks wrong.
"""
from __future__ import annotations

import io
import json
import logging
import sys
from contextlib import redirect_stdout
from pathlib import Path

from openpyxl import load_workbook

import config
from src import downloader
from src.columns import describe_mapping, find_header_row, map_columns
from src.parser import parse_file


def show(path: Path) -> None:
    print("=" * 100)
    print(f"FILE: {path.name}  ({path.stat().st_size/1e6:.1f} MB)")
    wb = load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True, max_row=14)]
        print(f"\n--- sheet '{ws.title}'")
        for i, r in enumerate(rows):
            print(f"  row{i:02d}: {r}")
    wb.close()
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = max(wb.worksheets, key=lambda w: w.max_row or 0)
    rows = [list(r) for r in ws.iter_rows(values_only=True, max_row=40)]
    rows = [r for r in rows if any(c not in (None, "") for c in r)]
    h = find_header_row(rows)
    headers = [str(c) if c is not None else "" for c in rows[h]]
    print(f"\nDetected header row index: {h}")
    print(f"Headers: {headers}")
    print("Column mapping:")
    print(describe_mapping(headers, map_columns(headers, config.COLUMN_OVERRIDES)))
    try:
        parsed = parse_file(path, config.COLUMN_OVERRIDES)
        print(f"\nParsed parcels: {len(parsed)}")
        for r in parsed[:5]:
            print(json.dumps(r, indent=1, default=str))
    except Exception as e:
        print(f"\nPARSE ERROR: {e}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    buf = io.StringIO()
    s = downloader._session()
    as_of, urls = downloader.read_source_page(s)
    files = [downloader.download(s, config.UNPAID_URL, config.RAW_DIR)[0]]
    adv = downloader.pick_advertised(urls)[:1]
    for u in adv:
        try:
            files.append(downloader.download(s, u, config.RAW_DIR)[0])
        except Exception as e:
            print(f"Could not download {u}: {e}")
    with redirect_stdout(buf):
        print(f"Source page as-of date: {as_of!r}")
        print(f"xlsx links on page: {urls}")
        for f in files:
            show(f)
    out = buf.getvalue()
    print(out)
    Path("discover_output.txt").write_text(out, encoding="utf-8")
    print("\nSaved to discover_output.txt")


if __name__ == "__main__":
    sys.exit(main())
