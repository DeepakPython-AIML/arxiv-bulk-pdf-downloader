#!/usr/bin/env python3
"""Bulk-download arXiv PDFs filtered by year / month / date / category.

Usage (combine any of the filters):
    python arxiv_bulk.py --year 2024                      # whole year, all categories
    python arxiv_bulk.py --year 2024 --month 3            # one month
    python arxiv_bulk.py --date 2024-03-15                # one day
    python arxiv_bulk.py --year 2024 --month 3 --category cs.AI
    python arxiv_bulk.py --date 2024-03-15 --category cs  # whole "cs" archive that day
    python arxiv_bulk.py --date 2024-03-15 --limit 5      # quick test
    python arxiv_bulk.py --year 2024 --month 3 --list-only

Rules: --date cannot be combined with --year/--month; --month needs --year.
--category accepts a full code (cs.AI, math.PR, hep-th) or an archive prefix (cs, math).
Note: arXiv category search also matches cross-listed papers.

How it works:
  1. Queries the official arXiv API day by day (day slices avoid the API's
     deep-paging cap) and collects paper IDs.
  2. For year/month modes keeps only IDs whose YYMM prefix is in range
     (arXiv's own month definition); date mode uses the submission date.
  3. Downloads each PDF from arxiv.org/pdf/<id>, skipping existing files, so
     the script can be stopped and re-run safely.

Requires: pip install requests
"""
import argparse
import calendar
import datetime as dt
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

