"""Small source/evidence fixtures; no decoding, network, models or credentials."""
from types import SimpleNamespace
import json

from app.production.models import Production, ProductionCommand, ProductionUnit, SourceAsset, SourceTranscript, TranscriptSegment
from app.production.storage import compute_content_hash, save_new_production, production_source_dir
from app.repurpose.cache import load_evidence
from app.repurpose.models import BatchRequest
from app.repurpose.storage import atomic_json

PID = "prd_20261109_repurpose_fixture_001"


def source_fixture(root):
    source = production_source_dir(PID, root)
    source.mkdir(parents=True)
    media = source/"src-001.mp4"
    media.write_bytes(b"synthetic-media-not-decoded")
    asset = SourceAsset(asset_id="src-001", original_filename="fixture.mp4", stored_path="source/src-001.mp4",
        sha256=compute_content_hash(media), duration=300, file_size=media.stat().st_size, ingested_at="2026-11-09", source_kind="local_file")
    production = Production(production_id=PID, title="Repurpose fixture", command=ProductionCommand(
        text="Fixture source", workload_shape="single_video", issued_at="2026-11-09"),
        units=[ProductionUnit(unit_id="unit-001", ordinal=1, unit_type="standalone_video", title="Original film")],
        source_assets=[asset], created_at="2026-11-09")
    save_new_production(production, root=root)
    transcript = SourceTranscript(asset_id="src-001", provider="local_whisper", created_at="2026-11-09",
        transcript_path="source/src-001.transcript.json", language="en",
        segments=[TranscriptSegment(text=f"The character explains the dramatic moment number {i}.", start=i*5, end=i*5+5) for i in range(60)])
    atomic_json(source/"src-001.transcript.json", transcript)
    return load_evidence(PID, "src-001", root=root)


def request_fixture(evidence, **changes):
    return BatchRequest(request_id="fixture-one-call", confirm=True, source_asset_id="src-001",
        evidence_revision=evidence.revision, **changes)


def output_fixture(mode="narrated"):
    clips = []
    for i in range(3):
        cuts = [{"start_seconds": i*60+j*5, "end_seconds": i*60+j*5+5,
                 "transcript_segment_ids": [i*12+j]} for j in range(9)]
        clips.append({"title": f"A standalone moment {i+1}", "hook": f"Why did the character change everything? {i+1}",
            "rationale": "A complete source-grounded dramatic turning point.", "cuts": cuts,
            "narration_text": " ".join(["The character faces a dramatic choice and finds a grounded answer."]*12) if mode == "narrated" else None})
    return {"clips": clips}


class FakePlanner:
    model = "gpt-6.1-sol"
    _api_style = "responses"
    def __init__(self, output=None, error=None):
        self.calls, self.output, self.error = [], output or output_fixture(), error

    def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(model=self.model, content=json.dumps(self.output))