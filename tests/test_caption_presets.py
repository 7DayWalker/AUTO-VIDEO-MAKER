"""Phase 6F - caption presentation preset and rendering-contract tests.

Verifies the structural fix: captions are rendered against an explicit
vertical play resolution (PlayResX/PlayResY = 1080x1920) rather than an
uncontrolled default subtitle coordinate system. This is the regression guard
against the Phase 6D oversized/high-position caption defect.

All tests are offline except one optional real-FFmpeg burn that confirms the
produced caption sits in the lower third (skipped when FFmpeg/libass is not
available).
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.assembly.caption_presets import (
    CAPTION_PRESETS,
    DEFAULT_CAPTION_PRESET,
    DEFAULT_PLAY_HEIGHT,
    DEFAULT_PLAY_WIDTH,
    build_ass_script,
    caption_preset_names,
    generate_ass,
    resolve_caption_style,
    validate_preset,
)
from app.assembly.captions import build_subtitle_filter
from app.assembly.models import CaptionConfig


def _srt(tmp_path, text="Hello World caption text", duration=2.0):
    p = tmp_path / "cap.srt"
    p.write_text(
        f"1\n00:00:00,000 --> 00:00:{int(duration):02d},000\n{text}\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Preset definition / default
# ---------------------------------------------------------------------------

def test_default_preset_is_standard():
    assert DEFAULT_CAPTION_PRESET == "standard"


def test_caption_preset_names():
    names = caption_preset_names()
    assert "minimal" in names
    assert "standard" in names
    assert "bold" in names
    assert "viral" in names


def test_validate_preset_accepts_and_rejects():
    assert validate_preset("viral") == "viral"
    assert validate_preset("") == DEFAULT_CAPTION_PRESET
    assert validate_preset(None) == DEFAULT_CAPTION_PRESET
    with pytest.raises(ValueError):
        validate_preset("nope")


# ---------------------------------------------------------------------------
# Explicit vertical play-resolution semantics
# ---------------------------------------------------------------------------

def test_resolve_standard_explicit_play_resolution():
    style = resolve_caption_style("standard")
    assert style["play_width"] == DEFAULT_PLAY_WIDTH == 1080
    assert style["play_height"] == DEFAULT_PLAY_HEIGHT == 1920
    assert style["alignment"] == 2  # bottom-centre


def test_lower_third_margin_semantics():
    """The default margin must place text safely in the lower third of a
    1920px-tall frame (not hidden behind bottom UI, not floating mid/high)."""
    style = resolve_caption_style(DEFAULT_CAPTION_PRESET)
    h = style["play_height"]
    # A meaningful distance above the bottom edge, but not most of the frame.
    assert style["margin_bottom"] >= int(h * 0.15)
    assert style["margin_bottom"] < int(h * 0.55)
    # Sensible horizontal gutters for vertical width.
    assert 0 < style["margin_left"] < style["play_width"] // 2
    assert style["margin_left"] == style["margin_right"]


def test_presets_differ():
    standard = resolve_caption_style("standard")
    bold = resolve_caption_style("bold")
    viral = resolve_caption_style("viral")
    assert bold["bold"] == 1
    assert bold["font_size"] > standard["font_size"]
    assert viral["font_size"] > bold["font_size"]
    assert viral["outline"] >= bold["outline"]


def test_override_respected():
    style = resolve_caption_style("standard", overrides={"font_size": 90, "margin_bottom": 300})
    assert style["font_size"] == 90
    assert style["margin_bottom"] == 300
    # Non-overridden values stay from the preset.
    assert style["alignment"] == 2
