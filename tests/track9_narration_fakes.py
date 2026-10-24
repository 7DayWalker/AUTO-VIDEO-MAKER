"""Local evidence and adapter doubles for real Standard narration contracts."""

from __future__ import annotations

import json

from app.content.model_registry import get_model
from app.content.providers.base import BaseLLMProvider, LLMResponse
from app.production.models import SourceAsset, SourceTranscript, TranscriptSegment


def standard_evidence(duration: float = 120.0):
    asset = SourceAsset(
        asset_id="src-001", original_filename="source.mp4", stored_path="source/src-001.mp4",
        sha256="a" * 64, duration=duration, width=640, height=360, file_size=1024,
        ingested_at="2026-10-24T09:00:00+00:00",
    )
    transcript = SourceTranscript(
        asset_id=asset.asset_id, language="en", provider="fixture-transcript",
        segments=[
            TranscriptSegment(
                text=f"Source moment {index} describes an observed event with detailed explanation and context for this hermetic narration contract.",
                start=duration * index / 12, end=duration * (index + 1) / 12,
            )
            for index in range(12)
        ],
        created_at=asset.ingested_at, transcript_path="source/src-001.transcript.json",
    )
    return asset, transcript


def standard_response(duration: float = 120.0):
    return {
        "summary": "A preparation and a later discovery form a concise recap.",
        "beats": [
            {
                "order": 1, "title": "Preparation",
                "narration_text": "A careful preparation establishes the goal.",
                "source_refs": [{
                    "asset_id": "src-001", "start_seconds": duration / 12,
                    "end_seconds": duration * 2 / 12, "transcript_segment_ids": [1],
                }],
            },
            {
                "order": 2, "title": "Discovery",
                "narration_text": "A later discovery changes the outcome.",
                "source_refs": [{
                    "asset_id": "src-001", "start_seconds": duration * 9 / 12,
                    "end_seconds": duration * 10 / 12, "transcript_segment_ids": [9],
                }],
            },
        ],
    }


class StubNarrationProvider(BaseLLMProvider):
    """Exercises the real text/JSON adapter contract; never a mock-plan builder."""

    def __init__(self, *, name="fixture-adapter", model="fixture-wire", data=None, raw=None, error=None):
        self._name, self._model = name, model
        self.raw = raw if raw is not None else json.dumps(data if data is not None else standard_response())
        self.error = error
        self.calls = []
        self.json_calls = 0

    @property
    def name(self):
        return self._name

    @property
    def model(self):
        return self._model

    def is_available(self):
        return True

    def generate(self, prompt, system_prompt=None, response_schema=None, temperature=0.7, max_tokens=4096):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt, "max_tokens": max_tokens})
        if self.error is not None:
            raise self.error
        return LLMResponse(content=self.raw, provider=self.name, model=self.model)

    def generate_json(self, *args, **kwargs):
        self.json_calls += 1
        return super().generate_json(*args, **kwargs)


def install_standard_provider(monkeypatch, model_id="qwen-3.8-flash", **kwargs):
    """Keep canonical bindings/resolver real, replace only the provider factory."""
    from app.content import provider_registry

    binding = get_model(model_id).bindings[0]
    for key in binding.config_keys:
        monkeypatch.setenv(key, "https://example.invalid/v1" if key.endswith("BASE_URL") else "synthetic-narration-key")
    provider = StubNarrationProvider(name=binding.provider, model=binding.provider_model_id, **kwargs)
    factory_calls = []

    def factory(wire_id=None):
        factory_calls.append(wire_id)
        assert wire_id == binding.provider_model_id
        return provider

    monkeypatch.setitem(provider_registry._REGISTRY, binding.provider, factory)
    return provider, factory_calls, binding