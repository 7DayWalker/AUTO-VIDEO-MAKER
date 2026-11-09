"""Strict intent and execution contracts; no providers or media work at import."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

MODEL_ID = "gpt-6.1-sol"
OUTPUT_TOKENS = 16384
Mode = Literal["narrated", "raw_highlight"]
Framing = Literal["cinema_canvas", "center_crop"]


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RepurposeError(ValueError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


class BatchRequest(Contract):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,100}$")
    confirm: Literal[True]
    source_asset_id: str = Field(pattern=r"^src-\d{3}$")
    source_unit_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    evidence_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: Mode = "narrated"
    count: StrictInt = Field(default=3, ge=3, le=10)
    target_seconds: float = Field(default=45, ge=30, le=60)
    framing: Framing = "cinema_canvas"
    model_id: Literal["gpt-6.1-sol"] = MODEL_ID
    cache_only: Literal[True] = True
    allow_overlap: StrictBool = False

    @model_validator(mode="before")
    @classmethod
    def explicit_booleans(cls, value):
        if isinstance(value, dict) and (value.get("confirm") is not True or value.get("cache_only", True) is not True):
            raise ValueError("Explicit confirmation and cache-only planning are required")
        return value


class SourceCut(Contract):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    transcript_segment_ids: list[StrictInt] = Field(default_factory=list)
    visual_frame_timestamps: list[float] = Field(default_factory=list)
    source_segment_id: str | None = Field(default=None, pattern=r"^seg-\d{3}$")

    @model_validator(mode="after")
    def bounds(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Source cuts must have positive duration")
        return self


class ClipIntent(Contract):
    title: str = Field(min_length=1, max_length=100)
    hook: str = Field(min_length=1, max_length=140)
    narration_text: str | None = Field(default=None, max_length=2200)
    rationale: str = Field(min_length=1, max_length=900)
    cuts: list[SourceCut] = Field(min_length=1, max_length=24)
    unit_id: str | None = Field(default=None, pattern=r"^unit-short-\d{3}$")


class PlannerOutput(Contract):
    clips: list[ClipIntent] = Field(min_length=3, max_length=10)


class RepurposePlan(Contract):
    schema_version: Literal[1] = 1
    production_id: str
    batch_id: str = Field(pattern=r"^rpb-[a-f0-9]{24}$")
    request: BatchRequest
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    transcript_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    clips: list[ClipIntent] = Field(min_length=3, max_length=10)
    evidence_notes: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=timestamp)


class RenderRequest(Contract):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,100}$")
    confirm: Literal[True]
    expected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    unit_ids: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if isinstance(value, dict) and value.get("confirm") is not True:
            raise ValueError("Render confirmation must be a JSON boolean true")
        return value

    @model_validator(mode="after")
    def selected_units(self):
        if self.confirm is not True or len(set(self.unit_ids)) != len(self.unit_ids):
            raise ValueError("Confirm distinct selected units")
        return self


class RenderArtifact(Contract):
    schema_version: Literal[1] = 1
    production_id: str
    unit_id: str
    batch_id: str
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: Mode
    framing: Framing
    output_path: str
    file_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    file_size: int = Field(gt=0)
    duration_seconds: float = Field(ge=30, le=60.1)
    width: Literal[1080] = 1080
    height: Literal[1920] = 1920
    cuts: list[dict[str, Any]]
    captions_precision: Literal["word", "segment_estimated_phrases"]
    loudness: dict[str, Any]
    validation: dict[str, Any]
    generated_at: str = Field(default_factory=timestamp)