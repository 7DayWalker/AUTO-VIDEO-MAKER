"""Batch 1C - Alibaba Cloud Model Studio video provider (PRIMARY).

Implements the Alibaba video adapter at the EXISTING visual-provider seam
(``SceneGenerationRequest`` -> ``SceneGenerationResult``; the Phase 8
provider protocol — ``generate_scene`` / ``resume_scene`` / ``wants_resume``
/ ``is_available`` — verbatim). All DashScope task mechanics (submission,
task ids, polling, result URLs) stay inside this adapter; the rest of AVM
sees only the existing contracts.

Flow (existing Phase 7E async shape, Alibaba protocol):

    SceneGenerationRequest
        -> explicit-parameter safety validation (BEFORE any billable call)
        -> submit async task (bounded)
        -> task_id -> bounded polling (never resubmit an in-flight task)
        -> SUCCEEDED -> download -> local materialization
        -> validate_video_file + aspect check + AudioMediaFacts
        -> SceneGenerationResult (provenance in ``verification``)

Safety guards (mandatory, not provider defaults): model, size/resolution and
duration are ALWAYS explicit in the submitted payload and validated against
the model's declared capabilities BEFORE submission. A requested duration or
size outside the model's supported bounds is a TRUTHFUL capability failure —
never a silent provider default, and never a resubmission with altered
parameters.

Classification (fixed policy): the provider labels every failure result with
``verification["failure_category"]`` / ``verification["fallback_eligible"]``
so the wrapper (``FallbackVideoVisualProvider``) can decide the single
authorized Alibaba -> OpenRouter fallback deterministically:

    eligible     rate limit, 5xx, network/timeout, task failure, capability
    NOT eligible invalid AVM request (domain violation), configuration,
                 authentication, credential class, moderation, artifact
                 validation, polling timeout (a billable task is still in
                 flight — falling back would double-spend; resume instead)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from app.assembly.audio_contract import AudioMediaFacts
from app.assembly.media import inspect_audio
from app.flow.scene_generator import validate_video_file
from app.flow.scene_models import SceneGenerationRequest, SceneGenerationResult
from app.orchestrator.alibaba_gateway import (
    TASK_IN_FLIGHT_STATUSES,
    TASK_STATUS_CANCELED,
    TASK_STATUS_FAILED,
    TASK_STATUS_SUCCEEDED,
    TASK_STATUS_UNKNOWN,
    DEFAULT_ALIBABA_DASHSCOPE_BASE_URL,
    DashScopeClient,
)
from app.orchestrator.alibaba_video_config import (
    ALIBABA_API_KEY_ENV,
    ALIBABA_VIDEO_BASE_URL_ENV,
    ALIBABA_VIDEO_MODEL_ENV,
    ALIBABA_VIDEO_SIZES_ENV,
    ALIBABA_VIDEO_DURATIONS_ENV,
    ALIBABA_POLL_INTERVAL_ENV,
    ALIBABA_POLL_MAX_ATTEMPTS_ENV,
    DEFAULT_VIDEO_MODEL,
    DEFAULT_ALLOWED_DURATIONS,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_MAX_ATTEMPTS,
    model_capabilities,
    select_video_size,
)
from app.providers.fallback_policy import (
    FALLBACK_ELIGIBLE_CATEGORIES as FALLBACK_ELIGIBLE,
    ClassifiedProviderError,
    FailureCategory,
    is_fallback_eligible,
)
from app.orchestrator.planning_constraints import ModelCapabilityFacts

logger = logging.getLogger(__name__)

ALIBABA_VIDEO_PROVIDER = "alibaba_video"

# DashScope text-to-video async endpoint (Wan / HappyHorse families).
VIDEO_SUBMIT_PATH = "/api/v1/services/aigc/video-generation/video-synthesis"

# DashScope remote statuses that ended in failure (never resubmitted).
VIDEO_TERMINAL_FAILURE_STATUSES = frozenset(
    {TASK_STATUS_FAILED, TASK_STATUS_CANCELED, TASK_STATUS_UNKNOWN}
)


class AlibabaVideoCapabilityError(RuntimeError):
    """Model cannot satisfy the scene. Classified: fallback-eligible."""

    category = FailureCategory.CAPABILITY


class AlibabaVideoInvalidRequestError(RuntimeError):
    """AVM-side invalid request (outside the valid domain). Classified:
    NOT fallback-eligible — the same invalid request must not be sent to
    OpenRouter."""

    category = FailureCategory.INVALID_REQUEST


class AlibabaVideoPollTimeoutError(RuntimeError):
    """Bounded polling exhausted while the remote task is still in flight.

    Classified: NOT fallback-eligible (the billable Alibaba task exists and
    may still complete; a fallback would create parallel billable spend).
    The task stays resumable through ``resume_scene``."""

    category = FailureCategory.POLL_TIMEOUT


class AlibabaVideoVisualProvider:
    """Alibaba (DashScope) text-to-video provider at the existing seam.

    Every failure result is labeled with a Batch 1C classification
    (``verification['failure_category']`` and
    ``verification['fallback_eligible']``) so the classified fallback
    wrapper can act deterministically — the provider itself never decides
    about other providers.
    """

    name = ALIBABA_VIDEO_PROVIDER
    requires_browser = False

    def __init__(
        self,
        output_dir: Path,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        size_map: Optional[Dict[str, str]] = None,
        allowed_durations: Optional[List[int]] = None,
        poll_interval: Optional[float] = None,
        poll_max_attempts: Optional[int] = None,
        http_client: Any = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.model = (
            model
            or _env(ALIBABA_VIDEO_MODEL_ENV, "")
            or DEFAULT_VIDEO_MODEL
        )
        self._api_key = (
            api_key if api_key is not None else _env(ALIBABA_API_KEY_ENV, "")
        )
        self._base_url = (
            base_url
            or _env(ALIBABA_VIDEO_BASE_URL_ENV, "")
            or DEFAULT_ALIBABA_DASHSCOPE_BASE_URL
        )
        self._size_map = dict(
            size_map
            if size_map is not None
            else parse_size_map(_env(ALIBABA_VIDEO_SIZES_ENV, ""))
        )
        self.allowed_durations = tuple(
            sorted(allowed_durations)
            if allowed_durations is not None
            else (
                parse_durations(_env(ALIBABA_VIDEO_DURATIONS_ENV, ""))
                or list(DEFAULT_ALLOWED_DURATIONS)
            )
        )
        self._poll_interval_seconds = float(
            poll_interval
            if poll_interval is not None
            else _env(ALIBABA_POLL_INTERVAL_ENV, "")
            or DEFAULT_POLL_INTERVAL
        )
        self._poll_max_attempts = int(
            poll_max_attempts
            if poll_max_attempts is not None
            else _env(ALIBABA_POLL_MAX_ATTEMPTS_ENV, "")
            or DEFAULT_POLL_MAX_ATTEMPTS
        )
        self._client = http_client

    # -- provider protocol ------------------------------------------------------

    def resolve_asset(self, request: SceneGenerationRequest) -> Optional[Path]:
        return None

    def _get_client(self) -> DashScopeClient:
        if self._client is None:
            self._client = DashScopeClient(
                self._api_key, self._base_url
            )
        return self._client

    def is_available(self) -> bool:
        """Configuration-only availability; never a network call."""
        return bool(self._api_key)

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(seconds)

    def _fail(
        self,
        request: SceneGenerationRequest,
        error: str,
        *,
        category: str = FailureCategory.UNKNOWN,
        status: str = "failed",
        **verification: Any,
    ) -> SceneGenerationResult:
        result = SceneGenerationResult.failure(
            request.scene_id, request.sequence, error
        )
        result.status = status
        result.verification.update(
            {
                "provider": ALIBABA_VIDEO_PROVIDER,
                "model": self.model,
                "failure_category": category,
                "fallback_eligible": category in FALLBACK_ELIGIBLE,
                **{k: _jsonable(v) for k, v in verification.items()},
            }
        )
        return result

    # -- request translation + safety guards (BEFORE any billable call) --------

    def _translate_request(
        self, request: SceneGenerationRequest
    ) -> Dict[str, Any]:
        """Translate the AVM request into an explicit DashScope payload.

        Safety guards: the requested duration must be inside AVM's valid
        scene domain (1..60s, integer) — a violation is an INVALID AVM
        request (non-eligible); it must then be within the model's DECLARED
        supported durations — a violation is a truthful CAPABILITY failure
        (never silently replaced by a provider default). The size/aspect
        mapping is explicit; an unsupported aspect is also a capability
        failure. Nothing is ever submitted with omitted parameters hoping
        the provider defaults are sane.
        """
        try:
            duration = int(round(float(request.config.duration)))
        except (TypeError, ValueError):
            duration = 0
        if duration < 1 or duration > 60:
            raise AlibabaVideoInvalidRequestError(
                f"invalid AVM scene duration {request.config.duration!r}: "
                "scene duration must be an integer number of seconds in 1..60"
            )
        if duration not in self.allowed_durations:
            raise AlibabaVideoCapabilityError(
                f"Alibaba video model '{self.model}' was not submitted: "
                f"requested duration {duration}s is outside the model's "
                f"supported durations {list(self.allowed_durations)} "
                "(no provider default was substituted)"
            )
        caps = model_capabilities(self.model)
        if "t2v" not in caps.get("modes", ()):
            raise AlibabaVideoCapabilityError(
                f"Alibaba video model '{self.model}' does not declare "
                "text-to-video support"
            )
        aspect = request.config.aspect_ratio
        size, size_error = select_video_size(
            self.model, aspect, self._size_map
        )
        if size_error:
            # Model-aware, truthful capability failure BEFORE any billable
            # submission: no provider default, no invented resolution.
            raise AlibabaVideoCapabilityError(size_error)
        logger.info(
            "provider.request_resolved scene=%s provider=alibaba_video "
            "model=%s size=%s duration=%d aspect=%s",
            request.scene_id, self.model, size, duration, aspect,
        )
        return {
            "model": self.model,
            "input": {"prompt": request.visual_prompt},
            "parameters": {
                "size": size,
                "duration": duration,
                "prompt_extend": True,
            },
        }

    def _checkpoint(
        self,
        callback: Optional[Callable[[Dict[str, Any]], None]],
        payload: Dict[str, Any],
    ) -> None:
        if callback is not None:
            callback({"provider": ALIBABA_VIDEO_PROVIDER, **payload})

    def _task_status(self, payload: Mapping[str, Any]) -> str:
        output = payload.get("output") or {}
        return str(output.get("task_status") or "").upper()

    def _task_id(self, payload: Mapping[str, Any]) -> str:
        output = payload.get("output") or {}
        return str(output.get("task_id") or "")

    def _task_video_url(self, payload: Mapping[str, Any]) -> Optional[str]:
        output = payload.get("output") or {}
        url = output.get("video_url") or output.get("url")
        return str(url) if url else None

    def _task_error(self, payload: Mapping[str, Any]) -> str:
        output = payload.get("output") or {}
        message = str(output.get("message") or "")
        code = str(output.get("code") or "")
        return f"{code}: {message}".strip(": ") or "unknown task error"

    def _task_usage(self, payload: Mapping[str, Any]) -> Any:
        usage = payload.get("usage")
        return _jsonable(usage)


    # -- lifecycle ----------------------------------------------------------------

    def generate_scene(
        self,
        request: SceneGenerationRequest,
        *,
        dry_run: bool = False,
        require_confirmation: bool = True,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        **_: Any,
    ) -> SceneGenerationResult:
        del require_confirmation
        if not self._api_key:
            return self._fail(
                request,
                f"Alibaba video provider is not configured; "
                f"set {ALIBABA_API_KEY_ENV}",
                category=FailureCategory.CONFIGURATION,
            )
        output_path = self.output_dir / f"{request.scene_id}.mp4"
        if output_path.is_file():
            existing = validate_video_file(
                output_path, request.config.aspect_ratio
            )
            if existing.get("valid"):
                facts = AudioMediaFacts.from_probe_data(existing)
                result = SceneGenerationResult.success_result(
                    request.scene_id,
                    request.sequence,
                    output_path,
                    existing.get("file_size", output_path.stat().st_size),
                    existing.get("duration", 0.0),
                    existing.get("width", 0),
                    existing.get("height", 0),
                    existing.get("codec", "unknown"),
                    generation_cost=None,
                    downloaded_at=datetime.now(timezone.utc).isoformat(),
                    audio_facts=facts,
                )
                result.verification.update(
                    {
                        "provider": ALIBABA_VIDEO_PROVIDER,
                        "model": self.model,
                        "reuse": "completed_valid_local_scene",
                        "fallback_used": False,
                    }
                )
                return result
        try:
            body = self._translate_request(request)
        except AlibabaVideoInvalidRequestError as exc:
            # Invalid AVM request: truthful failure, never a fallback.
            return self._fail(request, str(exc), category=exc.category)
        except AlibabaVideoCapabilityError as exc:
            # Capability gap of the configured model: eligible for the
            # classified fallback because the MODEL cannot satisfy the scene
            # while the AVM request itself is valid.
            return self._fail(request, str(exc), category=exc.category)

        if dry_run:
            return SceneGenerationResult.dry_run(
                request.scene_id,
                request.sequence,
                generation_cost=None,
                verification={
                    "provider": ALIBABA_VIDEO_PROVIDER,
                    "model": self.model,
                    "request": body,
                },
            )

        started = time.monotonic()
        try:
            submit_payload = self._get_client().submit_task(
                VIDEO_SUBMIT_PATH, body, operation="Alibaba video submission"
            )
        except ClassifiedProviderError as exc:
            return self._fail(
                request,
                f"Alibaba video submission failed: {exc}",
                category=exc.category,
            )
        except Exception as exc:  # noqa: BLE001 - conservative unknown
            return self._fail(
                request,
                f"Alibaba video submission failed unexpectedly: {exc}",
                category=FailureCategory.UNKNOWN,
            )
        task_id = self._task_id(submit_payload)
        logger.info(
            "provider.request_submitted scene=%s provider=alibaba_video "
            "model=%s task_id=%s request_id_kind=task_id",
            request.scene_id, self.model, task_id,
        )
        self._checkpoint(
            checkpoint_callback,
            {
                "scene_id": request.scene_id,
                "status": "generation_submitted",
                "task_id": task_id,
                "model": self.model,
                "request": body,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return self._poll_and_download(
            request,
            task_id=task_id,
            submitted_body=body,
            started=started,
            checkpoint_callback=checkpoint_callback,
        )

    def _poll_and_download(
        self,
        request: SceneGenerationRequest,
        *,
        task_id: str,
        submitted_body: Mapping[str, Any],
        started: float,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SceneGenerationResult:
        """Bounded polling: never infinite, never a resubmission.

        A polling timeout leaves the billable task in flight and reports a
        truthful non-eligible ``timeout`` status carrying the task identity
        so a later run resumes it via ``resume_scene`` instead of paying for
        a second generation.
        """
        client = self._get_client()
        payload: Mapping[str, Any] = {}
        last_status: Optional[str] = None
        for attempt in range(self._poll_max_attempts):
            try:
                payload = client.poll_task(
                    task_id, operation="Alibaba video polling"
                )
            except ClassifiedProviderError as exc:
                # A poll-transport problem does not resubmit the task.
                logger.warning(
                    "provider.poll_failed scene=%s task_id=%s: %s",
                    request.scene_id, task_id, exc,
                )
                return self._fail(
                    request,
                    f"Alibaba video polling failed for task {task_id}: {exc}",
                    category=exc.category,
                    task_id=task_id,
                )
            status = self._task_status(payload)
            if status != last_status or attempt == 0:
                # Bounded polling visibility: the first poll and every remote
                # status change — never one line per identical poll.
                logger.info(
                    "provider.poll_progress scene=%s task_id=%s "
                    "attempt=%d remote_status=%s",
                    request.scene_id, task_id, attempt + 1, status or "unknown",
                )
                last_status = status
            if status == TASK_STATUS_SUCCEEDED:
                return self._download_and_materialize(
                    request,
                    task_id=task_id,
                    submitted_body=submitted_body,
                    status_payload=payload,
                    started=started,
                    checkpoint_callback=checkpoint_callback,
                )
            if status in VIDEO_TERMINAL_FAILURE_STATUSES:
                error = self._task_error(payload)
                logger.warning(
                    "provider.terminal_failure scene=%s task_id=%s "
                    "remote_status=%s error=%s",
                    request.scene_id, task_id, status, error,
                )
                self._checkpoint(
                    checkpoint_callback,
                    {
                        "scene_id": request.scene_id,
                        "status": "failed",
                        "task_id": task_id,
                        "model": self.model,
                        "remote_status": status,
                        "error": error,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                # Provider-side task failure after a billable submission:
                # the artifact was never produced, so the classified
                # fallback may attempt the scene on OpenRouter. Batch 1H:
                # retain the provider usage quantities from the terminal
                # payload as accounting evidence (may be absent — never
                # invented).
                return self._fail(
                    request,
                    f"Alibaba video task {task_id} failed "
                    f"(status={status}; error={error})",
                    category=FailureCategory.TASK_FAILURE,
                    task_id=task_id,
                    remote_status=status,
                    usage=self._task_usage(payload),
                )
            self._checkpoint(
                checkpoint_callback,
                {
                    "scene_id": request.scene_id,
                    "status": "polling",
                    "task_id": task_id,
                    "model": self.model,
                    "remote_status": status,
                    "attempt": attempt + 1,
                },
            )
            self._sleep(self._poll_interval_seconds)

        return self._fail(
            request,
            (
                f"Alibaba video task {task_id} did not complete within the "
                f"bounded polling window ({self._poll_max_attempts} "
                f"attempts x {self._poll_interval_seconds:.0f}s); the "
                "remote task is still in flight and was NOT resubmitted"
            ),
            category=FailureCategory.POLL_TIMEOUT,
            status="timeout",
            task_id=task_id,
            remote_status=self._task_status(payload) or None,
        )

    def _download_and_materialize(
        self,
        request: SceneGenerationRequest,
        *,
        task_id: str,
        submitted_body: Mapping[str, Any],
        status_payload: Mapping[str, Any],
        started: float,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SceneGenerationResult:
        url = self._task_video_url(status_payload)
        if not url:
            return self._fail(
                request,
                f"Alibaba video task {task_id} succeeded without a video URL",
                category=FailureCategory.RESPONSE_MALFORMED,
                task_id=task_id,
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"{request.scene_id}.mp4"
        temp_path = self.output_dir / f"{request.scene_id}.part"
        try:
            content = self._get_client().download_bytes(
                url, operation="Alibaba video download"
            )
            temp_path.write_bytes(content)
            temp_path.replace(output_path)
        except ClassifiedProviderError as exc:
            temp_path.unlink(missing_ok=True)
            return self._fail(
                request,
                f"Alibaba video download failed for task {task_id}: {exc}",
                category=exc.category,
                task_id=task_id,
            )
        except Exception as exc:  # noqa: BLE001 - materialization problem
            temp_path.unlink(missing_ok=True)
            return self._fail(
                request,
                (
                    f"Alibaba video materialization failed for task "
                    f"{task_id}: {exc}"
                ),
                category=FailureCategory.ARTIFACT_VALIDATION,
                task_id=task_id,
            )
        return self._result_from_file(
            request,
            output_path=output_path,
            task_id=task_id,
            submitted_body=submitted_body,
            status_payload=status_payload,
            started=started,
            checkpoint_callback=checkpoint_callback,
        )

    def _result_from_file(
        self,
        request: SceneGenerationRequest,
        *,
        output_path: Path,
        task_id: str,
        submitted_body: Mapping[str, Any],
        status_payload: Mapping[str, Any],
        started: float,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SceneGenerationResult:
        """Validate the artifact through the EXISTING Phase 7A gate.

        A downloaded artifact that fails validation is an artifact failure
        (NOT fallback-eligible: the root cause may be AVM-side validation or
        model output quality, not a provider outage). Native audio in the
        generated media is observed truthfully and recorded as first-class
        ``AudioMediaFacts`` (Phase 7D untouched).
        """
        validation = validate_video_file(
            output_path, request.config.aspect_ratio
        )
        if not validation.get("valid"):
            # Remove the invalid artifact: a failed download must never
            # linger where a later run could mistake it for a reusable scene.
            output_path.unlink(missing_ok=True)
            return self._fail(
                request,
                f"Alibaba video output failed scene validation: "
                f"{validation.get('error')}",
                category=FailureCategory.ARTIFACT_VALIDATION,
                task_id=task_id,
            )
        try:
            facts = inspect_audio(output_path)
        except Exception:  # noqa: BLE001 - audio facts are observational
            facts = AudioMediaFacts.from_probe_data(validation)
        latency_ms = int((time.monotonic() - started) * 1000)
        parameters = dict(submitted_body.get("parameters") or {})
        verification = {
            "provider": ALIBABA_VIDEO_PROVIDER,
            "model": self.model,
            "task_id": task_id,
            "request": {
                "size": parameters.get("size"),
                "duration": parameters.get("duration"),
                "aspect_ratio": request.config.aspect_ratio,
            },
            "usage": self._task_usage(status_payload),
            "remote_status": self._task_status(status_payload),
            "latency_ms": latency_ms,
            "audio_facts": facts.model_dump(),
            "fallback_used": False,
        }
        result = SceneGenerationResult.success_result(
            scene_id=request.scene_id,
            sequence=request.sequence,
            file_path=output_path,
            file_size=validation.get("file_size", output_path.stat().st_size),
            actual_duration=validation.get("duration", 0.0),
            width=validation.get("width", 0),
            height=validation.get("height", 0),
            codec=validation.get("codec", "unknown"),
            generation_cost=None,
            downloaded_at=datetime.now(timezone.utc).isoformat(),
            warnings=[f"model={self.model}"],
            audio_facts=facts,
        )
        result.verification.update(verification)
        logger.info(
            "provider.materialized scene=%s provider=alibaba_video "
            "model=%s task_id=%s duration=%.1f width=%d height=%d "
            "latency_ms=%d usage_present=%s",
            request.scene_id, self.model, task_id,
            float(validation.get("duration") or 0.0),
            validation.get("width", 0),
            validation.get("height", 0),
            latency_ms,
            verification.get("usage") is not None,
        )
        self._checkpoint(
            checkpoint_callback,
            {
                "scene_id": request.scene_id,
                "status": "completed",
                "task_id": task_id,
                "model": self.model,
                "usage": verification["usage"],
                "file_path": str(output_path),
                "validation": {**validation, "audio_facts": facts.model_dump()},
                "audio_facts": facts.model_dump(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return result

    # -- resume ------------------------------------------------------------------

    def wants_resume(self, checkpoint: Optional[Dict[str, Any]]) -> bool:
        """Resume when a submitted Alibaba task exists and is not completed.

        ``remote_job_identity`` cannot see DashScope tasks (there is no
        polling URL), so this provider owns its resume decision — the
        dispatch itself stays in ``scene_execution``.
        """
        cp = checkpoint or {}
        return bool(
            cp.get("task_id")
            and cp.get("status") != "completed"
            and cp.get("provider", ALIBABA_VIDEO_PROVIDER)
            == ALIBABA_VIDEO_PROVIDER
        )

    def resume_scene(
        self,
        request: SceneGenerationRequest,
        checkpoint: Dict[str, Any],
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        **_: Any,
    ) -> SceneGenerationResult:
        """Resume an already-submitted remote task by polling (never submit).

        A checkpoint that observed a terminal remote failure is unrecoverable
        and is never resubmitted.
        """
        task_id = str((checkpoint or {}).get("task_id") or "")
        if not task_id:
            return self._fail(
                request,
                "Alibaba checkpoint has no recoverable task_id; a deliberate "
                "new generation is required",
                category=FailureCategory.INVALID_REQUEST,
            )
        terminal_status = (
            checkpoint.get("remote_status") or checkpoint.get("status")
        )
        if terminal_status in VIDEO_TERMINAL_FAILURE_STATUSES:
            error = checkpoint.get("error") or "terminal remote failure"
            return self._fail(
                request,
                (
                    f"Alibaba remote task {task_id} ended in a terminal "
                    f"state ({terminal_status}); it was not recovered and "
                    f"not resubmitted ({error})"
                ),
                category=FailureCategory.TASK_FAILURE,
                task_id=task_id,
                remote_status=terminal_status,
            )
        if not self._api_key:
            return self._fail(
                request,
                f"Alibaba video provider is not configured; "
                f"set {ALIBABA_API_KEY_ENV}",
                category=FailureCategory.CONFIGURATION,
                task_id=task_id,
            )
        try:
            submitted_body = {
                "model": checkpoint.get("model") or self.model,
                "input": {"prompt": request.visual_prompt},
            }
            return self._poll_and_download(
                request,
                task_id=task_id,
                submitted_body=submitted_body,
                started=time.monotonic(),
                checkpoint_callback=checkpoint_callback,
            )
        except Exception as exc:  # noqa: BLE001 - truthful resume failure
            return self._fail(
                request,
                f"Alibaba remote task {task_id} could not be recovered "
                f"without resubmission: {exc}",
                category=FailureCategory.NETWORK,
                task_id=task_id,
            )

    def resume_submitted_scene(
        self,
        request: SceneGenerationRequest,
        checkpoint: Dict[str, Any],
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SceneGenerationResult:
        """Compatibility alias used by the existing shared provider protocol."""
        return self.resume_scene(request, checkpoint, checkpoint_callback)


class FallbackVideoVisualProvider:
    """Batch 1C classified video fallback: Alibaba PRIMARY -> OpenRouter.

    The ONE authorized cross-provider video policy, applied at the existing
    visual-provider seam (no orchestration changes, no parallel generation):

        generate_scene
            -> Alibaba (primary)
            -> success? return (OpenRouter never called)
            -> read the classified failure category
            -> NOT fallback-eligible? truthful failure
            -> fallback disabled/unconfigured? truthful failure
            -> OpenRouter (fallback, exactly once, sequentially)
               -> success? annotate provenance and return
               -> truthful failure (both providers reported)

    Resume is delegated by checkpoint identity: a checkpoint that carries an
    OpenRouter job is resumed by OpenRouter; an Alibaba task by Alibaba. A
    billable in-flight Alibaba task (polling timeout) is never converted
    into a second OpenRouter generation.
    """

    name = ALIBABA_VIDEO_PROVIDER
    requires_browser = False

    def __init__(
        self,
        output_dir: Path,
        *,
        primary: Optional[AlibabaVideoVisualProvider] = None,
        fallback: Optional[Any] = None,
        fallback_enabled: Optional[bool] = None,
        **primary_kwargs: Any,
    ) -> None:
        self.output_dir = Path(output_dir)
        self._primary = primary or AlibabaVideoVisualProvider(
            output_dir, **primary_kwargs
        )
        self._fallback = fallback
        self._fallback_enabled = (
            _video_fallback_enabled()
            if fallback_enabled is None
            else bool(fallback_enabled)
        )
        # Track 1 hardening (defect D2): the enabled-but-unusable fallback
        # warning is logged exactly once per wrapper instance.
        self._fallback_warning_logged = False

    def _get_fallback(self) -> Optional[Any]:
        """Resolve the OpenRouter fallback provider lazily and safely.

        The fallback switch wins first: a disabled fallback is ``None``
        (honest Alibaba-only operation) even when a fallback object was
        injected. An unconfigured OpenRouter is an unavailable fallback, not
        an error: eligible Alibaba failures then fail truthfully.
        """
        if not self._fallback_enabled:
            return None
        if self._fallback is not None:
            return self._fallback
        try:
            from app.orchestrator.openrouter_video import (
                OPENROUTER_API_KEY_ENV,
                OPENROUTER_SHARED_API_KEY_ENV,
                OpenRouterVideoVisualProvider,
            )

            self._fallback = OpenRouterVideoVisualProvider(self.output_dir)
            # Track 1 hardening (defect D2): a fallback that is configured as
            # ENABLED but is not actually usable (e.g. no resolvable
            # OpenRouter credential) is a deployment misconfiguration the
            # operator must see. ONE warning per wrapper instance; env-var
            # NAMES only — credential values are never logged.
            if not self._fallback.is_available():
                self._warn_unavailable_fallback(
                    f"set {OPENROUTER_API_KEY_ENV} "
                    f"(or {OPENROUTER_SHARED_API_KEY_ENV})"
                )
        except Exception:  # noqa: BLE001 - a fallback must never break primary
            self._fallback = None
            self._warn_unavailable_fallback("fallback construction failed")
        return self._fallback

    def _warn_unavailable_fallback(self, hint: str) -> None:
        """Log ONE truthful warning for an enabled-but-unusable fallback."""
        if self._fallback_warning_logged:
            return
        self._fallback_warning_logged = True
        logger.warning(
            "video_fallback_unavailable provider=%s fallback=openrouter_video "
            "enabled=true available=false hint=%s",
            ALIBABA_VIDEO_PROVIDER,
            hint,
        )

    def resolve_asset(self, request: SceneGenerationRequest) -> Optional[Path]:
        return self._primary.resolve_asset(request)

    def generate_scene(
        self,
        request: SceneGenerationRequest,
        *,
        dry_run: bool = False,
        require_confirmation: bool = True,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        **_: Any,
    ) -> SceneGenerationResult:
        primary_result = self._primary.generate_scene(
            request,
            dry_run=dry_run,
            require_confirmation=require_confirmation,
            checkpoint_callback=checkpoint_callback,
        )
        if primary_result.success:
            return self._annotate(
                primary_result, self._primary.name, fallback_used=False
            )
        verification = primary_result.verification or {}
        if verification.get("fallback_used"):
            # The primary already routed through its own fallback policy.
            return primary_result
        category = verification.get(
            "failure_category", FailureCategory.UNKNOWN
        )
        reason = str(verification.get("error") or primary_result.error or "")
        if not is_fallback_eligible(category):
            logger.error(
                "fallback.not_eligible scene=%s category=%s: %s",
                request.scene_id, category, reason,
            )
            return primary_result
        fallback = self._get_fallback()
        if fallback is None or not fallback.is_available():
            logger.error(
                "fallback.unavailable scene=%s category=%s: truthful "
                "Alibaba-only failure",
                request.scene_id, category,
            )
            return primary_result
        logger.warning(
            "fallback.triggered scene=%s category=%s; invoking OpenRouter "
            "fallback (single sequential attempt, no parallel generation)",
            request.scene_id, category,
        )
        fallback_result = fallback.generate_scene(
            request,
            require_confirmation=require_confirmation,
            checkpoint_callback=checkpoint_callback,
        )
        if fallback_result.success:
            self._annotate(
                fallback_result,
                self._primary.name,
                fallback_used=True,
                fallback_reason=category,
                final=fallback.name,
            )
            fallback_result.warnings = list(fallback_result.warnings or []) + [
                f"alibaba_fallback_reason={category}"
            ]
            logger.info(
                "fallback.succeeded scene=%s final_provider=%s "
                "primary_provider=%s category=%s",
                request.scene_id, fallback.name, self._primary.name, category,
            )
            return fallback_result
        logger.error(
            "fallback.failed scene=%s: truthful failure (primary=%s "
            "category=%s; fallback error=%s)",
            request.scene_id, self._primary.name, category,
            fallback_result.error,
        )
        return self._annotate(
            fallback_result,
            self._primary.name,
            fallback_used=True,
            fallback_reason=category,
            final=fallback.name,
        )

    def is_available(self) -> bool:
        """Configuration-only: the PRIMARY must be configured.

        A missing Alibaba credential is a configuration problem; silently
        serving it through the paid fallback would turn every un-configured
        deployment into an OpenRouter deployment.
        """
        return self._primary.is_available()

    @staticmethod
    def _annotate(
        result: SceneGenerationResult,
        primary: str,
        *,
        fallback_used: bool,
        fallback_reason: Optional[str] = None,
        final: Optional[str] = None,
    ) -> SceneGenerationResult:
        result.verification.update(
            {
                "primary_provider": primary,
                "final_provider": final or result.verification.get("provider"),
                "fallback_used": fallback_used,
                "fallback_reason": fallback_reason,
            }
        )
        return result

    # -- resume (delegated by checkpoint identity) -----------------------------

    def wants_resume(self, checkpoint: Optional[Dict[str, Any]]) -> bool:
        cp = checkpoint or {}
        owner = cp.get("provider")
        if owner == "openrouter_video":
            fallback = self._get_fallback()
            wants = getattr(fallback, "wants_resume", None)
            return bool(callable(wants) and wants(cp))
        return self._primary.wants_resume(cp)

    def resume_scene(
        self,
        request: SceneGenerationRequest,
        checkpoint: Dict[str, Any],
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        **kwargs: Any,
    ) -> SceneGenerationResult:
        cp = checkpoint or {}
        owner = cp.get("provider")
        if owner == "openrouter_video":
            fallback = self._get_fallback()
            if fallback is None:
                return SceneGenerationResult.failure(
                    request.scene_id,
                    request.sequence,
                    "OpenRouter checkpoint present but the OpenRouter "
                    "fallback is no longer configured/enabled",
                )
            return fallback.resume_scene(
                request, cp, checkpoint_callback=checkpoint_callback,
                **kwargs,
            )
        return self._primary.resume_scene(
            request, cp, checkpoint_callback=checkpoint_callback, **kwargs
        )

    def resume_submitted_scene(
        self,
        request: SceneGenerationRequest,
        checkpoint: Dict[str, Any],
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SceneGenerationResult:
        """Compatibility alias used by the existing shared provider protocol."""
        return self.resume_scene(request, checkpoint, checkpoint_callback)


def alibaba_video_planning_facts() -> Optional[ModelCapabilityFacts]:
    """Batch 1E: provider-owned projection of GENERIC planning capability
    facts for the currently resolved Alibaba video model.

    - Availability gate mirrors the adapter's own ``is_available()``
      semantics (a dedicated application key must be configured); an
      unconfigured adapter yields NO facts (the caller may consult the
      existing fallback source instead).
    - Durations mirror EXACTLY what the adapter's pre-submission guard
      enforces: the operator's ``AVM_ALIBABA_VIDEO_DURATIONS`` override
      wins, otherwise the existing model capability system
      (``model_capabilities``) and the conservative default.  No duration
      is ever invented here.
    - Read-only local metadata; never a network call; never a provider or
      model identity in the returned facts.
    """
    if not _env(ALIBABA_API_KEY_ENV, ""):
        return None
    model_id = _env(ALIBABA_VIDEO_MODEL_ENV, "") or DEFAULT_VIDEO_MODEL
    durations: List[int] = parse_durations(
        _env(ALIBABA_VIDEO_DURATIONS_ENV, "")
    )
    if not durations:
        caps = model_capabilities(model_id)
        durations = [int(d) for d in (caps.get("durations") or ())]
    if not durations:
        durations = list(DEFAULT_ALLOWED_DURATIONS)
    cleaned = tuple(sorted({int(d) for d in durations if int(d) > 0}))
    if not cleaned:
        return None
    return ModelCapabilityFacts(
        supported_durations=cleaned,
        source="model_capability",
    )


def _video_fallback_enabled() -> bool:
    """Video-level fallback switch (independent of the LLM-level switch)."""
    return (_env("AVM_ALIBABA_FALLBACK_ENABLED", "true").strip().lower()
            not in ("0", "false", "no", "off"))


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default) or default


def parse_size_map(raw: str) -> Dict[str, str]:
    """Parse ``9:16=720*1280,16:9=1280*720`` into a mapping."""
    mapping: Dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        aspect, size = part.split("=", 1)
        mapping[aspect.strip()] = size.strip()
    return mapping


def parse_durations(raw: str) -> List[int]:
    """Parse ``5,10`` into a sorted list of allowed integer durations."""
    values: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(float(part))
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.append(value)
    return sorted(set(values))


def _jsonable(value: Any) -> Any:
    return value if isinstance(value, (int, float, str, bool, list, dict)) or value is None else str(value)
