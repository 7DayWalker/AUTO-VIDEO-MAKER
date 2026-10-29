"""
AVM Step 1 -- Canonical Model Registry (READ-ONLY, DATA-ONLY)

Future model-centric AI Brain:

    USER
      |
    CanonicalModel (stable id, e.g. "qwen-3.8-flash")
      |
    ProviderBinding[]  (open-ended; one entry per provider that can
                        serve that canonical model, with its
                        provider-specific model id)
      |
    future resolver (NOT implemented here)
      |
    BaseLLMProvider (existing contract, unchanged)
      |
    execution (ProductionPlan)

Invariants (Step 1):

* CANONICAL MODEL != PROVIDER != PROVIDER-SPECIFIC MODEL ID.
  CanonicalModel.id ("qwen-3.8-flash") is the stable user-facing
  vocabulary. ProviderBinding.provider_model_id ("qwen3.8-27b", etc.)
  is the exact wire id that provider requires. Never confused.

* EXACT model fallback stays within ONE canonical model's bindings.
  Cross-model substitution belongs to future AVM Auto -- NOT here.

* Provider identity is OPEN STRING, not enum. Future provider = new
  ProviderBinding(provider="future_x", ...) without schema change.

* Registry is DETERMINISTIC, LOCAL, READ-ONLY. No DB, no JSON, no
  env reads, no network, no provider instantiation on import.

* Truthful incompleteness over false equivalence. Gemma is NOT Qwen.

Track 9 provider-neutral migration:
* Canonical IDs are true model identities (gemini-3-flash-preview, qwen-vl-max, gpt-4o)
* Deprecated summary-pro-* / summary-standard-* aliases are retained only
  via alias map for backward compat, NOT as active registry entries.
* Provider is internal execution detail, never part of user-facing model identity.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InputModality:
    """Distinct input modalities. NOT collapsed into one boolean.

    ``IMAGE`` and ``NATIVE_VIDEO`` are DIFFERENT capabilities (Task B1/B2):
    a model that accepts stills cannot be called a video-understanding model.
    ``FRAME_SEQUENCE`` is AVM's own derived route -- timestamped stills cut from
    a bounded source window and sent as images. It is recorded distinctly so a
    frame-based execution is never reported as native video ingestion.
    """

    TEXT = "text"
    IMAGE = "image"
    NATIVE_VIDEO = "native_video"
    FRAME_SEQUENCE = "frame_sequence"
    AUDIO = "audio"


class ApiOperation:
    """Provider API operations an execution route may require."""

    CHAT_COMPLETION = "chat_completion"
    JSON_MODE = "json_mode"
    TOOL_USE = "tool_use"


class IdentityVerification:
    """How well a provider binding's MODEL IDENTITY is actually established.

    This is deliberately separate from capability. A provider can demonstrably
    perform a capability while its per-model identity remains unproven.

    * ``VERIFIED``  -- first-party provider serving its own model under its own
      documented wire id (e.g. Google serving ``gemini-3-flash-preview``), or an
      authoritative model-list entry that names the exact upstream model.
    * ``DECLARED``  -- the provider advertises the model id/capability but AVM
      cannot prove the request reached that exact model. Usable for execution
      ONLY under a canonical identity that represents the gateway route itself,
      never as a second binding for a first-party canonical model.
    * ``UNVERIFIED`` -- no trustworthy evidence. Must not be registered.
    """

    VERIFIED = "verified"
    DECLARED = "declared"
    UNVERIFIED = "unverified"


class QuotaEvidence:
    """Truthful account/quota state (Task B4).

    A configured credential proves NOTHING about remaining credits, and a
    model-list response proves nothing about this account's quota. When a
    provider exposes no balance endpoint the state stays ``UNKNOWN``; it is
    never upgraded to "available" by inference.
    """

    UNKNOWN = "unknown"
    CREDENTIALS_CONFIGURED = "credentials_configured"
    MODEL_LISTED = "model_listed"
    CAPABILITY_VERIFIED = "capability_verified"
    BALANCE_VERIFIED = "balance_verified"
    EXHAUSTED = "exhausted"
    REQUEST_SUCCEEDED = "request_succeeded"


class ModelCapabilities(BaseModel):
    """Generic, provider-agnostic capability flags for a canonical model."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    text: bool = True
    json_: bool = Field(default=True, alias="json")
    reasoning: bool = False
    vision: bool = False
    video: bool = False
    multimodal: bool = False
    long_context: bool = False
    tool_use: bool = False
    #: Explicit context window in tokens when known. ``None`` = unknown, never
    #: guessed, so a workload-size check can say "unknown" truthfully.
    context_tokens: Optional[int] = None

    @property
    def json(self) -> bool:  # noqa: A003 - intentional capability name, compat alias
        return self.json_

    @property
    def image_input(self) -> bool:
        """Still-image understanding. Distinct from native video."""
        return bool(self.vision)

    @property
    def native_video_input(self) -> bool:
        """Native video-file ingestion (temporal understanding by the model)."""
        return bool(self.video)

    @property
    def frame_sequence_capable(self) -> bool:
        """Frame-based video understanding via timestamped stills.

        Available to any image-capable model. Recorded as its own execution
        method -- NOT as native video ingestion.
        """
        return bool(self.vision)

    def supported_input_modalities(self) -> Tuple[str, ...]:
        """Concrete input modalities, in deterministic order."""
        mods: list[str] = []
        if self.text:
            mods.append(InputModality.TEXT)
        if self.image_input:
            mods.append(InputModality.IMAGE)
        if self.native_video_input:
            mods.append(InputModality.NATIVE_VIDEO)
        if self.frame_sequence_capable:
            mods.append(InputModality.FRAME_SEQUENCE)
        return tuple(mods)

    def supports_input_modality(self, modality: str) -> bool:
        return modality in self.supported_input_modalities()

    def is_multimodal_capable(self) -> bool:
        """Whether this model can perform Pro multimodal source understanding."""
        if self.multimodal:
            return True
        if self.vision and self.video:
            return True
        if self.vision:
            return True
        if self.video:
            return True
        return False

    def is_pro_eligible(self) -> bool:
        """Capability predicate for Summary Pro: multimodal video understanding."""
        return self.is_multimodal_capable()

    def can_understand_source_video(self) -> Tuple[bool, Optional[str]]:
        """Can this model understand EXISTING source video, and HOW?

        Returns ``(capable, method)`` where method is the TRUTHFUL execution
        route: ``native_video`` when the model ingests video itself, otherwise
        ``frame_sequence`` when it can only read stills. A text-only model is
        never classified as a video-understanding model.
        """
        if self.native_video_input:
            return True, InputModality.NATIVE_VIDEO
        if self.image_input:
            return True, InputModality.FRAME_SEQUENCE
        return False, None



