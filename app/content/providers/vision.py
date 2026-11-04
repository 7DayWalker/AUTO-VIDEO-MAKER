"""Strict timestamped image loading shared by the real vision adapters.

Image bytes remain inside Python/request payloads. Never log payloads, base64,
credentials or frame contents, and never silently turn failed images into text.
"""
from __future__ import annotations

import base64
import math
from pathlib import Path


def encoded_frames(images):
    frames = []
    total = 0
    for index, item in enumerate(images or []):
        timestamp = None
        if isinstance(item, (tuple, list)) and len(item) == 2:
            timestamp, item = item
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp < 0:
                raise ValueError("Visual frame timestamp must be finite and non-negative")
        try:
            path = Path(item)
            mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".webp": "image/webp"}.get(path.suffix.lower())
            if mime is None or not 0 < path.stat().st_size <= 8 * 1024 * 1024:
                raise ValueError
            data = path.read_bytes()
        except (OSError, TypeError, ValueError):
            raise ValueError(f"Visual frame {index} is missing, empty, too large or unsupported") from None
        total += len(data)
        if total > 14 * 1024 * 1024:
            raise ValueError("Visual frame payload exceeds the bounded request limit")
        frames.append((timestamp, mime, base64.b64encode(data).decode("ascii")))
    if not frames:
        raise ValueError("Frame sequence requires at least one readable visual frame")
    return frames


def openai_image_parts(images):
    parts = []
    for timestamp, mime, data in encoded_frames(images):
        if timestamp is not None:
            parts.append({"type": "text", "text": f"Source frame at {timestamp:.3f}s"})
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
    return parts