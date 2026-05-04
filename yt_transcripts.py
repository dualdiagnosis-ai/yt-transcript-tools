#!/usr/bin/env python3
"""
yt_transcripts.py  —  Bulk YouTube transcript downloader + AI summarizer
==========================================================================

Four modes, each saving/updating files as  01_Title.txt, 02_Title.txt, …

  channel   ALL videos on a channel, ordered OLDEST → NEWEST (no limit)
  playlist  ALL videos in a playlist, in PLAYLIST ORDER (no limit)
  search    Interactive: prompts for query & result count (25/50/100/200/custom)
  summarize Batch-summarize an existing transcript directory

USAGE
-----
  python yt_transcripts.py channel  "https://www.youtube.com/@Channel"
  python yt_transcripts.py playlist "https://www.youtube.com/playlist?list=PLxxx"
  python yt_transcripts.py search                        # fully interactive
  python yt_transcripts.py search   "your query"         # prompts for count
  python yt_transcripts.py search   "your query" --limit 50  # non-interactive
  python yt_transcripts.py summarize ./transcripts --style bullets

COMMON OPTIONS  (channel / playlist)
  --output-dir DIR      Save directory            (default: ./transcripts)
  --lang CODE           Preferred transcript language   (default: en)
  --delay SECS          Pause between requests          (default: 1.0)
  --summarize           Append an AI summary to each file inline
  --summary-model M     LLM model                (default: claude-opus-4-7)
  --style STYLE         prose | bullets | technical | brief  (default: prose)

SEARCH OPTIONS
  query                 Search query (prompted interactively if omitted)
  --limit N             Skip the interactive menu and download N results
  --output-dir DIR      Save directory  (default: ./transcripts)
  --lang / --delay / --summarize / --summary-model / --style  (same as above)

SUMMARIZE MODE OPTIONS
  input_dir             Directory with existing transcript .txt files
  --model M             LLM model                (default: claude-opus-4-7)
  --style STYLE         prose | bullets | technical | brief  (default: prose)
  --overwrite           Re-generate summaries for files that already have one

SUMMARIZATION DETAILS
  Transcripts <= 12,000 chars  -> single LLM call
  Transcripts  > 12,000 chars  -> map-reduce:
      MAP:    each ~8,000-char chunk is summarized independently
      REDUCE: chunk summaries are synthesized into a final styled summary
  Claude models use the streaming API (.stream + get_final_message)
  The system prompt is sent with cache_control=ephemeral so repeated calls
  in the same batch benefit from Anthropic prompt cache (5-min TTL).
  Opus 4.7 / Opus 4.6 / Sonnet 4.6 use adaptive thinking automatically.
  A _digest.txt master file is written after all summaries complete.

INSTALLATION
  pip install yt-dlp youtube-transcript-api
  pip install anthropic    # Claude (recommended) — set ANTHROPIC_API_KEY
  pip install openai       # OpenAI option        — set OPENAI_API_KEY

MODELS
  claude-opus-4-7   (default, recommended)
  claude-haiku-4-5  (faster, cheaper)
  gpt-4o-mini       (OpenAI option)
  gpt-4o            (OpenAI, higher quality)
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Dependency checks ────────────────────────────────────────────────────────

try:
    from yt_dlp import YoutubeDL
except ImportError:
    sys.exit("ERROR: yt-dlp not installed.  Run:  pip install yt-dlp")

try:
    from youtube_transcript_api import (
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
        YouTubeTranscriptApi,
    )
except ImportError:
    sys.exit(
        "ERROR: youtube-transcript-api not installed.\n"
        "Run:  pip install youtube-transcript-api"
    )


# ════════════════════════════════════════════════════════════════════════════
# UTILITY HELPERS
# ════════════════════════════════════════════════════════════════════════════

def sanitize_filename(name: str, max_len: int = 80) -> str:
    """Strip filesystem-unsafe characters and trim to max_len."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name)
    name = re.sub(r'[_\s]+', '_', name).strip('_')
    return name[:max_len] if name else "untitled"


