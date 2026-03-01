"""
Zotero-sweep: Scan research directories for untracked PDFs and import them
into Zotero as linked-file attachments.

Usage:
  python main.py [--verbose] scan
  python main.py [--verbose] scan --all
  python main.py [--verbose] scan --folders=recommended
  python main.py [--verbose] scan --no-filter
  python main.py [--verbose] import
  python main.py [--verbose] import --dry-run
  python main.py [--verbose] import --all
  python main.py [--verbose] import --folders=/some/path
  python main.py [--verbose] cleanup
  python main.py [--verbose] cleanup --fix
"""

import argparse
import pathlib
import sys

from zotero_sweep.config import load_config
from zotero_sweep.logger import get_logger, set_verbose, setup_file_handler
from zotero_sweep.scanner import get_known_pdfs, scan_for_pdfs, filter_non_papers
from zotero_sweep.metadata import get_metadata
from zotero_sweep import cleanup as cleanup_mod
from zotero_sweep import importer as importer_mod

# Initialise root logger before config load (no file handler yet)
log = get_logger("main")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_size(path: pathlib.Path) -> str:
    """Return file size as a human-readable string."""
    try:
        size = path.stat().st_size
        if size >= 1_048_576:
            return f"{size / 1_048_576:.1f} MB"
        return f"{size / 1024:.0f} KB"
    except OSError:
        return "? KB"


def _print_item(pdf_path: pathlib.Path, meta: dict):
    """Display a single candidate item for the interactive import prompt."""
    if meta.get("needs_review"):
        title_guess = meta.get("extracted_title") or "(could not extract)"
        print(
            f"\n[NO METADATA] File: {pdf_path}  ({_format_size(pdf_path)})\n"
            f"              Extracted title guess: \"{title_guess}\""
        )
    else:
        score = meta.get("confidence_score", 0)
        title = meta.get("title", meta.get("filename", pdf_path.name))
        authors = meta.get("authors", [])
        author_str = authors[0].rsplit(" ", 1)[-1] if authors else "(unknown)"
        journal = meta.get("journal", "")
        year = meta.get("year", "")
        details = "  —  ".join(filter(None, [author_str, journal, year]))
        print(
            f"\n[conf: {score:.1f}] \"{title}\"\n"
            f"             {details}\n"
            f"             File: {pdf_path}  ({_format_size(pdf_path)})"
        )


# ---------------------------------------------------------------------------
# Helpers: folder selection and auto-skip summary
# ---------------------------------------------------------------------------

def select_directories(args, config) -> list[str]:
    """Return the list of directories to scan based on --folders flag or interactive menu."""
    folders_arg = getattr(args, "folders", None)

    if folders_arg:
        if folders_arg == "all":
            return config["scan_directories"]
        if folders_arg == "recommended":
            return config.get("recommended_folders", config["scan_directories"])
        # Treat as a custom path
        p = pathlib.Path(folders_arg)
        if not p.exists():
            print(f"  Warning: path does not exist: {folders_arg}")
        return [folders_arg]

    # Interactive menu (TTY only)
    if sys.stdin.isatty():
        print("\nSelect folders to scan:")
        print("  [1] Everything    — all directories in config")
        rec = config.get("recommended_folders")
        if rec:
            print(f"  [2] Recommended   — {len(rec)} high-priority folders")
        print("  [3] Browse        — enter a custom path")
        choice = input("\nChoice [1]: ").strip() or "1"
        if choice == "2" and rec:
            return rec
        if choice == "3":
            path = input("  Enter directory path: ").strip()
            if path:
                p = pathlib.Path(path)
                if not p.exists():
                    print(f"  Warning: path does not exist: {path}")
                return [path]
        # Default / choice "1" / empty input
        return config["scan_directories"]

    # Non-interactive, no flag: use all
    return config["scan_directories"]


def _print_auto_skip_summary(skipped: list[tuple[pathlib.Path, str]]) -> None:
    """Print a grouped summary of auto-skipped files."""
    if not skipped:
        return
    by_reason: dict[str, int] = {}
    for _, reason in skipped:
        by_reason[reason] = by_reason.get(reason, 0) + 1
    print(f"\n  Auto-skipped {len(skipped)} files (likely not research papers):")
    for reason, count in sorted(by_reason.items(), key=lambda x: -x[1]):
        print(f"    {count:>4}  {reason}")
    print()


# ---------------------------------------------------------------------------
# Subcommand: scan
# ---------------------------------------------------------------------------

