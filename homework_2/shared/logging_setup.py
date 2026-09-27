"""One logging setup, shared by every service in the stack.

Each service writes to stdout and to its own file under logs/, so a run can be
reviewed afterwards without scraping `docker compose logs`. The directory is
bind-mounted from the repo, which is why docker-compose.yml mounts ./logs and
why logs/.gitkeep is committed: Docker creates a missing bind-mount source as
root, and the containers run as an unprivileged user that could not then write
into it.

Rotation is deliberate rather than decorative. A long demo with a chatty agent
would otherwise grow one file without limit on a machine nobody is watching.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path("logs")
# One megabyte holds a few thousand lines, which is more than one demo produces.
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
TIME_FORMAT = "%H:%M:%S"
# Aligned so the eye can scan down the level and source columns. Date is left
# out on purpose: these are per-run files, and a date on every line is bloat.
LINE_FORMAT = "%(asctime)s %(levelname)-5s %(name)-11s %(message)s"

# Third-party libraries that narrate every HTTP request at INFO. One GAIA turn
# produces a dozen "HTTP Request: POST ..." lines that say nothing about what
# the service decided, which is the bloat these files exist to avoid. Their
# warnings and errors are kept: a failing request is worth knowing about.
# Matched by prefix rather than by exact name, because these libraries create
# loggers lazily and under more than one name (httpx and httpx2 both appeared
# in a real run), so an exact list would silently miss them.
NOISY_PREFIXES = ("httpx", "httpcore", "openai", "urllib3", "anyio")


def _drop_third_party_chatter(record: logging.LogRecord) -> bool:
    if record.levelno >= logging.WARNING:
        return True
    return not record.name.startswith(NOISY_PREFIXES)


# uvicorn's access log records the health probe every five seconds, which would
# be twelve lines a minute of nothing in every service's file. No filtering is
# needed to keep it out: uvicorn's own LOGGING_CONFIG sets propagate=False on
# "uvicorn" and "uvicorn.access" and gives them their own handlers, so those
# records never reach the root logger and so never reach the file below. The
# same is true of uvicorn's startup banner, which stays in `docker compose logs`.
# Everything this project logs goes through its own loggers, which do propagate.

# Marks the handlers this module installed. Re-running configure() replaces
# only these. Clearing every root handler instead would be simpler and wrong:
# it discards handlers this module never owned, including the one pytest
# attaches to capture logs, so tests would silently see nothing.
_INSTALLED_BY_US = "_gaia_logging_handler"


def configure(service: str, log_dir: Path | None = None) -> None:
    """Send this service's logs to stdout and to logs/<service>.log.

    Safe to call more than once: handlers are replaced, not stacked, so a
    reload or a test calling it twice does not double every line.
    """
    directory = LOG_DIR if log_dir is None else log_dir
    directory.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LINE_FORMAT, datefmt=TIME_FORMAT)
    file_handler = RotatingFileHandler(
        directory / f"{service}.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _INSTALLED_BY_US, False)]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.INFO)
    for handler in (file_handler, stream_handler):
        handler.addFilter(_drop_third_party_chatter)
        setattr(handler, _INSTALLED_BY_US, True)
        root.addHandler(handler)

