# Phase 2E Integration Audit Report

**Date:** August 20, 2026  
**Auditor:** AUTO Video Maker Phase 2E Implementation  
**Purpose:** Verify all contracts, schemas, paths, IDs, and module interfaces before building the production orchestrator.

---

## Executive Summary

The audit reveals that Phases 2A-2D are **structurally complete** but have **critical integration gaps** that must be addressed for end-to-end operation:

1. **Schema Mismatch**: Assembly engine uses different `Scene` model than Content engine
2. **Path Conventions**: Each phase uses different output directory structures
3. **No Unified Manifest**: No single state file tracks pipeline progress
4. **Missing Orchestration**: No code connects the phases together

---

## Phase 2A: Assembly Engine

### Module Location
- `app/assembly/` - Core assembly logic
- `assemble.py` - CLI interface

### Public API
```python
from app.assembly import (
    AssemblyProject,      # Input model
    Scene,                # Scene definition (DIFFERENT from content.Scene!)
    AudioTrack,           # Audio track config
    CaptionConfig,        # Caption settings
    OutputConfig,         # Output file settings
    VideoResolution,      # Resolution spec
    AssemblyResult,       # Output model
    assemble_project,     # Main entry point
)
```

### Input Contract: `AssemblyProject`
```python
{
    "project_id": str,           # Required, min 1 char
    "title": str,                # Default: "Untitled"
    "aspect_ratio": str,         # Default: "9:16"
    "resolution": {
        "width": int,            # Default: 1080
        "height": int            # Default: 1920
    },
    "scenes": [                  # Required, min 1 scene
        {
            "id": str,           # Required, unique
            "video": str,        # Required, path to video file
            "duration": float    # Optional, override duration
        }
    ],
    "voiceover": str,            # Optional, path to audio
    "background_music": str,     # Optional, path to music
    "music_volume": float,       # Default: 0.15
    "captions": {
        "enabled": bool,         # Default: false
        "source": str,           # Path to SRT/VTT
        "font_size": int,        # Optional override
        "font_color": str,       # Optional override
        "margin_bottom": int     # Optional override
    },
    "output": {
        "filename": str,         # Default: "final_short.mp4"
        "directory": str         # Optional override
    }
}
```

### Output Contract: `AssemblyResult`
```python
{
    "success": bool,
    "output_path": str,          # Full path to final MP4
    "duration": float,           # Actual duration
    "width": int,
    "height": int,
    "file_size": int,            # Bytes
    "errors": [str],
    "warnings": [str]
}
```

### Output Paths
- Default output: `output/assembly/`
- Temp files: `temp/assembly/{project_id}_{timestamp}/`
- Logs: `logs/assembly/`

### ⚠️ CRITICAL FINDING: Scene Model Mismatch
The Assembly `Scene` model is **different** from Content `Scene`:
- Assembly Scene: `{id, video, duration}` - expects video file path
- Content Scene: `{id, sequence, narration, visual_prompt, estimated_duration, purpose, ...}`

**Resolution Required:** Orchestrator must transform Content scenes to Assembly scenes by mapping generated video files.

---

## Phase 2B: Content/Script Planner

### Module Location
- `app/content/` - Core content logic
- `script_planner.py` - CLI interface

### Public API
```python
from app.content import (
    ContentBrief,              # Input specification
    Script,                    # Generated script
    Scene,                     # Scene with narration + visual prompt
    ProductionPlan,            # Main output
    ContentGenerationResult,   # Generation result
    ContentGenerator,          # Generator class
    ScenePlanner,              # High-level orchestrator
    validate_production_plan,  # Validation
    ValidationResult,
)
```

### Input Contract: `ContentBrief`
```python
{
    "topic": str,                # Required, 3-500 chars
    "niche": str,                # Default: "general"
    "target_audience": str,      # Default: "general audience"
    "tone": str,                 # Default: "engaging and informative"
    "target_duration": int,      # Default: 30, range: 10-180
    "platform": str,             # Default: "youtube_shorts"
    "language": str,             # Default: "en"
    "content_goal": str,         # Default: "education"
    "call_to_action": str,       # Optional
    "style": str,                # Default: "engaging documentary"
    "number_of_scenes": int      # Default: 6, range: 2-20
}
```

