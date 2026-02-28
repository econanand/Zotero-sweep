import pathlib

from .logger import get_logger

log = get_logger("importer")


def _chunk(lst: list, n: int = 50) -> list[list]:
    """Split a list into chunks of at most n items.

    The Zotero API rejects create_items() calls with more than 50 items.
    """
    return [lst[i:i + n] for i in range(0, len(lst), n)]


def _build_parent_item(metadata: dict) -> dict:
    """Build a Zotero parent bibliographic item dict from metadata."""
    item_type = metadata.get("item_type", "journalArticle")

    creators = []
    for author in metadata.get("authors", []):
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

    item = {
        "itemType": item_type,
        "title": metadata.get("title", ""),
        "creators": creators,
        "date": metadata.get("year", ""),
        "DOI": metadata.get("doi", ""),
        "tags": [],
        "relations": {},
    }

    # Journal-article-specific fields
    if item_type == "journalArticle":
        item["publicationTitle"] = metadata.get("journal", "")
        item["volume"] = metadata.get("volume", "")
        item["issue"] = metadata.get("issue", "")
        item["pages"] = metadata.get("pages", "")
    elif item_type == "bookSection":
        item["bookTitle"] = metadata.get("journal", "")
        item["pages"] = metadata.get("pages", "")

    return item


def _build_attachment(pdf_path: pathlib.Path, parent_key: str) -> dict:
    """Build a linked-file attachment dict."""
    return {
        "itemType": "attachment",
        "linkMode": "linked_file",
        "title": pdf_path.name,
        "contentType": "application/pdf",
        "path": str(pdf_path.resolve()),
        "parentItem": parent_key,
        "tags": [],
        "relations": {},
    }


def import_pdf(
    zot,
    pdf_path: pathlib.Path,
    metadata: dict,
    dry_run: bool = False,
) -> dict:
    """Create a parent item + linked-file attachment in Zotero.

    Args:
        zot:      pyzotero Zotero instance
        pdf_path: path to the PDF file
        metadata: dict from metadata.get_metadata()
        dry_run:  if True, print what would happen but make no API calls

    Returns a result dict with keys: status, key (or error), title, path
    """
    title = metadata.get("title") or metadata.get("filename", pdf_path.name)
    file_mb = pdf_path.stat().st_size / 1_048_576

    if dry_run:
        authors_str = "; ".join(metadata.get("authors", []))
        print(
            f"  [DRY RUN] Would import:\n"
            f"    Title:   {title}\n"
            f"    Authors: {authors_str or '(unknown)'}\n"
            f"    Year:    {metadata.get('year', '(unknown)')}\n"
            f"    DOI:     {metadata.get('doi', '(none)')}\n"
            f"    File:    {pdf_path}  ({file_mb:.1f} MB)\n"
        )
        return {"status": "dry_run", "title": title, "path": str(pdf_path)}

    try:
        # Step 1: Create parent bibliographic item
        parent_item = _build_parent_item(metadata)
        log.debug("Creating parent item: %s", title[:60])
        response = zot.create_items([parent_item])

        successful = response.get("successful", {})
        if "0" not in successful:
            failed = response.get("failed", {})
            raise RuntimeError(f"Parent item creation failed: {failed}")

        parent_key = successful["0"]["key"]
        log.debug("Parent item created with key: %s", parent_key)

        # Step 2: Create linked-file attachment
        attachment = _build_attachment(pdf_path, parent_key)
        log.debug("Creating attachment for: %s", pdf_path.name)
        zot.create_items([attachment])

        log.info("Imported: %s  [key: %s]", title[:60], parent_key)
        return {
            "status": "imported",
            "key": parent_key,
            "title": title,
            "path": str(pdf_path),
        }

    except Exception as exc:
        log.error("Failed to import %s: %s", pdf_path.name, exc)
        return {
            "status": "failed",
            "error": str(exc),
            "title": title,
            "path": str(pdf_path),
        }
