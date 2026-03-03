# Zotero-sweep — Claude Context

## What This Project Does

Scans research directories for PDF files not already in Zotero, enriches them
with metadata from CrossRef, and imports them as linked-file attachments. Also
provides a cleanup mode to find duplicates and fill missing metadata in the
existing library.

## Development Status

Active development — harvest feature complete; all other changes remain
bug-fix/minor-improvement only.

## Key Rules

- **Never write to `zotero.sqlite`** — the tool only reads it. All library
  writes go through the Zotero API (pyzotero).
- **Always use `--dry-run` first** when testing any command that modifies the
  Zotero library (`import`, `cleanup --fix`).
- **Sync Zotero before importing**: remind the user to press Ctrl+Shift+S in
  the Zotero desktop app before running `import`.
- **Approved libraries only**: `pyzotero` and `PyPDF2`. Do not introduce any
  other third-party dependencies without asking first.
- **`--ai-verify` is slower**: makes one Semantic Scholar API call per
  candidate PDF (~3–4 s unauthenticated, ~1.1 s with API key). Always use
  `--dry-run` first when testing.

## Project Structure

```
main.py                  # CLI entry point; dispatches to subcommands
zotero_sweep/
  config.py              # Loads and validates config.json
  scanner.py             # Finds untracked PDFs; queries zotero.sqlite
  metadata.py            # Extracts DOI/title from PDFs; CrossRef lookups
  importer.py            # Creates Zotero items and linked-file attachments
  cleanup.py             # Detects duplicates; enriches items missing metadata
  logger.py              # Logging setup (console + file)
config.json.template     # Template — copy to config.json and fill in secrets
logs/                    # Runtime logs (git-excluded)
```

## Git-Excluded Files

- `config.json` — contains Zotero API key; never commit
- `logs/` — runtime log files

## Common Commands (for reference)

```bash
python main.py scan                 # preview candidates (read-only)
python main.py import --dry-run     # show what would be imported
python main.py import               # interactive import
python main.py cleanup              # report duplicates / missing metadata
python main.py cleanup --fix --dry-run  # preview metadata enrichment

# Harvest workflow
python main.py discover             # find paper folders; writes discovered_folders.txt
python main.py import --folders=/path --all --ai-verify --dry-run
python main.py import --folders=/path --all --ai-verify
```

## Semantic Scholar API Key Setup

To get faster S2 rate limits (1 req/s instead of shared 100 req/5 min):
1. Register at https://www.semanticscholar.org/product/api
2. Copy your key into `config.json`: `"semantic_scholar_api_key": "your-key-here"`

## Harvest Workflow

```
Session 1 — Discover
  python main.py discover
  → prints table of candidate folders with PDF counts
  → writes discovered_folders.txt (one path per line)
  → review the file and delete any rows you don't want

Session 2+ — Batch import (one folder per run)
  python main.py import --folders=/path/to/folder --all --ai-verify --dry-run
  [review: how many matched / ai_rejected / needs_review]
  python main.py import --folders=/path/to/folder --all --ai-verify
  Sync Zotero (Ctrl+Shift+S)
  Move to next folder in discovered_folders.txt
```

## AI Verify — Three-Tier Pipeline

When `--ai-verify` is passed:
| Tier | Condition | Result |
|------|-----------|--------|
| 1 | CrossRef DOI or title search succeeds | Imported (CrossRef metadata) |
| 2 | CrossRef fails → Semantic Scholar similarity ≥ 0.65 | Imported as preprint (PDF metadata, conf 12.0) |
| 3 | S2 fails/unavailable → working-paper signals in page 1 | Imported as report (PDF metadata, conf 7.0) |
| — | All tiers fail | `ai_rejected` — silently skipped, visible with `--verbose` |