class ProviderBinding(BaseModel):
    """One provider's ability to serve a canonical model.

    INTERNAL EXECUTION METADATA ONLY. None of these fields belongs to the
    user-facing model selection contract (Task B3/D): the model drawer asks for
    canonical models and never needs provider ids, wire ids or credentials.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    provider_model_id: str
    config_keys: Tuple[str, ...] = Field(default_factory=tuple)

    #: Input modalities this PROVIDER ROUTE actually supports for this model.
    #: Empty tuple = not separately established; the canonical model's own
    #: capabilities apply.
    supported_input_modalities: Tuple[str, ...] = Field(default_factory=tuple)
    #: API operations this route supports.
    supported_operations: Tuple[str, ...] = Field(default_factory=tuple)
    #: How well the MODEL IDENTITY of this route is established.
    identity_verification: str = IdentityVerification.VERIFIED
    #: Human-readable provenance of the identity/capability claim. Required for
    #: anything not first-party verified so the knowledge source is auditable.
    identity_evidence: Optional[str] = None

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("provider must be a non-empty string")
        return v.strip()


    @field_validator("provider_model_id")
    @classmethod
    def _validate_provider_model_id(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("provider_model_id must be a non-empty string")
        return v.strip()

    @field_validator("config_keys")
    @classmethod
    def _validate_config_keys(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        if v is None:
            return ()
        if isinstance(v, list):
            v = tuple(v)
        if not isinstance(v, tuple):
            raise ValueError("config_keys must be a tuple of strings")
        cleaned: list[str] = []
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("config_keys entries must be non-empty strings")
            cleaned.append(item.strip())
        return tuple(cleaned)

    @field_validator("identity_verification")
    @classmethod
    def _validate_identity_verification(cls, v: str) -> str:
        allowed = {
            IdentityVerification.VERIFIED,
            IdentityVerification.DECLARED,
            IdentityVerification.UNVERIFIED,
        }
        key = (v or "").strip()
        if key not in allowed:
            raise ValueError(
                f"identity_verification must be one of {sorted(allowed)}, got {v!r}"
            )
        if key == IdentityVerification.UNVERIFIED:
            # An unverified route is not an execution route. Registering one
            # would be exactly the "misleading binding" this task forbids.
            raise ValueError(
                "a binding with identity_verification='unverified' must not be registered"
            )
        return key

    def model_post_init(self, __context) -> None:  # type: ignore[override]
        # Anything weaker than first-party verification MUST name its source.
        if self.identity_verification != IdentityVerification.VERIFIED and not (
            self.identity_evidence or ""
        ).strip():
            raise ValueError(
                f"binding {self.provider}/{self.provider_model_id} is "
                f"'{self.identity_verification}' and must carry identity_evidence "
                f"naming the source of that knowledge"
            )

    def supports_modality(self, modality: str) -> Optional[bool]:
        """Whether this ROUTE supports *modality*.

        Returns ``None`` when the route declares no modality metadata -- an
        honest "not separately established" rather than a fabricated False.
        """
        if not self.supported_input_modalities:
            return None
        return modality in self.supported_input_modalities



class CanonicalModel(BaseModel):
    """Stable, provider-agnostic AVM model vocabulary."""

    model_config = ConfigDict(frozen=True)

    id: str
    display_name: str
    family: Optional[str] = None
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    bindings: Tuple[ProviderBinding, ...] = Field(default_factory=tuple)
    default_binding: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("id must be a non-empty string")
        return v.strip()

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("display_name must be a non-empty string")
        return v.strip()

    @field_validator("family")
    @classmethod
    def _validate_family(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str) or not v.strip():
            raise ValueError("family must be a non-empty string when set")
        return v.strip()

    @field_validator("default_binding")
    @classmethod
    def _validate_default_binding(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str) or not v.strip():
            raise ValueError("default_binding must be a non-empty string when set")
        return v.strip()

    def model_post_init(self, __context) -> None:  # type: ignore[override]
        providers = [b.provider for b in self.bindings]
        seen: set[str] = set()
        dupes: set[str] = set()
        for p in providers:
            if p in seen:
                dupes.add(p)
            seen.add(p)
        if dupes:
            raise ValueError(
                f"duplicate provider bindings for canonical model '{self.id}': "
                f"{sorted(dupes)}"
            )
        if self.default_binding is not None and self.default_binding not in providers:
            raise ValueError(
                f"default_binding '{self.default_binding}' for canonical model "
                f"'{self.id}' does not match any binding provider: {providers}"
            )
        # Allow provider_model_id == canonical id when identity truthfully matches
        # wire id (e.g. gemini-3-flash-preview). No validation error here.


SUMMARY_UNDERSTANDING_ROLE = "summary_understanding"
AI_BRAIN_ROLE = "ai_brain"
VIDEO_GENERATION_ROLE = "video_generation"

# Deprecated aliases: old feature-specific ids -> true canonical ids
# NOT active registry entries; resolved via alias map for backward compat.
_DEPRECATED_ALIASES: Dict[str, str] = {
    "summary-pro-gemini-3-flash-preview": "gemini-3-flash-preview",
    "summary-pro-gemini-2.0-flash": "gemini-3-flash-preview",
    "summary-pro-qwen-vl-max": "qwen-vl-max",
    "summary-pro-gpt4o": "gpt-4o",
    "summary-standard-qwen-flash": "qwen-3.8-flash",
}

_CANONICAL_TO_ALIASES: Dict[str, Tuple[str, ...]] = {}
for _alias, _true in _DEPRECATED_ALIASES.items():
    _CANONICAL_TO_ALIASES.setdefault(_true, []).append(_alias)
for _k in list(_CANONICAL_TO_ALIASES.keys()):
    _CANONICAL_TO_ALIASES[_k] = tuple(sorted(_CANONICAL_TO_ALIASES[_k]))


def _resolve_alias(canonical_id: str) -> str:
    key = (canonical_id or "").strip()
    if not key:
        return key
    return _DEPRECATED_ALIASES.get(key, key)


def is_deprecated_alias(canonical_id: str) -> bool:
    return (canonical_id or "").strip() in _DEPRECATED_ALIASES


def deprecated_aliases_for(canonical_id: str) -> Tuple[str, ...]:
    return _CANONICAL_TO_ALIASES.get((canonical_id or "").strip(), ())


def list_models_for_pro() -> Tuple[CanonicalModel, ...]:
    """Capability-driven discovery for Summary Pro: multimodal video understanding."""
    return tuple(m for m in MODEL_REGISTRY.values() if m.capabilities.is_pro_eligible())


def list_pro_model_ids() -> Tuple[str, ...]:
    return tuple(m.id for m in list_models_for_pro())


# ---------------------------------------------------------------------------
# TASK B2 -- JOB-REQUIREMENT CAPABILITY DISCOVERY
#
# Models are discovered by what their capability metadata SAYS THEY CAN DO.
# Nothing here inspects a model id for 'summary', 'pro', 'gemini', 'qwen' or any
# other product/provider label -- the catalogue is model-agnostic by construction.
# ---------------------------------------------------------------------------

#: The Summary Pro job: understand EXISTING source video and identify
#: meaningful events for a grounded, condensed narrated recap.
JOB_SOURCE_VIDEO_RECAP = "source_video_event_recap"
#: Summary Standard consumes the existing transcript, not native video/stills.
JOB_SOURCE_TRANSCRIPT_RECAP = "source_transcript_recap"


class JobRequirement(BaseModel):
    """What a JOB needs from a model, expressed purely as capabilities."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    requires_text: bool = True
    requires_json: bool = False
    #: At least ONE of these input modalities must be supported.
    requires_any_input_modality: Tuple[str, ...] = Field(default_factory=tuple)
    requires_tool_use: bool = False
    #: Minimum context window. ``None`` = no stated requirement.
    min_context_tokens: Optional[int] = None