def zero_pad(index: int, total: int) -> str:
    """Return zero-padded index string (minimum 2 digits)."""
    return str(index).zfill(max(2, len(str(total))))


def section(label: str, width: int = 60) -> str:
    bar = "─" * width
    return f"{bar}\n{label}\n{bar}"


# ════════════════════════════════════════════════════════════════════════════
# TRANSCRIPT FETCHING
# ════════════════════════════════════════════════════════════════════════════

def fetch_transcript(video_id: str, languages: list[str]) -> str | None:
    """
    Fetch the plain-text transcript for a single video.
    Strategy: try preferred languages, then fall back to any available transcript.
    Returns None if no transcript is available at all.
    """
    api = YouTubeTranscriptApi()

    try:
        fetched = api.fetch(video_id, languages=languages)
        return " ".join(s.text for s in fetched if s.text.strip())
    except NoTranscriptFound:
        pass
    except (TranscriptsDisabled, VideoUnavailable):
        return None
    except Exception as exc:
        print(f"    [warn] Transcript error for {video_id}: {exc}")
        return None

    # Fallback: accept any available language
    try:
        for t in api.list(video_id):
            try:
                fetched = t.fetch()
                return " ".join(s.text for s in fetched if s.text.strip())
            except Exception:
                continue
    except Exception:
        pass

    return None


# ════════════════════════════════════════════════════════════════════════════
# AI SUMMARIZATION
# ════════════════════════════════════════════════════════════════════════════

# Models that support adaptive thinking
_ADAPTIVE_THINKING_MODELS: frozenset[str] = frozenset({
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
})

# Map-reduce thresholds
_CHUNK_SIZE       = 8_000   # chars per map-step chunk
_DIRECT_THRESHOLD = 12_000  # below -> single call; above -> map-reduce

# ── Prompts ───────────────────────────────────────────────────────────────────
# The system prompt is sent with cache_control=ephemeral.
# When processing many videos in one run the prompt is cached after the first
# call (5-min Anthropic TTL), reducing input token costs for subsequent videos.

_SUMMARIZE_SYSTEM = """\
You are an expert analyst who summarizes YouTube video transcripts with precision and clarity.
Your summaries faithfully represent the source material and are immediately useful to readers
who have not watched the video.

SUMMARY STYLES
==============

prose (default)
  Write 3-5 paragraphs of flowing prose covering:
  - Para 1: the topic, speaker context, and stated purpose of the video.
  - Para 2: the main argument, thesis, or central narrative arc.
  - Para 3-4: key supporting evidence, examples, demonstrations, or data.
  - Para 5 (if applicable): conclusions, recommendations, or calls to action.
  Use clear transitions. No bullet points. Journalistic, accessible tone.

bullets
  Write a structured bullet-point summary with these labeled sections:
  - TOPIC: One sentence -- what is this video fundamentally about?
  - MAIN ARGUMENT: 1-2 sentences -- the central claim or narrative thread.
  - KEY POINTS (5-10 bullets): the most important ideas, facts, or assertions.
  - NOTABLE EXAMPLES / DATA (2-5 bullets): specific cases, statistics, demos cited.
  - TOOLS / RESOURCES MENTIONED: named software, books, frameworks, or courses.
  - CONCLUSIONS (2-4 bullets): what the video concludes and any viewer action items.

technical
  Write for a professional or expert audience.
  Abstract (2-3 sentences): what the video covers and why it is relevant.
  Methods / Approach: how is the topic addressed -- methodology, framework, process?
  Technical Details: specific technologies, algorithms, formulas, benchmarks, or
    quantitative results. Be precise: "achieves 94.2% top-1 accuracy on ImageNet"
    not "performs well".
  Limitations / Caveats: anything the speaker acknowledges as uncertain or context-dependent.
  Key Takeaways: what should a practitioner do or know after watching?

brief
  A single tight paragraph of exactly 40-70 words.
  Begin with the topic; end with the single most important insight or takeaway.
  No bullet points, no padding, no hedging. Every word must earn its place.

QUALITY STANDARDS (all styles)
===============================
- Faithfulness: only include information present in the transcript. Do not invent.
- Attribution: write in third person; use the speaker's name if mentioned.
- Completeness signals: if the transcript is cut off or partial, note this at the end.
- Opinion vs fact: clearly distinguish the speaker's claims from established facts.
- Technical accuracy: preserve technical terms exactly as the speaker uses them.
- Neutrality: do not evaluate whether claims are correct unless explicitly asked.

FORMAT RULES
============
- Do NOT begin with "Here is a summary of..." or any similar preamble.
- Do NOT include a heading or title at the top (the calling code adds the title).
- Do NOT include a closing remark like "I hope this was helpful."
- Respond ONLY with the summary text in the requested style.
"""

