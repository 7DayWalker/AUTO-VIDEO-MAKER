"""
AUTO Video Maker - Phase 9: Production intent validation.

Deliberate, evidence-based validation of durable Production intent artifacts:

- schema/structure compatibility (``schema_version`` supported check);
- intent/EXECUTION separation: execution-state-shaped keys are rejected
  anywhere in the artifact (checkpoints, job/polling state, actual costs,
  generated artifact state, retry/fallback state, browser state, ...);
- secret-safety: secret-shaped keys are rejected anywhere in the artifact
  (Phase 9 spec §28; key-name based to avoid overreaching into legitimate
  creative text);
- referential integrity: Bible references, unit dependencies (incl. cycle
  detection via the model), plan-reference resolution and content hashes.

Malformed or contaminated artifacts FAIL LOUDLY — never silently repaired.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, List, Optional

from app.production.models import (
    SUPPORTED_SCHEMA_VERSION,
    Production,
)

# Execution-state-shaped key names (case-insensitive match, underscore/hyphen
# normalized). These represent EXECUTION concepts and must never appear as
# payload keys inside a durable intent artifact (PHASE_9 spec §9/§14).
EXECUTION_STATE_KEYS = frozenset(
    {
        "checkpoint",
        "checkpoints",
        "scene_checkpoint",
        "scene_checkpoints",
        "stage_result",
        "stage_results",
        "stage_status",
        "job_id",
        "jobid",
        "polling_url",
        "poll_url",
        "remote_job",
        "remote_job_id",
        "remote_status",
        "remote_state",
        "generation_status",
        "execution_status",
        "execution_progress",
        "execution_state",
        "retry_count",
        "retry_state",
        "retry_policy",
        "fallback",
        "fallback_state",
        "fallback_policy",
        "provider_result",
        "generation_result",
        "actual_cost",
        "actual_usage",
        "usage",
        "credits_used",
        "generated_path",
        "generated_file",
        "artifact_state",
        "browser_state",
        "browser_session",
        "session_state",
        "resume_state",
        "publication_state",
        "published_at",
    }
)

# Secret-shaped key names (Phase 9 spec §28). Key-name based so legitimate
# creative text (a character literally named "Secret") is never rejected.
SECRET_STATE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "auth_token",
        "bearer",
        "bearer_token",
        "password",
        "passwd",
        "secret",
        "secret_key",
        "credential",
        "credentials",
        "authorization",
        "auth_header",
        "cookie",
        "cookies",
        "private_key",
        "client_secret",
    }
)


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def scan_for_forbidden_keys(payload: Any, base_path: str = "") -> List[str]:
    """Recursively find execution-state / secret-shaped keys in a payload.

    Returns a list of human-readable issue strings (empty = clean). Only KEY
    NAMES are inspected — ordinary string values (creative text) are never
    rejected for containing ordinary words.
    """
    issues: List[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = _normalize_key(str(key))
            location = f"{base_path}.{key}" if base_path else str(key)
            if normalized in EXECUTION_STATE_KEYS:
                issues.append(
                    f"execution-state key '{location}' is forbidden in a "
                    "durable intent artifact"
                )
            elif normalized in SECRET_STATE_KEYS:
                issues.append(
                    f"secret-shaped key '{location}' is forbidden in a "
                    "durable intent artifact"
                )
            issues.extend(scan_for_forbidden_keys(value, location))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            issues.extend(scan_for_forbidden_keys(value, f"{base_path}[{index}]"))
    return issues


def check_schema_version(production: Production) -> List[str]:
    """Loudly reject unsupported schema versions (structure compatibility)."""
    issues: List[str] = []
    if production.schema_name != "avm.production":
        issues.append(
            f"unknown artifact family '{production.schema_name}' "
            "(expected 'avm.production')"
        )
    if production.schema_version != SUPPORTED_SCHEMA_VERSION:
        issues.append(
            f"unsupported schema_version {production.schema_version} "
            f"(supported: {SUPPORTED_SCHEMA_VERSION})"
        )
    return issues


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def check_plan_reference_hashes(
    production: Production, production_dir: Path
) -> List[str]:
    """Verify resolved plan references against the referenced artifact bytes."""
    issues: List[str] = []
    root = Path(production_dir)
    for unit in production.units:
        reference = unit.plan_reference
        if reference is None or reference.status != "resolved":
            continue
        plan_file = root / reference.relative_location
        if not plan_file.is_file():
            issues.append(
                f"unit '{unit.unit_id}': referenced plan artifact not found: "
                f"{reference.relative_location}"
            )
            continue
        actual = _sha256_file(plan_file)
        if actual != reference.content_hash:
            issues.append(
                f"unit '{unit.unit_id}': plan reference content hash mismatch "
                f"(expected {reference.content_hash}, actual {actual}); "
                "this is a DATA VALIDATION problem"
            )
    return issues


def validate_production(
    production: Production,
    *,
    production_dir: Optional[Path] = None,
) -> List[str]:
    """Validate one Production intent artifact. Returns issues (empty = valid).

    Structural rules (uniqueness, ordering, references, cycles, provider-name
    validity, episode-metadata consistency) are enforced by the model's own
    validators; this function adds the artifact-level guarantees:

    - supported ``schema_version`` / artifact family;
    - intent/execution separation (recursive exclusion scan);
    - secret safety (recursive exclusion scan);
    - resolved plan-reference hash integrity (when ``production_dir`` is given).
    """
    issues: List[str] = []
    issues.extend(check_schema_version(production))
    payload = production.model_dump(by_alias=True)
    issues.extend(scan_for_forbidden_keys(payload))
    if production_dir is not None:
        issues.extend(check_plan_reference_hashes(production, production_dir))
    return issues


def assert_valid_production(
    production: Production,
    *,
    production_dir: Optional[Path] = None,
) -> None:
    """Raise ``ValueError`` with all issues when the artifact is not valid."""
    issues = validate_production(production, production_dir=production_dir)
    if issues:
        raise ValueError(
            "Invalid production intent artifact ("
            f"{len(issues)} issue(s)): " + "; ".join(issues)
        )

