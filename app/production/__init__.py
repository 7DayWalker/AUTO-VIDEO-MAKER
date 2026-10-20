"""
AUTO Video Maker - Phase 9: Durable Production Plan (INTENT layer).

Public surface of the production intent package:

- ``models``      — the durable intent artifact schema (Production and its
                    components). Intent ONLY: no execution state, no transport,
                    no credentials, no provider invocation.
- ``routes``      — the first-class PRODUCTION ROUTE registry (Batch 1D):
                    product-level route identities, requirements/defaults,
                    and the centralized legacy ``generation_strategy``
                    compatibility mapping. Route is intent: never a provider,
                    model, media strategy, audio strategy, engine, assembly,
                    quality, or distribution concept.
- ``identity``    — collision-safe ``prd_YYYYMMDD_<slug>_<###>`` allocation.
- ``validation``  — deliberate, evidence-based artifact validation
                    (schema compatibility, intent/execution separation,
                    secret safety, reference integrity). Fails loudly.
- ``storage``     — atomic, versioned, immutable-snapshot persistence under
                    ``productions/`` (fully separate from execution state).

Frozen authority: PHASE_9_DURABLE_PRODUCTION_PLAN.md.
"""

from app.production.models import (
    AUDIO_INTENTS,
    BIBLE_CANONICAL_SECTIONS,
    DEFAULT_AUDIO_INTENT,
    PROVIDER_INTENT_DEFAULT,
    PROVIDER_INTENT_NAMES,
    PRODUCTION_ROUTES,
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
    normalize_audio_intent,
)
from app.production.routes import (
    DEFAULT_PRODUCTION_ROUTE,
    ROUTE_REGISTRY,
    EffectiveRoute,
    LegacyStrategyRouteMapping,
    resolve_effective_route,
    route_id_for_legacy_strategy,
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
    "PRODUCTION_ROUTES",
    # V1 audio intent (production-level audio decision; never Scene.narration)
    "AUDIO_INTENTS",
    "DEFAULT_AUDIO_INTENT",
    "normalize_audio_intent",
    "Production",
    "ProductionCommand",
    "ProductionBible",
    "BibleElement",
    "ProductionUnit",
    "EpisodeMetadata",
    "PlanReference",
    # routes (Batch 1D: first-class Production Route)
    "DEFAULT_PRODUCTION_ROUTE",
    "ROUTE_REGISTRY",
    "EffectiveRoute",
    "LegacyStrategyRouteMapping",
    "resolve_effective_route",
    "route_id_for_legacy_strategy",
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
