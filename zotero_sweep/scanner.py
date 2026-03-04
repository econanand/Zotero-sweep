import os
import pathlib
import sqlite3

from .logger import get_logger

log = get_logger("scanner")

DEFAULT_DISCOVERY_KEYWORDS = [
    "papers", "literature", "lit review", "litreview",
    "articles", "references", "readings", "reading",
    "bibliography", "pdfs", "research",
]

_DEFAULT_DISCOVERY_SKIP = [
    ".git", ".cache", "__pycache__", "node_modules", ".venv",
    "venv", ".trash", "trash",
]

try:
    import PyPDF2 as _PyPDF2
    _PYPDF2_AVAILABLE = True
except ImportError:
    _PYPDF2_AVAILABLE = False

_DEFAULT_SKIP_PATTERNS = [
    "syllabus", "invoice", "receipt", "certificate", "brochure",
    "flyer", "newsletter", "contract", "announcement",
    # Document types clearly not research papers
    "questionnaire", "codebook", "consent", "rubric",
    "timetable", "transcript",
]

# Phrases that, if found in the first 400 characters of page 1, strongly
# indicate the document is not a research paper.  Multi-word phrases are used
# deliberately to reduce false-positive risk.
_FIRST_PAGE_SKIP_PHRASES = [
    # Forms / questionnaires
    "please fill in", "please complete this", "please answer the following",
    # Answer / solution keys
    "answer key", "solution key",
    # Meeting documents
    "meeting agenda", "agenda for the meeting",
    # Proposals (specific multi-word forms to avoid matching paper titles)
    "research proposal", "grant proposal", "project proposal",
]


def get_known_pdfs(db_path: str) -> tuple[set, set]:
    """Read the local Zotero SQLite database (read-only) and return:
      - known_filenames: set of lowercase PDF basenames already in Zotero
      - known_dois:      set of DOI strings already in the library

    Zotero stores attachment paths as 'storage:Filename.pdf'; this function
    strips the 'storage:' prefix to get the bare filename.
    """
    known_filenames: set[str] = set()
    known_dois: set[str] = set()

    db = pathlib.Path(db_path)
    if not db.exists():
        log.warning("Zotero database not found at %s — skipping duplicate check", db_path)
        return known_filenames, known_dois

    try:
        # Open read-only via URI so we never write to Zotero's live database
        uri = db.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cursor = conn.cursor()

        # Collect PDF filenames from attachments
        cursor.execute(
            "SELECT path FROM itemAttachments WHERE path IS NOT NULL"
        )
        for (path,) in cursor.fetchall():
            if path and path.lower().endswith(".pdf"):
                if path.startswith("storage:"):
                    # Stored copy: "storage:Filename.pdf" → "Filename.pdf"
                    filename = path[len("storage:"):]
                else:
                    # Linked file: absolute path → extract basename
                    filename = pathlib.Path(path).name
                known_filenames.add(filename.lower())

        # Collect DOIs from item fields
        cursor.execute(
            """
            SELECT DISTINCT fieldValues.value
            FROM   itemData
            JOIN   fields       ON fields.fieldID       = itemData.fieldID
            JOIN   itemDataValues AS fieldValues
                                ON fieldValues.valueID  = itemData.valueID
            WHERE  fields.fieldName = 'DOI'
              AND  fieldValues.value != ''
            """
        )
        for (doi,) in cursor.fetchall():
            known_dois.add(doi.strip().lower())

        conn.close()
        log.debug(
            "Known PDFs from Zotero DB: %d filenames, %d DOIs",
            len(known_filenames),
            len(known_dois),
        )

    except sqlite3.OperationalError as exc:
        log.warning(
            "Could not read Zotero database: %s\n"
            "If Zotero is open, close it or use a copy of the database.",
            exc,
        )

    return known_filenames, known_dois


