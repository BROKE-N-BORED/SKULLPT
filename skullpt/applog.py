"""The app log (U12) - one place for what SKULLPT did, for when
something goes wrong.

Two sinks, one logger ("skullpt"):

  - a rotating timestamped log file in the workspace
    (<workspace>/skullpt.log, 2 MB per file, 2 backups), so a
    problem can be diagnosed after the fact;
  - a ring buffer holding the last 200 lines in memory, whose tail
    goes into the debug report (scrubbed of token-shaped strings
    there, like the rest of the report).

Uncaught exceptions are caught by an excepthook and logged with
their traceback, then handed back to Python's default hook - a
crash leaves a record instead of vanishing, and the traceback still
prints.

Nothing here ever raises: logging must never break the app it
watches. A workspace that cannot take a log file (read-only, gone)
simply gets the ring buffer.
"""
from __future__ import annotations

import logging
import sys
import threading
import traceback
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "skullpt"
LOG_FILENAME = "skullpt.log"
RING_SIZE = 200
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 2

_ring: deque = deque(maxlen=RING_SIZE)
_ring_lock = threading.Lock()
_wired: set[str] = set()
_hook_installed = False


class _RingHandler(logging.Handler):
    """A logging handler that keeps the last RING_SIZE formatted
    lines in memory."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        with _ring_lock:
            _ring.append(line)


def _logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(isinstance(h, _RingHandler) for h in logger.handlers):
        handler = _RingHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    """The app logger. Safe to call before setup() - the ring buffer
    is always attached, the file follows when a workspace is known."""
    return _logger()


def tail(n: int = 30) -> list[str]:
    """The last `n` ring-buffer lines (oldest first)."""
    with _ring_lock:
        return list(_ring)[-n:]


def install_excepthook() -> None:
    """Log uncaught exceptions with their traceback, then hand back
    to Python's default hook. Installed once per process."""
    global _hook_installed
    if _hook_installed:
        return
    _hook_installed = True
    previous = sys.excepthook

    def _hook(exc_type, exc, tb) -> None:
        try:
            _logger().error(
                "uncaught exception:\n%s",
                "".join(traceback.format_exception(exc_type, exc, tb)).rstrip())
        except Exception:
            pass
        previous(exc_type, exc, tb)

    sys.excepthook = _hook


def setup(workspace: str | Path) -> None:
    """Attach the rotating file log for a workspace (once per
    workspace per process) and install the crash hook. Safe to call
    repeatedly and from any entry point - the CLI calls it when the
    workspace is known, the Bench calls it at launch."""
    install_excepthook()
    key = str(Path(workspace))
    if key in _wired:
        return
    _wired.add(key)
    logger = _logger()
    try:
        path = Path(workspace) / LOG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=MAX_BYTES,
                                      backupCount=BACKUP_COUNT,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
    except OSError:
        return  # no file log for this workspace - the ring still works
    from . import __version__

    logger.info("SKULLPT v%s starting, workspace %s", __version__, key)
