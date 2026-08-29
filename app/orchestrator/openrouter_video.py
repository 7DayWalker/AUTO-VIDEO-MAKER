"""Phase 7E - generic OpenRouter video-generation gateway.

OpenRouter is the provider identity.  Individual model IDs are discovered from
OpenRouter's dedicated video-model catalog and are never represented as AVM
providers.  This module deliberately keeps the HTTP boundary small so tests can
replace it with a fake without making a network request.

The current OpenRouter contract is:

* ``GET /api/v1/videos/models`` for video capabilities and pricing metadata;
* ``POST /api/v1/videos`` for one asynchronous generation;
* ``GET`` of the returned ``polling_url`` for status;
* ``GET /api/v1/videos/{id}/content`` for the completed MP4.

No method in this module performs a request during import or construction.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urljoin

import requests

from app.assembly.audio_contract import AudioMediaFacts
from app.assembly.media import inspect_audio
from app.flow.scene_generator import validate_video_file
from app.flow.scene_models import SceneGenerationRequest, SceneGenerationResult


OPENROUTER_VIDEO_PROVIDER = "openrouter_video"
OPENROUTER_API_KEY_ENV = "AVM_OPENROUTER_API_KEY"
OPENROUTER_BASE_URL_ENV = "AVM_OPENROUTER_BASE_URL"
OPENROUTER_MODEL_ENV = "AVM_OPENROUTER_MODEL"
OPENROUTER_ALLOW_PAID_ENV = "AVM_OPENROUTER_ALLOW_PAID"
OPENROUTER_ALLOW_PAID_FALLBACK_ENV = "AVM_OPENROUTER_ALLOW_PAID_FALLBACK"
OPENROUTER_CACHE_PATH_ENV = "AVM_OPENROUTER_MODEL_CACHE"
OPENROUTER_CACHE_TTL_ENV = "AVM_OPENROUTER_MODEL_CACHE_TTL"
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_VIDEO_MODELS_PATH = "/videos/models"
OPENROUTER_VIDEOS_PATH = "/videos"
OPENROUTER_DEFAULT_CACHE_TTL = 900
OPENROUTER_DEFAULT_POLL_INTERVAL = 30.0
OPENROUTER_DEFAULT_POLL_MAX_ATTEMPTS = 20
OPENROUTER_FREE = "free"
OPENROUTER_PAID = "paid"
# Remote statuses that are terminal generation failures. ``expired`` is an
# unrecoverable remote state (distinct from an ordinary local polling timeout)
# and must never be resubmitted.
OPENROUTER_TERMINAL_FAILURE_STATUSES = frozenset(
    {"failed", "error", "cancelled", "canceled", "expired"}
)


class OpenRouterVideoError(RuntimeError):
    """Base class for truthful OpenRouter video failures."""


class OpenRouterVideoConfigurationError(OpenRouterVideoError):
    """Raised before a request when configuration or authorization is invalid."""


class OpenRouterVideoCapabilityError(OpenRouterVideoError):
    """Raised before a billable request when a model cannot satisfy the scene."""


class OpenRouterVideoHTTPError(OpenRouterVideoError):
    """Raised for an OpenRouter HTTP/API failure."""


def _as_list(value: Any) -> Optional[List[Any]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    return None


def _as_float(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _jsonable(value: Any) -> Any:
    """Convert common response objects into JSON-safe checkpoint data."""
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _truthy_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def classify_access(pricing_skus: Optional[Mapping[str, Any]], model_id: str = "") -> str:
    """Classify a catalog entry from live pricing, never from vendor names.

    An explicit ``:free`` variant is free.  Otherwise every advertised numeric
    SKU must be zero to classify as free.  Missing pricing is deliberately
    treated as paid/unknown-safe so it cannot accidentally cross the billing
    boundary.
    """
    if model_id.endswith(":free"):
        return OPENROUTER_FREE
    if not pricing_skus:
        return OPENROUTER_PAID
    prices: List[float] = []
    for raw in pricing_skus.values():
        number = _as_float(raw)
        if number is not None:
            prices.append(number)
    return OPENROUTER_FREE if prices and all(price == 0 for price in prices) else OPENROUTER_PAID


@dataclass(frozen=True)
class OpenRouterVideoModel:
    """Normalized dedicated video-model catalog entry."""

    model_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    created: Optional[int] = None
    supported_resolutions: Optional[tuple[str, ...]] = None
    supported_aspect_ratios: Optional[tuple[str, ...]] = None
    supported_sizes: Optional[tuple[str, ...]] = None
    supported_durations: Optional[tuple[int, ...]] = None
    supported_frame_images: Optional[tuple[str, ...]] = None
    generate_audio: Optional[bool] = None
    seed: Optional[bool] = None
    pricing_skus: Mapping[str, Any] = field(default_factory=dict)
    allowed_passthrough_parameters: tuple[str, ...] = ()
    access: str = OPENROUTER_PAID
    video_capability: bool = True
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def provider_vendor(self) -> Optional[str]:
        """Return vendor from the model ID without treating it as a provider."""
        return self.model_id.split("/", 1)[0] if "/" in self.model_id else None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OpenRouterVideoModel":
        model_id = str(data.get("id") or "").strip()
        if not model_id:
            raise ValueError("OpenRouter video model entry has no id")
        architecture = data.get("architecture")
        output_modalities = (
            data.get("output_modalities")
            or architecture.get("output_modalities")
            if isinstance(architecture, Mapping)
            else data.get("output_modalities")
        )
        video_capability = (
            True
            if output_modalities is None
            else "video" in {str(value).lower() for value in (output_modalities or [])}
        )
        return cls(
            model_id=model_id,
            name=data.get("name"),
            description=data.get("description"),
            created=_as_int(data.get("created")),
            supported_resolutions=tuple(str(v) for v in (_as_list(data.get("supported_resolutions")) or [])) or None,
            supported_aspect_ratios=tuple(str(v) for v in (_as_list(data.get("supported_aspect_ratios")) or [])) or None,
            supported_sizes=tuple(str(v) for v in (_as_list(data.get("supported_sizes")) or [])) or None,
            supported_durations=tuple(int(v) for v in (_as_list(data.get("supported_durations")) or []) if _as_int(v) is not None) or None,
            supported_frame_images=tuple(str(v) for v in (_as_list(data.get("supported_frame_images")) or [])) or None,
            generate_audio=(bool(data["generate_audio"]) if "generate_audio" in data and data["generate_audio"] is not None else None),
            seed=(bool(data["seed"]) if "seed" in data and data["seed"] is not None else None),
            pricing_skus=dict(data.get("pricing_skus") or data.get("pricing") or {}),
            allowed_passthrough_parameters=tuple(str(v) for v in (_as_list(data.get("allowed_passthrough_parameters")) or [])),
            access=classify_access(
                data.get("pricing_skus") or data.get("pricing"),
                model_id,
            ),
            video_capability=video_capability,
            raw=dict(data),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.model_id,
            "name": self.name,
            "description": self.description,
            "created": self.created,
            "supported_resolutions": list(self.supported_resolutions) if self.supported_resolutions is not None else None,
            "supported_aspect_ratios": list(self.supported_aspect_ratios) if self.supported_aspect_ratios is not None else None,
            "supported_sizes": list(self.supported_sizes) if self.supported_sizes is not None else None,
            "supported_durations": list(self.supported_durations) if self.supported_durations is not None else None,
            "supported_frame_images": list(self.supported_frame_images) if self.supported_frame_images is not None else None,
            "generate_audio": self.generate_audio,
            "seed": self.seed,
            "pricing_skus": _jsonable(self.pricing_skus),
            "allowed_passthrough_parameters": list(self.allowed_passthrough_parameters),
            "access": self.access,
            "video_capability": self.video_capability,
        }


def parse_video_model_catalog(payload: Mapping[str, Any]) -> List[OpenRouterVideoModel]:
    """Parse and retain only entries returned by the dedicated video endpoint."""
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, list):
        raise ValueError("OpenRouter video model catalog response has no data array")
    models: List[OpenRouterVideoModel] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        try:
            models.append(OpenRouterVideoModel.from_dict(item))
        except ValueError:
            continue
    return models


def filter_video_models(models: Iterable[OpenRouterVideoModel]) -> List[OpenRouterVideoModel]:
    """Keep only models represented by the dedicated video catalog."""
    return [model for model in models if model.model_id and model.video_capability]


def _requested_resolution(request: SceneGenerationRequest, config: Mapping[str, Any]) -> str:
    explicit = config.get("resolution")
    if explicit:
        return str(explicit)
    width = config.get("resolution_width")
    height = config.get("resolution_height")
    if width and height:
        return f"{width}x{height}"
    # AVM's current scene configuration only carries an aspect ratio.  The
    # provider default is deliberately explicit and is validated against the
    # live model catalog before submission.
    return "720p"


def _model_supports_resolution(model: OpenRouterVideoModel, resolution: str) -> bool:
    """Match either the API's named resolution or an exact supported size."""
    return bool(
        (model.supported_sizes and resolution in model.supported_sizes)
        or (model.supported_resolutions and resolution in model.supported_resolutions)
    )


