import difflib
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
CONFIDENCE_THRESHOLD = 30

# Lines from PDF page-1 text that look like codes rather than titles
_BAD_TITLE_PATTERNS = [
    re.compile(r"^pii:", re.I),           # PII codes:  PII: S0304-3932(98)...
    re.compile(r"^10\.\d{4,9}/"),         # Raw DOI:    10.1093/qje/...
    re.compile(r"^\w{6,20}\.pdf$", re.I), # Filename leak: QJEForPublication.pdf
    re.compile(r"^s\d{6,}[a-z]$", re.I), # Science IDs: se360101818p
]

# Year found in a filename stem, e.g. "Taschereau-Dumouchel 2018"
_YEAR_IN_STEM = re.compile(r"\b(19|20)\d{2}\b")


def _extract_first_page_text(pdf_path: pathlib.Path) -> str | None:
    """Return text from page 1 of *pdf_path*, or None on any failure."""
    if PyPDF2 is None:
        return None
    try:
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f, strict=False)
            if not reader.pages:
                return None
            return reader.pages[0].extract_text() or None
    except Exception:
        return None


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

            # Step 2: First plausible line of page 1 text
            if reader.pages:
                try:
                    text = reader.pages[0].extract_text() or ""
                    for line in text.splitlines():
                        line = line.strip()
                        if len(line) <= 10:
                            continue
                        if any(pat.match(line) for pat in _BAD_TITLE_PATTERNS):
                            log.debug("Skipping non-title line: %s", line[:60])
                            continue
                        log.debug("Title from page text: %s", line[:80])
                        return line
                except Exception:
                    pass

    except Exception as exc:
        log.debug("Could not extract title from %s: %s", pdf_path.name, exc)

    return None


_AUTHOR_STOP_WORDS = {
    "abstract", "introduction", "keywords", "jel", "working paper",
    "university", "department", "institute", "forthcoming", "draft",
}
_WORD_RE = re.compile(r"^[A-Z][a-zA-Z'\-]+$")


def extract_authors_from_pdf(pdf_path: pathlib.Path) -> list[str]:
    """Heuristically extract author names from the first page of a PDF.

    Looks for title-case, 2-4-word lines in the zone just below the title
    (skipping the first 1-2 lines).  Stops at section boundaries.
    Returns up to 5 author strings, or [] if nothing is found.
    """
    text = _extract_first_page_text(pdf_path)
    if not text:
        return []

    lines = [ln.strip() for ln in text.splitlines()]
    authors: list[str] = []

    for line in lines[2:]:           # skip first two lines (likely the title)
        if not line:
            continue
        # Stop at section headers (digit-led) or known boundary words
        lower = line.lower()
        if re.match(r"^\d", line):
            break
        if any(kw in lower for kw in ("abstract", "introduction")):
            break
        # Skip lines containing known false-positive phrases
        if any(kw in lower for kw in _AUTHOR_STOP_WORDS):
            continue

        words = line.split()
        if not (2 <= len(words) <= 4):
            continue
        if len(line) > 60:
            continue
        if all(_WORD_RE.match(w) for w in words):
            authors.append(line)
            if len(authors) == 5:
                break

    log.debug("Authors extracted from %s: %s", pdf_path.name, authors)
    return authors


def extract_year_from_pdf(pdf_path: pathlib.Path) -> str | None:
    """Return the first plausible 4-digit year found on page 1, or None."""
    text = _extract_first_page_text(pdf_path)
    if not text:
        return None
    match = re.search(r"\b(199[0-9]|20[0-2][0-9]|2030)\b", text)
    if match:
        log.debug("Year extracted from %s: %s", pdf_path.name, match.group(0))
        return match.group(0)
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


S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_SIMILARITY_THRESHOLD = 0.65


def _normalise_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return " ".join(re.sub(r"[^\w\s]", " ", title.lower()).split())


