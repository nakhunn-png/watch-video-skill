---
name: watch-video
description: SLASH-COMMAND-ONLY. Invoke ONLY when the user explicitly types the literal `/watch-video` slash command. Never auto-trigger on phrases like "watch this video," "summarize this video," "take notes on this video," or on a bare YouTube URL. For ad-hoc YouTube questions, fetch the transcript directly (YouTube page or yt-dlp) and skim — do not run this heavyweight pipeline.
---

# Watch Video

Claude can't stream video directly. This skill fakes it with scripts under `scripts/` (pipeline vendored from [bradautomates/claude-video](https://github.com/bradautomates/claude-video)):

- `watch.py`: downloads the video, pulls a timestamped transcript (the video's own captions first, Whisper API fallback), extracts frames with ffmpeg, drops near-duplicate frames, and prints a markdown report listing every frame path.
- `gemini.py`: has Gemini watch the whole video (frames + audio) and prints a short timestamped text index. Gemini pays for the video tokens; Claude reads only the text.

The job is to answer the user's question with as few Claude tokens as the question allows. Pick the cheapest tier that can answer it, and go deeper only where it can't.

## When to invoke

**Slash-command only.** Run this skill ONLY when the user literally types `/watch-video`. That is the sole trigger.

Do NOT invoke on:
- Casual phrases like "watch this video," "summarize this," "take notes on this YouTube video," "analyze this reel"
- A bare YouTube URL pasted with a question ("what useful tips are in this video?" + URL)
- Any natural-language ask about a video that lacks the explicit `/watch-video` command

**Default for YouTube questions without the slash command:** pull the transcript the fastest way available (yt-dlp captions, or `watch.py --transcript-only`), skim it, and answer from that.

## Dependencies

- **ffmpeg + ffprobe** and **yt-dlp** on PATH, **Python 3.9+**
- **Pillow** (optional) for frame dedupe. Without it every frame is kept.
- Keys, read from the environment first, then `~/.config/watch/.env`:
  - `GEMINI_API_KEY` (optional): enables `gemini.py`. `GEMINI_MODEL` overrides the default model (`gemini-3.8-flash`).
  - `GROQ_API_KEY` (preferred) or `OPENAI_API_KEY` (optional): Whisper fallback for videos without captions.

`python scripts/setup.py --check` is silent when ready; `--json` shows which keys are present.

## Cost model

| What Claude reads | Approx. Claude tokens |
|---|---|
| Transcript, 10-min talk | 3–5k |
| One 512px frame | ~200 |
| One 1024px frame | ~800 |
| 40 frames at 512px | ~8k |
| Gemini index, any length | 1–3k |

- Tiling frames into contact sheets saves tool calls, not tokens. Image tokens scale with pixel area.
- Gemini's own usage (~50k tokens for a 10-min video at low media resolution) is billed to the Gemini key, not to Claude.

## Routing: pick the cheapest tier that answers the question

Decide before running anything, from the user's question and the video's length.

**Tier 0: transcript only.** Use it when the answer is in what is said: summaries of talks, lectures, interviews, podcasts, "what does X say about Y".
```
python scripts/watch.py "<url>" --transcript-only
```
No download, no frames. Captions come in the video's spoken language, not an auto-translation (`--lang ko` to force one). If no captions come back, go to Tier 1.

**Tier 1: Gemini index.** Use it when the visuals matter (slides, screen demos, spreadsheets, charts, on-screen numbers) and `GEMINI_API_KEY` is set. Also use it when there are no captions, or the video runs past 10 minutes.
```
python scripts/gemini.py "<url-or-file>" [--ask "<the user's question>"] [--start T --end T]
```
- It returns an overview, a scene index with `[MM:SS-MM:SS]` ranges and verbatim on-screen text, the numbers shown, and a "Verify with frames" list.
- `--ask` puts the user's question to Gemini as well.
- `--media-resolution` defaults to `low`. Use `high` only on a short range with small text.
- Run it alongside Tier 0 when both speech and visuals matter. The transcript is the reliable record of what was said; the index tells you where things are on screen.

**Tier 2: targeted frames.** Use it to confirm specific visual facts: exact figures, table contents, code, UI details. Every number you put in an answer from a video needs a frame behind it, because Gemini misreads small on-screen numbers.
```
python scripts/watch.py "<source>" --start 07:05 --end 07:40 --max-frames 8
```
- Take the ranges from the Gemini index ("Verify with frames") or from transcript cues ("엑셀 파일을 열어 보면…", "as you can see on this slide").
- Keep `--max-frames` small and add `--resolution 1024` only when 512px text is unreadable. Read only the frames you need.

**Tier 3: full frame scan.** This is the fallback when there is no Gemini key and the whole visual flow matters, or the user asks for complete visual notes.
```
python scripts/watch.py "<source>" --max-frames 40
```
- Near-duplicate frames are dropped automatically (`--dedupe 0.02`; `0` disables). A static slide shows up once as `t=02:42-03:55`.
- On slide- or screen-heavy videos about half the frames go.
- Past 10 minutes, prefer Tier 1 + Tier 2 over a sparse full scan.

**Several videos:** run them in parallel (background shell jobs), each with its own `--out-dir` in the scratchpad, at the same tier.

**Very long videos (>30 min):** confirm with the user before Tier 3. Tier 0 and Tier 1 are fine to run.

## watch.py flags

- `--transcript-only`: captions + metadata, no download (URLs only)
- `--start T` / `--end T`: focus range (`SS`, `MM:SS`, `HH:MM:SS`); the frame budget gets denser inside it
- `--max-frames N`: frame cap (default 80, hard max 100)
- `--resolution W`: frame width (default 512)
- `--dedupe X`: near-duplicate threshold (default 0.02, 0 = off)
- `--lang CODE`: caption language (default: the video's spoken language)
- `--para-chars N`: transcript paragraph size (default 300; 0 = one line per cue)
- `--whisper groq|openai`, `--no-whisper`: Whisper fallback control
- `--out-dir DIR`: working directory (default: tmp)

The report has a header (title, uploader, duration, transcript source), `## Frames` (`- \`<path>\` (t=MM:SS[-MM:SS])`), `## Transcript` (`[MM:SS] paragraph…`), and a `Work dir:` footer.

## Answering

- **For a question:** answer it directly, citing `[MM:SS]`. Don't write a notes file nobody asked for.
- **For notes or a summary:** write `<slug>-notes.md` (in the user's working folder when they want to keep it, otherwise the work dir):

```markdown
# <Title>
**Source:** <URL> · **Duration:** <mm:ss> · **Transcript:** <captions / whisper / gemini / none>

## One-line summary
## TL;DR            (3–5 bullets)
## Timeline         (one row per meaningful beat: **[mm:ss]** what's on screen + what's said)
## Key quotes       (> "verbatim" — [mm:ss])
## Visual notes     (what the transcript alone would miss)
## Takeaways        (only if relevant to the user's goals)
```

- Say which tiers you used. When a figure comes from the Gemini index and has not been checked against a frame, flag it as unverified.

## Clean up

Delete each `Work dir` after answering; it holds the downloaded video and frames. If the user chose a non-tmp `--out-dir`, ask first. `gemini.py` deletes any file it uploaded.

## Common gotchas

- **Shorts / age-gated / members-only:** yt-dlp may fail. Show its stderr verbatim; don't retry silently. `gemini.py` only works with public YouTube URLs. For anything else, pass a local file.
- **Gemini 404 "model no longer available":** the model was retired. Set `GEMINI_MODEL` to the one named in the error message.
- **Gemini misreads small text:** low media resolution blurs spreadsheet cells. Verify figures with Tier 2 frames.
- **No captions + no keys:** frames only. Tell the user they can add `GEMINI_API_KEY` or `GROQ_API_KEY`.
- **Windows console encoding:** scripts force UTF-8 stdout. If you post-process output in your own Python, set `PYTHONIOENCODING=utf-8`.
- **Groq Cloudflare 403:** `whisper.py` sets a custom User-Agent to avoid it. There is no automatic Groq → OpenAI fallback; pass `--whisper openai`.

## What NOT to do

- Don't "watch" a video by inventing content from its title or thumbnail. If the pipeline fails, say so.
- Don't describe visuals you haven't seen. To describe the screen you need either a Gemini index or frames.
- Don't run Tier 3 by reflex when Tier 0 or Tier 1 already answers the question.
- Don't skip cleanup.
- Don't auto-fire on a video URL. Slash-command only.

## Engine attribution

The download/frames/Whisper scripts are vendored from [bradautomates/claude-video](https://github.com/bradautomates/claude-video) (MIT). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
