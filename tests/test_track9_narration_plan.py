from __future__ import annotations

import pytest

from app.production.models import (
    Production,
    ProductionCommand,
    ProductionUnit,
    SourceAsset,
    SourceTranscript,
    TranscriptSegment,
)
from app.production.narration import (
    build_grounded_narration_plan,
    validate_narration_artifact,
)
from app.production.storage import (
    load_narration_artifact,
    save_narration_artifact,
    save_new_production,
)


def _make_production(**overrides):
    payload = {
        "production_id": "prd_20260831_track9_001",
        "title": "Track 9 narration plan",
        "command": ProductionCommand(
            text="Create a grounded summary narration plan",
            workload_shape="single_video",
            explicit_constraints={},
            creation_context={"source": "test"},
            issued_at="2026-08-31T09:00:00+00:00",
        ),
        "units": [
            ProductionUnit(
                unit_id="unit-001",
                ordinal=1,
                unit_type="standalone_video",
                title="Original unit",
                premise="Sample source",
                content_intent={"tone": "calm"},
            )
        ],
        "created_at": "2026-08-31T09:00:00+00:00",
    }
    payload.update(overrides)
    return Production(**payload)


def _make_asset() -> SourceAsset:
    return SourceAsset(
        asset_id="src-001",
        original_filename="source.mp4",
        stored_path="source/src-001.mp4",
        sha256="a" * 64,
        duration=180.0,
        width=1280,
        height=720,
        mime="video/mp4",
        file_size=1234,
        ingested_at="2026-08-31T09:00:00+00:00",
    )


def _make_transcript() -> SourceTranscript:
    segments = [
        TranscriptSegment(text=f"Segment {index}", start=index * 10.0, end=(index + 1) * 10.0)
        for index in range(12)
    ]
    return SourceTranscript(
        asset_id="src-001",
        language="en",
        segments=segments,
        provider="mock",
        provider_version="1.0",
        created_at="2026-08-31T09:00:00+00:00",
        transcript_path="source/src-001.transcript.json",
    )


def test_track9_narration_plan_persists_and_round_trips(tmp_path):
    production = _make_production(source_assets=[_make_asset()])
    save_new_production(production, root=tmp_path)
    asset = production.source_assets[0]
    transcript = _make_transcript()

    artifact = build_grounded_narration_plan(
        production.production_id,
        "unit-001",
        asset,
        transcript,
        target_duration_seconds=30.0,
        mock_mode=True,
    )
    path = save_narration_artifact(
        artifact,
        production_id=production.production_id,
        unit_id="unit-001",
        root=tmp_path,
        source_asset=asset,
        source_transcript=transcript,
    )

    assert path.is_file()
    reloaded = load_narration_artifact(production.production_id, "unit-001", root=tmp_path)
    assert reloaded.narration_id == artifact.narration_id
    assert reloaded.beats[0].source_refs[0].asset_id == "src-001"
    assert reloaded.target_duration_seconds == 30.0
    assert len(reloaded.beats) > 1


def test_track9_rejects_invalid_grounding_and_transcript_refs():
    asset = _make_asset()
    transcript = _make_transcript()
    artifact = build_grounded_narration_plan(
        "prd_20260831_track9_002",
        "unit-001",
        asset,
        transcript,
        target_duration_seconds=30.0,
        mock_mode=True,
    )

    bad_range = artifact.model_copy(
        update={
            "beats": [
                artifact.beats[0].model_copy(
                    update={
                        "source_refs": [
                            artifact.beats[0].source_refs[0].model_copy(
                                update={"start_seconds": 999.0, "end_seconds": 1001.0}
                            )
                        ]
                    }
                )
            ]
        }
    )
    with pytest.raises(ValueError):
        validate_narration_artifact(bad_range, source_asset=asset, source_transcript=transcript)

    bad_transcript = artifact.model_copy(
        update={
            "beats": [
                artifact.beats[0].model_copy(update={"transcript_refs": ["99999"]})
            ]
        }
    )
    with pytest.raises(ValueError):
        validate_narration_artifact(bad_transcript, source_asset=asset, source_transcript=transcript)


def test_track9_long_form_chunking_keeps_order_and_grounding(tmp_path):
    production = _make_production(source_assets=[_make_asset()])
    save_new_production(production, root=tmp_path)
    asset = production.source_assets[0]
    transcript = _make_transcript()
    long_transcript = SourceTranscript(
        asset_id="src-001",
        language="en",
        segments=[
            TranscriptSegment(text=f"Long segment {index}", start=index * 5.0, end=(index + 1) * 5.0)
            for index in range(25)
        ],
        provider="mock",
        provider_version="1.0",
        created_at="2026-08-31T09:00:00+00:00",
        transcript_path="source/src-001.transcript.json",
    )

    artifact = build_grounded_narration_plan(
        production.production_id,
        "unit-001",
        asset,
        long_transcript,
        target_duration_seconds=45.0,
        mock_mode=True,
    )
    assert len(artifact.beats) > 1
    assert [beat.order for beat in artifact.beats] == sorted(beat.order for beat in artifact.beats)
    assert all(beat.source_refs[0].asset_id == "src-001" for beat in artifact.beats)
    assert all(beat.transcript_refs for beat in artifact.beats)
