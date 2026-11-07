"""Isolate process-lifetime observer circuit breakers between hermetic cases."""
import pytest


@pytest.fixture(autouse=True)
def isolated_vision_quarantine(monkeypatch):
    from app.production import narration_real
    monkeypatch.setattr(narration_real, "_VISION_QUARANTINE", {})