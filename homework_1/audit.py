# ===========================================================================
# audit.py  --  file-based audit trail for Tool 3 + the audit:// resource
# ===========================================================================
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path



BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = Path(os.getenv("AUDIT_LOG", "audit.log"))
if not LOG_PATH.is_absolute():
    LOG_PATH = BASE_DIR / LOG_PATH
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("audit")
logger.setLevel(logging.INFO)

# Keep audit messages from being duplicated by the root logger.
logger.propagate = False

# Avoid attaching multiple file handlers if this module is imported again.
if not logger.handlers:
    # Rotate the file after 1 MB and retain up to three older log files.
    handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3)
    # Store each event with its timestamp and message.
    handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    logger.addHandler(handler)


def write_event(actor: str, action: str, detail: str = "") -> None:
    """Append one formatted audit event to the log file."""
    logger.info("%s | %s | %s", actor, action, detail)


def read_log() -> str:
    """Return the complete audit log, or an empty string if it does not exist."""
    if not LOG_PATH.exists():
        return ""
    return LOG_PATH.read_text(encoding="utf-8")