### Output Contract: `ProductionPlan`
```python
{
    "project_id": str,           # Format: YYYYMMDD_topic_slug_XXX
    "title": str,                # Video title
    "hook": str,                 # Opening hook
    "script": str,               # Full narration script
    "call_to_action": str,       # Optional CTA
    "estimated_duration": float, # Total estimated duration
    "scenes": [
        {
            "id": str,           # Format: scene_01, scene_02, etc.
            "sequence": int,     # 1-based sequence
            "narration": str,    # Voiceover text for this scene
            "visual_prompt": str,# Visual generation prompt
            "estimated_duration": float,  # Scene duration (2-10s)
            "purpose": str,      # hook, intro, fact, conclusion, etc.
            "transition": str,   # Optional
            "on_screen_text": str,  # Optional
            "visual_style": str,    # Optional
            "camera_direction": str, # Optional
            "sound_design": str     # Optional
        }
    ],
    "metadata": {
        "platform": str,
        "language": str,
        "niche": str,
        "target_audience": str,
        "content_goal": str,
        "style": str,
        "requires_fact_check": bool,
        "generated_at": str,     # ISO timestamp
        "provider": str,
        "model": str
    }
}
```

### Output Paths
- Production plan: `output/content/{project_id}/production_plan.json`
- Content brief: `output/content/{project_id}/content_brief.json`
- Logs: `logs/content/`

### Scene ID Generation
- Format: `scene_01`, `scene_02`, etc. (zero-padded)
- Validated: alphanumeric with underscores/hyphens only
- Unique within project

---

## Phase 2C: Voice/TTS Engine

### Module Location
- `app/voice/` - Core voice logic
- `voice_generator.py` - CLI interface

### Public API
```python
from app.voice import (
    VoiceGenerationRequest,    # Input request
    VoiceGenerationResult,     # Output result
    VoiceMetadata,             # Generated file metadata
    SceneTiming,               # Per-scene timing
    VoiceTiming,               # Complete timing data
    VoiceGenerator,            # Main orchestrator
    validate_audio_file,       # Audio validation
    AudioValidationResult,
)
```

### Input Contract
The `VoiceGenerator.generate_from_plan()` method expects:
- `plan: ProductionPlan` - Must have scenes with non-empty `narration` field

### Output Contract: `VoiceGenerationResult`
```python
{
    "success": bool,
    "output_path": str,          # Path to voiceover file
    "provider": str,
    "voice": str,
    "duration": float,           # Actual audio duration
    "file_size": int,
    "format": str,               # "mp3" or "wav"
    "sample_rate": int,
    "channels": int,
    "warnings": [str],
    "error": str                 # If failed
}
```

### Output Paths
- Voiceover: `output/voice/{project_id}/voiceover.mp3`
- Narration text: `output/voice/{project_id}/narration.txt`
- Timing: `output/voice/{project_id}/voice_timing.json`
- Metadata: `output/voice/{project_id}/voice_metadata.json`
- Logs: `logs/voice/`

### Voice Timing Schema
```python
{
    "project_id": str,
    "voiceover_duration": float,
    "total_estimated_duration": float,
    "duration_difference": float,
    "scenes": [
        {
            "scene_id": str,
            "sequence": int,
            "estimated_duration": float,
            "scaled_duration": float,
            "start_time": float,
            "end_time": float
        }
    ]
}
```

---

## Phase 2D: Flow Scene Generator

### Module Location
- `app/flow/scene_*.py` - Scene generation logic
- `flow_scene_generator.py` - CLI interface

### Public API
```python
from app.flow.scene_generator import (
    FlowSceneGenerator,        # Main generator class
    FlowSceneError,
    FlowConnectionError,
    FlowAuthenticationError,
    FlowGenerationError,
    FlowCreditError,
    validate_video_file,
    load_manifest,
    save_manifest,
)
from app.flow.scene_models import (
    FlowGenerationConfig,      # Generation settings
    SceneGenerationRequest,    # Per-scene request
    SceneGenerationResult,     # Per-scene result
    SceneMetadata,             # Generated scene metadata
    SceneManifest,             # Project manifest
    SceneManifestEntry,        # Manifest entry
    FlowUIState,               # UI state tracking
)
```

### Input Contract: `SceneGenerationRequest`
```python
{
    "project_id": str,
    "scene_id": str,
    "sequence": int,
    "visual_prompt": str,        # From ProductionPlan.scenes[].visual_prompt
    "config": {
        "model": str,            # Default: "Veo 3.1 - Lite"
        "aspect_ratio": str,     # Default: "9:16"
        "duration": int,         # Default: 4 seconds
        "count": int,            # Default: 1
        "agent_enabled": bool    # Default: false
    },
    "flow_project_url": str      # Required for generation
}
```

