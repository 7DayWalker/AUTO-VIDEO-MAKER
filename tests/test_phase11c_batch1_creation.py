"""Phase 11C Batch 1 creation and local execution boundary tests."""

import json
from pathlib import Path

from app.production.storage import load_production
from app.web import server as web


def _request():
    return {
        "mode": "single_video",
        "creation_method": "local",
        "title": "Local-first test video",
        "brief": "A small deterministic production for local validation.",
        "output_intent": "youtube_shorts",
        "target_duration": 30,
        "scene_count": 6,
        "tone": "clear and cinematic",
    }


def test_creation_persists_intent_only(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    status, payload = web.create_production_payload(_request())

    assert status == 201
    production_path = (
        tmp_path / "productions" / payload["production_id"] / "production.json"
    )
    assert production_path.is_file()
    production = load_production(payload["production_id"], root=tmp_path / "productions")

    assert production.command.workload_shape == "single_video"
    assert production.command.intended_unit_count == 1
    assert len(production.units) == 1
    assert production.units[0].provider_intent == "image_motion"
    assert not (tmp_path / "projects").exists()
    raw = json.loads(production_path.read_text(encoding="utf-8"))
    assert "checkpoint" not in json.dumps(raw).lower()
    assert "execution_status" not in json.dumps(raw).lower()


def test_creation_rejects_later_or_invalid_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    request = _request()
    request["mode"] = "series"

    status, payload = web.create_production_payload(request)

    assert status == 400
    assert payload["error"] == "INVALID_CREATION_REQUEST"
    assert not (tmp_path / "productions").exists()


def test_local_production_executor_is_real_and_offline_planned(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    monkeypatch.setattr(web, "PROJECTS_DIR", tmp_path / "projects")
    status, payload = web.create_production_payload(_request())
    assert status == 201

    executor = web._production_executor(payload["production_id"])

    assert executor.base_config.mock_mode is False
    assert executor.base_config.visual_mode == "local"
    assert executor.base_config.visual_default_provider == "image_motion"
    assert executor.base_config.image_source == "fixture"
    assert executor._planner_factory().provider.name == "mock"