def _matches_aspect_ratio(width: Any, height: Any, expected: str) -> bool:
    try:
        left, right = (int(value) for value in expected.split(":", 1))
        return int(width) * right == int(height) * left
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        return False


def capability_errors(
    model: OpenRouterVideoModel,
    request: SceneGenerationRequest,
    *,
    resolution: Optional[str] = None,
    require_audio: bool = False,
    frame_type: Optional[str] = None,
) -> List[str]:
    """Return all known incompatibilities before the billable boundary."""
    errors: List[str] = []
    if not model.model_id:
        errors.append("model has no OpenRouter model ID")
    ratio = request.config.aspect_ratio
    if model.supported_aspect_ratios is None:
        errors.append("model does not advertise supported aspect ratios")
    elif ratio not in model.supported_aspect_ratios:
        errors.append(f"aspect ratio {ratio} is not supported (supported: {list(model.supported_aspect_ratios)})")
    duration = int(request.config.duration)
    if model.supported_durations is None:
        errors.append("model does not advertise supported durations")
    elif duration not in model.supported_durations:
        errors.append(f"duration {duration}s is not supported (supported: {list(model.supported_durations)})")
    requested_resolution = resolution or _requested_resolution(request, {})
    if not _model_supports_resolution(model, requested_resolution):
        advertised = list(model.supported_sizes or model.supported_resolutions or ())
        errors.append(f"resolution {requested_resolution} is not supported (supported: {advertised})")
    if require_audio and model.generate_audio is not True:
        errors.append("native audio was requested but the model does not advertise generate_audio support")
    if frame_type:
        if model.supported_frame_images is None or frame_type not in model.supported_frame_images:
            errors.append(f"reference frame type {frame_type} is not supported (supported: {list(model.supported_frame_images or ())})")
    return errors