_MAP_SYSTEM = """\
You are summarizing one segment of a longer YouTube video transcript.
Write a concise factual summary of exactly this segment.
Capture key topics, arguments, facts, examples, and transitions.
Do not add information not present in the segment.
Do not begin with "This segment covers..." -- go straight into the content.
"""

_STYLE_PREFIXES: dict[str, str] = {
    "prose":     "Summarize the following YouTube video transcript using the prose style.",
    "bullets":   "Summarize the following YouTube video transcript using the bullets style.",
    "technical": "Summarize the following YouTube video transcript using the technical style.",
    "brief":     "Summarize the following YouTube video transcript using the brief style.",
}


# ── Text chunking ─────────────────────────────────────────────────────────────

def _chunk_text(text: str, size: int = _CHUNK_SIZE) -> list[str]:
    """Split text into chunks of approximately `size` chars at word boundaries."""
    words: list[str] = text.split()
    chunks: list[str] = []
    current: list[str] = []
    cur_len = 0
    for word in words:
        w = len(word) + 1
        if cur_len + w > size and current:
            chunks.append(" ".join(current))
            current, cur_len = [], 0
        current.append(word)
        cur_len += w
    if current:
        chunks.append(" ".join(current))
    return chunks


# ── Claude (Anthropic) ────────────────────────────────────────────────────────

def _require_anthropic():
    try:
        import anthropic  # type: ignore
        return anthropic
    except ImportError:
        sys.exit(
            "ERROR: anthropic package not installed.\n"
            "Run:  pip install anthropic   and set ANTHROPIC_API_KEY"
        )


def _call_claude(model: str, system: str, user_text: str) -> str:
    """
    Stream a Claude API call with:
      - Ephemeral prompt caching on the system prompt
      - Adaptive thinking for Opus 4.7 / Opus 4.6 / Sonnet 4.6
    Returns the text response, filtering out any thinking blocks.
    """
    anthropic = _require_anthropic()
    client    = anthropic.Anthropic()

    extra: dict = {}
    if model in _ADAPTIVE_THINKING_MODELS:
        extra["thinking"] = {"type": "adaptive"}

    with client.messages.stream(
        model=model,
        max_tokens=2048,
        system=[{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_text}],
        **extra,
    ) as stream:
        msg = stream.get_final_message()

    # Adaptive thinking may produce thinking blocks before the answer block.
    # Extract only text blocks (thinking blocks have type "thinking").
    return "\n".join(b.text for b in msg.content if b.type == "text")


# ── OpenAI ────────────────────────────────────────────────────────────────────

def _call_openai(model: str, system: str, user_text: str) -> str:
    try:
        import openai  # type: ignore
    except ImportError:
        sys.exit(
            "ERROR: openai package not installed.\n"
            "Run:  pip install openai   and set OPENAI_API_KEY"
        )
    client = openai.OpenAI()
    resp   = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user_text},
        ],
    )
    return resp.choices[0].message.content or ""


# ── Map-reduce for long transcripts ──────────────────────────────────────────