def cmd_scan(args, config, zot=None):
    """List PDFs where metadata was found (or all candidates with --all)."""
    directories = select_directories(args, config)
    known_filenames, known_dois = get_known_pdfs(config["zotero_db_path"])
    candidates, skipped = scan_for_pdfs(directories, known_filenames, config)

    heuristic_skipped = []
    if not args.no_filter:
        candidates, heuristic_skipped = filter_non_papers(candidates, config)
        _print_auto_skip_summary(heuristic_skipped)

    if not candidates:
        print("No untracked PDF candidates found.")
        if skipped or heuristic_skipped:
            print(
                f"({len(skipped)} PDFs were skipped by size/name filters"
                f"{', ' + str(len(heuristic_skipped)) + ' by heuristic filter' if heuristic_skipped else ''}"
                f" — run with --verbose to see details)"
            )
        return

    log.debug("Skipped %d files. Use --verbose to see reasons.", len(skipped))
    if args.verbose:
        all_skipped = skipped + heuristic_skipped
        for path, reason in all_skipped[:20]:
            log.debug("  SKIP %-60s  %s", str(path)[-60:], reason)
        if len(all_skipped) > 20:
            log.debug("  ... and %d more skipped files", len(all_skipped) - 20)

    print(f"\nFound {len(candidates)} candidate PDFs. Looking up metadata...\n")

    matched = []
    needs_review = []

    for i, pdf in enumerate(candidates, 1):
        print(f"\r  [{i}/{len(candidates)}] {pdf.name[:60]:<60}", end="", flush=True)
        meta = get_metadata(pdf, email=config["crossref_email"], known_dois=known_dois)

        if meta.get("already_tracked"):
            log.debug("Already tracked (DOI match): %s", pdf.name)
            continue

        if meta.get("needs_review"):
            needs_review.append((pdf, meta))
        else:
            matched.append((pdf, meta))

    print()  # newline after progress

    print(f"\n{'='*60}")
    print(f"  Scan results: {len(matched)} matched,  {len(needs_review)} needs review")
    print(f"{'='*60}")

    for pdf, meta in matched:
        _print_item(pdf, meta)

    if args.all and needs_review:
        print(f"\n--- Items needing review ({len(needs_review)}) ---")
        for pdf, meta in needs_review:
            _print_item(pdf, meta)

    if not args.all and needs_review:
        print(
            f"\n{len(needs_review)} item(s) had no metadata match "
            f"(run 'scan --all' to see them)."
        )


# ---------------------------------------------------------------------------
# Subcommand: import
# ---------------------------------------------------------------------------

def cmd_import(args, config, zot):
    """Import PDFs into Zotero, with interactive confirmation."""
    # Sync reminder
    print(
        "\nTip: Please sync Zotero first (Ctrl+Shift+S) to avoid version conflicts."
    )
    input("Press Enter to continue...")

    directories = select_directories(args, config)
    known_filenames, known_dois = get_known_pdfs(config["zotero_db_path"])
    candidates, skipped = scan_for_pdfs(directories, known_filenames, config)

    heuristic_skipped = []
    if not args.no_filter:
        candidates, heuristic_skipped = filter_non_papers(candidates, config)
        _print_auto_skip_summary(heuristic_skipped)

    if not candidates:
        print("No untracked PDF candidates found.")
        return

    print(f"\nFound {len(candidates)} candidate PDFs. Looking up metadata...\n")

    matched = []
    needs_review = []

    for i, pdf in enumerate(candidates, 1):
        print(f"\r  [{i}/{len(candidates)}] {pdf.name[:60]:<60}", end="", flush=True)
        meta = get_metadata(pdf, email=config["crossref_email"], known_dois=known_dois)

        if meta.get("already_tracked"):
            log.debug("Already tracked (DOI match): %s", pdf.name)
            continue

        if meta.get("needs_review"):
            needs_review.append((pdf, meta))
        else:
            matched.append((pdf, meta))

    print()  # newline after progress

    results = {"imported": 0, "skipped": 0, "failed": 0, "dry_run": 0}

    # --- Process matched items ---
    import_all = args.all
    skip_unmatched = False

    print(f"\n{'='*60}")
    print(f"  {len(matched)} items with metadata match")
    print(f"{'='*60}")

    for pdf, meta in matched:
        _print_item(pdf, meta)

        if args.dry_run:
            result = importer_mod.import_pdf(zot, pdf, meta, dry_run=True)
            results["dry_run"] += 1
            continue

        if import_all:
            result = importer_mod.import_pdf(zot, pdf, meta)
        else:
            while True:
                choice = input("  Import? [y]es / [n]o / [a]ll / [q]uit: ").strip().lower()
                if choice in ("y", "yes"):
                    result = importer_mod.import_pdf(zot, pdf, meta)
                    break
                elif choice in ("n", "no"):
                    result = {"status": "skipped"}
                    break
                elif choice in ("a", "all"):
                    import_all = True
                    result = importer_mod.import_pdf(zot, pdf, meta)
                    break
                elif choice in ("q", "quit"):
                    print("\nQuitting. Partial import complete.")
                    _print_summary(results)
                    return
                else:
                    print("  Please enter y, n, a, or q.")

        results[result["status"]] = results.get(result["status"], 0) + 1

    # --- Process needs_review items ---
    if needs_review and not args.dry_run:
        print(f"\n{'='*60}")
        print(f"  {len(needs_review)} items with no metadata match")
        print(f"{'='*60}")

        for pdf, meta in needs_review:
            _print_item(pdf, meta)

            if skip_unmatched:
                results["skipped"] += 1
                continue

            while True:
                choice = input(
                    "  Import anyway? [y]es / [n]o / [s]kip all unmatched: "
                ).strip().lower()
                if choice in ("y", "yes"):
                    result = importer_mod.import_pdf(zot, pdf, meta)
                    results[result["status"]] = results.get(result["status"], 0) + 1
                    break
                elif choice in ("n", "no"):
                    results["skipped"] += 1
                    break
                elif choice in ("s", "skip"):
                    skip_unmatched = True
                    results["skipped"] += 1
                    break
                else:
                    print("  Please enter y, n, or s.")

    _print_summary(results)

    if not args.dry_run and results.get("imported", 0) > 0:
        print(
            "\nDone. Please sync Zotero (Ctrl+Shift+S) to see the new items in your library.\n"
        )


