"""
AUTO Video Maker - Content Generator

Generates production plans from content briefs using LLM providers.
"""

import json
import re
import time
import logging
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.content.models import (
    ContentBrief,
    ProductionPlan,
    ContentGenerationResult,
    generate_project_id,
)
from app.content.execution_evidence import normalize_llm_verification
from app.content.providers.base import BaseLLMProvider
from app.content.prompts.scene_planner import get_combined_generation_prompt
from app.content.validator import validate_production_plan
from app.content.config import (
    DURATION_TOLERANCE,
    MAX_RETRY_ATTEMPTS,
    MAX_SCENE_DURATION,
    MIN_SCENE_DURATION,
    RETRY_DELAY,
)
from app.content.exceptions import (
    LLMProviderError,
    LLMAuthenticationError,
    LLMQuotaError,
    LLMResponseError,
    RetryExhaustedError,
)

logger = logging.getLogger(__name__)


def _normalized_allowed_durations(
    allowed_durations: Optional[Sequence[int]],
) -> Optional[Tuple[int, ...]]:
    """Normalize operator/derived allowed durations; None = unconstrained."""
    if not allowed_durations:
        return None
    try:
        cleaned = tuple(
            sorted({int(d) for d in allowed_durations if int(d) > 0})
        )
    except (TypeError, ValueError):
        return None
    return cleaned or None


def _project_onto_allowed_durations(
    scene_dicts: List[dict],
    allowed: Tuple[int, ...],
    warnings: List[str],
) -> bool:
    """Batch 1E: deterministically project scene durations onto the EXACT
    allowed duration set (e.g. {5, 10} seconds for native motion video).

    - Candidate totals are only totals achievable with EXACTLY
      ``len(scenes)`` values from the allowed set; the candidate closest to
      the preserved total wins (ties -> smaller total).
    - The projected total must stay within the plan duration tolerance of
      the original total; otherwise NOTHING is projected and a truthful
      warning is recorded (the schema path then runs and the execution
      adapter remains the final authority — no duration is ever invented).
    - Per-scene assignment preserves relative pacing: scenes are processed
      longest-first and receive the feasible allowed value closest to their
      original duration (ties -> larger value).
    - Idempotent: durations already on the allowed set produce no changes.

    Returns True when the projection was applied (or was already a no-op);
    False when projection was impossible/not applicable (warnings carry the
    deterministic signal).
    """
    durations: List[Optional[float]] = []
    for scene in scene_dicts:
        value = scene.get("estimated_duration")
        if isinstance(value, bool) or value is None:
            durations.append(None)
            continue
        try:
            durations.append(float(value))
        except (TypeError, ValueError):
            durations.append(None)
    if not durations or any(d is None for d in durations):
        return False

    count = len(durations)
    values = [float(d) for d in durations]
    total = sum(values)

    # Feasible totals: sums of EXACTLY `count` values from the allowed set.
    reachable: List[Set[int]] = [set() for _ in range(count + 1)]
    reachable[0].add(0)
    for k in range(1, count + 1):
        reachable[k] = {
            previous + a for previous in reachable[k - 1] for a in allowed
        }
    candidates = reachable[count]
    if not candidates:
        return False
    best_total = min(candidates, key=lambda t: (abs(t - total), t))
    if abs(best_total - total) > DURATION_TOLERANCE + 1e-6:
        warnings.append(
            "Execution capability constraint: a target total of "
            f"{total:g}s cannot be represented within tolerance using "
            f"{count} scene(s) at the allowed durations "
            f"{sorted(allowed)}s; scene durations were left on the schema "
            "contract and the execution adapter remains the final authority."
        )
        return False

    # Deterministic pacing-preserving assignment (longest scenes first).
    order = sorted(range(count), key=lambda i: values[i], reverse=True)
    assignment = [0.0] * count
    remaining = int(round(best_total))
    for position, index in enumerate(order):
        scenes_left = count - position
        feasible = [
            a
            for a in sorted(allowed, reverse=True)
            if (remaining - a) in reachable[scenes_left - 1]
        ]
        # Reachability of `best_total` guarantees a feasible value here.
        chosen = min(feasible, key=lambda a: (abs(a - values[index]), -a))
        assignment[index] = float(chosen)
        remaining -= chosen

    changed = [
        (
            scene_dicts[i].get("id") or f"scene #{i + 1}",
            values[i],
            assignment[i],
        )
        for i in range(count)
        if abs(assignment[i] - values[i]) > 0.05
    ]
    if changed:
        for i, value in enumerate(assignment):
            scene_dicts[i]["estimated_duration"] = value
        described = ", ".join(
            f"{sid}: {old:g}s -> {new:g}s" for sid, old, new in changed
        )
        total_note = (
            f"{total:g}s -> {best_total:g}s"
            if abs(best_total - total) > 0.05
            else f"preserved at {total:g}s"
        )
        warnings.append(
            "Scene durations projected onto the execution capability "
            f"constraint (allowed durations {sorted(allowed)}s; total "
            f"{total_note}) ({described})."
        )
        logger.warning(
            "Scene duration allowed-set projection applied: %s", described
        )
    return True


