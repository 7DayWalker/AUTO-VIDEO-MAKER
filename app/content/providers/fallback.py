"""Batch 1C - classified Alibaba -> OpenRouter LLM fallback wrapper.

Implements the ONE authorized cross-provider policy at the existing
``BaseLLMProvider`` seam:

    REQUEST -> Alibaba (primary)
              -> SUCCESS? return
              -> classify failure
              -> fallback eligible? NO  -> truthful failure
                                    YES -> OpenRouter (fallback)
                                          -> SUCCESS? return
                                          -> truthful failure

Not implemented here (and never to be implemented in this layer):
parallel generation, quality comparison, provider ranking, or unconditional
"backup copy" calls. OpenRouter is a resilience provider, not a co-primary:
if Alibaba succeeds, OpenRouter is never called.

An unavailable/unconfigured primary is a CONFIGURATION problem and is NOT
fallback-eligible (invalid configuration must stay truthful); the same rule
applies to credential misuse and forbidden ``sk-sp-`` keys, which raise
before any network activity.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel

from app.content.providers.base import BaseLLMProvider, LLMResponse
from app.providers.fallback_policy import (
    FailureCategory,
    classify_provider_failure,
    is_fallback_eligible,
)

logger = logging.getLogger(__name__)


class FallbackLLMProvider(BaseLLMProvider):
    """Primary (Alibaba) with classified OpenRouter fallback.

    The wrapper is transparent about success provenance: a successful
    response records which provider produced it, whether fallback occurred,
    and (when it did) the classified reason — via ``LLMResponse.metadata``.
    """

    def __init__(
        self,
        primary: BaseLLMProvider,
        fallback: Optional[BaseLLMProvider] = None,
        *,
        fallback_enabled: bool = True,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallback_enabled = bool(fallback_enabled and fallback is not None)
        self.last_fallback_used: bool = False
        self.last_fallback_reason: Optional[str] = None

    @property
    def name(self) -> str:
        return self._primary.name

    @property
    def model(self) -> str:
        return self._primary.model

    @property
    def primary_provider(self) -> BaseLLMProvider:
        return self._primary

    @property
    def fallback_provider(self) -> Optional[BaseLLMProvider]:
        return self._fallback

    def _success_metadata(self, response: LLMResponse, **extra: Any) -> Dict[str, Any]:
        return {
            **(response.metadata or {}),
            "primary_provider": self._primary.name,
            "final_provider": response.provider,
            **extra,
        }

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs = dict(
            prompt=prompt,
            system_prompt=system_prompt,
            response_schema=response_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        try:
            response = self._primary.generate(**kwargs)
        except Exception as exc:  # noqa: BLE001 - classified below
            category = classify_provider_failure(exc)
            if not is_fallback_eligible(category) or not self._fallback_enabled:
                # Truthful failure: never hide AVM-side problems behind
                # OpenRouter.
                logger.error(
                    "Primary provider '%s' failed (%s, fallback not "
                    "eligible): %s", self._primary.name, category, exc,
                )
                raise
            started = time.monotonic()
            response = self._fallback.generate(**kwargs)
            latency_ms = int((time.monotonic() - started) * 1000)
            self.last_fallback_used = True
            self.last_fallback_reason = category
            response.metadata = self._success_metadata(
                response,
                fallback_used=True,
                fallback_reason=category,
                primary_model=self._primary.model,
                latency_ms=latency_ms,
            )
            logger.warning(
                "Primary provider '%s' failed (%s); fallback to '%s' used",
                self._primary.name, category, response.provider,
            )
            return response

        # Normal operation: Alibaba only. Provenance without fallback noise.
        self.last_fallback_used = False
        self.last_fallback_reason = None
        response.metadata = self._success_metadata(response, fallback_used=False)
        return response

    def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs = dict(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        try:
            response = self._primary.generate_json(**kwargs)
        except Exception as exc:  # noqa: BLE001 - classified below
            category = classify_provider_failure(exc)
            if not is_fallback_eligible(category) or not self._fallback_enabled:
                raise
            started = time.monotonic()
            response = self._fallback.generate_json(**kwargs)
            latency_ms = int((time.monotonic() - started) * 1000)
            self.last_fallback_used = True
            self.last_fallback_reason = category
            response.metadata = self._success_metadata(
                response,
                fallback_used=True,
                fallback_reason=category,
                primary_model=self._primary.model,
                latency_ms=latency_ms,
            )
            logger.warning(
                "Primary provider '%s' failed (%s); JSON fallback to '%s' used",
                self._primary.name, category, response.provider,
            )
            return response

        self.last_fallback_used = False
        self.last_fallback_reason = None
        response.metadata = self._success_metadata(response, fallback_used=False)
        return response


    def is_available(self) -> bool:
        """Available when the primary is configured.

        Deliberately NOT available when the primary is merely unconfigured:
        a missing Alibaba credential is an invalid-configuration problem, and
        silently serving it through OpenRouter would convert every
        un-configured deployment into a paid OpenRouter deployment. Ordinary
        Alibaba-only success therefore requires ONLY the Alibaba credential.
        """
        return self._primary.is_available()
