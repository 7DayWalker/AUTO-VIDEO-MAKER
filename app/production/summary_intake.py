"""Non-destructive preparation of a dedicated recap from registered evidence."""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from app.production.identity import generate_production_id
from app.production.models import Production, ProductionCommand, ProductionUnit
from app.production.source import find_source_asset, is_sandbox_source, sha256_file
from app.production.storage import (
    load_production, production_dir, productions_root, save_new_production,
    production_source_meta, source_artifact_path, transcript_path_for,
)
from app.transcription.transcribe import load_transcript


def clone_summary_source(production_id: str, asset_id: str, title: str, *, root: Path | None = None) -> Production:
    """Copy ONLY immutable source/transcript evidence to a new identity.

    No registration, decoding, transcription, provider call or master handoff.
    Never share a writable hardlink, mutate the origin, or clone unrelated unit
    narration/masters. Exclusive destination opens refuse any overwrite.
    """
    title = title.strip()
    if not title or len(title) > 200 or "\x00" in title:
        raise ValueError("A dedicated recap title of 1-200 characters is required")
    origin = load_production(production_id, root=root)
    asset = find_source_asset(origin, asset_id, root=root)
    if asset is None or is_sandbox_source(asset):
        raise ValueError("Select an exact registered real movie source")
    transcript = load_transcript(production_id, asset_id, root=root)
    if transcript.provider == "mock" or not transcript.segments:
        raise ValueError("A completed real transcript is required for instant resume")
    expected_transcript = f"source/{asset_id}.transcript.json"
    if transcript.asset_id != asset_id or transcript.transcript_path != expected_transcript:
        raise ValueError("Transcript does not match this source's canonical sidecar")
    if any(segment.end > asset.duration + 1e-6 for segment in transcript.segments):
        raise ValueError("Transcript timestamps exceed the registered movie")
    media = source_artifact_path(production_id, asset.stored_path, root=root)
    sidecar = transcript_path_for(production_id, asset_id, root=root)
    if not media.is_file() or (asset.file_size is not None and media.stat().st_size != asset.file_size):
        raise ValueError("Registered source media is missing or its size changed")
    if sha256_file(media) != asset.sha256:
        raise ValueError("Registered source checksum changed; refusing to clone")

    now = datetime.now().isoformat()
    new_id = generate_production_id(title, productions_dir=productions_root(root))
    dedicated = Production(
        production_id=new_id, title=title, created_at=now,
        command=ProductionCommand(
            text=f"Create a full-movie, source-grounded narrated recap: {title}.",
            workload_shape="single_video", intended_unit_count=1, issued_at=now,
            explicit_constraints={"summary_source_asset_id": asset_id},
            creation_context={"source": "summary_resume", "source_production_id": production_id},
        ),
        units=[ProductionUnit(
            unit_id="unit-001", ordinal=1, unit_type="standalone_video", title=title,
            premise=f"Recap the registered movie from its opening through its ending: {title}.",
            content_intent={"format": "narrated_movie_recap"},
        )],
        source_assets=[asset.model_copy(deep=True)],
    )
    directory = production_dir(new_id, root=root)
    # Reserve an unused directory exclusively, but publish its intent only
    # after both copies validate. An interrupted copy is never a ready project.
    directory.mkdir(parents=True, exist_ok=False)
    transcript_sha = sha256_file(sidecar)
    for original, relative in ((media, asset.stored_path), (sidecar, expected_transcript)):
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with original.open("rb") as incoming, destination.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        shutil.copystat(original, destination)
    if sha256_file(directory / asset.stored_path) != asset.sha256:
        raise ValueError("Dedicated source failed checksum verification; origin remains untouched")
    if sha256_file(directory / expected_transcript) != transcript_sha:
        raise ValueError("Dedicated transcript failed checksum verification; origin remains untouched")
    if load_transcript(new_id, asset_id, root=root) != transcript:
        raise ValueError("Transcript changed while cloning; origin remains untouched")
    with production_source_meta(new_id, root=root).open("x", encoding="utf-8") as handle:
        json.dump({"production_id": new_id, "assets": [asset.model_dump()]}, handle, indent=2, ensure_ascii=False)
    save_new_production(dedicated, root=root)
    return load_production(new_id, root=root)