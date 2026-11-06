"""Dedicated resume never mutates protected source/transcript or old masters."""
import hashlib

import pytest

from app.production.models import Production, ProductionCommand, ProductionUnit, SourceAsset, SourceTranscript, TranscriptSegment
from app.production.storage import save_new_production, production_dir
from app.production.summary_intake import clone_summary_source
from app.transcription.transcribe import load_transcript


def _origin(tmp_path, *, provider="local_whisper"):
    payload = b"hermetic source bytes; no decoding or external providers"
    asset = SourceAsset(asset_id="src-004", original_filename="Kung Fu.mp4", stored_path="source/src-004.mp4",
                        sha256=hashlib.sha256(payload).hexdigest(), duration=3742.174331,
                        file_size=len(payload), ingested_at="2026-11-06T12:00:00", source_kind="browser_upload")
    pid = "prd_20261106_original_bee_001"
    production = Production(production_id=pid, title="Bee project", created_at="2026-11-06T12:00:00",
        command=ProductionCommand(text="Bee", workload_shape="single_video", issued_at="2026-11-06T12:00:00"),
        units=[ProductionUnit(unit_id="unit-001", ordinal=1, unit_type="standalone_video", title="Bee")], source_assets=[asset])
    save_new_production(production, root=tmp_path)
    directory = production_dir(pid, tmp_path)
    (directory / "source").mkdir()
    (directory / asset.stored_path).write_bytes(payload)
    transcript = SourceTranscript(asset_id=asset.asset_id, provider=provider, language="zh", provider_version="tiny",
        created_at="2026-11-06T12:01:00", transcript_path="source/src-004.transcript.json",
        segments=[TranscriptSegment(text="grounded dialogue", start=0, end=3705.74)])
    (directory / transcript.transcript_path).write_text(transcript.model_dump_json(), encoding="utf-8")
    (directory / "units/unit-001/plan/narrated_summary.mp4").write_bytes(b"protected master")
    return production, transcript


def test_clone_reuses_exact_real_evidence_without_copying_old_unit_artifacts(tmp_path):
    origin, transcript = _origin(tmp_path)
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    clone = clone_summary_source(origin.production_id, "src-004", "Dedicated Kung Fu movie", root=tmp_path)
    assert clone.production_id != origin.production_id and clone.plan_version == 1
    assert clone.title == clone.units[0].title == "Dedicated Kung Fu movie"
    assert clone.source_assets == origin.source_assets
    assert load_transcript(clone.production_id, "src-004", root=tmp_path) == transcript
    directory = production_dir(clone.production_id, tmp_path)
    assert not (directory / "units/unit-001/plan/narrated_summary.mp4").exists()
    for name, content in before.items():
        assert (tmp_path / name).read_bytes() == content
    # Independent bytes, not a shared writable hardlink.
    (directory / "source/src-004.mp4").write_bytes(b"clone-only change")
    assert (production_dir(origin.production_id, tmp_path) / "source/src-004.mp4").read_bytes() != b"clone-only change"


@pytest.mark.parametrize("failure", ["mock", "missing_transcript", "changed_media", "wrong_asset"])
def test_invalid_evidence_refuses_clone_without_creating_a_production(tmp_path, failure):
    origin, _ = _origin(tmp_path, provider="mock" if failure == "mock" else "local_whisper")
    directory = production_dir(origin.production_id, tmp_path)
    if failure == "missing_transcript":
        (directory / "source/src-004.transcript.json").unlink()
    if failure == "changed_media":
        (directory / "source/src-004.mp4").write_bytes(b"changed")
    with pytest.raises((ValueError, FileNotFoundError)):
        clone_summary_source(origin.production_id, "src-005" if failure == "wrong_asset" else "src-004", "Kung Fu", root=tmp_path)
    assert [path.name for path in tmp_path.iterdir()] == [origin.production_id]