def verify_with_semantic_scholar(title: str, api_key: str = "", sleep_seconds: float | None = None) -> bool | None:
    """Check whether *title* matches a Semantic Scholar record.

    Returns:
      True  — best normalised-similarity result ≥ S2_SIMILARITY_THRESHOLD
      False — no result meets the threshold
      None  — network / HTTP error (treat as "S2 unavailable"; do not hard-reject)
    """
    norm_query = _normalise_title(title)
    if not norm_query:
        return False

    params = urllib.parse.urlencode({
        "query": title,
        "limit": 3,
        "fields": "title,year,publicationTypes",
    })
    url = f"{S2_SEARCH_URL}?{params}"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key

    # Respect rate limits before each call
    if sleep_seconds is not None:
        time.sleep(sleep_seconds)
    else:
        time.sleep(1.1 if api_key else 10.0)

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.warning("Semantic Scholar request failed: %s", exc)
        return None

    results = data.get("data", [])
    best_ratio = 0.0
    best_title = ""
    for item in results:
        s2_title = item.get("title") or ""
        norm_s2 = _normalise_title(s2_title)
        ratio = difflib.SequenceMatcher(None, norm_query, norm_s2).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_title = s2_title

    log.debug("S2 best match: ratio=%.3f  title=%s", best_ratio, best_title[:80])
    return best_ratio >= S2_SIMILARITY_THRESHOLD


def detect_working_paper_signals(pdf_path: pathlib.Path) -> bool:
    """Return True if page 1 contains signals that this is a working paper.

    Checks named series, generic phrases, and JEL classification codes.
    """
    text = _extract_first_page_text(pdf_path)
    if not text:
        return False

    lower = text.lower()

    named_series = [
        "nber", "ssrn", "iza discussion paper", "cepr discussion paper",
        "national bureau of economic research", "social science research network",
    ]
    for signal in named_series:
        if signal in lower:
            log.debug("Working-paper signal '%s' found in %s", signal, pdf_path.name)
            return True

    generic = ["working paper", "discussion paper", "job market paper"]
    for signal in generic:
        if signal in lower:
            log.debug("Working-paper signal '%s' found in %s", signal, pdf_path.name)
            return True

    if re.search(r"jel[\s:]+[a-z][0-9]", lower):
        log.debug("JEL code found in %s", pdf_path.name)
        return True

    return False


