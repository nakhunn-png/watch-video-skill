#!/usr/bin/env python3
"""Have Gemini watch a video and return a compact, timestamped text index.

The point is token economy on the Claude side: Gemini ingests the whole video
(frames + audio) on its own bill, and Claude only reads a few thousand tokens of
text. Use the index to decide which ranges deserve real frames via
`watch.py --start/--end`.

YouTube URLs are passed straight through (no download). Local files are uploaded
with the Gemini Files API and deleted afterwards.

Key: GEMINI_API_KEY in the environment or ~/.config/watch/.env.
Pure stdlib.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import urllib.error
from pathlib import Path
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from download import is_url  # noqa: E402
from frames import parse_time  # noqa: E402
from whisper import read_key  # noqa: E402


API = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-3.8-flash"

DEFAULT_PROMPT = """You are indexing this video so another model can answer questions without watching it.
Reply in the language the video is spoken in. Be dense; no preamble.

## Overview
2-3 sentences: what the video is, who presents, the core claim.

## Scene index
One line per meaningful scene change, chronological:
- [MM:SS-MM:SS] what is on screen (slide title / app / document / talking head) | key point said
  Copy on-screen titles, headings, table headers and numbers VERBATIM. If a number or small text is
  not clearly legible, write it as "(unclear)" instead of guessing.

## Numbers & on-screen text
Bullet list of every figure, amount, date, URL, file name, command or code shown, each with [MM:SS].

## Verify with frames
Timestamps where small text or dense tables/spreadsheets appear, so they can be checked at higher resolution.
"""


def _request(method: str, url: str, api_key: str, body: bytes | None = None,
             headers: dict | None = None, timeout: int = 600, retries: int = 3) -> tuple[dict, dict]:
    """HTTP call with retries on 429/500/503 (Gemini overload is usually brief)."""
    for attempt in range(retries + 1):
        req = Request(url, data=body, method=method)
        req.add_header("x-goog-api-key", api_key)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return (json.loads(raw) if raw else {}), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            if exc.code in (429, 500, 503) and attempt < retries:
                wait = 10 * (attempt + 1)
                print(f"[gemini] HTTP {exc.code}, retrying in {wait}s…", file=sys.stderr)
                time.sleep(wait)
                continue
            raise SystemExit(f"[gemini] HTTP {exc.code} on {method} {url.split('?')[0]}: {detail}")
    raise AssertionError("unreachable")


def upload_file(path: Path, api_key: str) -> dict:
    """Resumable upload to the Files API, then wait until the file is ACTIVE."""
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    print(f"[gemini] uploading {path.name} ({size / 1e6:.1f} MB)…", file=sys.stderr)
    _, headers = _request(
        "POST", f"{API}/upload/v1beta/files", api_key,
        body=json.dumps({"file": {"display_name": path.name}}).encode(),
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json",
        },
    )
    upload_url = next((v for k, v in headers.items() if k.lower() == "x-goog-upload-url"), None)
    if not upload_url:
        raise SystemExit("[gemini] Files API did not return an upload URL")
    data, _ = _request(
        "POST", upload_url, api_key, body=path.read_bytes(),
        headers={"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"},
    )
    file = data.get("file", data)
    while file.get("state") == "PROCESSING":
        time.sleep(5)
        file, _ = _request("GET", f"{API}/v1beta/{file['name']}", api_key)
    if file.get("state") != "ACTIVE":
        raise SystemExit(f"[gemini] uploaded file is not usable: state={file.get('state')}")
    return file


def delete_file(name: str, api_key: str) -> None:
    try:
        _request("DELETE", f"{API}/v1beta/{name}", api_key)
    except SystemExit as exc:
        print(f"[gemini] could not delete uploaded file {name}: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(prog="gemini", description=__doc__.splitlines()[0])
    ap.add_argument("source", help="YouTube URL or local video path")
    ap.add_argument("--prompt", default=None, help="Replace the default indexing prompt")
    ap.add_argument("--ask", default=None, help="Extra question appended to the indexing prompt")
    ap.add_argument("--start", default=None, help="Range start (SS, MM:SS, HH:MM:SS)")
    ap.add_argument("--end", default=None, help="Range end")
    ap.add_argument("--fps", type=float, default=None, help="Frames/sec Gemini samples (its default is 1)")
    ap.add_argument("--media-resolution", choices=["low", "medium", "high"], default="low",
                    help="low ≈ 1/3 the Gemini tokens; use high only for small on-screen text")
    ap.add_argument("--model", default=None, help=f"Default: $GEMINI_MODEL or {DEFAULT_MODEL}")
    args = ap.parse_args()

    api_key = read_key("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("[gemini] GEMINI_API_KEY not set (environment or ~/.config/watch/.env)")
    model = args.model or read_key("GEMINI_MODEL") or DEFAULT_MODEL

    uploaded = None
    if is_url(args.source):
        file_data = {"file_uri": args.source}
    else:
        path = Path(args.source).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"File not found: {path}")
        uploaded = upload_file(path, api_key)
        file_data = {"file_uri": uploaded["uri"], "mime_type": uploaded.get("mimeType", "video/mp4")}

    video_part: dict = {"file_data": file_data}
    meta: dict = {}
    start, end = parse_time(args.start), parse_time(args.end)
    if start is not None:
        meta["start_offset"] = f"{int(start)}s"
    if end is not None:
        meta["end_offset"] = f"{int(end)}s"
    if args.fps:
        meta["fps"] = args.fps
    if meta:
        video_part["video_metadata"] = meta

    prompt = args.prompt or DEFAULT_PROMPT
    if args.ask:
        prompt += f"\n## Question\n{args.ask}\nAnswer it at the end under '## Answer', citing [MM:SS]."

    body = {
        "contents": [{"parts": [video_part, {"text": prompt}]}],
        "generationConfig": {"mediaResolution": f"MEDIA_RESOLUTION_{args.media_resolution.upper()}"},
    }
    print(f"[gemini] {model} watching {args.source}…", file=sys.stderr)
    try:
        data, _ = _request(
            "POST", f"{API}/v1beta/models/{model}:generateContent", api_key,
            body=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
        )
    finally:
        if uploaded:
            delete_file(uploaded["name"], api_key)

    candidates = data.get("candidates") or []
    text = "".join(p.get("text", "") for p in (candidates[0].get("content", {}).get("parts", []) if candidates else []))
    if not text:
        raise SystemExit(f"[gemini] empty response: {json.dumps(data)[:800]}")

    usage = data.get("usageMetadata", {})
    print("# gemini: video index")
    print()
    print(f"- **Source:** {args.source}")
    print(f"- **Model:** {model} (media resolution {args.media_resolution})")
    if meta:
        print(f"- **Range:** {args.start or '00:00'} → {args.end or 'end'}")
    print(f"- **Gemini tokens:** {usage.get('promptTokenCount', '?')} in / "
          f"{usage.get('candidatesTokenCount', '?')} out")
    print("- **Caveat:** Gemini can misread small on-screen numbers. Check anything that matters "
          "against frames (`watch.py --start/--end`).")
    print()
    print(text.strip())
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
