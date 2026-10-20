from __future__ import annotations
import json, re
from datetime import datetime
from typing import List
from pydantic import BaseModel, Field
from app.production.models import NarrativeBeat, NarrationArtifact, NarrationSourceRef
class _AISRef(BaseModel):
    asset_id: str
    start_seconds: float
    end_seconds: float
    transcript_segment_ids: List[int] = Field(default_factory=list)
class _AIBeat2(BaseModel):
    order: int = Field(ge=1)
    title: str = Field(min_length=1)
    narration_text: str = Field(min_length=1)
    source_refs: List[_AISRef] = Field(min_length=1)
class _AISum(BaseModel):
    summary: str = Field(min_length=1)
    beats: List[_AIBeat2] = Field(min_length=1)
def _call_json(prov, sys_p, usr_p, max_tokens=4096):
    try:
        has = hasattr(prov, "generate_json")
        if has:
            resp = prov.generate_json(prompt=usr_p, system_prompt=sys_p, temperature=0.35, max_tokens=max_tokens)
        else:
            resp = prov.generate(prompt=usr_p, system_prompt=sys_p, temperature=0.35, max_tokens=max_tokens)
        raw = resp.content or ""
    except TypeError:
        resp = prov.generate(prompt=usr_p, system_prompt=sys_p, temperature=0.35, max_tokens=max_tokens)
        raw = resp.content or ""
    if not raw or not raw.strip():
        raise ValueError("LLM empty")
    s = raw.strip()
    fence = chr(96)*3
    if s.startswith(fence):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
        s = s.strip()
    a = s.find("{"); b = s.rfind("}")
    if a == -1 or b <= a:
        raise ValueError(f"no JSON: {raw[:400]!r}")
    return json.loads(s[a:b+1])
