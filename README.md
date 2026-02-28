# Zotero-sweep

A tool to scan research directories for untracked academic PDFs, enrich them
with CrossRef metadata, and import them into Zotero as linked-file attachments.
Also cleans up existing library: finds duplicates and fills missing metadata.

## Features

- Scans specific directories for PDFs not already in your Zotero library
- Extracts DOIs and titles from PDFs; looks up full metadata via CrossRef
- Imports papers as **linked-file attachments** (no Zotero cloud storage used)
- Interactive confirmation: see title, journal, year, and confidence score before importing
- Cleanup mode: find duplicates, items missing DOI/authors/date, and enrich them

## One-time Setup

### 1. Install dependencies

```bash
python3 -m pip install pyzotero PyPDF2
```

### 2. Get Zotero credentials

- **User ID**: log in to zotero.org → click your username (top right) → note the number in the URL
- **API key**: go to zotero.org/settings/keys → "Create new private key"
  - Enable: Allow library access, Allow write access, Allow file access

### 3. Configure

```bash
cp config.json.template config.json
```

Edit `config.json`:
- Set `zotero_user_id` and `zotero_api_key`
- Set `crossref_email` to your university email (gets you CrossRef polite pool)
- Add specific subdirectories to `scan_directories` (avoid scanning all of Dropbox at once)

### 4. Sync Zotero desktop first

Press **Ctrl+Shift+S** in Zotero before running any import command.

## Usage

```bash
# Preview what would be imported (no writes)
python main.py scan
python main.py scan --all          # include low-confidence items too

# Import with confirmation
python main.py import              # confirm each item individually
python main.py import --dry-run    # show plan without writing anything
python main.py import --all        # import all matched items without prompting

# Library cleanup
python main.py cleanup             # report: duplicates, missing metadata
python main.py cleanup --fix       # fill in missing metadata (with confirmation)

# Verbose logging
python main.py --verbose scan
```

## Configuration Reference

| Key | Description |
|-----|-------------|
| `zotero_user_id` | Your numeric Zotero user ID |
| `zotero_api_key` | API key from zotero.org/settings/keys |
| `zotero_library_type` | `"user"` (personal library) or `"group"` |
| `scan_directories` | List of specific folders to scan |
| `zotero_storage_path` | Path to Zotero's local storage folder |
| `zotero_db_path` | Path to `zotero.sqlite` |
| `crossref_email` | Your email for CrossRef polite pool |
| `min_pdf_size_kb` | Skip PDFs smaller than this (default: 50 KB) |
| `max_pdf_size_mb` | Skip PDFs larger than this (default: 50 MB) |
| `skip_folder_names` | Skip PDFs inside folders matching these names |

## How it works

1. **Scan**: reads Zotero's local SQLite database to get known filenames and DOIs.
   Walks scan directories; skips files already known, too small/large, or in
   excluded folders (student submissions, assessments, etc.).

2. **Metadata**: for each candidate PDF, extracts a DOI using regex on the first
   3 pages. If found, looks it up directly on CrossRef. If not, extracts a title
   candidate and does a CrossRef title search. Confidence threshold: score > 15.

3. **Import**: creates a parent bibliographic item (journal article, book chapter,
   etc.) then attaches the PDF as a linked file — no data is copied, just a
   pointer to the original location.

4. **Cleanup**: fetches all library items via the API; normalises titles to find
   duplicates; reports items missing DOI, authors, or date.

## Notes

- Never writes to `zotero.sqlite`; only reads it.
- `config.json` is excluded from git (contains your API key).
- Logs are written to `logs/zotero_sweep.log` (excluded from git).
