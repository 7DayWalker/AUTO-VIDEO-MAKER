"""Output-timeline ASS captions. Estimated phrase timing is explicitly labelled."""
from __future__ import annotations

import math
import re
import unicodedata


def escape_ass(text):
    # Full-width controls are inert visible text, not libass override commands.
    return "".join(c for c in str(text) if c in "\n\t" or unicodedata.category(c) != "Cc").replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", " ").replace("\r", " ")


def ass_time(seconds):
    total = max(0, round(seconds * 100))
    return f"{total // 360000}:{total // 6000 % 60:02d}:{total // 100 % 60:02d}.{total % 100:02d}"


def group_words(words, *, maximum=5):
    groups, current = [], []
    for word in sorted(words, key=lambda w: w["start"]):
        if not word["text"].strip() or word["end"] <= word["start"]:
            continue
        if current and (len(current) >= maximum or word["start"] - current[-1]["end"] > .45
                        or word["end"] - current[0]["start"] > 2.5):
            groups.append(current)
            current = []
        current.append(word)
        if re.search(r"[.!?。！？]$", word["text"].strip()):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def narration_words(boundaries, *, speed=1.0, duration):
    return [{"text": b["text"], "start": max(0, b["start"] / speed), "end": min(duration, b["end"] / speed)}
            for b in boundaries if b.get("kind") == "WordBoundary" and b["start"] / speed < duration]


def beat_caption_entries(words, *, output_start, beat_order):
    """Group only within this measured beat; never merge across a story cut."""
    shifted = [{**word, "start": output_start + word["start"], "end": output_start + word["end"]} for word in words]
    return [{"start": group[0]["start"], "end": group[-1]["end"], "text": " ".join(word["text"] for word in group),
        "words": group, "beat_order": beat_order} for group in group_words(shifted)]


def transcript_captions(transcript, cuts):
    entries, precision = [], "word"
    for cut in cuts:
        left, right = cut["start_seconds"], cut["end_seconds"]
        offset, scale = cut["output_start"], cut["output_duration"] / (right - left)
        cut_words = []
        for segment in transcript.segments:
            start, end = max(left, segment.start), min(right, segment.end)
            if end <= start:
                continue
            if segment.words:
                for word in segment.words:
                    a, b = max(left, float(word["start"])), min(right, float(word["end"]))
                    if b > a:
                        cut_words.append({"text": word.get("word", word.get("text", "")),
                            "start": offset + (a-left)*scale, "end": offset + (b-left)*scale})
            else:
                precision = "segment_estimated_phrases"
                tokens = segment.text.split()
                if len(tokens) == 1 and len(tokens[0]) > 14:
                    tokens = re.findall(r".{1,12}", tokens[0])
                phrases = [" ".join(tokens[i:i+5]) for i in range(0, len(tokens), 5)]
                for i, phrase in enumerate(phrases):
                    a = segment.start + (segment.end-segment.start)*i/len(phrases)
                    b = segment.start + (segment.end-segment.start)*(i+1)/len(phrases)
                    a, b = max(a, left), min(b, right)
                    if b > a:
                        entries.append({"start": offset+(a-left)*scale, "end": offset+(b-left)*scale,
                            "text": phrase, "words": None})
        entries.extend({"start": g[0]["start"], "end": g[-1]["end"], "text": " ".join(w["text"].strip() for w in g), "words": g}
                       for g in group_words(cut_words))
    return sorted(entries, key=lambda e: e["start"]), precision


def build_ass(hook, entries, duration):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,65,&H00FFFFFF,&H0000D7FF,&H00151022,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,100,160,360,1
Style: Hook,Arial,62,&H00FFFFFF,&H00FFFFFF,&H001B122C,&H001B122C,-1,0,0,0,100,100,0,0,3,14,0,8,85,85,185,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header, f"Dialogue: 1,{ass_time(0)},{ass_time(duration)},Hook,,0,0,0,,{escape_ass(hook)}"]
    for entry in entries:
        start, end = max(0, entry["start"]), min(duration, entry["end"])
        if not math.isfinite(start) or not math.isfinite(end) or end - start < .01:
            continue
        text = escape_ass(entry["text"])
        if entry.get("words"):
            cursor, parts = start, []
            for word in entry["words"]:
                gap = max(0, word["start"]-cursor)
                if gap >= .01:
                    parts.append(r"{\k" + str(round(gap*100)) + "} ")
                parts.append(r"{\kf" + str(max(1, round((word["end"]-word["start"])*100))) + "}" + escape_ass(word["text"].strip()) + " ")
                cursor = word["end"]
            text = "".join(parts).strip()
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"