def normalize_scene_durations(
    scene_dicts: List[dict],
    allowed_durations: Optional[Sequence[int]] = None,
) -> Tuple[List[dict], List[str]]:
    """Bring scene estimated_duration values inside the schema bounds.

    Phase 11C Batch 1 hardening (Finding 2): the planner contract and the
    ``ProductionPlan`` schema must agree. ``Scene.estimated_duration`` is
    schema-constrained to ``[MIN_SCENE_DURATION, MAX_SCENE_DURATION]``; free
    models sometimes emit 12-20s scenes for longer targets, which used to
    reject an otherwise valid plan on every retry.

    This is duration-PRESERVING redistribution, NOT blind clamping:

    - over-long scenes surrender their excess proportionally to the other
      scenes (under-short scenes draw theirs up the same way), so the plan's
      TOTAL duration — the production's duration contract with the brief —
      and each scene's relative pacing weight are preserved;
    - it runs ONLY when at least one scene is outside the bounds;
    - when the total duration cannot fit within the bounds for this scene
      count at all, it is a NO-OP: the schema/validator then fail truthfully
      with an actionable message (the plan is never distorted);
    - missing/non-numeric durations are left untouched (truthful schema
      failure downstream).

    Every applied change is returned as a warning so it is recorded in the
    generation result and the durable plan metadata — never silent.

    Batch 1E: when ``allowed_durations`` is provided (generic capability
    facts for the resolved video model, e.g. {5, 10} seconds for a native
    motion-video route), durations are FIRST projected deterministically
    onto that exact set (``_project_onto_allowed_durations``).  When exact
    representation is impossible the truthful warning is recorded, the
    schema-bounds path below still runs, and the execution adapter remains
    the final authority.  ``allowed_durations=None`` preserves the legacy
    behavior exactly.
    """
    warnings: List[str] = []
    allowed = _normalized_allowed_durations(allowed_durations)
    if allowed is not None:
        if _project_onto_allowed_durations(scene_dicts, allowed, warnings):
            return scene_dicts, warnings
        # Exact representation under the allowed set was impossible (or the
        # input was not normalizable): fall through to the schema-bounds
        # path; the truthful infeasibility warning is already recorded.
    durations: List[Optional[float]] = []
    for scene in scene_dicts:
        value = scene.get("estimated_duration")
        if isinstance(value, bool) or value is None:
            durations.append(None)
            continue
        try:
            durations.append(float(value))
        except (TypeError, ValueError):
            durations.append(None)

    if not durations or any(d is None for d in durations):
        return scene_dicts, warnings

    lo = float(MIN_SCENE_DURATION)
    hi = float(MAX_SCENE_DURATION)
    if all(lo <= d <= hi for d in durations):
        return scene_dicts, warnings

    total = sum(durations)
    count = len(durations)
    if total > count * hi + 1e-6 or total < count * lo - 1e-6:
        # Infeasible for this scene count: never distort the plan; the
        # schema and validator report the truthful failure instead.
        return scene_dicts, warnings

    values = list(durations)
    for _ in range(count * 4 + 4):
        over = [i for i, d in enumerate(values) if d > hi + 1e-9]
        under = [i for i, d in enumerate(values) if d < lo - 1e-9]
        if not over and not under:
            break
        if over:
            excess = sum(values[i] - hi for i in over)
            for i in over:
                values[i] = hi
            free = [i for i, d in enumerate(values) if d < hi - 1e-9]
            if free and excess > 1e-9:
                base = sum(values[i] for i in free)
                if base > 1e-9:
                    for i in free:
                        values[i] += excess * values[i] / base
                else:
                    share = excess / len(free)
                    for i in free:
                        values[i] += share
        if under:
            deficit = sum(lo - values[i] for i in under)
            for i in under:
                values[i] = lo
            donors = [i for i, d in enumerate(values) if d > lo + 1e-9]
            if donors and deficit > 1e-9:
                base = sum(values[i] - lo for i in donors)
                if base > 1e-9:
                    for i in donors:
                        values[i] -= deficit * (values[i] - lo) / base

    # Round to 0.1s and distribute the rounding residual within bounds so
    # the preserved total survives rounding.
    values = [round(v, 1) for v in values]
    residual = round(total - sum(values), 1)
    if abs(residual) >= 0.1:
        order = sorted(
            range(count), key=lambda i: values[i], reverse=(residual > 0)
        )
        for i in order:
            if abs(residual) < 0.05:
                break
            if residual > 0:
                add = min(residual, round(hi - values[i], 1))
            else:
                add = -min(-residual, round(values[i] - lo, 1))
            if add:
                values[i] = round(values[i] + add, 1)
                residual = round(residual - add, 1)

    changed = [
        (
            scene_dicts[i].get("id") or f"scene #{i + 1}",
            durations[i],
            values[i],
        )
        for i in range(count)
        if abs(values[i] - durations[i]) > 0.05
    ]
    if changed:
        for scene, value in zip(scene_dicts, values):
            scene["estimated_duration"] = value
        described = ", ".join(
            f"{sid}: {old:g}s -> {new:g}s" for sid, old, new in changed
        )
        warnings.append(
            "Scene durations normalized to the "
            f"{lo:g}-{hi:g}s schema contract with the total duration "
            f"preserved at {total:g}s ({described})."
        )
        logger.warning("Scene duration normalization applied: %s", described)
    return scene_dicts, warnings


