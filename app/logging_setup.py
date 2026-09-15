"""Batch 1I — central runtime logging configuration for AVM.

A deliberately SMALL, configuration-first module. AVM already writes
extensive, useful lifecycle logging through per-module
``logging.getLogger(__name__)`` loggers; the confirmed problem was that the
Command Center/server runtime never configured the logging system, so
Python's last-resort handler silently dropped every INFO record.

This module restores operator visibility without creating a new
observability architecture:

- standard library ``logging`` only (no structured/JSON logging, no
  third-party libraries, no metrics/tracing/event systems);
- the existing AVM text format;
- idempotent configuration (repeated calls never duplicate handlers);
- ``LOG_LEVEL`` from the environment (via ``app.config``), default INFO;
- an optional timestamped file destination for the CLI runtimes;
- a stream handler that tolerates Windows console encoding limitations
  (``errors="replace"`` semantics) without changing log semantics.

LOGS ARE NOT DURABLE PRODUCTION TRUTH: provenance, accounting, and
checkpoints remain the authoritative records. Logging only observes.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional, Union

import app.config as _config

# The existing AVM text format (used by the legacy CLI setup functions).
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Marker attribute set on the root logger once this module configured it.
_CONFIGURED_MARKER = "_avm_logging_configured"

# Third-party libraries whose per-request records are noise for operators
# (existing produce.py behavior, preserved centrally).
_NOISY_LIBRARIES = ("urllib3", "httpx")


class _EncodingSafeStreamHandler(logging.StreamHandler):
    """A stream handler that survives Windows console encoding limits.

    Console streams on Windows are frequently cp1252 while AVM log text
    contains UTF-8 punctuation. Rather than raising per record, unmappable
    characters are replaced — log semantics are unchanged.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            stream = self.stream
            encoding = getattr(stream, "encoding", None) or "utf-8"
            try:
                stream.write(message + self.terminator)
            except UnicodeEncodeError:
                safe = message.encode(encoding, "replace").decode(
                    encoding, "replace"
                )
                stream.write(safe + self.terminator)
            self.flush()
        except Exception:  # noqa: BLE001 - stdlib handler contract
            self.handleError(record)


def _resolve_level(level: Union[None, str, int]) -> int:
    if level is None:
        level = _config.LOG_LEVEL
    if isinstance(level, str):
        resolved = logging.getLevelName(level.strip().upper())
        if isinstance(resolved, int):
            return resolved
        return logging.INFO
    return int(level)


def configure_logging(
    level: Union[None, str, int] = None,
    *,
    log_file: Optional[Path] = None,
    stream: Optional[object] = None,
) -> logging.Logger:
    """Configure the ROOT logger for AVM runtimes (idempotent).

    - Default level comes from ``app.config.LOG_LEVEL`` (environment
      ``LOG_LEVEL``, default ``INFO``); an explicit ``level`` wins.
    - One stdout stream handler (encoding-safe on Windows) and, when
      ``log_file`` is provided (CLI runtimes), one UTF-8 file handler.
    - Repeated calls NEVER duplicate handlers; a repeated call without an
      explicit level keeps the previously configured level.
    - Noisy third-party libraries are held at WARNING.

    Returns the configured root logger.
    """
    root = logging.getLogger()

    if getattr(root, _CONFIGURED_MARKER, False):
        # Idempotent: never add duplicate handlers on re-entry.
        if level is not None:
            root.setLevel(_resolve_level(level))
        return root

    resolved_level = _resolve_level(level)
    root.setLevel(resolved_level)

    formatter = logging.Formatter(LOG_FORMAT)
    stream_handler = _EncodingSafeStreamHandler(
        stream if stream is not None else sys.stdout
    )
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if log_file is not None:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    for noisy in _NOISY_LIBRARIES:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    setattr(root, _CONFIGURED_MARKER, True)
    return root