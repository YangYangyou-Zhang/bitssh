from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_QUERY = re.compile(r"\?[^\s\"']*")


class RedactingFormatter(logging.Formatter):
    """Strip query strings and anything that looks like a token from log lines."""

    def format(self, record: logging.LogRecord) -> str:
        return _QUERY.sub("", super().format(record))


def setup_logging(directory: Path, level: int = logging.INFO) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / "bitssh-backend.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn installs its own handlers; clear them so everything lands in our file.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