#: Registered job requirements. Summary Pro needs to understand existing source
#: video; a model qualifies through NATIVE video OR through frame-based visual
#: analysis (both are recorded distinctly at execution time).
JOB_REQUIREMENTS: Dict[str, JobRequirement] = {
    JOB_SOURCE_TRANSCRIPT_RECAP: JobRequirement(
        job_id=JOB_SOURCE_TRANSCRIPT_RECAP,
        requires_text=True,
        requires_json=True,
        requires_any_input_modality=(InputModality.TEXT,),
    ),
    JOB_SOURCE_VIDEO_RECAP: JobRequirement(
        job_id=JOB_SOURCE_VIDEO_RECAP,
        requires_text=True,
        requires_json=True,
        requires_any_input_modality=(
            InputModality.NATIVE_VIDEO,
            InputModality.FRAME_SEQUENCE,
        ),
    ),
}


def model_meets_job(model: CanonicalModel, requirement: JobRequirement) -> Tuple[bool, Tuple[str, ...]]:
    """Whether *model* satisfies *requirement*. Returns ``(ok, missing_reasons)``."""
    caps = model.capabilities
    missing: list[str] = []
    if requirement.requires_text and not caps.text:
        missing.append("text_understanding")
    if requirement.requires_json and not caps.json_:
        missing.append("structured_json_output")
    if requirement.requires_tool_use and not caps.tool_use:
        missing.append("tool_use")
    if requirement.requires_any_input_modality:
        supported = caps.supported_input_modalities()
        if not any(m in supported for m in requirement.requires_any_input_modality):
            missing.append(
                "input_modality:" + "|".join(requirement.requires_any_input_modality)
            )
    if requirement.min_context_tokens is not None:
        # Unknown context is NOT treated as sufficient -- it is reported as unknown.
        if caps.context_tokens is None:
            missing.append("context_tokens:unknown")
        elif caps.context_tokens < requirement.min_context_tokens:
            missing.append(f"context_tokens<{requirement.min_context_tokens}")
    return (not missing), tuple(missing)


