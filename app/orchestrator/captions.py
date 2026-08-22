"""
AUTO Video Maker - Caption Generation

Generates SRT captions from voice timing data.
"""

import json
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def format_srt_timestamp(seconds: float) -> str:
    """
    Format seconds as SRT timestamp.
    
    Format: HH:MM:SS,mmm
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt_from_timing(
    timing_path: Path,
    narration_path: Path,
    output_path: Path,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
) -> Path:
    """
    Generate SRT captions from voice timing and narration.
    
    Args:
        timing_path: Path to voice_timing.json
        narration_path: Path to narration.txt or production_plan.json
        output_path: Path for output SRT file
        max_chars_per_line: Maximum characters per caption line
        max_lines: Maximum lines per caption
    
    Returns:
        Path to generated SRT file
    """
    logger.info(f"Generating SRT captions from {timing_path}")
    
    # Load timing data
    with open(timing_path, "r", encoding="utf-8") as f:
        timing_data = json.load(f)
    
    # Load narration text
    narration_texts = _load_narration_texts(narration_path, timing_data)
    
    # Generate SRT entries
    srt_entries = []
    entry_num = 1
    
    for scene_timing in timing_data.get("scenes", []):
        scene_id = scene_timing.get("scene_id")
        # Voice timing uses the canonical `start`/`end` fields. Accept the
        # legacy names as a compatibility fallback, but never default a valid
        # scene to a zero-length caption.
        start_time = scene_timing.get("start", scene_timing.get("start_time"))
        end_time = scene_timing.get("end", scene_timing.get("end_time"))
        if start_time is None or end_time is None:
            logger.warning("Skipping scene %s: timing boundaries are missing", scene_id)
            continue
        
        # Get narration text for this scene
        narration = narration_texts.get(scene_id, "")
        if not narration:
            continue
        
        # Split narration into caption chunks
        chunks = _split_into_caption_chunks(
            narration,
            max_chars_per_line=max_chars_per_line,
            max_lines=max_lines,
        )
        
        if not chunks:
            continue
        
        # Calculate timing for each chunk
        scene_duration = end_time - start_time
        chunk_duration = scene_duration / len(chunks)
        
        for i, chunk in enumerate(chunks):
            chunk_start = start_time + (i * chunk_duration)
            chunk_end = start_time + ((i + 1) * chunk_duration)
            
            # Ensure minimum display time (1 second)
            if chunk_end - chunk_start < 1.0 and chunk_end < end_time:
                chunk_end = min(chunk_start + 1.0, end_time)
            
            srt_entry = _format_srt_entry(entry_num, chunk_start, chunk_end, chunk)
            srt_entries.append(srt_entry)
            entry_num += 1
    
    # Write SRT file
    srt_content = "\n".join(srt_entries)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(srt_content, encoding="utf-8")
    
    logger.info(f"Generated {entry_num - 1} caption entries: {output_path}")
    return output_path


def _load_narration_texts(narration_path: Path, timing_data: dict) -> dict:
    """
    Load narration texts mapped by scene ID.
    
    Supports:
    - production_plan.json (preferred)
    - narration.txt (fallback, splits by scene timing)
    """
    narration_texts = {}
    
    if narration_path.suffix == ".json":
        # Load from production plan
        with open(narration_path, "r", encoding="utf-8") as f:
            plan_data = json.load(f)
        
        for scene in plan_data.get("scenes", []):
            scene_id = scene.get("id")
            narration = scene.get("narration", "")
            if scene_id and narration:
                narration_texts[scene_id] = narration.strip()
    
    elif narration_path.suffix == ".txt":
        # Load from narration text file
        # This is a fallback - we'll split by scene timing
        full_text = narration_path.read_text(encoding="utf-8")
        
        # Try to split by scene markers if present
        scenes = timing_data.get("scenes", [])
        if len(scenes) == 1:
            # Single scene - use full text
            narration_texts[scenes[0].get("scene_id")] = full_text.strip()
        else:
            # Multiple scenes - split proportionally by estimated duration
            total_estimated = sum(s.get("estimated_duration", 1) for s in scenes)
            words = full_text.split()
            total_words = len(words)
            
            word_idx = 0
            for scene in scenes:
                scene_id = scene.get("scene_id")
                estimated = scene.get("estimated_duration", 1)
                proportion = estimated / total_estimated
                word_count = max(1, int(total_words * proportion))
                
                scene_words = words[word_idx:word_idx + word_count]
                narration_texts[scene_id] = " ".join(scene_words)
                word_idx += word_count
    
    return narration_texts


def _split_into_caption_chunks(
    text: str,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
) -> List[str]:
    """
    Split text into caption-sized chunks.
    
    Tries to break at sentence boundaries, then word boundaries.
    """
    if not text:
        return []
    
    max_chars = max_chars_per_line * max_lines
    chunks = []
    
    # First, try to split by sentences
    sentences = _split_sentences(text)
    
    current_chunk = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        
        # If sentence fits in current chunk
        if len(current_chunk) + len(sentence) + 1 <= max_chars:
            if current_chunk:
                current_chunk += " " + sentence
            else:
                current_chunk = sentence
        else:
            # Save current chunk if not empty
            if current_chunk:
                chunks.append(_format_caption_lines(current_chunk, max_chars_per_line, max_lines))
            
            # If sentence is too long, split it
            if len(sentence) > max_chars:
                sentence_chunks = _split_long_text(sentence, max_chars_per_line, max_lines)
                chunks.extend(sentence_chunks[:-1])
                current_chunk = sentence_chunks[-1] if sentence_chunks else ""
            else:
                current_chunk = sentence
    
    # Don't forget the last chunk
    if current_chunk:
        chunks.append(_format_caption_lines(current_chunk, max_chars_per_line, max_lines))
    
    return chunks


def _split_sentences(text: str) -> List[str]:
    """Split text into sentences."""
    import re
    
    # Split on sentence-ending punctuation followed by space or end
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]


def _split_long_text(
    text: str,
    max_chars_per_line: int,
    max_lines: int,
) -> List[str]:
    """Split long text into chunks at word boundaries."""
    max_chars = max_chars_per_line * max_lines
    words = text.split()
    chunks = []
    current_chunk = ""
    
    for word in words:
        if len(current_chunk) + len(word) + 1 <= max_chars:
            if current_chunk:
                current_chunk += " " + word
            else:
                current_chunk = word
        else:
            if current_chunk:
                chunks.append(_format_caption_lines(current_chunk, max_chars_per_line, max_lines))
            current_chunk = word
    
    if current_chunk:
        chunks.append(_format_caption_lines(current_chunk, max_chars_per_line, max_lines))
    
    return chunks


def _format_caption_lines(
    text: str,
    max_chars_per_line: int,
    max_lines: int,
) -> str:
    """Format text into caption lines."""
    if len(text) <= max_chars_per_line:
        return text
    
    words = text.split()
    lines = []
    current_line = ""
    
    for word in words:
        if len(current_line) + len(word) + 1 <= max_chars_per_line:
            if current_line:
                current_line += " " + word
            else:
                current_line = word
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
            
            if len(lines) >= max_lines:
                break
    
    if current_line and len(lines) < max_lines:
        lines.append(current_line)
    
    return "\n".join(lines)


def _format_srt_entry(
    entry_num: int,
    start_time: float,
    end_time: float,
    text: str,
) -> str:
    """Format a single SRT entry."""
    start_ts = format_srt_timestamp(start_time)
    end_ts = format_srt_timestamp(end_time)
    
    return f"{entry_num}\n{start_ts} --> {end_ts}\n{text}\n"


def validate_srt_file(srt_path: Path) -> dict:
    """
    Validate an SRT file.
    
    Returns dict with: valid, entry_count, total_duration, errors
    """
    if not srt_path.exists():
        return {"valid": False, "errors": ["File does not exist"]}
    
    content = srt_path.read_text(encoding="utf-8")
    if not content.strip():
        return {"valid": False, "errors": ["File is empty"]}
    
    # Parse entries
    entries = content.strip().split("\n\n")
    entry_count = 0
    max_end_time = 0
    errors = []
    
    for i, entry in enumerate(entries):
        lines = entry.strip().split("\n")
        if len(lines) < 3:
            errors.append(f"Entry {i+1}: Invalid format (too few lines)")
            continue
        
        # Check entry number
        try:
            int(lines[0])
        except ValueError:
            errors.append(f"Entry {i+1}: Invalid entry number")
            continue
        
        # Check timestamp line
        if " --> " not in lines[1]:
            errors.append(f"Entry {i+1}: Invalid timestamp format")
            continue
        
        # Parse both timestamp boundaries and reject zero/negative intervals.
        try:
            start_ts, end_ts = lines[1].split(" --> ")
            start_time = _parse_srt_timestamp(start_ts)
            end_time = _parse_srt_timestamp(end_ts)
            if end_time <= start_time:
                errors.append(f"Entry {i+1}: End time must be after start time")
                continue
            max_end_time = max(max_end_time, end_time)
        except Exception:
            errors.append(f"Entry {i+1}: Could not parse timestamp")
            continue
        
        entry_count += 1
    
    return {
        "valid": len(errors) == 0,
        "entry_count": entry_count,
        "total_duration": max_end_time,
        "errors": errors,
    }


def _parse_srt_timestamp(ts: str) -> float:
    """Parse SRT timestamp to seconds."""
    # Format: HH:MM:SS,mmm
    ts = ts.strip()
    parts = ts.replace(",", ":").split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = int(parts[2])
    millis = int(parts[3]) if len(parts) > 3 else 0
    
    return hours * 3600 + minutes * 60 + seconds + millis / 1000
