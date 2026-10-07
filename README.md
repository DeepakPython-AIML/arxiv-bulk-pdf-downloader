# arXiv Bulk PDF Downloader

Downloads all arXiv PDFs (all categories) for a given year and month using [arxiv_bulk.py](arxiv_bulk.py).

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install requests
```

## Usage

Combine any of the filters:

| Flags | Downloads |
|---|---|
| `--year 2024` | whole year, all categories |
| `--year 2024 --month 3` | one month, all categories |
| `--date 2024-03-15` | one day, all categories |
| `--year 2024 --month 3 --category cs.AI` | one month, one category |
| `--date 2024-03-15 --category cs.AI` | one day, one category |
| `--year 2024 --category cs` | whole year, whole `cs` archive |

```bash
python arxiv_bulk.py --year 2024 --month 3
python arxiv_bulk.py --date 2024-03-15 --category cs.AI
python arxiv_bulk.py --year 2024 --month 3 -o ~/pdfs      # custom output folder
python arxiv_bulk.py --date 2024-03-15 --limit 5          # quick test
python arxiv_bulk.py --year 2024 --month 3 --list-only    # only collect IDs
python arxiv_bulk.py --date 2024-03-15 --delay 5          # slower, more polite
```

Rules:
- `--date` cannot be combined with `--year` / `--month`; `--month` needs `--year`.
- `--category` takes a full code (`cs.AI`, `math.PR`, `hep-th`) or an archive prefix (`cs`, `math`). Category search also matches cross-listed papers.
- Valid range: April 2007 (first new-style arXiv IDs) up to today.

## Output

```
arxiv_pdfs/
└── 2024-03/              # period: 2024, 2024-03 or 2024-03-15
    └── cs.AI/            # category, or "all"
        ├── ids.txt       # cached list of paper IDs
        ├── 2403.00001.pdf
        └── ...
```

## How it works

1. Queries the official arXiv API one day at a time (optionally filtered by category) to collect paper IDs.
2. In year/month modes keeps only IDs whose prefix matches the month (e.g. `2403.`), matching arXiv's own numbering. In `--date` mode it uses the submission date.
3. Downloads each PDF from `arxiv.org/pdf/<id>`, waiting 3 seconds between requests (per arXiv API terms).

## Resuming

Stop any time with Ctrl-C and re-run the same command. Cached IDs (`ids.txt`) are reused, existing PDFs are skipped, and failed downloads are retried. Partial files use a `.part` suffix until complete.

## Notes

- One month is roughly 20–25k papers: about a day of runtime at 3 s/request and tens of GB of disk.
- Don't lower `--delay` below 3; arXiv may block your IP.
- For multiple months or years, use arXiv's requester-pays S3 bulk bucket (`s3://arxiv/pdf/`) instead.
