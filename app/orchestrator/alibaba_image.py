"""Batch 1C - Alibaba Cloud Model Studio image-generation visual source.

A generation-capable source INSIDE the existing Phase 6F visual-source
architecture (``VisualSource``) — never a parallel image pipeline. The
produced artifact flows through the EXISTING Phase 6C intake/preflight
(``intake_assets``) exactly like every other source; a provider URL is
never trusted as the final artifact — bytes are materialized locally and
verified with the existing ``verify_sourced_image`` gate.

Provenance (``metadata``): provider=alibaba, model=<configured DashScope
image model>, kind/source_type=ai_generated.

Failure classification is additive to the existing truthful failure
semantics: non-eligible Alibaba failures (invalid request, moderation,
credential class, configuration) are labeled ``fallback_eligible=False``
so the routing layer can STOP the configured chain instead of silently
sending the same bad request to the next generation-capable child
(OpenRouter). Eligible failures (rate limit, outage, network) keep the
normal chain fall-through.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.orchestrator.alibaba_gateway import (
    DEFAULT_ALIBABA_DASHSCOPE_BASE_URL,
    DashScopeClient,
)
from app.orchestrator.scene_execution import (
    BILLING_STATUS_BILLABLE,
    COST_SOURCE_NONE,
    build_operation_accounting,
)
from app.orchestrator.visual_sources import (
    SourceAvailability,
    VisualSource,
    VisualSourceContext,
    VisualSourceResult,
    VisualSourceStatus,
    verify_sourced_image,
)
from app.providers.fallback_policy import (
    ClassifiedProviderError,
    FailureCategory,
    is_fallback_eligible,
)

logger = logging.getLogger(__name__)

ALIBABA_IMAGE_MODEL_ENV = "AVM_ALIBABA_IMAGE_MODEL"
ALIBABA_IMAGE_SIZE_ENV = "AVM_ALIBABA_IMAGE_SIZE"
ALIBABA_POLL_INTERVAL_ENV = "AVM_ALIBABA_POLL_INTERVAL"
ALIBABA_POLL_MAX_ATTEMPTS_ENV = "AVM_ALIBABA_POLL_MAX_ATTEMPTS"
ALIBABA_API_KEY_ENV = "ALIBABA_API_KEY"
ALIBABA_BASE_URL_ENV = "AVM_ALIBABA_BASE_URL"

# Validated DashScope image families (Wan text-to-image). Configuration-
# driven; never hardcoded at call sites.
DEFAULT_ALIBABA_IMAGE_MODEL = "wan2.2-t2i-flash"

# Explicit size per AVM aspect ratio (DashScope convention W*H). AVM never
# relies on provider defaults for generation parameters.
DEFAULT_ALIBABA_IMAGE_SIZE_MAP = {
    "9:16": "720*1280",
    "16:9": "1280*720",
    "1:1": "1024*1024",
}

DEFAULT_ALIBABA_IMAGE_POLL_INTERVAL = 2.0
DEFAULT_ALIBABA_IMAGE_POLL_MAX_ATTEMPTS = 60

# DashScope text-to-image async endpoint.
IMAGE_SUBMIT_PATH = "/api/v1/services/aigc/text2image/image-synthesis"

_EXT_BY_MEDIA = {
    "image/png": ".png",
    "image/jpeg": ".jpeg",
    "image/jpg": ".jpeg",
    "image/webp": ".webp",
}



class AlibabaImageSource(VisualSource):
    """Alibaba AI image generation as a Phase 6F visual source (PRIMARY)."""

    name = "alibaba"

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        poll_interval: Optional[float] = None,
        poll_max_attempts: Optional[int] = None,
        http_client: Any = None,
    ) -> None:
        self.output_dir = Path(output_dir) if output_dir else None
        self._model = (
            model
            or os.getenv(ALIBABA_IMAGE_MODEL_ENV, "")
            or DEFAULT_ALIBABA_IMAGE_MODEL
        )
        self._api_key = (
            api_key if api_key is not None
            else os.getenv(ALIBABA_API_KEY_ENV, "")
        )
        self._base_url = (
            base_url
            or os.getenv(ALIBABA_BASE_URL_ENV, "")
            or DEFAULT_ALIBABA_DASHSCOPE_BASE_URL
        )
        self._poll_interval = float(
            poll_interval
            if poll_interval is not None
            else os.getenv(ALIBABA_POLL_INTERVAL_ENV, "")
            or DEFAULT_ALIBABA_IMAGE_POLL_INTERVAL
        )
        self._poll_max_attempts = int(
            poll_max_attempts
            if poll_max_attempts is not None
            else os.getenv(ALIBABA_POLL_MAX_ATTEMPTS_ENV, "")
            or DEFAULT_ALIBABA_IMAGE_POLL_MAX_ATTEMPTS
        )
        self._client = http_client

    def _get_client(self) -> DashScopeClient:
        if self._client is None:
            self._client = DashScopeClient(self._api_key, self._base_url)
        return self._client

    def availability(self) -> SourceAvailability:
        if not self._api_key or not self._model:
            return SourceAvailability.REQUIRES_CONFIGURATION
        return SourceAvailability.AVAILABLE

    def configuration_summary(self) -> str:
        if not self._api_key or not self._model:
            return (
                "visual source 'alibaba' requires configuration: set "
                "ALIBABA_API_KEY and AVM_ALIBABA_IMAGE_MODEL (cost-bearing "
                "per completed generation)"
            )
        return (
            "visual source 'alibaba' (AI image generation via Alibaba "
            f"Cloud Model Studio; model '{self._model}'; cost-bearing per "
            "completed generation)"
        )

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(seconds)

    @staticmethod
    def _build_prompt(ctx: VisualSourceContext) -> str:
        """Compose the generation prompt from the scene's visual intent."""
        parts = [ctx.visual_prompt.strip()]
        if ctx.visual_style:
            parts.append(f"Style: {ctx.visual_style.strip()}")
        if ctx.camera_direction:
            parts.append(f"Camera: {ctx.camera_direction.strip()}")
        parts.append(
            "Single cinematic still frame; no text, no watermark, no captions."
        )
        return " ".join(part for part in parts if part)

    @staticmethod
    def _size_for(ctx: VisualSourceContext) -> str:
        explicit = (os.getenv(ALIBABA_IMAGE_SIZE_ENV, "") or "").strip()
        if explicit:
            return explicit
        return DEFAULT_ALIBABA_IMAGE_SIZE_MAP.get(
            ctx.aspect_ratio, "1024*1024"
        )

    def source(self, ctx: VisualSourceContext) -> VisualSourceResult:
        """Translate, submit, poll, download, materialize, verify.

        Bounded polling; a polling timeout is a truthful FAILURE that keeps
        the routing chain's normal fall-through (a billable DashScope task
        may still be in flight — the timeout message says so).
        """
        if self.availability() != SourceAvailability.AVAILABLE:
            return VisualSourceResult(
                status=VisualSourceStatus.REQUIRES_CONFIGURATION,
                scene_id=ctx.scene_id,
                provider_id=self.name,
                message=(
                    f"Scene '{ctx.scene_id}': Alibaba image generation is "
                    f"not configured ({self.configuration_summary()})."
                ),
            )

        prompt = self._build_prompt(ctx)
        size = self._size_for(ctx)
        body = {
            "model": self._model,
            "input": {"prompt": prompt},
            "parameters": {"size": size, "n": 1},
        }
        client = self._get_client()
        started = time.monotonic()
        try:
            submit_payload = client.submit_task(
                IMAGE_SUBMIT_PATH,
                body,
                operation="Alibaba image submission",
            )
        except ClassifiedProviderError as exc:
            return self._failure(ctx, exc.category, str(exc))
        except Exception as exc:  # noqa: BLE001 - conservative unknown
            return self._failure(
                ctx, FailureCategory.UNKNOWN,
                f"Alibaba image submission failed unexpectedly: {exc}",
            )
        task_id = str(
            (submit_payload.get("output") or {}).get("task_id") or ""
        )
        logger.info(
            "provider.request_submitted scene=%s provider=alibaba_image "
            "model=%s task_id=%s request_id_kind=task_id size=%s",
            ctx.scene_id, self._model, task_id, size,
        )

        payload: Dict[str, Any] = {}
        last_status: Optional[str] = None
        for attempt in range(self._poll_max_attempts):
            try:
                payload = client.poll_task(
                    task_id, operation="Alibaba image polling"
                )
            except ClassifiedProviderError as exc:
                return self._failure(
                    ctx, exc.category,
                    f"Alibaba image polling failed for task {task_id}: {exc}",
                )
            status = str(
                (payload.get("output") or {}).get("task_status") or ""
            )
            if status != last_status or attempt == 0:
                # Bounded polling visibility: first poll + status changes.
                logger.info(
                    "provider.poll_progress scene=%s task_id=%s "
                    "attempt=%d remote_status=%s",
                    ctx.scene_id, task_id, attempt + 1, status or "unknown",
                )
                last_status = status
            if status == "SUCCEEDED":
                return self._acquire(
                    ctx, task_id=task_id, payload=payload,
                    prompt=prompt, size=size, started=started,
                )
            if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                message = str(
                    (payload.get("output") or {}).get("message")
                    or "unknown task error"
                )
                return self._failure(
                    ctx, FailureCategory.TASK_FAILURE,
                    f"Alibaba image task {task_id} failed "
                    f"(status={status}; error={message})",
                    task_id=task_id,
                )
            self._sleep(self._poll_interval)

        return self._failure(
            ctx, FailureCategory.POLL_TIMEOUT,
            (
                f"Alibaba image task {task_id} did not complete within the "
                f"bounded polling window ({self._poll_max_attempts} "
                f"attempts x {self._poll_interval:.0f}s); the remote task "
                "is still in flight and was NOT resubmitted"
            ),
            task_id=task_id,
        )

    def _acquire(
        self,
        ctx: VisualSourceContext,
        *,
        task_id: str,
        payload: Dict[str, Any],
        prompt: str,
        size: str,
        started: float,
    ) -> VisualSourceResult:
        """Download the provider URL, materialize, and verify locally."""
        output = payload.get("output") or {}
        results = output.get("results") or []
        url = ""
        if results and isinstance(results[0], dict):
            url = str(results[0].get("url") or "")
        if not url:
            return self._failure(
                ctx, FailureCategory.RESPONSE_MALFORMED,
                f"Alibaba image task {task_id} succeeded without a result URL",
                task_id=task_id,
            )
        base = ctx.output_dir if ctx.output_dir else (
            self.output_dir or Path()
        )
        base.mkdir(parents=True, exist_ok=True)
        output_path = base / f"autosrc_{ctx.scene_id}.png"
        try:
            content = self._get_client().download_bytes(
                url, operation="Alibaba image download"
            )
            output_path.write_bytes(content)
        except ClassifiedProviderError as exc:
            output_path.unlink(missing_ok=True)
            return self._failure(
                ctx, exc.category,
                f"Alibaba image download failed for task {task_id}: {exc}",
                task_id=task_id,
            )
        except Exception as exc:  # noqa: BLE001 - materialization problem
            output_path.unlink(missing_ok=True)
            return self._failure(
                ctx, FailureCategory.ARTIFACT_VALIDATION,
                f"Scene '{ctx.scene_id}': could not materialize the "
                f"generated Alibaba image: {exc}",
                task_id=task_id,
            )

        if not output_path.is_file() or output_path.stat().st_size <= 0:
            output_path.unlink(missing_ok=True)
            return self._failure(
                ctx, FailureCategory.ARTIFACT_VALIDATION,
                f"Scene '{ctx.scene_id}': generated Alibaba image is "
                "missing or empty.",
                task_id=task_id,
            )
        try:
            verify_sourced_image(output_path)
        except Exception as exc:  # noqa: BLE001 - truthful artifact failure
            output_path.unlink(missing_ok=True)
            return self._failure(
                ctx, FailureCategory.ARTIFACT_VALIDATION,
                f"Scene '{ctx.scene_id}': generated Alibaba image failed "
                f"media verification: {exc}",
                task_id=task_id,
            )

        metadata: Dict[str, Any] = {
            "kind": "ai_generated",
            "source_type": "ai_generated",
            "provider": "alibaba",
            "model": self._model,
            "task_id": task_id,
            "size": size,
            "media_type": "image/png",
            "prompt": prompt[:500],
            "usage": payload.get("usage") or {},
            # Batch 1H: generic provider accounting — Alibaba image generation
            # is billable (PAYG application key) with provider usage
            # quantities; DashScope reports no monetary cost, so cost stays
            # unknown (never zero).
            "accounting": build_operation_accounting(
                provider="alibaba",
                model=self._model,
                request_id=task_id,
                request_id_kind="task_id",
                billing_status=BILLING_STATUS_BILLABLE,
                usage=payload.get("usage"),
                cost=None,
                cost_source=COST_SOURCE_NONE,
                currency=None,
            ),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
        logger.info(
            "provider.materialized scene=%s provider=alibaba_image "
            "model=%s task_id=%s size=%s latency_ms=%d bytes=%d",
            ctx.scene_id, self._model, task_id, size,
            int((time.monotonic() - started) * 1000),
            output_path.stat().st_size,
        )
        return VisualSourceResult.success(
            ctx.scene_id, self.name, output_path, metadata=metadata
        )
    @staticmethod
    def _failure(
        ctx: VisualSourceContext,
        category: str,
        message: str,
        *,
        task_id: Optional[str] = None,
    ) -> VisualSourceResult:
        logger.warning(
            "provider.operation_failed scene=%s provider=alibaba_image "
            "category=%s task_id=%s error=%s",
            ctx.scene_id, category, task_id, message,
        )
        """Truthful failure carrying its Batch 1C classification.

        ``fallback_eligible`` tells the routing layer whether continuing the
        configured chain is authorized; a non-eligible failure (invalid
        request, moderation, credential class, configuration) must stop the
        chain instead of silently re-sending the same request to OpenRouter.
        """
        metadata: Dict[str, Any] = {
            "failure_category": category,
            "fallback_eligible": is_fallback_eligible(category),
        }
        if task_id:
            metadata["task_id"] = task_id
        result = VisualSourceResult.failure(ctx.scene_id, "alibaba", message)
        result.metadata = metadata
        return result

