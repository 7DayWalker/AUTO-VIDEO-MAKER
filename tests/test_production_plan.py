"""
Tests for Phase 9: Durable Production Plan (app/production).

Hermetic guarantees: every test writes only into pytest's ``tmp_path`` (never
the real ``productions/`` area), and nothing here invokes providers, network,
browser automation, credentials, or execution state.

Coverage follows PHASE_9_DURABLE_PRODUCTION_PLAN.md §30.
"""

from __future__ import annotations

import json
import random
import re
import shutil
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.production import identity
from app.production.models import (
    PROVIDER_INTENT_NAMES,
    SCHEMA_NAME,
    SUPPORTED_SCHEMA_VERSION,
    BibleElement,
    EpisodeMetadata,
    PlanReference,
    Production,
    ProductionBible,
    ProductionCommand,
    ProductionUnit,
)
from app.production.storage import (
    compute_content_hash,
    current_path,
    list_productions,
    list_versions,
    load_production,
    load_version_snapshot,
    production_dir,
    production_exists,
    productions_root,
    save_new_production,
    save_revision,
    unit_plan_dir,
    version_path,
)
from app.production.validation import (
    scan_for_forbidden_keys,
    validate_production,
)

FIXED_DAY = date(2026, 8, 31)
CREATED = "2026-08-31T09:00:00+00:00"
REVISED = "2026-08-31T10:00:00+00:00"


def make_command(**overrides) -> ProductionCommand:
    payload = {
        "text": "Produce a short video about tides.",
        "workload_shape": "single_video",
        "explicit_constraints": {"platform": "youtube", "duration_seconds": 60},
        "creation_context": {"source": "test-suite"},
        "issued_at": CREATED,
    }
    payload.update(overrides)
    return ProductionCommand(**payload)


def make_unit(
    unit_id: str = "unit-001",
    ordinal: int = 1,
    unit_type: str = "standalone_video",
    **overrides,
) -> ProductionUnit:
    payload = {
        "unit_id": unit_id,
        "ordinal": ordinal,
        "unit_type": unit_type,
        "title": f"Title for {unit_id}",
        "premise": "A calm explainer.",
        "content_intent": {"tone": "calm", "audience": "general"},
    }
    payload.update(overrides)
    return ProductionUnit(**payload)


def make_production(
    pid: str = "prd_20260831_tides_001",
    units=None,
    **overrides,
) -> Production:
    payload = {
        "production_id": pid,
        "title": "Tides Explained",
        "command": make_command(),
        "units": units if units is not None else [make_unit()],
        "created_at": CREATED,
    }
    payload.update(overrides)
    return Production(**payload)


# ---------------------------------------------------------------------------
# 1. Production creation
# ---------------------------------------------------------------------------


class TestProductionCreation:
    def test_minimal_production_has_frozen_defaults(self):
        production = make_production()
        assert production.schema_name == SCHEMA_NAME == "avm.production"
        assert production.schema_version == SUPPORTED_SCHEMA_VERSION == 1
        assert production.plan_version == 1
        assert production.bible is None
        assert production.revised_at is None
        assert production.revision_notes == []

    def test_production_requires_at_least_one_unit(self):
        with pytest.raises(ValidationError):
            make_production(units=[])

    def test_production_requires_command(self):
        with pytest.raises(ValidationError):
            Production(
                production_id="prd_20260831_tides_001",
                title="Tides Explained",
                units=[make_unit()],
                created_at=CREATED,
            )

    def test_duplicate_unit_identity_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate unit_id"):
            make_production(units=[make_unit("a", 1), make_unit("a", 2)])

    def test_duplicate_ordinals_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate ordinals"):
            make_production(units=[make_unit("a", 1), make_unit("b", 1)])

    def test_production_id_pattern_enforced(self):
        with pytest.raises(ValidationError):
            make_production(pid="project-123")

    def test_unknown_fields_rejected(self):
        with pytest.raises(ValidationError):
            make_production(surprise=1)



