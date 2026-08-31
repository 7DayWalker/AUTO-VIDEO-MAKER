"""
AUTO Video Maker - Phase 9: Durable Production Plan (INTENT layer).

Public surface of the production intent package:

- ``models``      — the durable intent artifact schema (Production and its
                    components). Intent ONLY: no execution state, no transport,
                    no credentials, no provider invocation.
- ``identity``    — collision-safe ``prd_YYYYMMDD_<slug>_<###>`` allocation.
- ``validation``  — deliberate, evidence-based artifact validation
                    (schema compatibility, intent/execution separation,
                    secret safety, reference integrity). Fails loudly.
- ``storage``     — atomic, versioned, immutable-snapshot persistence under
                    ``productions/`` (fully separate from execution state).

Frozen authority: PHASE_9_DURABLE_PRODUCTION_PLAN.md.
"""

from app.production.models import (
    BIBLE_CANONICAL_SECTIONS,
    PROVIDER_INTENT_DEFAULT,
    PROVIDER_INTENT_NAMES,
    RECOMMENDED_UNIT_TYPES,
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
from app.production.identity import (
    generate_production_id,
    production_id_exists,
    slugify,
)
from app.production.validation import (
    EXECUTION_STATE_KEYS,
    SECRET_STATE_KEYS,
    assert_valid_production,
    scan_for_forbidden_keys,
    validate_production,
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

__all__ = [
    # models
    "SCHEMA_NAME",
    "SUPPORTED_SCHEMA_VERSION",
    "PROVIDER_INTENT_NAMES",
    "PROVIDER_INTENT_DEFAULT",
    "BIBLE_CANONICAL_SECTIONS",
    "RECOMMENDED_UNIT_TYPES",
    "Production",
    "ProductionCommand",
    "ProductionBible",
    "BibleElement",
    "ProductionUnit",
    "EpisodeMetadata",
    "PlanReference",
    # identity
    "generate_production_id",
    "production_id_exists",
    "slugify",
    # validation
    "EXECUTION_STATE_KEYS",
    "SECRET_STATE_KEYS",
    "validate_production",
    "assert_valid_production",
    "scan_for_forbidden_keys",
    # storage
    "productions_root",
    "production_dir",
    "current_path",
    "version_path",
    "unit_plan_dir",
    "production_exists",
    "list_productions",
    "list_versions",
    "save_new_production",
    "save_revision",
    "load_production",
    "load_version_snapshot",
    "compute_content_hash",
]
