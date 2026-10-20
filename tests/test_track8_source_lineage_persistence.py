from __future__ import annotations

from app.production.models import (
    Production,
    ProductionCommand,
    ProductionUnit,
    SourceAsset,
    SourceLineage,
    derive_source_lineage,
)
from app.production.storage import load_production, save_new_production


def _make_production(**overrides):
    payload = {
        "production_id": "prd_20260831_lineage_001",
        "title": "Source lineage test",
        "command": ProductionCommand(
            text="Generate a lineage-backed clip",
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
                title="Lineage unit",
                premise="A short sample",
                content_intent={"tone": "calm"},
            )
        ],
        "created_at": "2026-08-31T09:00:00+00:00",
    }
    payload.update(overrides)
    return Production(**payload)


def test_production_unit_source_lineage_round_trip(tmp_path):
    lineage = SourceLineage(
        unit_id="unit-001",
        asset_id="src-001",
        source_segment_ids=["seg-001", "seg-002"],
        source_path="source/src-001.mp4",
    )
    production = _make_production(
        units=[
            ProductionUnit(
                unit_id="unit-001",
                ordinal=1,
                unit_type="standalone_video",
                title="Lineage unit",
                premise="A short sample",
                content_intent={"tone": "calm"},
                source_lineage=lineage,
            )
        ]
    )
    save_new_production(production, root=tmp_path)
    loaded = load_production(production.production_id, root=tmp_path)
    assert loaded.units[0].source_lineage.model_dump() == lineage.model_dump()


def test_production_source_assets_and_lineage_round_trip(tmp_path):
    asset = SourceAsset(
        asset_id="src-001",
        original_filename="clip.mp4",
        stored_path="source/src-001.mp4",
        sha256="a" * 64,
        duration=12.5,
        width=1280,
        height=720,
        mime="video/mp4",
        file_size=1234,
        ingested_at="2026-08-31T09:00:00+00:00",
    )
    lineage = SourceLineage(
        unit_id="unit-001",
        asset_id="src-001",
        source_segment_ids=["seg-001"],
        source_path="source/src-001.mp4",
    )
    production = _make_production(source_assets=[asset], source_lineage=[lineage])
    save_new_production(production, root=tmp_path)
    loaded = load_production(production.production_id, root=tmp_path)
    assert loaded.source_assets[0].model_dump() == asset.model_dump()
    assert loaded.source_lineage[0].model_dump() == lineage.model_dump()


def test_legacy_production_without_lineage_fields_loads(tmp_path):
    production = _make_production()
    save_new_production(production, root=tmp_path)
    loaded = load_production(production.production_id, root=tmp_path)
    assert loaded.source_assets is None
    assert loaded.source_lineage is None
    assert loaded.units[0].source_lineage is None


def test_derive_source_lineage_round_trip(tmp_path):
    asset = SourceAsset(
        asset_id="src-001",
        original_filename="clip.mp4",
        stored_path="source/src-001.mp4",
        sha256="c" * 64,
        duration=15.0,
        width=1280,
        height=720,
        mime="video/mp4",
        file_size=999,
        ingested_at="2026-08-31T09:00:00+00:00",
    )
    production = _make_production(
        units=[
            ProductionUnit(
                unit_id="unit-001",
                ordinal=1,
                unit_type="standalone_video",
                title="Derived unit",
                premise="Derived from source",
                content_intent={"tone": "calm"},
            )
        ],
        source_assets=[asset],
    )
    lineage = derive_source_lineage(
        production,
        "unit-001",
        asset_id="src-001",
        source_segment_ids=["seg-001"],
    )
    production = production.model_copy(
        update={
            "units": [
                ProductionUnit(
                    unit_id="unit-001",
                    ordinal=1,
                    unit_type="standalone_video",
                    title="Derived unit",
                    premise="Derived from source",
                    content_intent={"tone": "calm"},
                    source_lineage=lineage,
                )
            ],
            "source_lineage": [lineage],
        }
    )
    save_new_production(production, root=tmp_path)
    loaded = load_production(production.production_id, root=tmp_path)
    assert loaded.units[0].source_lineage.model_dump() == lineage.model_dump()
    assert loaded.source_lineage[0].model_dump() == lineage.model_dump()