# ---------------------------------------------------------------------------
# 2. Production identity generation
# ---------------------------------------------------------------------------


class TestProductionIdentityGeneration:
    def test_identity_format_matches_frozen_convention(self, tmp_path):
        pid = identity.generate_production_id(
            "My Show!", productions_dir=tmp_path, today=FIXED_DAY
        )
        assert re.fullmatch(r"prd_\d{8}_[a-z0-9]+(?:_[a-z0-9]+)*_\d{3}", pid)
        assert pid.startswith("prd_20260831_my_show_")

    def test_identity_uses_provided_date(self, tmp_path):
        pid = identity.generate_production_id(
            "show", productions_dir=tmp_path, today=date(2030, 1, 2)
        )
        assert pid.startswith("prd_20300102_show_")

    def test_identity_collision_safe_allocation(self, tmp_path):
        first = identity.generate_production_id(
            "My Show", productions_dir=tmp_path, today=FIXED_DAY,
            rng=random.Random(2026),
        )
        (tmp_path / first).mkdir()
        second = identity.generate_production_id(
            "My Show", productions_dir=tmp_path, today=FIXED_DAY,
            rng=random.Random(2026),
        )
        assert second != first
        assert not identity.production_id_exists(second, tmp_path)

    def test_many_allocations_are_unique(self, tmp_path):
        seen = set()
        for seed in range(50):
            pid = identity.generate_production_id(
                "batch show", productions_dir=tmp_path, today=FIXED_DAY,
                rng=random.Random(seed),
            )
            assert pid not in seen
            seen.add(pid)
            (tmp_path / pid).mkdir()

    def test_identity_exhaustion_fails_loudly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "_SUFFIX_ATTEMPTS", 3)
        for suffix in range(3):
            (tmp_path / f"prd_20260831_show_{suffix:03d}").mkdir()
        with pytest.raises(RuntimeError, match="collision-safe"):
            identity.generate_production_id(
                "show", productions_dir=tmp_path, today=FIXED_DAY
            )

    def test_slugify_handles_unusable_titles(self):
        assert identity.slugify("!!!") == "production"
        assert identity.slugify("The Great Show (2026)") == "the_great_show_2026"
        assert identity.slugify("") == "production"


# ---------------------------------------------------------------------------
# 3. Single-video production
# ---------------------------------------------------------------------------


class TestSingleVideoProduction:
    def test_round_trip_persistence(self, tmp_path):
        production = make_production()
        save_new_production(production, root=tmp_path)
        loaded = load_production(production.production_id, root=tmp_path)
        assert loaded == production

    def test_layout_and_binding_dirs_created(self, tmp_path):
        production = make_production()
        save_new_production(production, root=tmp_path)
        base = production_dir(production.production_id, tmp_path)
        assert (base / "production.json").is_file()
        # No superseded versions exist yet (snapshots are written on revision).
        assert list_versions(production.production_id, root=tmp_path) == []
        plan_dir = unit_plan_dir(production.production_id, "unit-001", tmp_path)
        assert plan_dir.is_dir()
        # Phase 10 binding target stays EMPTY in Phase 9.
        assert list(plan_dir.iterdir()) == []

    def test_no_tmp_files_left_after_atomic_writes(self, tmp_path):
        production = make_production()
        save_new_production(production, root=tmp_path)
        leftovers = list(
            production_dir(production.production_id, tmp_path).rglob("*.tmp")
        )
        assert leftovers == []

    def test_save_refuses_overwrite(self, tmp_path):
        production = make_production()
        save_new_production(production, root=tmp_path)
        with pytest.raises(FileExistsError, match="never overwritten"):
            save_new_production(production, root=tmp_path)

    def test_new_production_must_start_at_plan_version_one(self, tmp_path):
        production = make_production().model_copy(update={"plan_version": 2})
        with pytest.raises(ValueError, match="plan_version == 1"):
            save_new_production(production, root=tmp_path)

    def test_listing_and_existence(self, tmp_path):
        production = make_production()
        assert list_productions(tmp_path) == []
        assert not production_exists(production.production_id, tmp_path)
        save_new_production(production, root=tmp_path)
        assert production_exists(production.production_id, tmp_path)
        assert list_productions(tmp_path) == [production.production_id]


