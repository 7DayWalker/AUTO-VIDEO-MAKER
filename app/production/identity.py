"""
AUTO Video Maker - Phase 9: Production identity (A7).

Production IDs follow the frozen convention:

    prd_YYYYMMDD_<slug>_<###>

Allocation is COLLISION-SAFE: a candidate is only returned when no production
directory with that identity exists. This deliberately improves on the legacy
project-ID convention (``generate_project_id`` uses a random suffix without a
collision check — content/models.py:283-301).

Identity is stable forever and independent of ordering, execution state, and
absolute filesystem paths (the directory name derives from the identity, never
the reverse).
"""

from __future__ import annotations

import re
import random
from datetime import date
from pathlib import Path
from typing import Optional

_SLUG_MAX_LENGTH = 40
_SUFFIX_ATTEMPTS = 1000  # ### -> 000..999


def slugify(title: str) -> str:
    """Create a filesystem-safe lowercase slug (repository convention).

    Mirrors ``generate_project_id`` slug handling (content/models.py:293-297)
    but tolerates empty results by falling back to a neutral slug.
    """
    slug = (title or "").lower()
    slug = re.sub(r"[^a-z0-9\s]", "", slug)
    slug = re.sub(r"\s+", "_", slug.strip())
    slug = slug[:_SLUG_MAX_LENGTH]
    slug = slug.strip("_")
    return slug or "production"


def production_id_exists(production_id: str, productions_dir: Path) -> bool:
    """True when a production directory with this identity already exists."""
    return (Path(productions_dir) / production_id).exists()


def generate_production_id(
    title: str,
    *,
    productions_dir: Path,
    today: Optional[date] = None,
    rng: Optional[random.Random] = None,
) -> str:
    """Allocate a collision-safe ``prd_YYYYMMDD_<slug>_<###>`` identity.

    The ``###`` suffix is scanned (randomized start, bounded) and a candidate
    is only returned when ``<productions_dir>/<candidate>`` does NOT exist.
    Raises ``RuntimeError`` if all suffixes collide (directory exhaustion).
    """
    day = today or date.today()
    date_prefix = day.strftime("%Y%m%d")
    slug = slugify(title)
    base = f"prd_{date_prefix}_{slug}_"

    chooser = rng if rng is not None else random
    start = chooser.randint(0, _SUFFIX_ATTEMPTS - 1) if rng is not None else random.randint(0, _SUFFIX_ATTEMPTS - 1)

    for offset in range(_SUFFIX_ATTEMPTS):
        candidate = f"{base}{(start + offset) % _SUFFIX_ATTEMPTS:03d}"
        if not production_id_exists(candidate, productions_dir):
            return candidate

    raise RuntimeError(
        f"Cannot allocate a collision-safe production_id for '{title}': "
        f"all {_SUFFIX_ATTEMPTS} suffixes for {base}* are taken in "
        f"{productions_dir}"
    )
