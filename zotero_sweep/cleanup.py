import re

from .logger import get_logger
from .metadata import fetch_from_crossref

log = get_logger("cleanup")


def _normalise_title(title: str) -> str:
    """Lowercase, remove punctuation, collapse whitespace.

    Does NOT strip leading articles — too aggressive and not needed
    for detecting exact-duplicate imports.
    """
    normalised = re.sub(r"[^\w\s]", "", title.lower())
    return re.sub(r"\s+", " ", normalised).strip()


def find_duplicates(zot) -> list[list[dict]]:
    """Find library items that share a normalised title.

    Fetches all items via the API; groups by normalised title;
    returns groups where more than one item shares that title.

    Each group is a list of full item dicts.
    """
    log.info("Fetching all items from Zotero...")
    all_items = zot.everything(zot.items(itemType="-attachment"))
    log.info("Fetched %d items", len(all_items))

    title_groups: dict[str, list[dict]] = {}
    for item in all_items:
        title = item.get("data", {}).get("title", "").strip()
        if not title:
            continue
        key = _normalise_title(title)
        title_groups.setdefault(key, []).append(item)

    duplicates = [group for group in title_groups.values() if len(group) > 1]
    log.info("Found %d duplicate groups", len(duplicates))
    return duplicates


def find_missing_metadata(zot) -> dict[str, list[dict]]:
    """Return items that are missing DOI, authors, or date.

    Returns a dict with keys 'missing_doi', 'missing_authors', 'missing_date'.
    Each value is a list of item dicts (non-attachment items only).
    """
    log.info("Checking for items with missing metadata...")
    all_items = zot.everything(zot.items(itemType="-attachment"))

    missing_doi = []
    missing_authors = []
    missing_date = []

    for item in all_items:
        data = item.get("data", {})
        item_type = data.get("itemType", "")

        # Only check bibliographic items (skip notes, attachments already filtered)
        if item_type in ("note", "attachment"):
            continue

        if not data.get("DOI", "").strip():
            missing_doi.append(item)

        if not data.get("creators", []):
            missing_authors.append(item)

        if not data.get("date", "").strip():
            missing_date.append(item)

    log.info(
        "Missing: DOI=%d  authors=%d  date=%d",
        len(missing_doi), len(missing_authors), len(missing_date),
    )
    return {
        "missing_doi": missing_doi,
        "missing_authors": missing_authors,
        "missing_date": missing_date,
    }


def enrich_item(zot, item: dict, email: str = "", dry_run: bool = False) -> dict:
    """Try to fill in missing fields for a single library item via CrossRef.

    Searches CrossRef by title; if a match is found (score > 15), updates
    the item's DOI, authors, date, journal, volume, issue, pages in place.

    pyzotero's update_item() handles version tracking automatically when
    you pass the full item dict.

    Returns a summary dict describing what changed.
    """
    data = item.get("data", {})
    title = data.get("title", "").strip()
    if not title:
        return {"status": "skipped", "reason": "no title"}

    log.debug("Enriching: %s", title[:60])
    crossref = fetch_from_crossref(title=title, email=email)
    if not crossref:
        return {"status": "no_match", "title": title}

    changes: dict[str, tuple] = {}  # field -> (old, new)

    def _update(field: str, new_value):
        old = data.get(field, "")
        if new_value and not old:
            changes[field] = (old, new_value)
            data[field] = new_value

    _update("DOI", crossref.get("doi", ""))
    _update("date", crossref.get("year", ""))
    _update("publicationTitle", crossref.get("journal", ""))
    _update("volume", crossref.get("volume", ""))
    _update("issue", crossref.get("issue", ""))
    _update("pages", crossref.get("pages", ""))

    # Authors: only fill if creators list is empty
    if not data.get("creators") and crossref.get("authors"):
        creators = []
        for author in crossref["authors"]:
            parts = author.rsplit(" ", 1)
            if len(parts) == 2:
                given, family = parts[0], parts[1]
            else:
                given, family = "", parts[0]
            creators.append({
                "creatorType": "author",
                "firstName": given,
                "lastName": family,
            })
        changes["creators"] = ([], creators)
        data["creators"] = creators

    if not changes:
        return {"status": "no_changes", "title": title}

    if dry_run:
        log.info("[DRY RUN] Would update %s: %s", title[:50], list(changes.keys()))
        return {"status": "dry_run", "title": title, "changes": list(changes.keys())}

    try:
        zot.update_item(item)
        log.info("Enriched: %s  fields=%s", title[:50], list(changes.keys()))
        return {
            "status": "updated",
            "title": title,
            "changes": list(changes.keys()),
            "confidence_score": crossref.get("confidence_score"),
        }
    except Exception as exc:
        log.error("Failed to update %s: %s", title[:50], exc)
        return {"status": "failed", "title": title, "error": str(exc)}


def generate_report(zot) -> dict:
    """Print a summary of the library state to the console and log.

    Reports:
      - Total item count
      - Duplicate groups
      - Items missing DOI / authors / date
      - Items with no PDF attachment

    Makes no changes to the library.
    """
    log.info("Generating cleanup report...")

    duplicates = find_duplicates(zot)
    missing = find_missing_metadata(zot)

    # Items with no PDF attachment
    all_items = zot.everything(zot.items(itemType="-attachment"))
    total = len(all_items)
    all_attachments = zot.everything(zot.items(itemType="attachment"))

    items_with_pdf: set[str] = set()
    for att in all_attachments:
        data = att.get("data", {})
        if data.get("contentType") == "application/pdf":
            parent = data.get("parentItem")
            if parent:
                items_with_pdf.add(parent)

    no_pdf = [
        item for item in all_items
        if item["key"] not in items_with_pdf
        and item.get("data", {}).get("itemType") not in ("note", "attachment")
    ]

    report = {
        "total_items": total,
        "duplicate_groups": len(duplicates),
        "missing_doi": len(missing["missing_doi"]),
        "missing_authors": len(missing["missing_authors"]),
        "missing_date": len(missing["missing_date"]),
        "no_pdf_attached": len(no_pdf),
        "duplicates": duplicates,
        "missing": missing,
        "no_pdf_items": no_pdf,
    }

    print("\n" + "=" * 55)
    print("  Zotero Library Cleanup Report")
    print("=" * 55)
    print(f"  Total items:           {total:>6}")
    print(f"  Duplicate groups:      {len(duplicates):>6}")
    print(f"  Missing DOI:           {len(missing['missing_doi']):>6}")
    print(f"  Missing authors:       {len(missing['missing_authors']):>6}")
    print(f"  Missing date:          {len(missing['missing_date']):>6}")
    print(f"  No PDF attached:       {len(no_pdf):>6}")
    print("=" * 55 + "\n")

    if duplicates:
        print("Duplicate groups:")
        for i, group in enumerate(duplicates, 1):
            print(f"  Group {i}:")
            for item in group:
                data = item.get("data", {})
                title = data.get("title", "(no title)")[:60]
                year = data.get("date", "")[:4]
                print(f"    [{item['key']}] {title}  ({year})")
        print()

    return report