# ---------------------------------------------------------------------------
# 4. Multi-video batch production
# ---------------------------------------------------------------------------


class TestMultiVideoBatchProduction:
    def test_batch_round_trip(self, tmp_path):
        units = [
            make_unit("item-001", 1, "batch_item"),
            make_unit("item-002", 2, "batch_item", dependencies=["item-001"]),
            make_unit("item-003", 3, "batch_item", dependencies=["item-002"]),
        ]
        production = make_production(pid="prd_20260831_batch_007", units=units)
        save_new_production(production, root=tmp_path)
        loaded = load_production(production.production_id, root=tmp_path)
        assert loaded == production
        assert [unit.ordinal for unit in loaded.units] == [1, 2, 3]

    def test_unknown_dependency_rejected(self):
        with pytest.raises(ValidationError, match="unknown units"):
            make_production(units=[make_unit("a", 1, dependencies=["ghost"])])

    def test_self_dependency_rejected(self):
        with pytest.raises(ValidationError, match="itself"):
            make_production(units=[make_unit("a", 1, dependencies=["a"])])

    def test_dependency_cycle_rejected(self):
        units = [
            make_unit("a", 1, dependencies=["b"]),
            make_unit("b", 2, dependencies=["a"]),
        ]
        with pytest.raises(ValidationError, match="cycle"):
            make_production(units=units)

    def test_open_unit_type_vocabulary_is_extensible(self):
        unit = make_unit("a", 1, "shorts_teaser")
        assert unit.unit_type == "shorts_teaser"


# ---------------------------------------------------------------------------
# 5. Episodic production with Production Bible
# ---------------------------------------------------------------------------


class TestEpisodicProductionWithBible:
    def _episodic(self) -> Production:
        bible = ProductionBible(
            elements=[
                BibleElement(
                    element_id="characters/kai",
                    section="characters",
                    content={"name": "Kai", "role": "host"},
                ),
                BibleElement(
                    element_id="world/reef",
                    section="world",
                    content={"name": "The Reef"},
                ),
            ]
        )
        units = [
            make_unit(
                "ep-001", 1, "episode",
                episode=EpisodeMetadata(
                    season_number=1, episode_number=1, episode_title="Pilot"
                ),
                bible_refs=["characters/kai", "world/reef"],
            ),
            make_unit(
                "ep-002", 2, "episode",
                episode=EpisodeMetadata(
                    season_number=1, episode_number=2,
                    episode_title="Currents", continuity_refs=["ep-001"],
                ),
                bible_refs=["characters/kai"],
                dependencies=["ep-001"],
            ),
        ]
        return make_production(
            pid="prd_20260831_reef_003",
            title="Reef Series",
            bible=bible,
            units=units,
            command=make_command(
                workload_shape="unit_count", intended_unit_count=2
            ),
        )

    def test_episodic_round_trip_preserves_bible_and_metadata(self, tmp_path):
        production = self._episodic()
        save_new_production(production, root=tmp_path)
        loaded = load_production(production.production_id, root=tmp_path)
        assert loaded == production
        assert loaded.bible is not None
        assert loaded.bible.elements[0].element_id == "characters/kai"
        assert loaded.units[1].episode.episode_number == 2
        assert loaded.units[1].episode.continuity_refs == ["ep-001"]

    def test_episode_metadata_requires_episode_unit_type(self):
        with pytest.raises(ValidationError, match="unit_type == 'episode'"):
            make_unit(
                "x", 1, "standalone_video",
                episode=EpisodeMetadata(episode_number=1),
            )

    def test_dangling_bible_reference_rejected(self):
        bible = ProductionBible(
            elements=[BibleElement(element_id="characters/kai", section="characters")]
        )
        with pytest.raises(ValidationError, match="unknown Bible elements"):
            make_production(
                bible=bible,
                units=[make_unit("a", 1, bible_refs=["characters/ghost"])],
            )

    def test_bible_reference_without_bible_rejected(self):
        with pytest.raises(ValidationError, match="no Production Bible"):
            make_production(units=[make_unit("a", 1, bible_refs=["characters/kai"])])

    def test_duplicate_bible_element_ids_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate Bible element_id"):
            ProductionBible(
                elements=[
                    BibleElement(element_id="characters/kai", section="characters"),
                    BibleElement(element_id="characters/kai", section="cast"),
                ]
            )