def discover_models_for_job(job_id: str) -> Tuple[CanonicalModel, ...]:
    """Canonical models capable of *job_id*, by CAPABILITY METADATA only.

    Raises KeyError for an unregistered job rather than guessing.
    """
    key = (job_id or "").strip()
    if key not in JOB_REQUIREMENTS:
        raise KeyError(f"unknown job requirement: '{job_id}'")
    req = JOB_REQUIREMENTS[key]
    return tuple(m for m in MODEL_REGISTRY.values() if model_meets_job(m, req)[0])


def job_video_understanding_method(model: CanonicalModel) -> Optional[str]:
    """TRUTHFUL execution method this model would use for source video.

    ``native_video`` when the model ingests video itself, ``frame_sequence``
    when AVM must supply timestamped stills, ``None`` when it cannot do the job.
    """
    return model.capabilities.can_understand_source_video()[1]



def model_drawer_projection(job_id: str = JOB_SOURCE_VIDEO_RECAP) -> Dict[str, object]:
    """TASK D -- Core contract for the future model drawer.

    Returns USER-FACING canonical model information for a specific job.

    DELIBERATELY ABSENT (and asserted absent by tests):
      * provider names
      * provider-specific wire model ids
      * API keys / config key names
      * any internal routing detail

    Cost is NOT reported: no trustworthy per-job pricing or job-cost estimate
    exists in Core yet, so ``cost_estimate`` stays ``None`` and
    ``cost_basis`` says so rather than inventing a number. Availability is
    likewise not claimed here -- eligibility/quota belongs to the routing layer
    (``app.content.capability_router``) which reports evidence truthfully.

    K3 is NOT modified by this function; it only establishes the contract.
    """
    req = JOB_REQUIREMENTS.get((job_id or "").strip())
    if req is None:
        raise KeyError(f"unknown job requirement: '{job_id}'")

    models = []
    for m in MODEL_REGISTRY.values():
        ok, _missing = model_meets_job(m, req)
        if not ok:
            continue
        caps = m.capabilities
        method = job_video_understanding_method(m)
        models.append({
            "id": m.id,
            "display_name": m.display_name,
            "family": m.family,
            "capabilities": {
                "text": caps.text,
                "json": caps.json_,
                "reasoning": caps.reasoning,
                "vision": caps.vision,
                "video": caps.video,
                "multimodal": caps.multimodal,
                "long_context": caps.long_context,
                "tool_use": caps.tool_use,
                # Distinct modalities -- image input is NOT native video.
                "image_input": caps.image_input,
                "native_video_input": caps.native_video_input,
                "frame_based_video_understanding": caps.frame_sequence_capable,
            },
            "context_tokens": caps.context_tokens,
            "eligible_for_job": True,
            # TRUTHFUL method label: frame-based analysis is never presented as
            # native video ingestion.
            "video_understanding_method": method,
            "cost_estimate": None,
            "cost_basis": "unavailable: no verified per-job pricing data in Core",
        })
    return {
        "job_id": req.job_id,
        # Preserved for existing consumers.
        "capability": "multimodal_video_understanding",
        "models": tuple(models),
        "ranking_basis": "capability_rank",
        "cost_optimized": False,
    }