def _map_reduce_summarize(text: str, model: str, style: str) -> str:
    """
    MAP:    summarize each ~8,000-char chunk independently.
    REDUCE: synthesize all chunk summaries into a final styled summary.
    """
    _call  = _call_claude if "claude" in model.lower() else _call_openai
    chunks = _chunk_text(text)
    total  = len(chunks)
    print(f"         -> map-reduce: {total} chunks x ~{_CHUNK_SIZE:,} chars")

    chunk_summaries: list[str] = []
    for idx, chunk in enumerate(chunks, 1):
        print(f"         -> chunk {idx}/{total}...", end="", flush=True)
        cs = _call(model, _MAP_SYSTEM, f"[Segment {idx} of {total}]\n\n{chunk}")
        print(" done")
        chunk_summaries.append(f"[Segment {idx}]\n{cs}")

    combined   = "\n\n".join(chunk_summaries)
    prefix     = _STYLE_PREFIXES.get(style, _STYLE_PREFIXES["prose"])
    reduce_msg = (
        f"{prefix}\n\n"
        f"The transcript was long and has been pre-summarized in {total} sequential "
        f"segments below. Synthesize these into a single cohesive summary as if you "
        f"had read the full transcript.\n\n{combined}"
    )
    print("         -> reduce step...", end="", flush=True)
    result = _call(model, _SUMMARIZE_SYSTEM, reduce_msg)
    print(" done")
    return result


# ── Public entry point ────────────────────────────────────────────────────────

def summarize_transcript(text: str, model: str, style: str = "prose") -> str:
    """
    Summarize `text` using `model` in the given `style`.

    Routing:
      "claude" in model name  ->  Anthropic SDK (streaming + prompt caching)
      anything else           ->  OpenAI SDK

    Transcripts longer than 12,000 chars are handled with map-reduce to
    preserve full content rather than truncating.
    """
    _call = _call_claude if "claude" in model.lower() else _call_openai

    if len(text) <= _DIRECT_THRESHOLD:
        prefix = _STYLE_PREFIXES.get(style, _STYLE_PREFIXES["prose"])
        return _call(model, _SUMMARIZE_SYSTEM, f"{prefix}\n\n{text}")

    return _map_reduce_summarize(text, model, style)


# ════════════════════════════════════════════════════════════════════════════
# DIGEST WRITER
# ════════════════════════════════════════════════════════════════════════════

# File-format section headers (used for writing and later for parsing)
_SUMMARY_HDR    = section("SUMMARY")
_TRANSCRIPT_HDR = section("TRANSCRIPT")


def write_digest(
    entries: list[tuple[int, str, str, str]],
    output_dir: Path,
    model: str,
    style: str,
) -> None:
    """
    Write _digest.txt containing every summary in sequence.
    entries: list of (position, title, url, summary_text)
    """
    total = len(entries)
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pad   = max(2, len(str(total)))

    lines = [
        "=" * 60,
        f"DIGEST  --  {total} VIDEO SUMMARIES",
        f"Generated : {now}",
        f"Directory : {output_dir.resolve()}",
        f"Model     : {model}",
        f"Style     : {style}",
        "=" * 60,
        "",
    ]
    for pos, title, url, summary in entries:
        lines += [
            f"[{str(pos).zfill(pad)}/{str(total).zfill(pad)}]  {title}",
            f"URL: {url}",
            "-" * 60,
            summary.strip(),
            "",
            "",
        ]

    digest_path = output_dir / "_digest.txt"
    digest_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  Digest written -> {digest_path}")


# ════════════════════════════════════════════════════════════════════════════
# VIDEO LIST FETCHERS  (yt-dlp flat-playlist)
# ════════════════════════════════════════════════════════════════════════════

_YDL_FLAT_OPTS: dict = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "ignoreerrors": True,
}


def _ydl_extract_entries(url: str) -> list[dict]:
    """Run yt-dlp flat extraction and return the list of entry dicts."""
    with YoutubeDL(_YDL_FLAT_OPTS) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return []
    return [e for e in (info.get("entries") or []) if e and e.get("id")]


