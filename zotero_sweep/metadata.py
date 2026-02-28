import re
import time
import urllib.parse
import urllib.request
import urllib.error
import json
import pathlib

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

from .logger import get_logger

log = get_logger("metadata")

# Regex patterns to locate DOIs in PDF text
_DOI_PATTERNS = [
    re.compile(r'10\.\d{4,9}/[^\s"<>]+', re.IGNORECASE),
    re.compile(r'doi\.org/([^\s"<>]+)', re.IGNORECASE),
]
# Characters that commonly trail a DOI due to sentence punctuation
_DOI_TRAILING_STRIP = '.,;:)"\''

CROSSREF_BASE = "https://api.crossref.org/works"
CONFIDENCE_THRESHOLD = 15


def extract_doi_from_pdf(pdf_path: pathlib.Path) -> str | None:
    """Extract a DOI string from the first 3 pages of a PDF.

    Returns a cleaned DOI string (trailing punctuation stripped), or None.
    """
    if PyPDF2 is None:
        log.warning("PyPDF2 is not installed; cannot extract DOI from PDF text")
        return None

    try:
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            pages_to_check = min(3, len(reader.pages))
            for i in range(pages_to_check):
                try:
                    text = reader.pages[i].extract_text() or ""
                except Exception:
                    continue

                for pattern in _DOI_PATTERNS:
                    match = pattern.search(text)
                    if match:
                        doi = match.group(0)
                        # Strip 'doi.org/' prefix from URL-style matches
                        if "doi.org/" in doi.lower():
                            doi = re.split(r"doi\.org/", doi, flags=re.IGNORECASE)[-1]
                        doi = doi.rstrip(_DOI_TRAILING_STRIP)
                        log.debug("DOI extracted from %s: %s", pdf_path.name, doi)
                        return doi
    except Exception as exc:
        log.debug("Could not extract DOI from %s: %s", pdf_path.name, exc)

    return None


def extract_title_from_pdf(pdf_path: pathlib.Path) -> str | None:
    """Attempt to extract a title string from a PDF.

    Strategy:
      1. Try the PDF /Title metadata field (if non-empty and > 10 chars)
      2. Fall back to the first non-empty line of page 1 text (≤ 80 chars)

    Returns a title string candidate, or None.
    """
    if PyPDF2 is None:
        log.warning("PyPDF2 is not installed; cannot extract title from PDF")
        return None

    try:
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)

            # Step 1: PDF metadata title field
            meta_title = (reader.metadata or {}).get("/Title", "") or ""
            meta_title = meta_title.strip()
            if len(meta_title) > 10:
                log.debug("Title from PDF metadata: %s", meta_title[:60])
                return meta_title

            # Step 2: First non-empty line of page 1
            if reader.pages:
                try:
                    text = reader.pages[0].extract_text() or ""
                    for line in text.splitlines():
                        line = line.strip()
                        if len(line) > 10:
                            title = line[:80]
                            log.debug("Title from page text: %s", title)
                            return title
                except Exception:
                    pass

    except Exception as exc:
        log.debug("Could not extract title from %s: %s", pdf_path.name, exc)

    return None


def fetch_from_crossref(
    doi: str | None = None,
    title: str | None = None,
    email: str = "",
) -> dict | None:
    """Look up metadata from the CrossRef API.

    DOI mode:   GET api.crossref.org/works/{doi}     — deterministic
    Title mode: GET api.crossref.org/works?query.title={title}&rows=3
                — takes the highest-scored result if score > CONFIDENCE_THRESHOLD

    Returns a metadata dict or None.
    """
    headers = {
        "User-Agent": f"ZoteroSweep/1.0 (mailto:{email})",
        "Accept": "application/json",
    }

    if doi:
        url = f"{CROSSREF_BASE}/{urllib.parse.quote(doi, safe='/')}"
        log.debug("CrossRef DOI lookup: %s", url)
        result = _crossref_get(url, headers)
        if result and result.get("status") == "ok":
            return _parse_crossref_item(result["message"], confidence_score=100.0)

    elif title:
        params = urllib.parse.urlencode({"query.title": title, "rows": 3})
        url = f"{CROSSREF_BASE}?{params}"
        log.debug("CrossRef title search: %s", title[:60])
        result = _crossref_get(url, headers)
        if result and result.get("status") == "ok":
            items = result["message"].get("items", [])
            if items:
                best = items[0]
                score = best.get("score", 0)
                log.debug("CrossRef best match score: %.1f  title: %s",
                           score, best.get("title", [""])[0][:60])
                if score > CONFIDENCE_THRESHOLD:
                    return _parse_crossref_item(best, confidence_score=score)
                else:
                    log.debug("Score %.1f below threshold %d — no match",
                               score, CONFIDENCE_THRESHOLD)

    return None