# ---------------------------------------------------------------------------
# 6. Provider intent declaration
# ---------------------------------------------------------------------------


class TestProviderIntentDeclaration:
    def test_all_neutral_provider_names_accepted(self):
        for name in PROVIDER_INTENT_NAMES:
            unit = make_unit("a", 1, provider_intent=name)
            assert unit.provider_intent == name

    def test_default_and_deferred_intent_accepted(self):
        assert make_unit("a", 1, provider_intent="default").provider_intent == "default"
        assert make_unit("a", 1).provider_intent is None

    def test_transport_shaped_or_unknown_provider_rejected(self):
        with pytest.raises(ValidationError, match="neutral provider name"):
            make_unit("a", 1, provider_intent="https://provider.example/api")

    def test_provider_intent_survives_round_trip(self, tmp_path):
        production = make_production(
            units=[make_unit("a", 1, provider_intent="external_video")]
        )
        save_new_production(production, root=tmp_path)
        loaded = load_production(production.production_id, root=tmp_path)
        assert loaded.units[0].provider_intent == "external_video"


# ---------------------------------------------------------------------------
# 7. Production command provenance
# ---------------------------------------------------------------------------


class TestProductionCommandProvenance:
    def test_command_requires_known_workload_shape(self):
        with pytest.raises(ValidationError, match="workload_shape"):
            make_command(workload_shape="whatever")

    def test_unit_count_shape_requires_intended_count(self):
        with pytest.raises(ValidationError, match="intended_unit_count"):
            make_command(workload_shape="unit_count")

    def test_open_ended_shape_needs_no_count(self):
        command = make_command(workload_shape="open_ended")
        assert command.intended_unit_count is None

    def test_command_provenance_round_trip(self, tmp_path):
        production = make_production()
        save_new_production(production, root=tmp_path)
        loaded = load_production(production.production_id, root=tmp_path)
        assert loaded.command.text == "Produce a short video about tides."
        assert loaded.command.issued_at == CREATED
        assert loaded.command.creation_context == {"source": "test-suite"}
        assert loaded.command.explicit_constraints["platform"] == "youtube"



# ---------------------------------------------------------------------------
# 8. Version increment behavior
# ---------------------------------------------------------------------------


def _revise(production: Production, **updates) -> Production:
    payload = {
        "plan_version": production.plan_version + 1,
        "revised_at": REVISED,
        "revision_notes": [*production.revision_notes, "revision applied"],
    }
    payload.update(updates)
    return production.model_copy(update=payload)