def _print_summary(results: dict):
    print(f"\n{'='*40}")
    print("  Import summary:")
    for status, count in results.items():
        if count:
            print(f"    {status:<12} {count:>4}")
    print(f"{'='*40}\n")


# ---------------------------------------------------------------------------
# Subcommand: cleanup
# ---------------------------------------------------------------------------

def cmd_cleanup(args, config, zot):
    """Report library issues; optionally fix missing metadata."""
    report = cleanup_mod.generate_report(zot)

    if not args.fix:
        print(
            "Run 'python main.py cleanup --fix' to attempt filling in missing metadata.\n"
        )
        return

    # Enrich items missing DOI (up to all of them)
    missing_doi_items = report["missing"]["missing_doi"]
    if not missing_doi_items:
        print("No items with missing DOI to enrich.")
        return

    print(f"\nAttempting to fill metadata for {len(missing_doi_items)} items missing DOI...\n")
    enriched = 0
    failed = 0
    no_match = 0

    for i, item in enumerate(missing_doi_items, 1):
        title = item.get("data", {}).get("title", "(no title)")[:60]
        print(f"\r  [{i}/{len(missing_doi_items)}] {title:<60}", end="", flush=True)

        result = cleanup_mod.enrich_item(
            zot, item, email=config["crossref_email"], dry_run=args.dry_run
        )

        status = result.get("status", "")
        if status in ("updated", "dry_run"):
            enriched += 1
        elif status == "failed":
            failed += 1
        else:
            no_match += 1

    print()
    print(f"\nEnrichment complete: {enriched} updated, {no_match} no match, {failed} failed\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Zotero-sweep: scan, import, and clean up your Zotero library",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py scan\n"
            "  python main.py scan --all\n"
            "  python main.py import --dry-run\n"
            "  python main.py import\n"
            "  python main.py import --all\n"
            "  python main.py cleanup\n"
            "  python main.py cleanup --fix\n"
            "  python main.py --verbose scan\n"
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show DEBUG-level messages on console"
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True

    # scan subcommand
    scan_parser = subparsers.add_parser(
        "scan",
        help="List candidate PDFs and their matched metadata (read-only)"
    )
    scan_parser.add_argument(
        "--all", action="store_true",
        help="Also show items where no metadata was found"
    )
    scan_parser.add_argument(
        "--folders", type=str, metavar="FOLDERS",
        help=(
            "Which folders to scan: 'all' (config scan_directories), "
            "'recommended' (config recommended_folders), or a custom path"
        ),
    )
    scan_parser.add_argument(
        "--no-filter", action="store_true",
        help="Disable research-paper heuristic; process all candidates"
    )

    # import subcommand
    import_parser = subparsers.add_parser(
        "import",
        help="Import candidate PDFs into Zotero"
    )
    import_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be imported without making any changes"
    )
    import_parser.add_argument(
        "--all", action="store_true",
        help="Import all matched items without prompting"
    )
    import_parser.add_argument(
        "--folders", type=str, metavar="FOLDERS",
        help=(
            "Which folders to scan: 'all' (config scan_directories), "
            "'recommended' (config recommended_folders), or a custom path"
        ),
    )
    import_parser.add_argument(
        "--no-filter", action="store_true",
        help="Disable research-paper heuristic; process all candidates"
    )

    # cleanup subcommand
    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="Report duplicates and missing metadata (--fix to enrich)"
    )
    cleanup_parser.add_argument(
        "--fix", action="store_true",
        help="Attempt to fill in missing metadata via CrossRef"
    )
    cleanup_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what --fix would change without writing anything"
    )

    args = parser.parse_args()

    # Load config and set up file logging
    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    setup_file_handler(config["log_file"])
    if args.verbose:
        set_verbose(True)

    # Initialise Zotero API client for commands that need it
    zot = None
    if args.command in ("import", "cleanup"):
        try:
            from pyzotero import zotero
        except ImportError:
            print(
                "\nError: pyzotero is not installed.\n"
                "Run: python3 -m pip install pyzotero\n",
                file=sys.stderr,
            )
            sys.exit(1)

        zot = zotero.Zotero(
            library_id=config["zotero_user_id"],
            library_type=config["zotero_library_type"],
            api_key=config["zotero_api_key"],
        )

    # Dispatch to subcommand
    try:
        if args.command == "scan":
            cmd_scan(args, config, zot)
        elif args.command == "import":
            cmd_import(args, config, zot)
        elif args.command == "cleanup":
            cmd_cleanup(args, config, zot)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