def _crossref_get(url: str, headers: dict) -> dict | None:
    """Make a GET request to CrossRef; handle rate-limiting with one retry."""
    time.sleep(0.5)
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                log.warning("CrossRef rate limit (429). Waiting 10 s before retry...")
                time.sleep(10)
                if attempt == 0:
                    continue
                log.warning("CrossRef still rate-limiting after retry — skipping")
                return None
            log.debug("CrossRef HTTP error %d for %s", exc.code, url)
            return None
        except Exception as exc:
            log.debug("CrossRef request error: %s", exc)
            return None
    return None


def _parse_crossref_item(item: dict, confidence_score: float) -> dict:
    """Extract fields from a CrossRef works item dict."""
    title_list = item.get("title") or []
    title = title_list[0] if title_list else ""

    authors = []
    for author in item.get("author", []):
        family = author.get("family", "")
        given = author.get("given", "")
        name = f"{given} {family}".strip() if given else family
        if name:
            authors.append(name)

    # Year: prefer published-print, fall back to published-online, then issued
    year = None
    for date_field in ("published-print", "published-online", "issued"):
        date_parts = item.get(date_field, {}).get("date-parts", [[]])
        if date_parts and date_parts[0]:
            year = str(date_parts[0][0])
            break

    doi = item.get("DOI", "")
    journal_list = item.get("container-title") or []
    journal = journal_list[0] if journal_list else ""

    # Determine item type
    crossref_type = item.get("type", "journal-article")
    item_type_map = {
        "journal-article": "journalArticle",
        "book-chapter": "bookSection",
        "book": "book",
        "proceedings-article": "conferencePaper",
        "dissertation": "thesis",
        "report": "report",
        "preprint": "preprint",
    }
    item_type = item_type_map.get(crossref_type, "journalArticle")

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "journal": journal,
        "volume": item.get("volume", ""),
        "issue": item.get("issue", ""),
        "pages": item.get("page", ""),
        "doi": doi,
        "item_type": item_type,
        "confidence_score": confidence_score,
        "needs_review": False,
    }


def get_metadata(
    pdf_path: pathlib.Path,
    email: str = "",
    known_dois: set | None = None,
) -> dict:
    """Orchestrate DOI extraction → CrossRef lookup → title fallback.

    Returns a metadata dict always containing at minimum:
      filename, confidence_score, needs_review
    """
    if known_dois is None:
        known_dois = set()

    # --- Step 1: Try DOI extraction ---
    doi = extract_doi_from_pdf(pdf_path)
    if doi:
        if doi.lower() in known_dois:
            log.debug("DOI already in library, skipping: %s", doi)
            return {
                "filename": pdf_path.name,
                "doi": doi,
                "confidence_score": 0,
                "needs_review": False,
                "already_tracked": True,
            }

        metadata = fetch_from_crossref(doi=doi, email=email)
        if metadata:
            metadata["filename"] = pdf_path.name
            return metadata

    # --- Step 2: Title fallback ---
    title = extract_title_from_pdf(pdf_path)
    if title:
        metadata = fetch_from_crossref(title=title, email=email)
        if metadata:
            # Check if CrossRef returned a DOI we already have
            returned_doi = metadata.get("doi", "").lower()
            if returned_doi and returned_doi in known_dois:
                log.debug("Matched DOI already in library via title search: %s", returned_doi)
                return {
                    "filename": pdf_path.name,
                    "doi": returned_doi,
                    "confidence_score": 0,
                    "needs_review": False,
                    "already_tracked": True,
                }
            metadata["filename"] = pdf_path.name
            return metadata

    # --- Step 3: No metadata found ---
    log.debug("No metadata found for %s", pdf_path.name)
    return {
        "filename": pdf_path.name,
        "extracted_title": title,
        "confidence_score": 0,
        "needs_review": True,
        "already_tracked": False,
    }