API_URL = "https://export.arxiv.org/api/query"
PDF_URL = "https://arxiv.org/pdf/{id}"
NS = {"a": "http://www.w3.org/2005/Atom"}
PAGE_SIZE = 1000
MIN_DATE = dt.date(2007, 4, 1)  # first new-style (YYMM.NNNN) arXiv IDs
USER_AGENT = "arxiv-bulk-downloader/1.0 (personal research use)"

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def get(url, params=None, delay=3.0, retries=5, stream=False):
    """GET with retry/backoff; sleeps `delay` after success to respect arXiv limits."""
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, params=params, timeout=60, stream=stream)
            if r.status_code == 200:
                time.sleep(delay)
                return r
            if r.status_code in (403, 429, 500, 502, 503, 504):
                wait = int(r.headers.get("Retry-After", 0)) or 10 * attempt
                print(f"  HTTP {r.status_code}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
        except requests.RequestException as e:
            wait = 10 * attempt
            print(f"  {e!r}, retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url}")


def build_query(day: dt.date, category=None) -> str:
    start, end = day.strftime("%Y%m%d") + "0000", day.strftime("%Y%m%d") + "2359"
    q = f"submittedDate:[{start} TO {end}]"
    if category:
        # "cs" -> all cs.* subcategories; "cs.AI" / "hep-th" used as-is
        cat = category if ("." in category or category in NO_SUBCAT) else category + ".*"
        q = f"cat:{cat} AND {q}"
    return q


# archives that have no subcategories (their code is the full category name)
NO_SUBCAT = {
    "hep-th", "hep-ph", "hep-ex", "hep-lat", "gr-qc", "nucl-th", "nucl-ex",
    "quant-ph", "astro-ph", "cond-mat", "nlin", "physics", "math-ph",
}


def ids_for_day(day: dt.date, delay: float, category=None):
    """Yield version-less arXiv IDs of papers first submitted on `day`."""
    offset = 0
    while True:
        params = {
            "search_query": build_query(day, category),
            "start": offset,
            "max_results": PAGE_SIZE,
            "sortBy": "submittedDate",
            "sortOrder": "ascending",
        }
        root = ET.fromstring(get(API_URL, params, delay).content)
        entries = root.findall("a:entry", NS)
        for e in entries:
            full = e.find("a:id", NS).text.rsplit("/abs/", 1)[-1]
            yield re.sub(r"v\d+$", "", full)
        if len(entries) < PAGE_SIZE:
            return
        offset += PAGE_SIZE


def collect_ids(first: dt.date, last: dt.date, id_prefixes, delay, category=None, limit=None):
    """Collect IDs for submission days first..last.

    With `id_prefixes` (year/month modes) the scan extends 2 days past each edge
    (arXiv IDs follow announcement cutoffs, not calendar days) and keeps only IDs
    with a matching YYMM prefix. With None (date mode) every hit is kept.
    """
    day, stop = first, last
    if id_prefixes:
        day, stop = first - dt.timedelta(days=2), last + dt.timedelta(days=2)
    seen = set()
    while day <= stop:
        n_before = len(seen)
        for pid in ids_for_day(day, delay, category):
            if id_prefixes is None or pid.startswith(id_prefixes):
                seen.add(pid)
        print(f"  {day}: +{len(seen) - n_before} (total {len(seen)})")
        if limit and len(seen) >= limit:
            break
        day += dt.timedelta(days=1)
    ids = sorted(seen)
    return ids[:limit] if limit else ids


def download_pdf(pid: str, out_dir: Path, delay: float) -> bool:
    dest = out_dir / (pid.replace("/", "_") + ".pdf")
    if dest.exists() and dest.stat().st_size > 0:
        return False
    tmp = dest.with_suffix(".part")
    r = get(PDF_URL.format(id=pid), delay=delay, stream=True)
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    tmp.rename(dest)
    return True


def resolve_range(args, parser):
    """Return (first_day, last_day, id_prefixes_or_None, period_label)."""
    if args.date:
        if args.year or args.month:
            parser.error("--date cannot be combined with --year/--month")
        try:
            d = dt.date.fromisoformat(args.date)
        except ValueError:
            parser.error("--date must be YYYY-MM-DD")
        return d, d, None, d.isoformat()
    if args.month and not args.year:
        parser.error("--month requires --year")
    if not args.year:
        parser.error("provide --year [--month] or --date")
    if args.month and not 1 <= args.month <= 12:
        parser.error("--month must be 1-12")
    months = [args.month] if args.month else range(1, 13)
    first = dt.date(args.year, months[0], 1)
    last = dt.date(args.year, months[-1], calendar.monthrange(args.year, months[-1])[1])
    prefixes = tuple(f"{args.year % 100:02d}{m:02d}." for m in months)
    label = f"{args.year}-{args.month:02d}" if args.month else str(args.year)
    return first, last, prefixes, label


def main():
    p = argparse.ArgumentParser(
        description="Download arXiv PDFs by year / month / date / category.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--year", type=int, help="e.g. 2024 (alone = whole year)")
    p.add_argument("--month", type=int, help="1-12, requires --year")
    p.add_argument("--date", help="single day, YYYY-MM-DD (exclusive with --year/--month)")
    p.add_argument("--category", help="e.g. cs.AI, math.PR, hep-th, or archive prefix like cs")
    p.add_argument("-o", "--output", default="arxiv_pdfs", help="base output folder")
    p.add_argument("--delay", type=float, default=3.0, help="seconds between requests (arXiv asks >=3)")
    p.add_argument("--limit", type=int, help="only download the first N papers (for testing)")
    p.add_argument("--list-only", action="store_true", help="only write the ID list, no PDFs")
    args = p.parse_args()

    first, last, prefixes, label = resolve_range(args, p)
    today = dt.date.today()
    if first < MIN_DATE or first > today:
        p.error(f"date range must be between {MIN_DATE} and {today}")

    category = args.category.strip() if args.category else None
    out_dir = Path(args.output) / label / (category or "all")
    out_dir.mkdir(parents=True, exist_ok=True)
    id_file = out_dir / "ids.txt"

    desc = f"{label}" + (f", category {category}" if category else ", all categories")
    if id_file.exists() and not args.limit:
        ids = id_file.read_text().split()
        print(f"Loaded {len(ids)} cached IDs from {id_file}")
    else:
        print(f"Collecting paper IDs for {desc} ...")
        ids = collect_ids(first, min(last, today), prefixes, args.delay, category, args.limit)
        if not args.limit:
            id_file.write_text("\n".join(ids) + "\n")
    print(f"{len(ids)} papers found.")
    if args.list_only:
        return

    done = skipped = failed = 0
    for i, pid in enumerate(ids, 1):
        try:
            if download_pdf(pid, out_dir, args.delay):
                done += 1
                print(f"[{i}/{len(ids)}] downloaded {pid}")
            else:
                skipped += 1
        except Exception as e:  # keep going; re-run later to retry failures
            failed += 1
            print(f"[{i}/{len(ids)}] FAILED {pid}: {e}", file=sys.stderr)
    print(f"Done. downloaded={done} skipped={skipped} failed={failed} -> {out_dir}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted; re-run the same command to resume.")