def get_metadata(
    pdf_path: pathlib.Path,
    email: str = "",
    known_dois: set | None = None,
    ai_verify: bool = False,
    s2_api_key: str = "",
    s2_sleep_seconds: float | None = None,
) -> dict:
    """Orchestrate DOI extraction → CrossRef lookup → optional AI verification.

    When ai_verify=False (default): existing behaviour — CrossRef only, then
    PDF-only fallback for papers without DOI, or needs_review if no title.

    When ai_verify=True: three-tier pipeline after CrossRef fails:
      Tier 2 — Semantic Scholar similarity check  → imports as preprint (conf 12.0)
      Tier 3 — Working-paper text signals         → imports as report   (conf  7.0)
      Rejected — all tiers fail                  → ai_rejected=True (silently skipped)

    Returns a metadata dict always containing at minimum:
      filename, confidence_score, needs_review, ai_rejected
    """
    if known_dois is None:
        known_dois = set()

    # --- Tier 1A: DOI extraction → CrossRef DOI lookup ---
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
                "ai_rejected": False,
            }

        metadata = fetch_from_crossref(doi=doi, email=email)
        if metadata:
            metadata["filename"] = pdf_path.name
            metadata["ai_rejected"] = False
            return metadata

    # --- Tier 1B: Title extraction → CrossRef title search ---
    title = extract_title_from_pdf(pdf_path)
    if title:
        metadata = fetch_from_crossref(title=title, email=email)
        if metadata:
            returned_doi = metadata.get("doi", "").lower()
            if returned_doi and returned_doi in known_dois:
                log.debug("Matched DOI already in library via title search: %s", returned_doi)
                return {
                    "filename": pdf_path.name,
                    "doi": returned_doi,
                    "confidence_score": 0,
                    "needs_review": False,
                    "already_tracked": True,
                    "ai_rejected": False,
                }
            # Year sanity check: if the filename contains a year and the CrossRef
            # result's year differs by more than 5, the match is likely wrong.
            fn_year_m = _YEAR_IN_STEM.search(pdf_path.stem)
            cr_year = metadata.get("year")
            if fn_year_m and cr_year:
                try:
                    gap = abs(int(fn_year_m.group()) - int(cr_year))
                    if gap > 5:
                        log.debug(
                            "CrossRef year %s vs filename year %s (gap %d) — discarding title match for %s",
                            cr_year, fn_year_m.group(), gap, pdf_path.name,
                        )
                        metadata = None
                except ValueError:
                    pass
        if metadata:
            metadata["filename"] = pdf_path.name
            metadata["ai_rejected"] = False
            return metadata

    # --- CrossRef found nothing ---

    if not ai_verify:
        # Existing behaviour: PDF-only fallback or needs_review
        if title:
            authors = extract_authors_from_pdf(pdf_path)
            year = extract_year_from_pdf(pdf_path)
            log.debug(
                "PDF-only fallback for %s — authors: %s  year: %s",
                pdf_path.name, authors, year,
            )
            return {
                "filename": pdf_path.name,
                "title": title,
                "authors": authors,
                "year": year,
                "journal": "",
                "volume": "",
                "issue": "",
                "pages": "",
                "doi": "",
                "item_type": "preprint",
                "confidence_score": 5.0,
                "needs_review": False,
                "already_tracked": False,
                "ai_rejected": False,
            }
        log.debug("No metadata found for %s", pdf_path.name)
        return {
            "filename": pdf_path.name,
            "extracted_title": title,
            "confidence_score": 0,
            "needs_review": True,
            "already_tracked": False,
            "ai_rejected": False,
        }

    # ai_verify=True path — no title means no query to send → reject
    if not title:
        log.debug("ai_verify: no extractable title for %s — rejected", pdf_path.name)
        return {
            "filename": pdf_path.name,
            "confidence_score": 0,
            "needs_review": False,
            "already_tracked": False,
            "ai_rejected": True,
        }

    authors = extract_authors_from_pdf(pdf_path)
    year = extract_year_from_pdf(pdf_path)

    # --- Tier 2: Semantic Scholar ---
    s2_result = verify_with_semantic_scholar(title, api_key=s2_api_key, sleep_seconds=s2_sleep_seconds)
    if s2_result is True:
        log.debug("ai_verify: S2 confirmed — importing as preprint: %s", pdf_path.name)
        return {
            "filename": pdf_path.name,
            "title": title,
            "authors": authors,
            "year": year,
            "journal": "",
            "volume": "",
            "issue": "",
            "pages": "",
            "doi": "",
            "item_type": "preprint",
            "confidence_score": 12.0,
            "needs_review": False,
            "already_tracked": False,
            "ai_rejected": False,
        }
    # s2_result is False or None (None = network failure → fall through to tier 3)

    # --- Tier 3: Working-paper signals ---
    if detect_working_paper_signals(pdf_path):
        log.debug("ai_verify: working-paper signals found — importing as report: %s", pdf_path.name)
        return {
            "filename": pdf_path.name,
            "title": title,
            "authors": authors,
            "year": year,
            "journal": "",
            "volume": "",
            "issue": "",
            "pages": "",
            "doi": "",
            "item_type": "report",
            "confidence_score": 7.0,
            "needs_review": False,
            "already_tracked": False,
            "ai_rejected": False,
        }

    # --- All tiers failed ---
    log.debug("ai_verify: all tiers failed for %s — rejected", pdf_path.name)
    return {
        "filename": pdf_path.name,
        "extracted_title": title,
        "confidence_score": 0,
        "needs_review": False,
        "already_tracked": False,
        "ai_rejected": True,
    }
