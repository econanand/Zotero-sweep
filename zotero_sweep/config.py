import json
import pathlib

REQUIRED_FIELDS = [
    "zotero_user_id",
    "zotero_api_key",
    "zotero_library_type",
    "scan_directories",
    "zotero_storage_path",
    "zotero_db_path",
    "log_file",
    "crossref_email",
    "min_pdf_size_kb",
    "max_pdf_size_mb",
    "skip_folder_names",
]

CONFIG_PATH = pathlib.Path(__file__).parent.parent / "config.json"
TEMPLATE_PATH = pathlib.Path(__file__).parent.parent / "config.json.template"


def load_config():
    """Load and validate config.json from the project root.

    Returns a plain dict. Raises a friendly error if the file is missing
    or any required field is absent.
    """
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"\nconfig.json not found at: {CONFIG_PATH}\n\n"
            f"To get started, copy the template and fill in your credentials:\n"
            f"  cp {TEMPLATE_PATH} {CONFIG_PATH}\n\n"
            f"Then edit config.json with:\n"
            f"  - Your Zotero user ID (from your profile URL at zotero.org)\n"
            f"  - Your API key (from zotero.org/settings/keys)\n"
            f"  - Your CrossRef email\n"
            f"  - Specific scan_directories (avoid scanning all of Dropbox at once)\n"
        )

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    missing = [field for field in REQUIRED_FIELDS if field not in config]
    if missing:
        raise ValueError(
            f"\nconfig.json is missing required fields: {missing}\n"
            f"Check {TEMPLATE_PATH} for the full list of expected fields.\n"
        )

    return config
