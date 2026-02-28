# Zotero-sweep — Claude Context

## What This Project Does

Scans research directories for PDF files not already in Zotero, enriches them
with metadata from CrossRef, and imports them as linked-file attachments. Also
provides a cleanup mode to find duplicates and fill missing metadata in the
existing library.

## Development Status

Maintenance mode. The core feature set is complete. Focus on bug fixes and
minor improvements only — do not add new features unless explicitly requested.

## Key Rules

- **Never write to `zotero.sqlite`** — the tool only reads it. All library
  writes go through the Zotero API (pyzotero).
- **Always use `--dry-run` first** when testing any command that modifies the
  Zotero library (`import`, `cleanup --fix`).
- **Sync Zotero before importing**: remind the user to press Ctrl+Shift+S in
  the Zotero desktop app before running `import`.
- **Approved libraries only**: `pyzotero` and `PyPDF2`. Do not introduce any
  other third-party dependencies without asking first.

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
```