def _brief_allowed_durations(
    brief: ContentBrief,
) -> Optional[Sequence[int]]:
    """Extract generic allowed scene durations from the brief's Batch 1E
    planning constraints (None = unconstrained)."""
    constraints = getattr(brief, "planning_constraints", None)
    if constraints is None:
        return None
    return constraints.allowed_scene_durations or None


def _retry_feedback(
    error_msg: str,
    allowed_durations: Optional[Sequence[int]] = None,
) -> str:
    """Render the truthful rejection feedback appended to a retry prompt."""
    feedback = (
        "\n\nYOUR PREVIOUS OUTPUT WAS REJECTED BY STRICT SCHEMA VALIDATION:\n"
        f"{error_msg}\n"
        "Fix EXACTLY these problems and output the corrected COMPLETE JSON. "
        "Remember: every scene estimated_duration must stay between "
        f"{MIN_SCENE_DURATION:g} and {MAX_SCENE_DURATION:g} seconds "
        "(inclusive), and the total target duration is reached through "
        "MULTIPLE scenes — add scenes when the requested count cannot cover "
        "the target duration within that limit."
    )
    allowed = _normalized_allowed_durations(allowed_durations)
    if allowed:
        # Batch 1E: generic execution capability constraint (never a
        # provider/model identity).
        feedback += (
            "\nAlso remember the EXECUTION CAPABILITY CONSTRAINT: every "
            "scene estimated_duration must use one of: "
            + ", ".join(f"{d:g}" for d in allowed)
            + " seconds."
        )
    return feedback