def get_channel_videos(channel_url: str) -> list[dict]:
    """
    Return ALL regular videos from a channel ordered OLDEST -> NEWEST.
    YouTube returns newest-first; we reverse the list.
    Appends /videos to restrict to regular uploads (no Shorts, no streams).
    """
    if "/videos" not in channel_url and ("/@" in channel_url or "/channel/" in channel_url):
        url = channel_url.rstrip("/") + "/videos"
    else:
        url = channel_url
    print("  Fetching full video list (may take a while for large channels)...")
    return list(reversed(_ydl_extract_entries(url)))


def get_playlist_videos(playlist_url: str) -> list[dict]:
    """
    Return ALL videos from a playlist in PLAYLIST ORDER (position 1, 2, 3 ...).

    Accepts both canonical playlist URLs and video-with-playlist URLs:
      https://www.youtube.com/playlist?list=PLxxx
      https://www.youtube.com/watch?v=xxx&list=PLxxx  (list= is extracted)
    """
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(playlist_url)
    params = parse_qs(parsed.query)
    if "list" in params and "playlist" not in parsed.path:
        list_id     = params["list"][0]
        playlist_url = f"https://www.youtube.com/playlist?list={list_id}"
        print(f"  Extracted playlist ID: {list_id}")
    print("  Fetching full playlist video list...")
    return _ydl_extract_entries(playlist_url)


def get_search_videos(query: str, limit: int) -> list[dict]:
    """
    Return top `limit` YouTube search results IN SERP RANK ORDER.
    yt-dlp's ytsearchN:QUERY returns results in YouTube's own search rank order.
    `limit` must be a positive integer; any value is accepted.
    """
    print(f'  Running YouTube search: "{query}" (top {limit} results)...')
    return _ydl_extract_entries(f"ytsearch{limit}:{query}")


# ════════════════════════════════════════════════════════════════════════════
# CORE PROCESSOR  (channel / playlist / search modes)
# ════════════════════════════════════════════════════════════════════════════