### Output Contract: `SceneGenerationResult`
```python
{
    "success": bool,
    "scene_id": str,
    "sequence": int,
    "status": str,               # completed, failed, timeout, skipped, dry_run
    "file_path": Path,           # Path to downloaded video
    "file_size": int,
    "actual_duration": float,
    "width": int,
    "height": int,
    "codec": str,
    "generation_cost": int,      # Credits consumed
    "generation_started_at": str,
    "generation_completed_at": str,
    "downloaded_at": str,
    "error": str,
    "warnings": [str]
}
```

### Output Paths
- Scene videos: `output/scenes/{project_id}/{scene_id}.mp4`
- Scene manifest: `output/scenes/{project_id}/scene_manifest.json`
- Scene metadata: `output/scenes/{project_id}/{scene_id}_metadata.json`
- Screenshots: `logs/flow_scenes/`

### Scene Manifest Schema
```python
{
    "project_id": str,
    "scenes": [
        {
            "scene_id": str,
            "sequence": int,
            "file": str,         # Filename only, e.g., "scene_01.mp4"
            "status": str        # completed, pending, failed
        }
    ],
    "created_at": str,
    "updated_at": str
}
```

---

## Integration Gap Analysis

### Gap 1: No Unified Project State
**Problem:** Each phase maintains its own output structure. No single manifest tracks overall pipeline progress.

**Solution:** Create `ProjectManifest` in orchestrator that tracks:
- Current pipeline stage
- Completion status of each phase
- Paths to all generated artifacts
- Resume capability

### Gap 2: Scene Model Transformation
**Problem:** Content `Scene` ≠ Assembly `Scene`

**Transformation Required:**
```python
# Content Scene → Assembly Scene
assembly_scene = {
    "id": content_scene.id,
    "video": f"output/scenes/{project_id}/{content_scene.id}.mp4",
    "duration": None  # Let FFprobe determine actual duration
}
```

### Gap 3: Path Resolution
**Problem:** Different phases use different base directories.

**Path Mapping:**
| Phase | Output Directory |
|-------|-----------------|
| Content | `output/content/{project_id}/` |
| Voice | `output/voice/{project_id}/` |
| Scenes | `output/scenes/{project_id}/` |
| Assembly | `output/assembly/` |
| Final | `output/final/{project_id}/` |

### Gap 4: Caption Generation
**Problem:** Assembly expects SRT/VTT file, but no phase generates captions.

**Solution:** Orchestrator must generate SRT from voice timing data before assembly.

### Gap 5: Flow Project URL
**Problem:** Scene generator requires a Flow project URL, but this is external input.

**Solution:** Orchestrator must accept Flow project URL as required parameter for real generation.

---

## Verified Contracts Summary

### Data Flow
```
ContentBrief
    ↓ (script_planner.py)
ProductionPlan (output/content/{project_id}/production_plan.json)
    ↓ (voice_generator.py)
VoiceGenerationResult + voice_timing.json (output/voice/{project_id}/)
    ↓ (flow_scene_generator.py)
SceneGenerationResult[] + scene_manifest.json (output/scenes/{project_id}/)
    ↓ (orchestrator transforms)
AssemblyProject
    ↓ (assemble.py)
AssemblyResult (output/assembly/final_short.mp4)
```

### Required Orchestrator Inputs
1. `topic: str` - Video topic
2. `flow_project_url: str` - Google Flow project URL
3. Optional: All ContentBrief parameters
4. Optional: Voice settings
5. Optional: Assembly settings

### Required Orchestrator Outputs
1. Final MP4 video
2. Project manifest with all artifacts
3. Detailed logs

---

## Recommendations for Phase 2E

1. **Create unified `ProjectManifest` model** that tracks all phases
2. **Implement idempotent stage execution** - skip completed stages on resume
3. **Generate SRT captions** from voice timing before assembly
4. **Transform Content scenes to Assembly scenes** with actual video paths
5. **Validate each stage output** before proceeding to next
6. **Support dry-run mode** for testing without consuming Flow credits
7. **Implement strict error handling** - fail fast, report exact failure point

---

## Audit Conclusion

The codebase is **ready for Phase 2E integration** with the following work required:

1. ✅ All phase modules are complete and functional
2. ✅ All data models are well-defined with Pydantic validation
3. ✅ CLI interfaces exist for each phase
4. ⚠️ No orchestration layer exists
5. ⚠️ No unified state management
6. ⚠️ Caption generation missing
7. ⚠️ Scene model transformation needed

**Proceed with Phase 2E implementation.**