def scan_for_pdfs(
    directories: list[str],
    known_filenames: set[str],
    config: dict,
) -> tuple[list[pathlib.Path], list[tuple[pathlib.Path, str]]]:
    """Walk each directory for PDF candidates not already tracked in Zotero.

    Skips a PDF if:
      - Smaller than min_pdf_size_kb
      - Larger than max_pdf_size_mb
      - Its lowercase filename is in known_filenames
      - It lives inside the Zotero storage directory
      - Any part of its path matches a skip_folder_name (case-insensitive substring)

    Returns:
      candidates   — list of pathlib.Path objects to process
      skipped_log  — list of (path, reason) tuples; nothing is silently discarded
    """
    min_bytes = config["min_pdf_size_kb"] * 1024
    max_bytes = config["max_pdf_size_mb"] * 1024 * 1024
    skip_names = [s.lower() for s in config.get("skip_folder_names", [])]
    storage_path = pathlib.Path(config["zotero_storage_path"]).resolve()

    candidates: list[pathlib.Path] = []
    skipped_log: list[tuple[pathlib.Path, str]] = []

    for directory in directories:
        scan_root = pathlib.Path(directory)
        if not scan_root.exists():
            log.warning("Scan directory does not exist, skipping: %s", directory)
            continue

        log.info("Scanning: %s", directory)
        pdf_count = 0

        for pdf in scan_root.rglob("*.pdf"):
            pdf_count += 1

            # Size checks (fast — no file open needed)
            try:
                size = pdf.stat().st_size
            except OSError as exc:
                skipped_log.append((pdf, f"stat error: {exc}"))
                continue

            if size < min_bytes:
                skipped_log.append((pdf, f"too small ({size // 1024} KB)"))
                continue

            if size > max_bytes:
                skipped_log.append((pdf, f"too large ({size // 1024 // 1024} MB)"))
                continue

            # Already in Zotero?
            if pdf.name.lower() in known_filenames:
                skipped_log.append((pdf, "already in Zotero (filename match)"))
                continue

            # Inside Zotero storage?
            try:
                pdf.resolve().relative_to(storage_path)
                skipped_log.append((pdf, "inside Zotero storage directory"))
                continue
            except ValueError:
                pass  # not inside storage — good

            # Skip-folder check (case-insensitive substring match on any path part)
            parts_lower = [part.lower() for part in pdf.parts]
            matched_skip = next(
                (skip for skip in skip_names
                 if any(skip in part for part in parts_lower)),
                None,
            )
            if matched_skip:
                skipped_log.append((pdf, f"skip folder '{matched_skip}'"))
                continue

            candidates.append(pdf)

        log.debug("  %d PDFs found, %d candidates so far", pdf_count, len(candidates))

    log.info(
        "Scan complete: %d candidates, %d skipped",
        len(candidates),
        len(skipped_log),
    )
    return candidates, skipped_log


def filter_non_papers(
    candidates: list[pathlib.Path],
    config: dict,
) -> tuple[list[pathlib.Path], list[tuple[pathlib.Path, str]]]:
    """Apply cheap heuristics to remove obvious non-research PDFs from candidates.

    Runs two checks (no network calls):
      A. Filename pattern match — flags invoices, syllabi, receipts, etc.
      B. Page count check via PyPDF2 — flags very short PDFs.

    Returns:
      papers       — candidates that passed all checks
      auto_skipped — list of (path, reason) for files that were filtered out
    """
    min_page_count = config.get("min_page_count", 3)
    extra_patterns = [p.lower() for p in config.get("auto_skip_filename_patterns", [])]
    skip_patterns = _DEFAULT_SKIP_PATTERNS + extra_patterns

    papers: list[pathlib.Path] = []
    auto_skipped: list[tuple[pathlib.Path, str]] = []

    for path in candidates:
        stem_lower = path.stem.lower()

        # A. Filename pattern check
        matched_pattern = next(
            (pat for pat in skip_patterns if pat in stem_lower),
            None,
        )
        if matched_pattern:
            auto_skipped.append(
                (path, f"filename suggests non-paper ('{matched_pattern}')")
            )
            continue

        # B. Page count + first-page content check
        if _PYPDF2_AVAILABLE:
            try:
                reader = _PyPDF2.PdfReader(str(path), strict=False)
                n = len(reader.pages)
                if n < min_page_count:
                    auto_skipped.append(
                        (path, f"too few pages (< {min_page_count})")
                    )
                    continue

                # C. First-page content check — catches forms, questionnaires,
                #    proposals, answer keys, and similar non-paper documents
                try:
                    page_text = (reader.pages[0].extract_text() or "")[:400].lower()
                    matched_phrase = next(
                        (p for p in _FIRST_PAGE_SKIP_PHRASES if p in page_text),
                        None,
                    )
                    if matched_phrase:
                        auto_skipped.append(
                            (path, f"first-page content suggests non-paper ('{matched_phrase}')")
                        )
                        continue
                except Exception:
                    pass  # can't read text — let it through

            except Exception:
                pass  # unreadable — let it through for manual review

        papers.append(path)

    log.info(
        "Heuristic filter: %d passed, %d auto-skipped",
        len(papers),
        len(auto_skipped),
    )
    return papers, auto_skipped


