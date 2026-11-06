"""File-backed FFmpeg concat lists shared by recap audio and moving cuts."""
from __future__ import annotations

from pathlib import Path


def write_concat_manifest(path, files):
    """Quote local absolute paths without a shell or Windows argv growth."""
    entries = []
    for file in files:
        value = Path(file).resolve().as_posix()
        if any(character in value for character in ("\n", "\r", "\0")):
            raise ValueError("Concat media paths cannot contain control characters")
        entries.append("file '" + value.replace("'", "'\\''") + "'\n")
    if not entries:
        raise ValueError("Concat requires at least one media segment")
    manifest = Path(path)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("ffconcat version 1.0\n" + "".join(entries), encoding="utf-8")
    return manifest