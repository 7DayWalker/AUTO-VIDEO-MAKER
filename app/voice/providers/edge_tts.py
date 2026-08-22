"""
AUTO Video Maker - Edge TTS Provider.

Microsoft Edge TTS provider using the edge-tts library.
"""

import asyncio
import logging
from pathlib import Path
from typing import List

from app.voice.exceptions import VoiceProviderError, VoiceNotFoundError
from app.voice.providers.base import BaseVoiceProvider, VoiceInfo

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 1.0
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class EdgeTTSProvider(BaseVoiceProvider):
    """Edge TTS provider with bounded retry handling."""

    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")

        self._edge_tts = None
        self._available = None
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    @property
    def name(self) -> str:
        return "edge_tts"

    def _ensure_imported(self) -> None:
        """Lazy import edge-tts to avoid import errors if not installed."""
        if self._edge_tts is None:
            try:
                import edge_tts

                self._edge_tts = edge_tts
                self._available = True
            except ImportError as exc:
                self._available = False
                raise VoiceProviderError(
                    self.name,
                    "edge-tts package not installed. Run: pip install edge-tts",
                ) from exc

    @staticmethod
    def _status_code(error: Exception) -> int | None:
        """Extract an HTTP status from aiohttp-style exceptions."""
        status = getattr(error, "status", None)
        if isinstance(status, int):
            return status
        response = getattr(error, "response", None)
        status = getattr(response, "status", None)
        return status if isinstance(status, int) else None

    @classmethod
    def _is_retryable(cls, error: Exception) -> bool:
        """Retry network/provider instability, not invalid configuration."""
        status = cls._status_code(error)
        if status is not None:
            return status in RETRYABLE_STATUS_CODES

        name = type(error).__name__.lower()
        module = type(error).__module__.lower()
        transient_names = (
            "timeout",
            "connection",
            "clientconnector",
            "serverdisconnected",
            "websocket",
        )
        return any(
            token in name or token in module
            for token in transient_names
        )

    async def _generate_once(
        self,
        text: str,
        output_path: Path,
        voice: str,
        rate: str,
        pitch: str,
        volume: str,
    ) -> None:
        communicate = self._edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            pitch=pitch,
            volume=volume,
        )
        await communicate.save(str(output_path))

    async def generate(
        self,
        text: str,
        output_path: Path,
        voice: str,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
    ) -> None:
        """Generate speech with bounded exponential backoff."""
        self._ensure_imported()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        last_error = None
        total_attempts = self.max_retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                output_path.unlink(missing_ok=True)
                logger.info(
                    "Generating speech with Edge TTS: voice=%s, attempt=%d/%d",
                    voice,
                    attempt,
                    total_attempts,
                )
                await self._generate_once(
                    text, output_path, voice, rate, pitch, volume
                )
                return
            except Exception as exc:
                last_error = exc
                error_msg = (
                    f"{type(exc).__name__}: {exc}"
                    f" (http_status={self._status_code(exc)!r})"
                )
                if "voice" in str(exc).lower() and "not found" in str(exc).lower():
                    raise VoiceNotFoundError(voice, self.name) from exc
                if attempt >= total_attempts or not self._is_retryable(exc):
                    raise VoiceProviderError(
                        self.name,
                        f"Generation failed after {attempt}/{total_attempts} "
                        f"attempt(s): {error_msg}",
                    ) from exc

                delay = self.backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Transient Edge TTS failure; retrying in %.2fs: %s",
                    delay,
                    error_msg,
                )
                await asyncio.sleep(delay)

        raise VoiceProviderError(
            self.name,
            f"Generation failed: {last_error}",
        ) from last_error

    async def list_voices(self, locale: str = None) -> List[VoiceInfo]:
        """List available Edge TTS voices."""
        self._ensure_imported()

        try:
            voices = await self._edge_tts.list_voices()
            result = []
            for voice in voices:
                voice_locale = voice.get("Locale", "")
                if locale and locale.lower() not in voice_locale.lower():
                    continue
                result.append(
                    VoiceInfo(
                        name=voice.get("FriendlyName", voice.get("ShortName", "")),
                        short_name=voice.get("ShortName", ""),
                        gender=voice.get("Gender"),
                        locale=voice_locale,
                        language=voice.get("Language"),
                    )
                )
            return result
        except Exception as exc:
            raise VoiceProviderError(
                self.name,
                f"Failed to list voices: {type(exc).__name__}: {exc}",
            ) from exc

    def is_available(self) -> bool:
        """Check if edge-tts is installed."""
        if self._available is not None:
            return self._available

        try:
            import edge_tts

            self._edge_tts = edge_tts
            self._available = True
            return True
        except ImportError:
            self._available = False
            return False