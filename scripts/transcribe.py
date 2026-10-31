#!/usr/bin/env python3
"""Parse a WebVTT subtitle file into a clean, timestamped transcript.

YouTube auto-subs emit rolling-duplicate cues (each line appears 2-3 times as it
scrolls). We dedupe consecutive identical cues and merge their time ranges.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


TS_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")
# Non-speech markers YouTube auto-subs insert; pure token noise for a reader.
NOISE_RE = re.compile(r"\[(?:음악|박수|웃음|Music|Applause|Laughter)\]|&gt;&gt;|>>", re.IGNORECASE)


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(path: str) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    segments: list[dict] = []
    recent: list[str] = []
    i = 0
    while i < len(lines):
        match = TS_RE.match(lines[i])
        if not match:
            i += 1
            continue

        start = _to_seconds(*match.groups()[:4])
        end = _to_seconds(*match.groups()[4:])
        i += 1

        cue_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            cleaned = " ".join(NOISE_RE.sub("", TAG_RE.sub("", lines[i])).split())
            # Rolling auto-subs repeat the previous line on top of each new cue;
            # keep only lines not seen in the last few cues.
            if cleaned and cleaned not in recent:
                cue_lines.append(cleaned)
                recent = (recent + [cleaned])[-4:]
            i += 1

        cue_text = " ".join(cue_lines).strip()
        if cue_text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": cue_text})
        i += 1

    return _dedupe(segments)


def _dedupe(segments: list[dict]) -> list[dict]:
    """Collapse rolling duplicates common in YouTube auto-subs."""
    out: list[dict] = []
    for seg in segments:
        if out and seg["text"] == out[-1]["text"]:
            out[-1]["end"] = seg["end"]
            continue
        if out and seg["text"].startswith(out[-1]["text"] + " "):
            out[-1]["text"] = seg["text"]
            out[-1]["end"] = seg["end"]
            continue
        out.append(seg)
    return out


def filter_range(
    segments: list[dict],
    start_seconds: float | None,
    end_seconds: float | None,
) -> list[dict]:
    """Return segments whose time range overlaps [start, end]."""
    if start_seconds is None and end_seconds is None:
        return segments
    lo = start_seconds if start_seconds is not None else float("-inf")
    hi = end_seconds if end_seconds is not None else float("inf")
    return [seg for seg in segments if seg["end"] >= lo and seg["start"] <= hi]


def _stamp(seconds: float) -> str:
    start = int(seconds)
    return f"[{start // 60:02d}:{start % 60:02d}]"


def format_transcript(segments: list[dict], para_chars: int = 0) -> str:
    """One line per segment, or (para_chars > 0) paragraphs of about para_chars
    characters, each stamped with its first segment's time. Paragraphs cut the
    per-line timestamp overhead and keep sentences together."""
    if para_chars <= 0:
        return "\n".join(f"{_stamp(seg['start'])} {seg['text']}" for seg in segments)
    out: list[str] = []
    buf: list[str] = []
    first: float | None = None
    for seg in segments:
        if first is None:
            first = seg["start"]
        buf.append(seg["text"])
        if sum(len(t) + 1 for t in buf) >= para_chars:
            out.append(f"{_stamp(first)} {' '.join(buf)}")
            buf, first = [], None
    if buf and first is not None:
        out.append(f"{_stamp(first)} {' '.join(buf)}")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: transcribe.py <vtt-path>", file=sys.stderr)
        raise SystemExit(2)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(format_transcript(parse_vtt(sys.argv[1]), para_chars=300))
