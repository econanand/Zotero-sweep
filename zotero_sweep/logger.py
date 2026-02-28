import logging
import pathlib

_console_handler = None


def get_logger(name: str) -> logging.Logger:
    """Return a logger with the given name.

    On first call (when the root 'zotero_sweep' logger has no handlers),
    two handlers are attached:
      - Console: INFO level by default
      - File (logs/zotero_sweep.log): DEBUG level always

    Subsequent calls return the existing logger for the given name, which
    inherits the root handlers.
    """
    global _console_handler

    root_logger = logging.getLogger("zotero_sweep")

    if not root_logger.handlers:
        root_logger.setLevel(logging.DEBUG)

        # Console handler
        _console_handler = logging.StreamHandler()
        _console_handler.setLevel(logging.INFO)
        console_fmt = logging.Formatter("%(levelname)s: %(message)s")
        _console_handler.setFormatter(console_fmt)
        root_logger.addHandler(_console_handler)

        # File handler — created lazily using config log_file path
        # actual path is set in setup_file_handler() below

    return logging.getLogger(f"zotero_sweep.{name}")


def setup_file_handler(log_file: str):
    """Attach a file handler to the root logger.

    Called once from main.py after config is loaded.
    Creates the logs/ directory if it does not exist.
    """
    log_path = pathlib.Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)

    root_logger = logging.getLogger("zotero_sweep")
    root_logger.addHandler(file_handler)


def set_verbose(enabled: bool):
    """Switch the console handler to DEBUG level when --verbose is passed."""
    global _console_handler
    if _console_handler is not None:
        _console_handler.setLevel(logging.DEBUG if enabled else logging.INFO)