class ContentGenerator:
    """
    Generates production plans from content briefs.
    
    Uses an LLM provider to generate structured content.
    """
    
    def __init__(
        self,
        provider: BaseLLMProvider,
        max_retries: int = MAX_RETRY_ATTEMPTS,
        retry_delay: float = RETRY_DELAY,
        canonical_model_id: Optional[str] = None,
        resolution_trace: Sequence[Dict[str, Any]] = (),
    ):
        """
        Args:
            provider: LLM provider instance to use.
            max_retries: Maximum retry attempts.
            retry_delay: Delay between retries in seconds.
            canonical_model_id: Step 6 (additive) — the user-facing CANONICAL
                model identity from the model registry (e.g.
                ``qwen-3.8-flash``) when this generator was built from a
                resolved model. None = UNKNOWN (legacy provider-first path,
                mock, local); it is NEVER inferred from ``provider.model``,
                which remains the provider wire model.
            resolution_trace: Step 6 (additive) — the resolver's failed-attempt
                evidence for intra-model PROVIDER fallback. Empty for a
                single-binding resolution (no fallback occurred).
        """
        self.provider = provider
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.canonical_model_id = (
            canonical_model_id.strip()
            if isinstance(canonical_model_id, str) and canonical_model_id.strip()
            else None
        )
        self.resolution_trace: Tuple[Dict[str, Any], ...] = tuple(
            resolution_trace or ()
        )
    
    def _execution_evidence(self, response) -> Dict[str, Any]:
        """Step 6: raw AI Brain execution evidence for one provider response.

        Combines the provider-reported facts (normalized onto the established
        ``verification`` vocabulary) with the resolver's resolution trace, so
        the persistence seam can project ONE authoritative
        provenance/accounting pair from ONE evidence object.

        Never includes raw response bodies, prompts or credentials.
        """
        evidence = normalize_llm_verification(response)
        if self.resolution_trace:
            evidence["resolution_trace"] = [
                dict(entry) for entry in self.resolution_trace
            ]
        return evidence

    def generate(self, brief: ContentBrief) -> ContentGenerationResult:
        """
        Generate a production plan from a content brief.
        
        Args:
            brief: The content brief specifying what to create.
        
        Returns:
            ContentGenerationResult with the plan or error.
        """
        logger.info(f"Generating content for topic: {brief.topic}")
        logger.info(f"Provider: {self.provider.name}, Model: {self.provider.model}")
        
        # Get prompts
        system_prompt, user_prompt = get_combined_generation_prompt(brief)

        # Batch 1E: generic execution capability constraint (e.g. allowed
        # scene durations) extracted once from the brief's planning input.
        allowed_durations = _brief_allowed_durations(brief)

        last_error = None
        attempts = 0
        retry_feedback = ""
        
        for attempt in range(1, self.max_retries + 1):
            attempts = attempt
            logger.info(f"Generation attempt {attempt}/{self.max_retries}")
            
            try:
                # Call LLM. Retries carry the exact previous rejection reason
                # so the model can converge on the schema contract instead of
                # repeating the same mistake blindly (Phase 11C Batch 1
                # hardening, Finding 2).
                response = self.provider.generate_json(
                    prompt=user_prompt + retry_feedback,
                    system_prompt=system_prompt,
                    temperature=0.7,
                    max_tokens=4096,
                )
                
                # Parse JSON response (includes the duration-preserving
                # schema-contract normalization; warnings are surfaced, never
                # silent).
                plan, normalization_warnings = self._parse_response(
                    response.content, brief
                )
                
                # Validate plan
                validation = validate_production_plan(plan, brief)
                
                if not validation.valid:
                    # Validation failed - retry if possible
                    error_msg = "; ".join(validation.errors)
                    logger.warning(f"Validation failed: {error_msg}")
                    last_error = error_msg
                    retry_feedback = _retry_feedback(
                        error_msg, allowed_durations
                    )
                    
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay)
                        continue
                    else:
                        return ContentGenerationResult.failure(
                            error=f"Validation failed after {attempts} attempts: {error_msg}",
                            provider=self.provider.name,
                            model=self.provider.model,
                            attempts=attempts,
                            canonical_model_id=self.canonical_model_id,
                            verification=self._execution_evidence(response),
                        )
                
                # Success
                logger.info(f"Generation successful on attempt {attempt}")
                return ContentGenerationResult.from_plan(
                    plan=plan,
                    provider=self.provider.name,
                    model=self.provider.model,
                    attempts=attempts,
                    warnings=list(normalization_warnings) + list(validation.warnings),
                    canonical_model_id=self.canonical_model_id,
                    verification=self._execution_evidence(response),
                )
                
            except LLMAuthenticationError as e:
                # Don't retry auth errors
                logger.error(f"Authentication error: {e}")
                return ContentGenerationResult.failure(
                    error=str(e),
                    provider=self.provider.name,
                    model=self.provider.model,
                    attempts=attempts,
                    canonical_model_id=self.canonical_model_id,
                )
            
            except LLMQuotaError as e:
                # Don't retry quota errors
                logger.error(f"Quota error: {e}")
                return ContentGenerationResult.failure(
                    error=str(e),
                    provider=self.provider.name,
                    model=self.provider.model,
                    attempts=attempts,
                    canonical_model_id=self.canonical_model_id,
                )
            
            except (LLMResponseError, json.JSONDecodeError) as e:
                # Retry on response/parsing errors
                logger.warning(f"Response error on attempt {attempt}: {e}")
                last_error = str(e)
                retry_feedback = _retry_feedback(str(e), allowed_durations)
                
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                    continue
            
            except LLMProviderError as e:
                # Retry on transient provider errors
                logger.warning(f"Provider error on attempt {attempt}: {e}")
                last_error = str(e)
                retry_feedback = _retry_feedback(str(e), allowed_durations)
                
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                    continue
            
            except Exception as e:
                # Unexpected error - don't retry
                logger.exception(f"Unexpected error: {e}")
                return ContentGenerationResult.failure(
                    error=f"Unexpected error: {e}",
                    provider=self.provider.name,
                    model=self.provider.model,
                    attempts=attempts,
                    canonical_model_id=self.canonical_model_id,
                )
        
        # All retries exhausted
        return ContentGenerationResult.failure(
            error=f"All {attempts} attempts failed. Last error: {last_error}",
            provider=self.provider.name,
            model=self.provider.model,
            attempts=attempts,
            canonical_model_id=self.canonical_model_id,
        )
    
    def _parse_response(
        self, content: str, brief: ContentBrief
    ) -> Tuple[ProductionPlan, List[str]]:
        """
        Parse LLM response into ProductionPlan.
        
        Args:
            content: Raw LLM response content.
            brief: Original content brief.
        
        Returns:
            Tuple of (parsed ProductionPlan, normalization warnings).
        
        Raises:
            LLMResponseError: If parsing fails.
        """
        # Clean up response
        content = content.strip()
        
        # Remove markdown code blocks if present
        if content.startswith("```"):
            # Find the end of the code block
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]  # Remove opening ```json or ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]  # Remove closing ```
            content = "\n".join(lines)
        
        # Try to extract JSON from response
        content = content.strip()
        
        # Find JSON object boundaries
        start = content.find("{")
        end = content.rfind("}") + 1
        
        if start == -1 or end == 0:
            raise LLMResponseError("No JSON object found in response", content[:500])
        
        json_str = content[start:end]
        
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise LLMResponseError(f"Invalid JSON: {e}", json_str[:500])
        
        # Ensure project_id exists
        if "project_id" not in data or not data["project_id"]:
            data["project_id"] = generate_project_id(brief.topic)
        
        # Phase 11C Batch 1 hardening (Finding 2): make the LLM output agree
        # with the scene-duration schema contract WITHOUT distorting the
        # plan — duration-preserving redistribution, recorded as warnings.
        scenes = data.get("scenes")
        normalization_warnings: List[str] = []
        if isinstance(scenes, list) and scenes and all(
            isinstance(scene, dict) for scene in scenes
        ):
            _scenes, normalization_warnings = normalize_scene_durations(
                scenes,
                allowed_durations=_brief_allowed_durations(brief),
            )
        
        # Parse into Pydantic model
        try:
            plan = ProductionPlan(**data)
        except Exception as e:
            raise LLMResponseError(f"Schema validation failed: {e}", json_str[:500])
        
        return plan, normalization_warnings