def _build_registry() -> Dict[str, CanonicalModel]:
    """Build the single authoritative registry.

    No I/O, no env reads, no provider imports. Deterministic every import.

    TASK A2 -- INVALID CROSS-MODEL BINDINGS REMOVED
    ------------------------------------------------
    These previously-registered bindings were audited against the providers'
    own authoritative model-list APIs and REMOVED, because each routed a
    canonical model to a DIFFERENT underlying model:

    * ``gemini-3-flash-preview`` -> alibaba ``qwen-vl-plus``
      Alibaba does not serve Google's Gemini. Verified live: Google's
      ``GET /v1beta/models`` lists ``gemini-3-flash-preview``; Alibaba's
      ``qwen-vl-plus`` is an unrelated Qwen model. Capability similarity is
      NOT model identity.

    * ``gpt-4o`` -> alibaba ``qwen-vl-plus``
      Same violation: Alibaba does not serve OpenAI's GPT-4o.

    * ``qwen-vl-max`` -> openrouter ``qwen/qwen2.5-vl-72b-instruct``
      Verified live against ``GET https://openrouter.ai/api/v1/models``:
      OpenRouter lists ``qwen/qwen2.5-vl-72b-instruct`` but NOT ``qwen-vl-max``.
      Qwen2.5-VL-72B-Instruct is a different model (different generation and
      weights), so it is not a route for this canonical id. It now has its OWN
      canonical identity instead of being renamed to look compatible.

    Retained bindings are first-party or authoritatively listed under the exact
    model identity. One verified binding is preferred over several misleading
    ones; no substitute binding was invented to manufacture redundancy.
    """
    models: list[CanonicalModel] = [
        CanonicalModel(
            id="qwen-3.8-flash",
            display_name="Qwen 3.8 Flash",
            family="qwen",
            capabilities=ModelCapabilities(
                text=True,
                json_=True,
                reasoning=False,
                vision=False,
                long_context=False,
                tool_use=False,
            ),
            bindings=(
                ProviderBinding(
                    provider="codecraft",
                    provider_model_id="qwen3.8-27b",
                    config_keys=("CODECRAFT_API_KEY", "CODECRAFT_BASE_URL"),
                    supported_input_modalities=(InputModality.TEXT,),
                    supported_operations=(ApiOperation.CHAT_COMPLETION, ApiOperation.JSON_MODE),
                    # CodeCraft is an aggregating gateway. Its /models metadata
                    # declares this id as text->text, but AVM proved the gateway
                    # does not reliably isolate per-model identity (a declared
                    # 2B text-only model answered frontier reasoning AND image
                    # prompts). Identity is therefore DECLARED, never verified.
                    identity_verification=IdentityVerification.DECLARED,
                    identity_evidence=(
                        "CodeCraft GET /v1/models declares id 'qwen3.8-27b' "
                        "(modality text->text). Per-model identity not provable: "
                        "see _real_smoke_wk/codecraft_identity_trust.json"
                    ),
                ),
            ),
            default_binding="codecraft",
        ),

        # ---- True canonical Pro models (provider is internal detail) -------
        CanonicalModel(
            id="gemini-3-flash-preview",
            display_name="Gemini 3 Flash Preview",
            family="gemini",
            capabilities=ModelCapabilities(
                text=True, json_=True, vision=True, video=True, multimodal=True,
                long_context=True, tool_use=True, context_tokens=1_048_576,
            ),
            bindings=(
                # FIRST-PARTY: Google serving its own model under its own id.
                ProviderBinding(
                    provider="gemini",
                    provider_model_id="gemini-3-flash-preview",
                    config_keys=("GEMINI_API_KEY",),
                    supported_input_modalities=(
                        InputModality.TEXT, InputModality.IMAGE,
                        InputModality.NATIVE_VIDEO, InputModality.FRAME_SEQUENCE,
                    ),
                    supported_operations=(ApiOperation.CHAT_COMPLETION, ApiOperation.JSON_MODE),
                    identity_verification=IdentityVerification.VERIFIED,
                    identity_evidence=(
                        "first-party Google Generative Language API; exact id present in "
                        "GET /v1beta/models (verified live)"
                    ),
                ),
                # REMOVED: alibaba/qwen-vl-plus -- Alibaba does not serve Gemini.
            ),
            default_binding="gemini",
        ),
        CanonicalModel(
            id="gemini-2.5-flash",
            display_name="Gemini 2.5 Flash",
            family="gemini",
            capabilities=ModelCapabilities(
                text=True, json_=True, vision=True, video=True, multimodal=True,
                long_context=True, tool_use=True, context_tokens=1_048_576,
            ),
            bindings=(
                ProviderBinding(
                    provider="gemini", provider_model_id="gemini-2.5-flash",
                    config_keys=("GEMINI_API_KEY",),
                    supported_input_modalities=(InputModality.TEXT, InputModality.IMAGE,
                                                InputModality.NATIVE_VIDEO, InputModality.FRAME_SEQUENCE),
                    supported_operations=(ApiOperation.CHAT_COMPLETION, ApiOperation.JSON_MODE),
                    identity_evidence="Google GET /v1beta/models lists gemini-2.5-flash (2026-10-29).",
                ),
                ProviderBinding(
                    provider="openrouter", provider_model_id="google/gemini-2.5-flash",
                    config_keys=("OPENROUTER_API_KEY",),
                    supported_input_modalities=(InputModality.TEXT,),
                    supported_operations=(ApiOperation.CHAT_COMPLETION, ApiOperation.JSON_MODE),
                    identity_evidence="OpenRouter GET /api/v1/models lists google/gemini-2.5-flash with response_format (2026-10-29); text JSON route.",
                ),
            ),
            default_binding="gemini",
        ),
        CanonicalModel(
            id="gemini-3.6-flash", display_name="Gemini 3.6 Flash", family="gemini",
            capabilities=ModelCapabilities(
                text=True, json_=True, vision=True, video=True, multimodal=True,
                long_context=True, tool_use=True, context_tokens=1_048_576,
            ),
            bindings=(ProviderBinding(
                provider="gemini", provider_model_id="gemini-3.6-flash", config_keys=("GEMINI_API_KEY",),
                supported_input_modalities=(InputModality.TEXT, InputModality.IMAGE,
                                            InputModality.NATIVE_VIDEO, InputModality.FRAME_SEQUENCE),
                supported_operations=(ApiOperation.CHAT_COMPLETION, ApiOperation.JSON_MODE),
                identity_evidence="First-party Google GET /v1beta/models lists gemini-3.6-flash; generateContent JSON verified live (2026-10-30).",
            ),),
            default_binding="gemini",
        ),
        CanonicalModel(
            id="qwen-vl-max",
            display_name="Qwen VL Max",
            family="qwen",
            capabilities=ModelCapabilities(
                text=True, json_=True, vision=True, video=True, multimodal=True,
                long_context=True, tool_use=False, context_tokens=32_768,
            ),
            bindings=(
                # FIRST-PARTY: Alibaba serving its own Qwen-VL-Max.
                ProviderBinding(
                    provider="alibaba",
                    provider_model_id="qwen-vl-max",
                    config_keys=("ALIBABA_API_KEY",),
                    supported_input_modalities=(
                        InputModality.TEXT, InputModality.IMAGE,
                        InputModality.NATIVE_VIDEO, InputModality.FRAME_SEQUENCE,
                    ),
                    supported_operations=(ApiOperation.CHAT_COMPLETION, ApiOperation.JSON_MODE),
                    identity_verification=IdentityVerification.VERIFIED,
                    identity_evidence=(
                        "first-party Alibaba Model Studio (DashScope); wire id equals the "
                        "canonical model identity"
                    ),
                ),
                # REMOVED: openrouter/qwen/qwen2.5-vl-72b-instruct -- a DIFFERENT
                # model. OpenRouter's live model list does not offer qwen-vl-max.
            ),
            default_binding="alibaba",
        ),
        CanonicalModel(
            id="gpt-4o",
            display_name="GPT-4o",
            family="openai",
            capabilities=ModelCapabilities(
                # GPT-4o accepts text+image (and files), NOT native video.
                # Source-video understanding is therefore FRAME-BASED for this
                # model and is recorded as such, never as native ingestion.
                text=True, json_=True, vision=True, video=False, multimodal=True,
                long_context=True, tool_use=True, context_tokens=128_000,
            ),
            bindings=(
                ProviderBinding(
                    provider="openrouter",
                    provider_model_id="openai/gpt-4o",
                    config_keys=("OPENROUTER_API_KEY",),
                    supported_input_modalities=(
                        InputModality.TEXT, InputModality.IMAGE, InputModality.FRAME_SEQUENCE,
                    ),
                    supported_operations=(ApiOperation.CHAT_COMPLETION, ApiOperation.JSON_MODE),
                    identity_verification=IdentityVerification.VERIFIED,
                    identity_evidence=(
                        "OpenRouter GET /api/v1/models lists exactly 'openai/gpt-4o' with "
                        "input_modalities ['text','image','file'] (verified live)"
                    ),
                ),
                # REMOVED: alibaba/qwen-vl-plus -- Alibaba does not serve GPT-4o.
            ),
            default_binding="openrouter",
        ),
        # ---- Qwen2.5-VL-72B-Instruct: its OWN canonical identity -----------
        # Previously mis-registered as a fallback route for qwen-vl-max. It is a
        # distinct model, so it gets a distinct canonical identity rather than
        # being renamed to appear compatible.
        CanonicalModel(
            id="qwen2.5-vl-72b-instruct",
            display_name="Qwen2.5-VL 72B Instruct",
            family="qwen",
            capabilities=ModelCapabilities(
                text=True, json_=True, vision=True, video=False, multimodal=True,
                long_context=True, tool_use=False, context_tokens=128_000,
            ),
            bindings=(
                ProviderBinding(
                    provider="openrouter",
                    provider_model_id="qwen/qwen2.5-vl-72b-instruct",
                    config_keys=("OPENROUTER_API_KEY",),
                    supported_input_modalities=(
                        InputModality.TEXT, InputModality.IMAGE, InputModality.FRAME_SEQUENCE,
                    ),
                    supported_operations=(ApiOperation.CHAT_COMPLETION, ApiOperation.JSON_MODE),
                    identity_verification=IdentityVerification.VERIFIED,
                    identity_evidence=(
                        "OpenRouter GET /api/v1/models lists exactly "
                        "'qwen/qwen2.5-vl-72b-instruct' with input_modalities "
                        "['text','image'] (verified live)"
                    ),
                ),
            ),
            default_binding="openrouter",
        ),
        # Additional configured Standard choices retain their OWN identity.
        # Never attach a cheaper/different wire model to a preview's binding.
        CanonicalModel(
            id="qwen-plus", display_name="Qwen Plus", family="qwen",
            capabilities=ModelCapabilities(text=True, json_=True),
            bindings=(ProviderBinding(
                provider="alibaba", provider_model_id="qwen-plus", config_keys=("ALIBABA_API_KEY",),
                supported_input_modalities=(InputModality.TEXT,),
                supported_operations=(ApiOperation.CHAT_COMPLETION, ApiOperation.JSON_MODE),
                identity_evidence="First-party DashScope qwen-plus; configured text model supported by the Alibaba adapter.",
            ),),
            default_binding="alibaba",
        ),
        CanonicalModel(
            id="gemma-4-26b-a4b-it", display_name="Gemma 4 26B A4B IT", family="gemma",
            capabilities=ModelCapabilities(text=True, json_=True, vision=True, video=True, multimodal=True,
                                           long_context=True, context_tokens=262_144),
            bindings=(ProviderBinding(
                provider="openrouter", provider_model_id="google/gemma-4-26b-a4b-it:free",
                config_keys=("OPENROUTER_API_KEY",),
                supported_input_modalities=(InputModality.TEXT,),
                supported_operations=(ApiOperation.CHAT_COMPLETION, ApiOperation.JSON_MODE),
                identity_evidence="OpenRouter GET /api/v1/models lists google/gemma-4-26b-a4b-it:free with text/image/video input and response_format (2026-10-29); Standard text route only.",
            ),),
            default_binding="openrouter",
        ),
        # ---- CodeCraft vision: DELIBERATELY NOT REGISTERED -----------------
        # TASK C findings (live evidence, see _real_smoke_wk/):
        #   * CodeCraft image input IS REAL -- a frame cut from the real source
        #     film was described accurately (codecraft_per_model_verify.json).
        #     The old "text-only" verdict was an ADAPTER limitation. The adapter
        #     now supports image input for model-specific verified ids.
        #   * BUT per-model MODEL IDENTITY is NOT verifiable on this gateway:
        #     'gemma-2-2b' -- declared by CodeCraft's own /models as a 2B,
        #     8192-ctx, text->text model -- both described a real image AND
        #     solved a frontier-level probability task, while the API echoed
        #     back the requested id (codecraft_identity_trust.json,
        #     codecraft_identity_echo.json).
        #
        # Per this task's rule "unknown mappings must remain unresolved until
        # verified", NO canonical model identity is assigned to a CodeCraft
        # vision route. Registering one would either (a) claim a specific
        # upstream model AVM cannot prove, or (b) put a PROVIDER name into the
        # user-facing model vocabulary. Both are forbidden.
        #
        # CodeCraft therefore remains registered ONLY as the pre-existing
        # text route for 'qwen-3.8-flash'. Its verified image capability stays
        # available through the adapter and is covered by tests.
        # ---- CodeCraft-served Claude Opus 5.5: its OWN canonical identity --
        # STEP 1/2 (Track 9 CodeCraft integration). Established by live
        # revalidation -- see _real_smoke_wk/opus55_revalidation.json:
        #
        #   * 'claude-opus-5.5' IS listed in CodeCraft's live GET /v1/models.
        #   * IMAGE INPUT IS VERIFIED AT RUNTIME: a real frame cut from the real
        #     source film was described accurately through the EXISTING adapter
        #     (grounding tokens: captive, guard, mud, woman).
        #   * NATIVE VIDEO INPUT IS NOT ESTABLISHED. No CodeCraft entry declares
        #     a video input modality, and a Playground upload control is not
        #     proof. video=False is therefore truthful, and Summary Pro will use
        #     FRAME-BASED visual analysis for this model.
        #
        # IDENTITY EVIDENCE CLASS: provider-declared + runtime capability
        # verified. It is NOT independently attested: CodeCraft echoes back
        # whatever 'model' was requested, and its catalogue recently dropped all
        # capability metadata (entries are now bare id/object/created/owned_by,
        # owned_by='cleanapis'). So AVM can prove WHAT THE ROUTE CAN DO, but not
        # that the upstream weights are Anthropic's Claude Opus 5.5.
        #
        # The canonical id is the MODEL name only. It is deliberately NOT
        # 'codecraft-claude-opus-5.5': CodeCraft is an internal provider binding
        # and must never leak into the user-facing model identity.
        CanonicalModel(
            id="claude-opus-5.5",
            display_name="Claude Opus 5.5",
            family="anthropic",
            capabilities=ModelCapabilities(
                text=True, json_=True, reasoning=True,
                # Image input: VERIFIED by real request.
                vision=True,
                # Native video: NOT ESTABLISHED -> must stay False.
                video=False,
                multimodal=True, long_context=True, tool_use=True,
                context_tokens=1_000_000,
            ),
            bindings=(
                ProviderBinding(
                    provider="codecraft",
                    provider_model_id="claude-opus-5.5",
                    config_keys=("CODECRAFT_API_KEY", "CODECRAFT_BASE_URL"),
                    supported_input_modalities=(
                        InputModality.TEXT, InputModality.IMAGE, InputModality.FRAME_SEQUENCE,
                    ),
                    supported_operations=(ApiOperation.CHAT_COMPLETION, ApiOperation.JSON_MODE),
                    identity_verification=IdentityVerification.DECLARED,
                    identity_evidence=(
                        "CodeCraft live GET /v1/models lists exactly 'claude-opus-5.5'; "
                        "IMAGE input VERIFIED at runtime against a real source frame "
                        "(_real_smoke_wk/opus55_revalidation.json). Upstream model "
                        "identity is provider-declared only, NOT independently "
                        "attested: the API echoes the requested id and the catalogue "
                        "no longer publishes capability metadata "
                        "(_real_smoke_wk/codecraft_identity_trust.json). Native video "
                        "input NOT established."
                    ),
                ),
            ),
            default_binding="codecraft",
        ),
    ]
    registry: Dict[str, CanonicalModel] = {}

    for m in models:
        if m.id in registry:
            raise ValueError(f"duplicate canonical model id in registry: '{m.id}'")
        registry[m.id] = m
    return registry