def compatible_models(
    models: Iterable[OpenRouterVideoModel],
    request: SceneGenerationRequest,
    *,
    resolution: Optional[str] = None,
    require_audio: bool = False,
    frame_type: Optional[str] = None,
) -> List[OpenRouterVideoModel]:
    return [
        model for model in filter_video_models(models)
        if not capability_errors(
            model, request, resolution=resolution, require_audio=require_audio, frame_type=frame_type
        )
    ]


def select_video_model(
    models: Iterable[OpenRouterVideoModel],
    request: SceneGenerationRequest,
    *,
    explicit_model: Optional[str] = None,
    allow_paid: bool = False,
    resolution: Optional[str] = None,
    require_audio: bool = False,
    frame_type: Optional[str] = None,
) -> OpenRouterVideoModel:
    """Deterministic explicit-choice/free-first selection.

    A selected paid model is an explicit authorization only when
    ``allow_paid=True``.  Automatic selection never chooses paid models.
    """
    all_models = filter_video_models(models)
    by_id = {model.model_id: model for model in all_models}
    if explicit_model:
        model = by_id.get(explicit_model)
        if model is None:
            raise OpenRouterVideoCapabilityError(
                f"OpenRouter video model '{explicit_model}' was not found in the current video catalog"
            )
        errors = capability_errors(model, request, resolution=resolution, require_audio=require_audio, frame_type=frame_type)
        if errors:
            raise OpenRouterVideoCapabilityError(f"Model '{model.model_id}' is incompatible: {'; '.join(errors)}")
        if model.access == OPENROUTER_PAID and not allow_paid:
            raise OpenRouterVideoConfigurationError(
                f"OpenRouter model '{model.model_id}' is paid; set AVM_OPENROUTER_ALLOW_PAID=true or explicitly authorize paid access"
            )
        return model
    compatible = compatible_models(
        all_models,
        request,
        resolution=resolution,
        require_audio=require_audio,
        frame_type=frame_type,
    )
    free = sorted(
        (model for model in compatible if model.access == OPENROUTER_FREE),
        key=lambda item: item.model_id,
    )
    if free:
        return free[0]
    # Automatic paid selection is intentionally never a fallback.  A paid
    # model must be selected explicitly; ``allow_paid_fallback`` is kept as
    # a separately persisted safety setting for a future cost-ceiling
    # implementation and does not authorize surprise spending here.
    if compatible:
        if allow_paid:
            raise OpenRouterVideoConfigurationError(
                "No compatible free OpenRouter video model is available; "
                "select a compatible paid model explicitly before generation"
            )
        raise OpenRouterVideoConfigurationError(
            "No compatible free OpenRouter video model is available; "
            "compatible models are paid and paid access is disabled"
        )
    raise OpenRouterVideoCapabilityError("No OpenRouter video model satisfies the requested capabilities")


