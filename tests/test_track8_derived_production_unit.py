from __future__ import annotations

import pytest

from app.production.models import (
    Production,
    ProductionCommand,
    ProductionUnit,
    SourceAsset,
    SourceSegment,
    derive_production_unit_from_segment,
)
from app.production.storage import (
    derive_production_unit_from_segment as derive_persisted,
    load_production,
    save_new_production,
)


def _make_production(**overrides):
    payload = {
        "production_id": "prd_20260831_derivation_001",
        "title": "Derived unit test",
        "command": ProductionCommand(
            text="Create a derived unit from source",
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
                premise="Baseline",
                content_intent={"tone": "calm"},
            )
        ],
        "source_assets": [
            SourceAsset(
                asset_id="src-001",
                original_filename="clip.mp4",
                stored_path="source/src-001.mp4",
                sha256="a" * 64,
                duration=30.0,
                width=1280,
                height=720,
                mime="video/mp4",
                file_size=1111,
                ingested_at="2026-08-31T09:00:00+00:00",
            )
        ],
        "created_at": "2026-08-31T09:00:00+00:00",
    }
    payload.update(overrides)
    return Production(**payload)


def test_successful_derivation_and_lineage(tmp_path):
    production = _make_production()
    save_new_production(production, root=tmp_path)
    asset = production.source_assets[0]
    segment = SourceSegment(
        segment_id="seg-001",
        asset_id="src-001",
        start_seconds=2.0,
        end_seconds=9.5,
        title="Intro clip",
        premise="A short source excerpt",
        transcript_segment_ids=[0, 1],
    )

    result = derive_persisted(
        production.production_id,
        asset,
        segment,
        root=tmp_path,
        title="Derived from source",
        premise="Operator-selected source segment",
        content_intent={"tone": "cinematic"},
    )
    loaded = load_production(production.production_id, root=tmp_path)

    assert len(loaded.units) == 2
    actual = loaded.units[-1]
    assert actual.unit_id == "unit-002"
    assert actual.source_lineage is not None
    assert actual.source_lineage.asset_id == "src-001"
    assert actual.source_lineage.source_segment_ids == ["seg-001"]
    assert actual.source_lineage.source_path == "source/src-001.mp4"
    assert loaded.source_lineage[-1].unit_id == actual.unit_id
    assert loaded.source_lineage[-1].asset_id == "src-001"
    assert result.units[-1].unit_id == actual.unit_id


def test_derivation_is_idempotent(tmp_path):
    production = _make_production()
    save_new_production(production, root=tmp_path)
    asset = production.source_assets[0]
    segment = SourceSegment(
        segment_id="seg-001",
        asset_id="src-001",
        start_seconds=1.0,
        end_seconds=6.0,
        title="A",
        premise="B",
        transcript_segment_ids=[0],
    )

    first = derive_persisted(production.production_id, asset, segment, root=tmp_path)
    second = derive_persisted(production.production_id, asset, segment, root=tmp_path)

    assert len(first.units) == 2
    assert len(second.units) == 2
    assert [u.unit_id for u in second.units if u.source_lineage is not None and u.source_lineage.asset_id == "src-001"] == ["unit-002"]


def test_ownership_and_timing_validation_fail(tmp_path):
    production = _make_production()
    save_new_production(production, root=tmp_path)
    asset = production.source_assets[0]

    wrong_asset = SourceAsset(
        asset_id="src-002",
        original_filename="other.mp4",
        stored_path="source/src-002.mp4",
        sha256="b" * 64,
        duration=10.0,
        width=1280,
        height=720,
        mime="video/mp4",
        file_size=100,
        ingested_at="2026-08-31T09:00:00+00:00",
    )
    bad_segment = SourceSegment(
        segment_id="seg-001",
        asset_id="src-001",
        start_seconds=0.0,
        end_seconds=5.0,
        title="bad",
        premise="timing",
    )

    with pytest.raises((ValueError, TypeError)):
        derive_persisted(production.production_id, wrong_asset, bad_segment, root=tmp_path)

    with pytest.raises(ValueError):
        derive_production_unit_from_segment(
            production,
            asset,
            SourceSegment(
                segment_id="seg-002",
                asset_id="src-001",
                start_seconds=-1.0,
                end_seconds=5.0,
                title="bad",
                premise="timing",
            ),
        )

    overlong = SourceSegment(
        segment_id="seg-002",
        asset_id="src-001",
        start_seconds=20.0,
        end_seconds=35.0,
        title="too long",
        premise="beyond asset",
    )
    with pytest.raises(ValueError):
        derive_production_unit_from_segment(production, asset, overlong)


def test_save_reload_keeps_lineage_and_structural_validity(tmp_path):
    production = _make_production()
    save_new_production(production, root=tmp_path)
    asset = production.source_assets[0]
    segment = SourceSegment(
        segment_id="seg-001",
        asset_id="src-001",
        start_seconds=1.5,
        end_seconds=7.5,
        title="Keep me",
        premise="Persisted lineage",
    )

    final = derive_persisted(production.production_id, asset, segment, root=tmp_path)
    reloaded = load_production(production.production_id, root=tmp_path)

    assert reloaded.units[-1].source_lineage is not None
    assert reloaded.source_lineage[-1].source_segment_ids == ["seg-001"]
    assert reloaded.units[-1].unit_id == final.units[-1].unit_id
    assert reloaded.units[-1].unit_id == "unit-002"