MODEL_REGISTRY: Dict[str, CanonicalModel] = _build_registry()


def get_model(canonical_id: str) -> CanonicalModel:
    """Return the canonical model for canonical_id.

    Supports deprecated alias fallback for backward compat.
    Raises KeyError if unknown. No provider selection.
    """
    key = (canonical_id or "").strip()
    if not key:
        raise KeyError("canonical model id must be a non-empty string")
    if key in MODEL_REGISTRY:
        return MODEL_REGISTRY[key]
    alias_target = _DEPRECATED_ALIASES.get(key)
    if alias_target and alias_target in MODEL_REGISTRY:
        return MODEL_REGISTRY[alias_target]
    raise KeyError(f"unknown canonical model: '{canonical_id}'") from None


def has_model(canonical_id: str) -> bool:
    """Whether canonical_id is an active canonical identity.

    Deprecated aliases are NOT active identities (returns False) even though
    get_model(alias) resolves for backward compat.
    """
    key = (canonical_id or "").strip()
    if not key:
        return False
    if key in _DEPRECATED_ALIASES:
        return False
    return key in MODEL_REGISTRY


def has_model_including_alias(canonical_id: str) -> bool:
    """Whether canonical_id is known including deprecated aliases."""
    key = (canonical_id or "").strip()
    if not key:
        return False
    if key in MODEL_REGISTRY:
        return True
    return key in _DEPRECATED_ALIASES


def list_models() -> Tuple[CanonicalModel, ...]:
    """All active canonical models in deterministic registry order (snapshot tuple).

    Deprecated aliases are NOT included.
    """
    return tuple(MODEL_REGISTRY.values())


__all__ = [
    "InputModality",
    "ApiOperation",
    "IdentityVerification",
    "QuotaEvidence",
    "ModelCapabilities",
    "ProviderBinding",
    "CanonicalModel",
    "MODEL_REGISTRY",
    "_DEPRECATED_ALIASES",
    "_CANONICAL_TO_ALIASES",
    "get_model",
    "has_model",
    "has_model_including_alias",
    "list_models",
    "list_models_for_pro",
    "list_pro_model_ids",
    "model_drawer_projection",
    "is_deprecated_alias",
    "deprecated_aliases_for",
    "JOB_SOURCE_VIDEO_RECAP",
    "JOB_SOURCE_TRANSCRIPT_RECAP",
    "JobRequirement",
    "JOB_REQUIREMENTS",
    "model_meets_job",
    "discover_models_for_job",
    "job_video_understanding_method",
]