def build_video_request(
    model: OpenRouterVideoModel,
    request: SceneGenerationRequest,
    *,
    resolution: Optional[str] = None,
    generate_audio: Optional[bool] = None,
    frame_images: Optional[Sequence[Mapping[str, Any]]] = None,
    seed: Optional[int] = None,
    passthrough: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Translate normalized AVM intent to the current OpenRouter schema."""
    requested_resolution = resolution or _requested_resolution(request, {})
    frame_type = None
    if frame_images:
        frame_type = frame_images[0].get("frame_type")
    errors = capability_errors(
        model,
        request,
        resolution=requested_resolution,
        require_audio=bool(generate_audio),
        frame_type=frame_type,
    )
    if errors:
        raise OpenRouterVideoCapabilityError(
            f"Model '{model.model_id}' is incompatible: {'; '.join(errors)}"
        )
    body: Dict[str, Any] = {
        "model": model.model_id,
        "prompt": request.visual_prompt,
        "duration": int(request.config.duration),
        "resolution": requested_resolution,
        "aspect_ratio": request.config.aspect_ratio,
    }
    if generate_audio is not None:
        if generate_audio and model.generate_audio is not True:
            raise OpenRouterVideoCapabilityError(
                f"Model '{model.model_id}' does not advertise native audio support"
            )
        body["generate_audio"] = bool(generate_audio)
    if frame_images:
        for item in frame_images:
            item_type = item.get("frame_type")
            if (
                not item_type
                or model.supported_frame_images is None
                or item_type not in model.supported_frame_images
            ):
                raise OpenRouterVideoCapabilityError(
                    f"Model '{model.model_id}' does not advertise the requested reference frame type"
                )
        body["frame_images"] = [_jsonable(item) for item in frame_images]
    if seed is not None:
        if model.seed is not True:
            raise OpenRouterVideoCapabilityError(f"Model '{model.model_id}' does not advertise seed support")
        body["seed"] = int(seed)
    if passthrough:
        unsupported = sorted(set(passthrough) - set(model.allowed_passthrough_parameters))
        if unsupported:
            raise OpenRouterVideoCapabilityError(
                f"Model '{model.model_id}' does not allow passthrough parameters: {unsupported}"
            )
        # OpenRouter's current request contract places model-specific options at
        # the top level. Only catalog-advertised keys may cross this boundary.
        body.update({key: _jsonable(value) for key, value in passthrough.items()})
    return body


class OpenRouterHTTPClient:
    """Small requests-based client for the documented OpenRouter endpoints."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = OPENROUTER_DEFAULT_BASE_URL,
        timeout: float = 60.0,
        session: Any = None,
        user_agent: str = "AUTO-VIDEO-MAKER/Phase-7E",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.user_agent = user_agent

    def _headers(self, *, json_body: bool = False) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    @staticmethod
    def _response_json(response: Any) -> Mapping[str, Any]:
        try:
            data = response.json()
        except Exception:
            data = {}
        if not isinstance(data, Mapping):
            return {}
        return data

    def _raise_for_status(self, response: Any, operation: str) -> None:
        if getattr(response, "ok", False):
            return
        payload = self._response_json(response)
        error = payload.get("error") if isinstance(payload, Mapping) else None
        detail = error.get("message") if isinstance(error, Mapping) else error
        if not detail:
            detail = getattr(response, "text", "HTTP request failed")
        raise OpenRouterVideoHTTPError(f"OpenRouter {operation} failed (HTTP {getattr(response, 'status_code', '?')}): {detail}")

    def list_video_models(self) -> Mapping[str, Any]:
        response = self.session.get(
            self._url(OPENROUTER_VIDEO_MODELS_PATH),
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._raise_for_status(response, "video model discovery")
        return self._response_json(response)

    def submit_video(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        response = self.session.post(
            self._url(OPENROUTER_VIDEOS_PATH),
            headers=self._headers(json_body=True),
            json=dict(body),
            timeout=self.timeout,
        )
        self._raise_for_status(response, "video submission")
        return self._response_json(response)

    def get_generation(self, polling_url: str) -> Mapping[str, Any]:
        response = self.session.get(
            polling_url,
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._raise_for_status(response, "video status polling")
        return self._response_json(response)

    def get_video_content(self, video_id: str, content_url: Optional[str] = None) -> bytes:
        path = f"{OPENROUTER_VIDEOS_PATH}/{video_id}/content"
        # The API's documented content operation is the stable route. A
        # completed status may also expose a direct content URL; use it only as
        # returned by OpenRouter and otherwise use the documented route.
        url = content_url or self._url(path)
        response = self.session.get(
            url,
            headers={**self._headers(), "Accept": "video/mp4"},
            timeout=self.timeout,
        )
        self._raise_for_status(response, "video content download")
        content_type = str(
            getattr(response, "headers", {}).get("Content-Type", "")
        ).lower()
        if content_type and not (
            content_type.startswith("video/")
            or "mp4" in content_type
            or "octet-stream" in content_type
        ):
            raise OpenRouterVideoHTTPError(
                f"OpenRouter video content download returned unexpected media type '{content_type}'"
            )
        content = getattr(response, "content", b"")
        if not content:
            raise OpenRouterVideoHTTPError("OpenRouter video content download returned an empty body")
        return bytes(content)


class OpenRouterVideoCatalog:
    """Deterministic disk cache for metadata only."""

    def __init__(self, client: OpenRouterHTTPClient, *, cache_path: Optional[Path] = None, ttl_seconds: Optional[int] = None, clock: Callable[[], float] = time.time):
        self.client = client
        self.cache_path = Path(cache_path) if cache_path else None
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else int(os.getenv(OPENROUTER_CACHE_TTL_ENV, OPENROUTER_DEFAULT_CACHE_TTL))
        self.clock = clock

    def _read_cache(self) -> Optional[List[OpenRouterVideoModel]]:
        if self.cache_path is None or not self.cache_path.is_file():
            return None
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            captured = float(payload.get("captured_at", 0))
            if self.clock() - captured > self.ttl_seconds:
                return None
            return parse_video_model_catalog({"data": payload.get("data", [])})
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write_cache(self, models: Sequence[OpenRouterVideoModel]) -> None:
        if self.cache_path is None:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"captured_at": self.clock(), "data": [model.to_dict() for model in models]}
        temp = self.cache_path.with_name(self.cache_path.name + ".part")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temp, self.cache_path)

    def models(self, *, refresh: bool = False) -> List[OpenRouterVideoModel]:
        if not refresh:
            cached = self._read_cache()
            if cached is not None:
                return cached
        models = parse_video_model_catalog(self.client.list_video_models())
        self._write_cache(models)
        return models


def _extract_status(payload: Mapping[str, Any]) -> str:
    return str(payload.get("status") or "").strip().lower()


def _extract_error(payload: Mapping[str, Any]) -> Optional[str]:
    value = payload.get("error") or payload.get("errors")
    if isinstance(value, Mapping):
        return str(value.get("message") or value.get("detail") or value)
    return str(value) if value else None


def _extract_video_url(payload: Mapping[str, Any]) -> Optional[str]:
    for key in ("video_url", "download_url", "content_url"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    video = payload.get("video") or payload.get("output")
    if isinstance(video, Mapping):
        for key in ("url", "video_url", "download_url", "content_url"):
            value = video.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _extract_usage(payload: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    usage = payload.get("usage") or payload.get("cost")
    return usage if isinstance(usage, Mapping) else None


class OpenRouterVideoVisualProvider:
    """Generic provider for every model in OpenRouter's video catalog."""

    name = OPENROUTER_VIDEO_PROVIDER
    requires_browser = False
    classification = "openrouter_video"
    asset_dir = None
    _poll_interval_seconds = OPENROUTER_DEFAULT_POLL_INTERVAL
    _poll_max_attempts = OPENROUTER_DEFAULT_POLL_MAX_ATTEMPTS

    def __init__(
        self,
        output_dir: Path,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        allow_paid: Optional[bool] = None,
        allow_paid_fallback: Optional[bool] = None,
        resolution: Optional[str] = None,
        poll_interval: Optional[float] = None,
        poll_max_attempts: Optional[int] = None,
        http_client: Any = None,
        catalog: Any = None,
        cache_path: Optional[Path] = None,
        generate_audio: Optional[bool] = None,
    ):
        self.output_dir = Path(output_dir)
        self.model = model or os.getenv(OPENROUTER_MODEL_ENV) or None
        self._api_key = api_key if api_key is not None else os.getenv(OPENROUTER_API_KEY_ENV, "")
        self.base_url = base_url or os.getenv(OPENROUTER_BASE_URL_ENV, OPENROUTER_DEFAULT_BASE_URL)
        self.allow_paid = _truthy_env(OPENROUTER_ALLOW_PAID_ENV) if allow_paid is None else bool(allow_paid)
        self.allow_paid_fallback = _truthy_env(OPENROUTER_ALLOW_PAID_FALLBACK_ENV) if allow_paid_fallback is None else bool(allow_paid_fallback)
        self.resolution = resolution
        self.generate_audio = bool(generate_audio) if generate_audio is not None else False
        self._poll_interval_seconds = poll_interval if poll_interval is not None else self._poll_interval_seconds
        self._poll_max_attempts = poll_max_attempts if poll_max_attempts is not None else self._poll_max_attempts
        self._client = http_client
        self._catalog = catalog
        self._cache_path = cache_path

    def resolve_asset(self, request: SceneGenerationRequest) -> Optional[Path]:
        return None

    def _get_client(self) -> OpenRouterHTTPClient:
        if not self._api_key:
            raise OpenRouterVideoConfigurationError(
                f"OpenRouter video provider is not configured; set {OPENROUTER_API_KEY_ENV}"
            )
        if self._client is None:
            self._client = OpenRouterHTTPClient(api_key=self._api_key, base_url=self.base_url)
        return self._client

    def _get_catalog(self) -> OpenRouterVideoCatalog:
        if self._catalog is None:
            self._catalog = OpenRouterVideoCatalog(
                self._get_client(),
                cache_path=self._cache_path,
            )
        return self._catalog

    def is_available(self) -> bool:
        """Configuration-only availability; discovery is explicit and safe."""
        return bool(self._api_key)

    def discover_models(self, *, refresh: bool = False) -> List[OpenRouterVideoModel]:
        """Return the current or unexpired cached video catalog safely."""
        return self._get_catalog().models(refresh=refresh)

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(seconds)

    def _fail(self, request: SceneGenerationRequest, error: str, **verification: Any) -> SceneGenerationResult:
        result = SceneGenerationResult.failure(request.scene_id, request.sequence, error)
        result.verification.update({"provider": OPENROUTER_VIDEO_PROVIDER, **_jsonable(verification)})
        return result

    def _checkpoint(self, callback: Optional[Callable[[Dict[str, Any]], None]], payload: Dict[str, Any]) -> None:
        if callback is not None:
            callback({"provider": OPENROUTER_VIDEO_PROVIDER, **payload})

    def _model_and_request(
        self,
        request: SceneGenerationRequest,
        *,
        require_audio: bool = False,
        refresh_catalog: bool = False,
    ) -> tuple[OpenRouterVideoModel, Dict[str, Any]]:
        models = self._get_catalog().models(refresh=refresh_catalog)
        model = select_video_model(
            models,
            request,
            explicit_model=self.model,
            allow_paid=self.allow_paid,
            resolution=self.resolution,
            require_audio=require_audio,
        )
        body = build_video_request(
            model,
            request,
            resolution=self.resolution,
            generate_audio=True if require_audio else None,
        )
        return model, body

    def _result_from_file(
        self,
        request: SceneGenerationRequest,
        model: OpenRouterVideoModel,
        output_path: Path,
        *,
        job_id: Optional[str],
        access: str,
        submit_payload: Mapping[str, Any],
        status_payload: Mapping[str, Any],
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]],
    ) -> SceneGenerationResult:
        validation = validate_video_file(output_path, request.config.aspect_ratio)
        if not validation.get("valid"):
            return self._fail(
                request,
                f"OpenRouter video output failed scene validation: {validation.get('error')}",
                model=model.model_id,
                access=access,
                job_id=job_id,
            )
        if not _matches_aspect_ratio(
            validation.get("width"),
            validation.get("height"),
            request.config.aspect_ratio,
        ):
            return self._fail(
                request,
                f"OpenRouter video output failed exact aspect-ratio validation: "
                f"expected {request.config.aspect_ratio}, got "
                f"{validation.get('width')}:{validation.get('height')}",
                model=model.model_id,
                access=access,
                job_id=job_id,
            )
        try:
            facts = inspect_audio(output_path)
        except Exception:
            facts = AudioMediaFacts.from_probe_data(validation)
        actual_cost = status_payload.get("actual_cost") or status_payload.get("cost")
        usage = _extract_usage(status_payload) or _extract_usage(submit_payload)
        verification = {
            "provider": OPENROUTER_VIDEO_PROVIDER,
            "model": model.model_id,
            "access": access,
            "access_route": access,
            "classification": access,
            "job_id": job_id,
            "polling_url": submit_payload.get("polling_url"),
            "estimated_cost": _jsonable(submit_payload.get("estimated_cost") or submit_payload.get("cost_estimate")),
            "actual_cost": _jsonable(actual_cost),
            "usage": _jsonable(usage),
            "pricing_skus": _jsonable(model.pricing_skus),
            "audio_facts": facts.model_dump(),
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
            warnings=[f"model={model.model_id}", f"access={access}"],
            audio_facts=facts,
        )
        result.verification.update(verification)
        self._checkpoint(
            checkpoint_callback,
            {
                "scene_id": request.scene_id,
                "status": "completed",
                "job_id": job_id,
                "polling_url": submit_payload.get("polling_url"),
                "model": model.model_id,
                "access": access,
                "access_route": access,
                "generation_cost": None,
                "estimated_cost": verification["estimated_cost"],
                "actual_cost": verification["actual_cost"],
                "usage": verification["usage"],
                "pricing_skus": verification["pricing_skus"],
                "file_path": str(output_path),
                "validation": {**validation, "audio_facts": facts.model_dump()},
                "audio_facts": facts.model_dump(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return result

    def _download_and_materialize(
        self,
        request: SceneGenerationRequest,
        model: OpenRouterVideoModel,
        *,
        job_id: str,
        submit_payload: Mapping[str, Any],
        status_payload: Mapping[str, Any],
        access: str,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]],
    ) -> SceneGenerationResult:
        output_path = self.output_dir / f"{request.scene_id}.mp4"
        temp_path = output_path.with_name(output_path.name + ".part")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        try:
            payload = self._get_client().get_video_content(
                job_id,
                _extract_video_url(status_payload),
            )
            if not payload:
                raise OpenRouterVideoHTTPError("OpenRouter returned an empty video body")
            temp_path.write_bytes(payload)
            if temp_path.stat().st_size <= 0:
                raise OpenRouterVideoHTTPError("OpenRouter returned an empty video file")
            result = self._result_from_file(
                request,
                model,
                temp_path,
                job_id=job_id,
                access=access,
                submit_payload=submit_payload,
                status_payload=status_payload,
                checkpoint_callback=None,
            )
            if not result.success:
                temp_path.unlink(missing_ok=True)
                return result
            os.replace(temp_path, output_path)
            result.file_path = output_path
            result.file_size = output_path.stat().st_size
            final_validation = validate_video_file(output_path, request.config.aspect_ratio)
            if not final_validation.get("valid"):
                output_path.unlink(missing_ok=True)
                return self._fail(
                    request,
                    f"OpenRouter materialized output failed scene validation: {final_validation.get('error')}",
                    model=model.model_id,
                    access=access,
                    job_id=job_id,
                )
            # Re-emit completion with the materialized final path.
            self._checkpoint(
                checkpoint_callback,
                {
                    "scene_id": request.scene_id,
                    "status": "completed",
                    "job_id": job_id,
                    "polling_url": submit_payload.get("polling_url"),
                    "model": model.model_id,
                    "access": access,
                    "access_route": access,
                    "generation_cost": None,
                    "estimated_cost": result.verification.get("estimated_cost"),
                    "actual_cost": result.verification.get("actual_cost"),
                    "usage": result.verification.get("usage"),
                    "pricing_skus": result.verification.get("pricing_skus"),
                    "file_path": str(output_path),
                    "validation": {
                        **final_validation,
                        "audio_facts": result.audio_facts.model_dump() if result.audio_facts else None,
                    },
                    "audio_facts": result.audio_facts.model_dump() if result.audio_facts else None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return result
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            return self._fail(request, f"OpenRouter video download/materialization failed: {exc}", model=model.model_id, access=access, job_id=job_id)

    def _poll_and_download(
        self,
        request: SceneGenerationRequest,
        model: OpenRouterVideoModel,
        submit_payload: Mapping[str, Any],
        *,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]],
    ) -> SceneGenerationResult:
        job_id = str(submit_payload.get("id") or "")
        polling_url = submit_payload.get("polling_url")
        if not job_id or not polling_url:
            return self._fail(request, "OpenRouter submission did not return both job id and polling_url", model=model.model_id)
        access = model.access
        self._checkpoint(
            checkpoint_callback,
            {
                "scene_id": request.scene_id,
                "status": "generation_submitted",
                "job_id": job_id,
                "polling_url": polling_url,
                "model": model.model_id,
                "access": access,
                "estimated_cost": _jsonable(submit_payload.get("estimated_cost") or submit_payload.get("cost_estimate")),
                "actual_cost": _jsonable(submit_payload.get("actual_cost")),
                "usage": _jsonable(_extract_usage(submit_payload)),
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        payload = dict(submit_payload)
        for attempt in range(self._poll_max_attempts):
            status = _extract_status(payload)
            if status in {"completed", "succeeded", "success"}:
                return self._download_and_materialize(
                    request, model, job_id=job_id, submit_payload=submit_payload,
                    status_payload=payload, access=access, checkpoint_callback=checkpoint_callback,
                )
            if status in OPENROUTER_TERMINAL_FAILURE_STATUSES:
                error = _extract_error(payload) or "unknown error"
                self._checkpoint(
                    checkpoint_callback,
                    {
                        "scene_id": request.scene_id,
                        "status": "failed",
                        "job_id": job_id,
                        "polling_url": polling_url,
                        "model": model.model_id,
                        "access": access,
                        "remote_status": status,
                        "error": error,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                if status == "expired":
                    message = (
                        f"OpenRouter video job expired (status=expired; error={error}); "
                        "the remote generation is unrecoverable and was not resubmitted"
                    )
                else:
                    message = f"OpenRouter video job failed (status={status}; error={error})"
                return self._fail(request, message, model=model.model_id, access=access, job_id=job_id)
            self._checkpoint(
                checkpoint_callback,
                {"scene_id": request.scene_id, "status": "generating", "job_id": job_id, "polling_url": polling_url, "model": model.model_id, "access": access, "remote_status": status, "updated_at": datetime.now(timezone.utc).isoformat()},
            )
            if attempt == self._poll_max_attempts - 1:
                self._checkpoint(
                    checkpoint_callback,
                    {
                        "scene_id": request.scene_id,
                        "status": "timeout",
                        "job_id": job_id,
                        "polling_url": polling_url,
                        "model": model.model_id,
                        "access": access,
                        "remote_status": status,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                return self._fail(request, f"OpenRouter video job polling timed out after {self._poll_max_attempts} polls; remote job {job_id} was not resubmitted", model=model.model_id, access=access, job_id=job_id)
            self._sleep(self._poll_interval_seconds)
            payload = dict(self._get_client().get_generation(str(polling_url)))
        return self._fail(request, "OpenRouter video polling ended unexpectedly", model=model.model_id, access=access, job_id=job_id)

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
            return self._fail(request, f"OpenRouter video provider is not configured; set {OPENROUTER_API_KEY_ENV}")
        output_path = self.output_dir / f"{request.scene_id}.mp4"
        if output_path.is_file():
            existing_validation = validate_video_file(output_path, request.config.aspect_ratio)
            if existing_validation.get("valid"):
                facts = AudioMediaFacts.from_probe_data(existing_validation)
                result = SceneGenerationResult.success_result(
                    request.scene_id,
                    request.sequence,
                    output_path,
                    existing_validation.get("file_size", output_path.stat().st_size),
                    existing_validation.get("duration", 0.0),
                    existing_validation.get("width", 0),
                    existing_validation.get("height", 0),
                    existing_validation.get("codec", "unknown"),
                    generation_cost=None,
                    downloaded_at=datetime.now(timezone.utc).isoformat(),
                    audio_facts=facts,
                )
                result.verification.update(
                    {
                        "provider": OPENROUTER_VIDEO_PROVIDER,
                        "model": self.model,
                        "access": "reused",
                        "reuse": "completed_valid_local_scene",
                    }
                )
                return result
        try:
            require_audio = self.generate_audio
            model, body = self._model_and_request(request, require_audio=require_audio)
            if dry_run:
                return SceneGenerationResult.dry_run(
                    request.scene_id,
                    request.sequence,
                    generation_cost=None,
                    verification={"provider": OPENROUTER_VIDEO_PROVIDER, "model": model.model_id, "access": model.access, "request": body},
                )
            if model.access == OPENROUTER_PAID and not self.allow_paid:
                return self._fail(request, f"OpenRouter model '{model.model_id}' is paid and paid generation is disabled; no generation was submitted", model=model.model_id, access=model.access)
            response = self._get_client().submit_video(body)
            return self._poll_and_download(request, model, response, checkpoint_callback=checkpoint_callback)
        except OpenRouterVideoError as exc:
            return self._fail(request, str(exc), model=self.model, access=None)
        except Exception as exc:
            return self._fail(request, f"OpenRouter video generation failed: {exc}", model=self.model, access=None)

    def resume_scene(
        self,
        request: SceneGenerationRequest,
        checkpoint: Dict[str, Any],
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        **_: Any,
    ) -> SceneGenerationResult:
        """Recover an already-submitted job; never submit a second job."""
        output_path = Path(checkpoint.get("file_path") or self.output_dir / f"{request.scene_id}.mp4")
        if checkpoint.get("status") in {"completed", "validated"} and output_path.is_file():
            validation = validate_video_file(output_path, request.config.aspect_ratio)
            if validation.get("valid"):
                facts = AudioMediaFacts.from_probe_data(validation)
                result = SceneGenerationResult.success_result(
                    request.scene_id, request.sequence, output_path,
                    validation.get("file_size", output_path.stat().st_size),
                    validation.get("duration", 0.0), validation.get("width", 0),
                    validation.get("height", 0), validation.get("codec", "unknown"),
                    generation_cost=None, downloaded_at=datetime.now(timezone.utc).isoformat(), audio_facts=facts,
                )
                result.verification.update(
                    {
                        "provider": OPENROUTER_VIDEO_PROVIDER,
                        "model": checkpoint.get("model", self.model),
                        "access": checkpoint.get("access"),
                        "access_route": checkpoint.get("access_route", checkpoint.get("access")),
                        "job_id": checkpoint.get("job_id"),
                        "polling_url": checkpoint.get("polling_url"),
                        "estimated_cost": checkpoint.get("estimated_cost"),
                        "actual_cost": checkpoint.get("actual_cost"),
                        "usage": checkpoint.get("usage"),
                        "pricing_skus": checkpoint.get("pricing_skus"),
                    }
                )
                return result
        job_id = checkpoint.get("job_id")
        polling_url = checkpoint.get("polling_url")
        if not job_id or not polling_url:
            return self._fail(request, "OpenRouter checkpoint has no recoverable remote job ID/polling URL; deliberate new generation is required", model=checkpoint.get("model", self.model), access=checkpoint.get("access"))
        # A checkpoint that already observed a terminal remote failure (failed,
        # cancelled, or expired) cannot be recovered by polling and must never
        # be re-submitted. Fail truthfully instead of entering a new poll loop.
        # ``remote_status`` is preferred because it carries the actual
        # OpenRouter terminal state (e.g. ``expired``); ``status`` is the
        # AVM-level checkpoint state.
        terminal_status = checkpoint.get("remote_status") or checkpoint.get("status")
        if terminal_status in OPENROUTER_TERMINAL_FAILURE_STATUSES:
            error = checkpoint.get("error") or "remote generation ended in a terminal failure"
            return self._fail(
                request,
                f"OpenRouter remote job {job_id} ended in a terminal state ({terminal_status}); "
                f"it was not recovered and not resubmitted ({error})",
                model=checkpoint.get("model", self.model),
                access=checkpoint.get("access"),
                job_id=job_id,
            )
        model_id = checkpoint.get("model") or self.model
        try:
            models = self._get_catalog().models()
            model = next((item for item in models if item.model_id == model_id), None)
            if model is None:
                return self._fail(request, f"OpenRouter remote job {job_id} cannot be resumed because model '{model_id}' is no longer in the current catalog", model=model_id, access=checkpoint.get("access"), job_id=job_id)
            payload = dict(self._get_client().get_generation(str(polling_url)))
            return self._poll_and_download(request, model, {**payload, "id": job_id, "polling_url": polling_url}, checkpoint_callback=checkpoint_callback)
        except Exception as exc:
            return self._fail(request, f"OpenRouter remote job {job_id} could not be recovered without resubmission: {exc}", model=model_id, access=checkpoint.get("access"), job_id=job_id)

    def resume_submitted_scene(
        self,
        request: SceneGenerationRequest,
        checkpoint: Dict[str, Any],
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SceneGenerationResult:
        """Compatibility alias used by the existing shared provider protocol."""
        return self.resume_scene(request, checkpoint, checkpoint_callback)


def default_openrouter_cache_path() -> Optional[Path]:
    value = os.getenv(OPENROUTER_CACHE_PATH_ENV)
    return Path(value) if value else None