def discover_paper_folders(
    roots: list[str],
    keywords: list[str],
    config: dict,
) -> list[tuple[pathlib.Path, int]]:
    """Walk root directories and find folders whose names contain any keyword.

    Respects discovery_max_depth, discovery_skip_patterns, and
    discovery_skip_hidden from config. Already-configured scan_directories
    and the Zotero storage path are excluded from results.

    Returns a list of (path, pdf_count) tuples sorted descending by pdf_count.
    Empty folders (0 PDFs) are excluded.
    """
    max_depth = config.get("discovery_max_depth", 4)
    skip_hidden = config.get("discovery_skip_hidden", True)
    skip_patterns = [
        s.lower()
        for s in config.get("discovery_skip_patterns", _DEFAULT_DISCOVERY_SKIP)
    ]

    # Build set of already-configured and Zotero paths to exclude
    excluded: set[str] = set()
    for d in config.get("scan_directories", []):
        try:
            excluded.add(str(pathlib.Path(d).resolve()))
        except Exception:
            pass
    zotero_storage = config.get("zotero_storage_path", "")
    if zotero_storage:
        try:
            excluded.add(str(pathlib.Path(zotero_storage).resolve()))
        except Exception:
            pass

    keywords_lower = [k.lower() for k in keywords]
    results: list[tuple[pathlib.Path, int]] = []

    for root_str in roots:
        root = pathlib.Path(root_str)
        if not root.exists():
            log.warning("Discovery root does not exist: %s", root_str)
            continue

        root_depth = len(root.parts)
        log.info("Discovering under: %s", root_str)

        for dirpath_str, dirnames, _ in os.walk(root, topdown=True):
            dirpath = pathlib.Path(dirpath_str)
            current_depth = len(dirpath.parts) - root_depth

            if current_depth >= max_depth:
                # Don't descend further, but still evaluate this folder below
                dirnames.clear()
            else:
                # Prune subdirectories matching skip rules in-place
                dirnames[:] = [
                    d for d in dirnames
                    if not (skip_hidden and d.startswith("."))
                    and not any(pat in d.lower() for pat in skip_patterns)
                ]

            # Don't evaluate the root itself
            if current_depth == 0:
                continue

            # Keyword match on the folder name
            folder_name_lower = dirpath.name.lower()
            if not any(kw in folder_name_lower for kw in keywords_lower):
                continue

            # Skip already-configured directories
            try:
                resolved = str(dirpath.resolve())
            except Exception:
                continue
            if resolved in excluded:
                continue

            # Count PDFs in this folder tree (recursive, matches what scan_for_pdfs sees)
            pdf_count = len(list(dirpath.rglob("*.pdf")))
            if pdf_count == 0:
                continue

            results.append((dirpath, pdf_count))

    results.sort(key=lambda x: -x[1])
    log.info("Discovery complete: %d matching folders found", len(results))
    return results