class TestVersionIncrementBehavior:
    def test_revisions_increment_monotonically(self, tmp_path):
        v1 = make_production()
        save_new_production(v1, root=tmp_path)
        v2 = _revise(v1)
        save_revision(v2, root=tmp_path)
        v3 = _revise(v2)
        save_revision(v3, root=tmp_path)
        assert load_production(v1.production_id, root=tmp_path).plan_version == 3
        assert list_versions(v1.production_id, root=tmp_path) == [1, 2]

    def test_version_skip_rejected(self, tmp_path):
        v1 = make_production()
        save_new_production(v1, root=tmp_path)
        skipped = v1.model_copy(update={"plan_version": 3})
        with pytest.raises(ValueError, match="monotonically"):
            save_revision(skipped, root=tmp_path)

    def test_version_reuse_rejected(self, tmp_path):
        v1 = make_production()
        save_new_production(v1, root=tmp_path)
        save_revision(_revise(v1), root=tmp_path)
        with pytest.raises(ValueError, match="monotonically"):
            save_revision(_revise(v1), root=tmp_path)  # plan_version 2 again

    def test_created_at_provenance_cannot_change(self, tmp_path):
        v1 = make_production()
        save_new_production(v1, root=tmp_path)
        forged = _revise(v1, created_at="2031-01-01T00:00:00+00:00")
        with pytest.raises(ValueError, match="created_at"):
            save_revision(forged, root=tmp_path)

    def test_unit_identities_cannot_be_removed(self, tmp_path):
        v1 = make_production(units=[make_unit("a", 1), make_unit("b", 2)])
        save_new_production(v1, root=tmp_path)
        v2 = _revise(v1, units=[make_unit("a", 1)])
        with pytest.raises(ValueError, match="stable across revisions"):
            save_revision(v2, root=tmp_path)

    def test_revision_may_add_units(self, tmp_path):
        v1 = make_production(units=[make_unit("a", 1)])
        save_new_production(v1, root=tmp_path)
        v2 = _revise(v1, units=[make_unit("a", 1), make_unit("b", 2)])
        save_revision(v2, root=tmp_path)
        loaded = load_production(v1.production_id, root=tmp_path)
        assert [unit.unit_id for unit in loaded.units] == ["a", "b"]
        assert unit_plan_dir(v1.production_id, "b", tmp_path).is_dir()

    def test_revision_requires_existing_production(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            save_revision(_revise(make_production()), root=tmp_path)


# ---------------------------------------------------------------------------
# 9. Immutable artifact snapshots
# ---------------------------------------------------------------------------


class TestImmutableSnapshots:
    def test_revision_snapshots_prior_current_intent(self, tmp_path):
        v1 = make_production()
        save_new_production(v1, root=tmp_path)
        v2 = _revise(v1, title="Tides Explained (Extended)")
        save_revision(v2, root=tmp_path)
        snapshot = load_version_snapshot(v1.production_id, 1, root=tmp_path)
        assert snapshot == v1
        assert load_production(v1.production_id, root=tmp_path) == v2

    def test_snapshot_bytes_unchanged_after_later_revisions(self, tmp_path):
        v1 = make_production()
        save_new_production(v1, root=tmp_path)
        save_revision(_revise(v1), root=tmp_path)  # v2 supersedes v1 -> v001
        snapshot_path = version_path(v1.production_id, 1, tmp_path)
        before = snapshot_path.read_bytes()
        v2 = load_production(v1.production_id, root=tmp_path)
        save_revision(_revise(v2), root=tmp_path)  # v3 supersedes v2 -> v002
        assert snapshot_path.read_bytes() == before
        assert list_versions(v1.production_id, root=tmp_path) == [1, 2]

    def test_existing_snapshot_is_never_overwritten(self, tmp_path):
        v1 = make_production()
        save_new_production(v1, root=tmp_path)
        v2 = _revise(v1)
        save_revision(v2, root=tmp_path)
        # Simulate a pre-existing v002 snapshot (foreign/manual artifact).
        foreign = version_path(v1.production_id, 2, tmp_path)
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("{}", encoding="utf-8")
        v3 = _revise(v2)
        with pytest.raises(FileExistsError, match="immutable snapshot"):
            save_revision(v3, root=tmp_path)
        assert foreign.read_text(encoding="utf-8") == "{}"

    def test_tampered_snapshot_fails_loudly_on_load(self, tmp_path):
        v1 = make_production()
        save_new_production(v1, root=tmp_path)
        save_revision(_revise(v1), root=tmp_path)  # creates the v001 snapshot
        snapshot_path = version_path(v1.production_id, 1, tmp_path)
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        payload["plan_version"] = 99
        snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="snapshot integrity"):
            load_version_snapshot(v1.production_id, 1, root=tmp_path)

    def test_snapshot_written_only_when_version_is_superseded(self, tmp_path):
        v1 = make_production()
        save_new_production(v1, root=tmp_path)
        assert list_versions(v1.production_id, root=tmp_path) == []
        save_revision(_revise(v1), root=tmp_path)
        assert list_versions(v1.production_id, root=tmp_path) == [1]
        assert load_version_snapshot(v1.production_id, 1, root=tmp_path) == v1