def process_videos(
    entries: list[dict],
    output_dir: Path,
    languages: list[str],
    do_summarize: bool,
    summary_model: str,
    summary_style: str,
    delay: float,
    source_label: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(entries)

    if total == 0:
        print("No videos found. Check the URL/query and try again.")
        return

    print(f"\nFound {total} videos from {source_label}")
    print(f"Saving transcripts to: {output_dir.resolve()}\n")

    saved = skipped = no_transcript = 0
    digest_entries: list[tuple] = []

    for i, entry in enumerate(entries, start=1):
        video_id  = entry.get("id", "")
        title     = entry.get("title") or f"video_{video_id}"
        upload_dt = entry.get("upload_date", "") or ""

        pad       = zero_pad(i, total)
        filename  = f"{pad}_{sanitize_filename(title)}.txt"
        filepath  = output_dir / filename
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        print(f"[{pad}/{zero_pad(total, total)}] {title[:72]}")

        if filepath.exists():
            print("         -> already exists, skipping")
            skipped += 1
            continue

        transcript = fetch_transcript(video_id, languages)
        if transcript is None:
            print("         -> no transcript available")
            no_transcript += 1
            time.sleep(delay)
            continue

        summary_text: str | None = None
        if do_summarize:
            print(f"         -> summarizing ({len(transcript):,} chars, style={summary_style})...")
            try:
                summary_text = summarize_transcript(transcript, summary_model, summary_style)
            except Exception as exc:
                print(f"         [warn] Summarization failed: {exc}")

        parts = [
            section("METADATA"),
            f"Video ID:     {video_id}",
            f"Title:        {title}",
            f"Upload Date:  {upload_dt or 'unknown'}",
            f"URL:          {video_url}",
            f"Position:     #{i} of {total}  ({source_label})",
            "",
        ]
        if summary_text:
            parts += [_SUMMARY_HDR, summary_text, ""]
            digest_entries.append((i, title, video_url, summary_text))
        parts += [_TRANSCRIPT_HDR, transcript, ""]

        filepath.write_text("\n".join(parts), encoding="utf-8")
        print(f"         -> saved  {filename}")
        saved += 1
        time.sleep(delay)

    if do_summarize and digest_entries:
        write_digest(digest_entries, output_dir, summary_model, summary_style)

    print(
        f"\n{'=' * 60}\n"
        f"Complete.  Saved: {saved}  |  "
        f"Skipped (exists): {skipped}  |  "
        f"No transcript: {no_transcript}\n"
        f"Output directory: {output_dir.resolve()}"
    )


# ════════════════════════════════════════════════════════════════════════════
# SUMMARIZE MODE  (batch-summarize an existing transcript directory)
# ════════════════════════════════════════════════════════════════════════════

def run_summarize_mode(
    input_dir: Path,
    model: str,
    style: str,
    overwrite: bool,
) -> None:
    """
    Batch-summarize all transcript .txt files in input_dir.

    For each file:
      - Parses the TRANSCRIPT section.
      - Calls summarize_transcript() (map-reduce for long texts).
      - Inserts or replaces the SUMMARY section between METADATA and TRANSCRIPT.

    After all files: writes _digest.txt with every summary in sequence.
    Files that already have a SUMMARY are skipped unless --overwrite is set.
    """
    files = sorted(f for f in input_dir.glob("*.txt") if not f.name.startswith("_"))
    if not files:
        print(f"No .txt transcript files found in {input_dir.resolve()}")
        print("Run the channel / playlist / search mode first to download transcripts.")
        return

    total = len(files)
    print(f"\nFound {total} transcript file(s) in {input_dir.resolve()}")
    print(f"Model: {model}  |  Style: {style}\n")

    pad = max(2, len(str(total)))
    processed = skipped = failed = 0
    digest_entries: list[tuple] = []

    for i, filepath in enumerate(files, start=1):
        content = filepath.read_text(encoding="utf-8")
        label   = f"[{str(i).zfill(pad)}/{str(total).zfill(pad)}]"

        title_m = re.search(r"^Title:\s+(.+)$", content, re.MULTILINE)
        url_m   = re.search(r"^URL:\s+(.+)$",   content, re.MULTILINE)
        title   = title_m.group(1).strip() if title_m else filepath.stem
        url     = url_m.group(1).strip()   if url_m   else ""

        print(f"{label} {title[:72]}")

        has_summary = _SUMMARY_HDR in content
        if has_summary and not overwrite:
            print("         -> already summarized (use --overwrite to redo)")
            m = re.search(
                r"─{60}\nSUMMARY\n─{60}\n(.*?)(?=\n─{60}\n|\Z)",
                content, re.DOTALL,
            )
            existing = m.group(1).strip() if m else "[see file]"
            digest_entries.append((i, title, url, existing))
            skipped += 1
            continue

        if _TRANSCRIPT_HDR not in content:
            print("         -> [skip] no TRANSCRIPT section found in file")
            failed += 1
            continue

        transcript_text = content.split(_TRANSCRIPT_HDR, 1)[1].strip()
        if not transcript_text:
            print("         -> [skip] TRANSCRIPT section is empty")
            failed += 1
            continue

        print(f"         -> summarizing ({len(transcript_text):,} chars)...")
        try:
            summary = summarize_transcript(transcript_text, model, style)
        except Exception as exc:
            print(f"         -> [error] {exc}")
            failed += 1
            continue

        if has_summary:
            # Replace existing summary block (--overwrite path)
            new_content = re.sub(
                r"(─{60}\nSUMMARY\n─{60}\n)(.*?)(?=\n─{60}\n)",
                lambda m2: m2.group(1) + summary + "\n",
                content, count=1, flags=re.DOTALL,
            )
        else:
            # Insert SUMMARY before the TRANSCRIPT section
            before, after = content.split(_TRANSCRIPT_HDR, 1)
            new_content = (
                before
                + _SUMMARY_HDR + "\n"
                + summary + "\n\n"
                + _TRANSCRIPT_HDR
                + after
            )

        filepath.write_text(new_content, encoding="utf-8")
        print(f"         -> updated  {filepath.name}")

        digest_entries.append((i, title, url, summary))
        processed += 1

    if digest_entries:
        write_digest(digest_entries, input_dir, model, style)

    print(
        f"\n{'=' * 60}\n"
        f"Complete.  Summarized: {processed}  |  "
        f"Skipped (exists): {skipped}  |  Errors: {failed}\n"
        f"Directory: {input_dir.resolve()}"
    )


# ════════════════════════════════════════════════════════════════════════════
# INTERACTIVE SEARCH PROMPT
# ════════════════════════════════════════════════════════════════════════════

_SEARCH_MENU = [
    ("1", 25,  " 1)   25 results"),
    ("2", 50,  " 2)   50 results"),
    ("3", 100, " 3)  100 results"),
    ("4", 200, " 4)  200 results"),
    ("5", None, " 5)  Other — please specify"),
]


def _prompt_search(query: str | None, limit: int | None) -> tuple[str, int]:
    """
    Interactively prompt for the search query and/or result count if not
    already provided on the command line.

    Returns (query, limit).
    """
    # ── Step a: search term ───────────────────────────────────────────────────
    if not query:
        print()
        query = input("  Enter your YouTube search query: ").strip()
        if not query:
            sys.exit("ERROR: Search query cannot be empty.")

    # ── Step b: result count (show menu only when --limit was not passed) ─────
    if limit is None:
        print()
        print(f'  Search query: "{query}"')
        print("  How many search results would you like to download?")
        print()
        for key, val, label in _SEARCH_MENU:
            print(f"    {label}")
        print()

        while True:
            choice = input("  Enter your choice [1-5]: ").strip()
            matched = {key: val for key, val, _ in _SEARCH_MENU}.get(choice)
            if matched is not None:
                limit = matched
                break
            if choice == "5":
                while True:
                    raw = input("  Enter the number of results to download: ").strip()
                    try:
                        n = int(raw)
                        if n > 0:
                            limit = n
                            break
                        print("  Please enter a positive integer.")
                    except ValueError:
                        print("  Invalid input. Please enter a whole number.")
                break
            print("  Invalid choice. Please enter 1, 2, 3, 4, or 5.")

    return query, limit


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="yt_transcripts.py",
        description="Bulk YouTube transcript downloader + AI summarizer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = root.add_subparsers(dest="mode", required=True, metavar="MODE")

    # ── Shared parent for channel / playlist ────────────────────────────────
    # Note: --limit is intentionally NOT in the shared parser.
    # Channel and playlist always download EVERYTHING.
    # Search has its own --limit (or interactive menu).
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--output-dir", default="./transcripts",
                        help="Save directory  (default: ./transcripts)")
    shared.add_argument("--lang", default="en",
                        help="Preferred transcript language code  (default: en)")
    shared.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between requests  (default: 1.0)")
    shared.add_argument("--summarize", action="store_true",
                        help="Append an AI summary to each transcript file")
    shared.add_argument("--summary-model", default="claude-opus-4-7",
                        help="LLM model for summarization  (default: claude-opus-4-7)")
    shared.add_argument("--style", default="prose",
                        choices=["prose", "bullets", "technical", "brief"],
                        help="Summary style  (default: prose)")

    # channel — downloads ALL videos with no limit
    p_ch = sub.add_parser(
        "channel", parents=[shared],
        help="Download ALL videos on a channel, OLDEST -> NEWEST (no limit)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_ch.add_argument("url", help="YouTube channel URL")

    # playlist — downloads ALL videos with no limit
    p_pl = sub.add_parser(
        "playlist", parents=[shared],
        help="Download ALL videos in a playlist, in PLAYLIST ORDER (no limit)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_pl.add_argument("url", help="YouTube playlist URL")

    # search — interactive prompts for query + count, or pass both on CLI
    p_sr = sub.add_parser(
        "search", parents=[shared],
        help="Download top-N search results in SERP RANK ORDER (interactive menu)",
        description=(
            "Download transcripts for YouTube search results in SERP rank order.\n\n"
            "If the query is omitted, you will be prompted to enter it.\n"
            "If --limit is omitted, an interactive menu lets you choose:\n"
            "  1) 25   2) 50   3) 100   4) 200   5) Other (specify)\n\n"
            "Pass --limit N to skip the menu (useful for scripting).\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_sr.add_argument(
        "query", nargs="?", default=None,
        help="YouTube search query (prompted interactively if omitted)",
    )
    p_sr.add_argument(
        "--limit", type=int, default=None,
        help="Number of results to download — skips the interactive menu",
    )

    # summarize
    p_sm = sub.add_parser(
        "summarize",
        help="Batch-summarize existing transcript directory + write _digest.txt",
        description=(
            "Add AI summaries to all .txt transcript files in a directory.\n"
            "Inserts a SUMMARY section into each file (between METADATA and TRANSCRIPT).\n"
            "Also writes _digest.txt with all summaries combined in one document.\n\n"
            "Long transcripts are handled automatically with map-reduce chunking.\n"
            "Files that already have a SUMMARY section are skipped unless --overwrite.\n\n"
            "Examples:\n"
            "  python yt_transcripts.py summarize ./transcripts\n"
            "  python yt_transcripts.py summarize ./transcripts --style bullets\n"
            "  python yt_transcripts.py summarize ./transcripts --model claude-haiku-4-5\n"
            "  python yt_transcripts.py summarize ./transcripts --overwrite"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_sm.add_argument("input_dir", type=Path,
                      help="Directory containing transcript .txt files")
    p_sm.add_argument("--model", default="claude-opus-4-7",
                      help="LLM model  (default: claude-opus-4-7)")
    p_sm.add_argument("--style", default="prose",
                      choices=["prose", "bullets", "technical", "brief"],
                      help="Summary style  (default: prose)")
    p_sm.add_argument("--overwrite", action="store_true",
                      help="Re-generate summaries for files that already have one")

    return root


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    # summarize subcommand
    if args.mode == "summarize":
        if not args.input_dir.is_dir():
            sys.exit(f"ERROR: '{args.input_dir}' is not a directory")
        print(f"\n{'=' * 60}")
        print("MODE: Summarize  (batch-summarize existing transcripts)")
        print(f"Directory : {args.input_dir.resolve()}")
        print(f"Model     : {args.model}  |  Style: {args.style}")
        run_summarize_mode(
            input_dir=args.input_dir,
            model=args.model,
            style=args.style,
            overwrite=args.overwrite,
        )
        return

    # channel / playlist / search
    lang_code  = args.lang.strip().lower()
    languages  = list(dict.fromkeys([lang_code, "en", "en-US", "en-GB", "en-auto"]))
    output_dir = Path(args.output_dir)

    if args.mode == "channel":
        print(f"\n{'=' * 60}")
        print("MODE: Channel  (ALL videos, oldest -> newest, no limit)")
        print(f"URL : {args.url}")
        entries      = get_channel_videos(args.url)
        source_label = f"channel  {args.url}"

    elif args.mode == "playlist":
        print(f"\n{'=' * 60}")
        print("MODE: Playlist  (ALL videos, playlist order, no limit)")
        print(f"URL : {args.url}")
        entries      = get_playlist_videos(args.url)
        source_label = f"playlist  {args.url}"

    elif args.mode == "search":
        query, limit = _prompt_search(
            getattr(args, "query", None),
            getattr(args, "limit", None),
        )
        print(f"\n{'=' * 60}")
        print("MODE: Search  (SERP rank order)")
        print(f'Query: "{query}"  |  Results: {limit}')
        entries      = get_search_videos(query, limit)
        source_label = f'search "{query}" top {limit}'

    else:
        parser.print_help()
        sys.exit(1)

    process_videos(
        entries       = entries,
        output_dir    = output_dir,
        languages     = languages,
        do_summarize  = args.summarize,
        summary_model = args.summary_model,
        summary_style = args.style,
        delay         = args.delay,
        source_label  = source_label,
    )


if __name__ == "__main__":
    main()
