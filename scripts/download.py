#!/usr/bin/env python3
"""Download a video via yt-dlp, or resolve a local file path.

Also fetches subtitles (manual first, then auto-generated) in VTT format so
transcribe.py can parse them without needing Whisper.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}


def is_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https")


def resolve_local(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"File not found: {p}")
    if p.suffix.lower() not in VIDEO_EXTS:
        print(
            f"[watch] warning: {p.suffix} is not a known video extension, proceeding anyway",
            file=sys.stderr,
        )
    return {
        "video_path": str(p),
        "subtitle_path": None,
        "info": {"title": p.name, "url": str(p)},
        "downloaded": False,
    }


def _pick_subtitle(out_dir: Path, lang: str = "en") -> Path | None:
    candidates = sorted(out_dir.glob("video*.vtt"))
    if not candidates:
        return None
    for pattern in (f".{lang}.vtt", f".{lang}-orig.vtt", f".{lang}", ".en"):
        preferred = [c for c in candidates if pattern in c.name]
        if preferred:
            return preferred[0]
    return candidates[0]


def original_language(raw: dict) -> str:
    """Spoken language of a YouTube video, so we fetch its own captions rather
    than an auto-translation (translated auto-subs are noisier and longer)."""
    for key in raw.get("automatic_captions") or {}:
        if key.endswith("-orig"):
            return key[: -len("-orig")]
    if raw.get("language"):
        return raw["language"]
    subtitles = raw.get("subtitles") or {}
    return next(iter(subtitles), "en")


def probe_url(url: str) -> dict:
    """Fetch yt-dlp metadata without downloading anything."""
    result = subprocess.run(
        ["yt-dlp", "-J", "--no-playlist", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise SystemExit(f"yt-dlp could not read {url}: {result.stderr.strip()[-500:]}")
    return json.loads(result.stdout)


def _pick_video(out_dir: Path) -> Path | None:
    for ext in (".mp4", ".mkv", ".webm", ".mov"):
        for candidate in out_dir.glob(f"video*{ext}"):
            return candidate
    for candidate in out_dir.glob("video.*"):
        if candidate.suffix.lower() in VIDEO_EXTS:
            return candidate
    return None


def download_url(url: str, out_dir: Path, lang: str | None = None, video: bool = True) -> dict:
    """Download video + captions. `video=False` fetches captions and metadata only."""
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed. Install with: brew install yt-dlp")

    out_dir.mkdir(parents=True, exist_ok=True)
    if lang is None:
        lang = original_language(probe_url(url))
    print(f"[watch] caption language: {lang}", file=sys.stderr)
    output_template = str(out_dir / "video.%(ext)s")

    cmd = [
        "yt-dlp",
        "-N", "8",
        "-f", "bv*[height<=720]+ba/b[height<=720]/bv+ba/b",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", f"{lang},{lang}-orig",
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "--ignore-errors",
        "-o", output_template,
        url,
    ]
    if not video:
        cmd.insert(1, "--skip-download")

    # yt-dlp may exit non-zero if a subtitle variant fails (e.g. 429) even when
    # the video itself downloaded fine. Treat "video file present" as success.
    result = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    video_file = _pick_video(out_dir)
    if video and video_file is None:
        raise SystemExit(
            f"yt-dlp did not produce a video file in {out_dir} (exit {result.returncode})"
        )

    subtitle = _pick_subtitle(out_dir, lang)
    info_path = out_dir / "video.info.json"
    info: dict = {}
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text(encoding="utf-8"))
            info = {
                "title": raw.get("title"),
                "uploader": raw.get("uploader") or raw.get("channel"),
                "duration": raw.get("duration"),
                "url": raw.get("webpage_url") or url,
            }
        except Exception:
            info = {"url": url}

    return {
        "video_path": str(video_file) if video_file else None,
        "subtitle_path": str(subtitle) if subtitle else None,
        "info": info or {"url": url},
        "downloaded": video,
    }


def download(source: str, out_dir: Path, lang: str | None = None, video: bool = True) -> dict:
    if is_url(source):
        return download_url(source, out_dir, lang=lang, video=video)
    return resolve_local(source)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: download.py <url-or-path> <out-dir>", file=sys.stderr)
        raise SystemExit(2)
    result = download(sys.argv[1], Path(sys.argv[2]))
    print(json.dumps(result, indent=2))