# ---------------------------------------------------------------------------
# 10. Malformed artifact failure
# ---------------------------------------------------------------------------


def _write_current(pid: str, root: Path, text: str) -> None:
    path = current_path(pid, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestMalformedArtifactFailure:
    def test_missing_artifact_fails_loudly(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="not found"):
            load_production("prd_20260831_ghost_001", root=tmp_path)

    def test_unparseable_json_fails_loudly(self, tmp_path):
        pid = "prd_20260831_broken_001"
        _write_current(pid, tmp_path, "{ not json :::")
        with pytest.raises(ValueError, match="malformed production artifact"):
            load_production(pid, root=tmp_path)

    def test_structurally_wrong_payload_fails_loudly(self, tmp_path):
        pid = "prd_20260831_wrong_001"
        _write_current(
            pid, tmp_path,
            json.dumps({"schema": "avm.production", "unexpected": True}),
        )
        with pytest.raises(ValueError, match="invalid production artifact"):
            load_production(pid, root=tmp_path)

    def test_artifacts_are_never_silently_repaired(self, tmp_path):
        pid = "prd_20260831_broken_002"
        _write_current(pid, tmp_path, "{ not json :::")
        with pytest.raises(ValueError):
            load_production(pid, root=tmp_path)
        assert current_path(pid, tmp_path).read_text(encoding="utf-8") == "{ not json :::"


# ---------------------------------------------------------------------------
# 11. Schema version rejection
# ---------------------------------------------------------------------------


class TestSchemaVersionRejection:
    def test_unsupported_schema_version_flagged(self):
        production = make_production().model_copy(update={"schema_version": 99})
        issues = validate_production(production)
        assert any("unsupported schema_version 99" in issue for issue in issues)

    def test_unknown_artifact_family_flagged(self):
        production = make_production().model_copy(
            update={"schema_name": "avm.execution"}
        )
        issues = validate_production(production)
        assert any("unknown artifact family" in issue for issue in issues)

    def test_load_rejects_unsupported_schema_version(self, tmp_path):
        v1 = make_production()
        save_new_production(v1, root=tmp_path)
        path = current_path(v1.production_id, tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = 99
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="unsupported schema_version"):
            load_production(v1.production_id, root=tmp_path)


# ---------------------------------------------------------------------------
# 12. Execution state exclusion
# ---------------------------------------------------------------------------


class TestExecutionStateExclusion:
    def test_scan_detects_execution_keys_recursively(self):
        payload = {
            "units": [
                {"content_intent": {"checkpoint": {"scene": 2},
                                    "nested": [{"retry_count": 3}]}}
            ]
        }
        issues = scan_for_forbidden_keys(payload)
        assert any("checkpoint" in issue for issue in issues)
        assert any("retry_count" in issue for issue in issues)

    @pytest.mark.parametrize(
        "key",
        ["job_id", "polling_url", "actual_cost", "generated_path",
         "browser_state", "fallback_policy", "execution_status"],
    )
    def test_each_execution_state_key_detected(self, key):
        assert scan_for_forbidden_keys({key: "x"}) != []

    def test_clean_payload_passes(self):
        payload = {"content_intent": {"tone": "calm", "tags": ["a", "b"]}}
        assert scan_for_forbidden_keys(payload) == []

    def test_contaminated_intent_payload_rejected_on_load(self, tmp_path):
        v1 = make_production()
        save_new_production(v1, root=tmp_path)
        path = current_path(v1.production_id, tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["units"][0]["content_intent"]["checkpoint"] = {"scene": 4}
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="checkpoint"):
            load_production(v1.production_id, root=tmp_path)

    def test_validate_flags_contaminated_model(self):
        unit = make_unit("a", 1, content_intent={"remote_job_id": "abc"})
        production = make_production(units=[unit])
        issues = validate_production(production)
        assert any("remote_job_id" in issue for issue in issues)


# ---------------------------------------------------------------------------
# 13. Secret exclusion
# ---------------------------------------------------------------------------


class TestSecretExclusion:
    @pytest.mark.parametrize(
        "key", ["api_key", "credentials", "authorization", "client_secret"]
    )
    def test_each_secret_key_detected(self, key):
        assert scan_for_forbidden_keys({key: "x"}) != []

    def test_legitimate_creative_text_values_pass(self):
        payload = {
            "title": "The Secret Garden",
            "premise": "A bearer of bad news learns the password is 'friend'",
            "content_intent": {"note": "in-story authorization, granted"},
        }
        assert scan_for_forbidden_keys(payload) == []

    def test_secret_shaped_key_in_command_context_rejected(self):
        command = make_command(
            creation_context={"source": "cli", "api_key": "sk-123"}
        )
        production = make_production(command=command)
        issues = validate_production(production)
        assert any("api_key" in issue for issue in issues)

    def test_secret_contamination_rejected_on_load(self, tmp_path):
        v1 = make_production()
        save_new_production(v1, root=tmp_path)
        path = current_path(v1.production_id, tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["units"][0]["content_intent"]["credentials"] = {"token": "x"}
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="credentials"):
            load_production(v1.production_id, root=tmp_path)


# ---------------------------------------------------------------------------
# 14. Plan-reference integrity (incl. tampered reference detection)
# ---------------------------------------------------------------------------


def _production_with_resolved_reference(root: Path):
    """Build a production whose unit references a real on-disk plan artifact."""
    pid = "prd_20260831_refs_001"
    plan_rel = "units/unit-001/plan/plan.json"
    plan_abs = production_dir(pid, root) / plan_rel
    plan_abs.parent.mkdir(parents=True, exist_ok=True)
    plan_abs.write_text('{"plan": true}', encoding="utf-8")
    reference = PlanReference(
        status="resolved",
        relative_location=plan_rel,
        referenced_plan_identity="plan-tides-001",
        referenced_plan_version=1,
        content_hash=compute_content_hash(plan_abs),
    )
    production = make_production(
        pid=pid, units=[make_unit("unit-001", 1, plan_reference=reference)]
    )
    return production, plan_abs


class TestPlanReferenceIntegrity:
    def test_unresolved_reference_carries_no_binding_data(self):
        reference = PlanReference(status="unresolved")
        assert reference.relative_location is None
        with pytest.raises(ValidationError, match="must not carry"):
            PlanReference(
                status="unresolved",
                relative_location="units/a/plan/plan.json",
            )

    def test_resolved_reference_requires_location_and_hash(self):
        with pytest.raises(ValidationError, match="resolved plan reference requires"):
            PlanReference(status="resolved")

    def test_reference_location_must_be_relative(self):
        with pytest.raises(ValidationError, match="relative"):
            PlanReference(
                status="resolved",
                relative_location="../escape/plan.json",
                content_hash="abc",
            )
        with pytest.raises(ValidationError, match="relative"):
            PlanReference(
                status="resolved",
                relative_location="/abs/plan.json",
                content_hash="abc",
            )
        with pytest.raises(ValidationError, match="relative"):
            PlanReference(
                status="resolved",
                relative_location="C:\\abs\\plan.json",
                content_hash="abc",
            )

    def test_resolved_reference_hash_verified_on_load(self, tmp_path):
        production, _ = _production_with_resolved_reference(tmp_path)
        save_new_production(production, root=tmp_path)
        loaded = load_production(production.production_id, root=tmp_path)
        assert loaded == production

    def test_tampered_referenced_artifact_detected(self, tmp_path):
        production, plan_abs = _production_with_resolved_reference(tmp_path)
        save_new_production(production, root=tmp_path)
        plan_abs.write_text('{"plan": "TAMPERED"}', encoding="utf-8")
        issues = validate_production(
            production,
            production_dir=production_dir(production.production_id, tmp_path),
        )
        assert any("content hash mismatch" in issue for issue in issues)
        with pytest.raises(ValueError, match="DATA VALIDATION problem"):
            load_production(production.production_id, root=tmp_path)

    def test_missing_referenced_artifact_detected(self, tmp_path):
        reference = PlanReference(
            status="resolved",
            relative_location="units/unit-001/plan/plan.json",
            referenced_plan_identity="plan-ghost",
            referenced_plan_version=1,
            content_hash="0" * 64,
        )
        production = make_production(
            units=[make_unit("unit-001", 1, plan_reference=reference)]
        )
        issues = validate_production(
            production,
            production_dir=production_dir(production.production_id, tmp_path),
        )
        assert any("referenced plan artifact not found" in issue for issue in issues)



# ---------------------------------------------------------------------------
# 15. Relocation
# ---------------------------------------------------------------------------


class TestRelocation:
    def test_production_directory_is_relocatable(self, tmp_path):
        root_a = tmp_path / "root_a"
        root_b = tmp_path / "elsewhere" / "root_b"  # different absolute path
        production = make_production()
        save_new_production(production, root=root_a)
        original_bytes = current_path(production.production_id, root_a).read_bytes()

        shutil.copytree(
            production_dir(production.production_id, root_a),
            production_dir(production.production_id, root_b),
        )
        relocated = load_production(production.production_id, root=root_b)
        assert relocated == production
        assert relocated.production_id == production.production_id
        assert (
            current_path(production.production_id, root_b).read_bytes()
            == original_bytes
        )

    def test_identity_does_not_depend_on_absolute_path(self, tmp_path):
        root_a = tmp_path / "one"
        root_b = tmp_path / "two"
        production = make_production()
        save_new_production(production, root=root_a)
        save_new_production(production, root=root_b)
        assert (
            load_production(production.production_id, root=root_a)
            == load_production(production.production_id, root=root_b)
        )


# ---------------------------------------------------------------------------
# 16. Serialization contract & storage configuration
# ---------------------------------------------------------------------------


class TestSerializationContract:
    def test_artifact_uses_schema_discriminator_key(self, tmp_path):
        production = make_production()
        save_new_production(production, root=tmp_path)
        raw = json.loads(
            current_path(production.production_id, tmp_path).read_text("utf-8")
        )
        assert raw["schema"] == "avm.production"
        assert raw["schema_version"] == 1
        assert raw["plan_version"] == 1
        assert raw["production_id"] == production.production_id

    def test_artifact_is_human_readable_json(self, tmp_path):
        production = make_production()
        save_new_production(production, root=tmp_path)
        text = current_path(production.production_id, tmp_path).read_text("utf-8")
        assert "\n" in text
        assert '  "production_id"' in text

    def test_default_root_is_the_configured_intent_area(self):
        from app.config import PRODUCTIONS_DIR

        assert productions_root() == Path(PRODUCTIONS_DIR)

    def test_config_registers_productions_dir(self):
        from app.config import PRODUCTIONS_DIR, REQUIRED_DIRS

        assert PRODUCTIONS_DIR in REQUIRED_DIRS
        assert PRODUCTIONS_DIR.name == "productions"

