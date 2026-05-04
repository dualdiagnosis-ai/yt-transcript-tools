# yt_transcripts.py — YouTube Transcript Downloader & AI Summarizer

Download transcripts from any YouTube channel, playlist, or search result page and save them as sequentially numbered plain-text files. Optionally generate AI summaries using Claude (Anthropic) or GPT (OpenAI), with a `_digest.txt` master file that collects every summary in one place.

```
python yt_transcripts.py channel  "https://www.youtube.com/@veritasium"
python yt_transcripts.py playlist "https://youtube.com/playlist?list=PLxxx"
python yt_transcripts.py search   "machine learning basics" --limit 50
python yt_transcripts.py summarize ./transcripts --style bullets
```

---

## Table of Contents

1. [How It Works](#1-how-it-works)
2. [Requirements](#2-requirements)
3. [Installation](#3-installation)
4. [API Configuration](#4-api-configuration)
5. [Finding the Right YouTube URL](#5-finding-the-right-youtube-url)
6. [Quick Start & Expected Output](#6-quick-start--expected-output)
7. [Modes](#7-modes)
   - [channel](#channel-mode)
   - [playlist](#playlist-mode)
   - [search](#search-mode)
   - [summarize](#summarize-mode)
8. [Options Reference](#8-options-reference)
9. [Summary Styles](#9-summary-styles)
10. [Output File Format](#10-output-file-format)
11. [How Summarization Works](#11-how-summarization-works)
12. [Ordering Guarantees](#12-ordering-guarantees)
13. [Rate Limiting & Reliability](#13-rate-limiting--reliability)
14. [Troubleshooting](#14-troubleshooting)
15. [Limitations](#15-limitations)
16. [Common Workflows](#16-common-workflows)

---

## 1. How It Works

```
YouTube
  │
  ├─ yt-dlp ──────────────────► ordered video ID + title list
  │     (no video download,         (newest→oldest for channels,
  │      metadata only)              playlist position for playlists,
  │                                  SERP rank for searches)
  │
  ├─ youtube-transcript-api ──► plain-text transcript per video
  │     (fetches captions from      (manual or auto-generated,
  │      YouTube's caption API)      language fallback chain)
  │
  └─ LLM API (optional) ──────► AI summary per transcript
        (Anthropic or OpenAI)       (map-reduce for long content,
                                     4 style options,
                                     _digest.txt for the full batch)

Output: ./transcripts/
  01_Oldest_Video_Title.txt      ← METADATA + [SUMMARY] + TRANSCRIPT
  02_Second_Video_Title.txt
  03_Third_Video_Title.txt
  ...
  _digest.txt                    ← all summaries collected in one file
```

The script **never downloads video files**. It fetches only metadata and caption text, which is fast, cheap, and requires no special authentication for public content.

---

## 2. Requirements

| Requirement | Version | Purpose |
|---|---|---|
| Python | 3.10+ | Runtime |
| `yt-dlp` | any recent | Fetch ordered video lists from YouTube |
| `youtube-transcript-api` | any recent | Download transcript/caption text |
| `anthropic` | optional | Claude summarization |
| `openai` | optional | GPT summarization |

---

## 3. Installation

### Step 1 — Install core dependencies

```bash
pip install yt-dlp youtube-transcript-api
```

### Step 2 — Install a summarization library (optional)

Only needed if you plan to use `--summarize` or the `summarize` subcommand.

```bash
# Recommended: Claude (Anthropic)
pip install anthropic

# Alternative: GPT (OpenAI)
pip install openai
```

### Step 3 — Verify the installation

```bash
python3 -c "
import yt_dlp, youtube_transcript_api
print('yt-dlp:', yt_dlp.version.__version__)
print('youtube-transcript-api: OK')
"
```

Expected output:
```
yt-dlp: 2026.03.17
youtube-transcript-api: OK
```

### Upgrading

```bash
pip install --upgrade yt-dlp youtube-transcript-api anthropic openai
```

Keeping `yt-dlp` up to date is important — YouTube regularly changes its internal APIs and `yt-dlp` updates its extractors to match.

---

## 4. API Configuration

No API key is needed to **download transcripts**. A key is only required when using `--summarize` or the `summarize` subcommand.

### Anthropic (Claude) — recommended

#### Getting a key

1. Go to [console.anthropic.com](https://console.anthropic.com) and create an account
2. Navigate to **Settings → API Keys → Create Key**
3. Copy the key immediately — it is only shown once

#### Setting the key

**For the current terminal session:**
```bash
export ANTHROPIC_API_KEY="sk-ant-api03-..."
```

**To persist across sessions — add to your shell profile:**
```bash
# For zsh (macOS default)
echo 'export ANTHROPIC_API_KEY="sk-ant-api03-..."' >> ~/.zshrc
source ~/.zshrc

# For bash
echo 'export ANTHROPIC_API_KEY="sk-ant-api03-..."' >> ~/.bashrc
source ~/.bashrc
```

**To pass inline for a single command without saving to history:**
```bash
ANTHROPIC_API_KEY="sk-ant-..." python yt_transcripts.py summarize ./transcripts
```

#### Verify the key works

```bash
python3 -c "
import anthropic
client = anthropic.Anthropic()
msg = client.messages.create(
    model='claude-haiku-4-5',
    max_tokens=16,
    messages=[{'role': 'user', 'content': 'Reply: OK'}]
)
print(msg.content[0].text)
"
```

#### Available Claude models

| Model | ID to pass | Speed | Approx. cost per summary | Notes |
|---|---|---|---|---|
| Claude Opus 4.7 | `claude-opus-4-7` | ~10–30s | $0.01–$0.05 | **Default.** Best quality. Adaptive thinking auto-enabled. |
| Claude Opus 4.6 | `claude-opus-4-6` | ~10–30s | $0.01–$0.05 | Adaptive thinking auto-enabled. |
| Claude Haiku 4.5 | `claude-haiku-4-5` | ~3–8s | $0.001–$0.005 | Best for large batches where cost matters. |

> Cost estimates assume a 10–30 minute video transcript (~5,000–15,000 chars). Long transcripts triggering map-reduce will cost proportionally more.

#### Estimating batch cost

A rough formula for a batch of N videos with Claude Opus 4.7:

```
Cost ≈ N × $0.03  (average video, prose or bullets style)
```

For 50 videos: ~$1.50. For 500 videos: ~$15. Use `claude-haiku-4-5` to cut this by ~10×.

---

### OpenAI (GPT)

#### Getting a key

1. Go to [platform.openai.com](https://platform.openai.com) and create an account
2. Navigate to **API Keys → Create new secret key**
3. Copy the key immediately

#### Setting the key

```bash
export OPENAI_API_KEY="sk-proj-..."

# Persist in shell profile
echo 'export OPENAI_API_KEY="sk-proj-..."' >> ~/.zshrc && source ~/.zshrc
```

#### Verify the key works

```bash
python3 -c "
import openai
client = openai.OpenAI()
r = client.chat.completions.create(
    model='gpt-4o-mini',
    messages=[{'role': 'user', 'content': 'Reply: OK'}],
    max_tokens=4
)
print(r.choices[0].message.content)
"
```

#### Available OpenAI models

| Model | ID to pass | Speed | Approx. cost per summary | Notes |
|---|---|---|---|---|
| GPT-4o mini | `gpt-4o-mini` | ~5–15s | $0.001–$0.003 | Good quality-to-cost ratio. |
| GPT-4o | `gpt-4o` | ~10–20s | $0.005–$0.02 | Higher quality. |

---

## 5. Finding the Right YouTube URL

### Channel URLs

The script accepts three channel URL formats. Use whichever appears in your browser:

| Format | Example |
|---|---|
| Handle (most common) | `https://www.youtube.com/@veritasium` |
| Custom URL | `https://www.youtube.com/c/veritasium` |
| Channel ID | `https://www.youtube.com/channel/UCHnyfMqiRRG1u-2MsSQLbXA` |

**To find a channel's URL:**
1. Open the channel page in a browser
2. Copy the URL from the address bar — it will be in one of the three formats above

**Default behavior:** The script appends `/videos` to target regular uploads only, excluding Shorts and live streams. To include all content, pass the full URL with the tab you want:

```bash
# Regular videos only (default)
python yt_transcripts.py channel "https://www.youtube.com/@channel"

# Everything including Shorts and streams
python yt_transcripts.py channel "https://www.youtube.com/@channel/videos"

# Shorts only
python yt_transcripts.py channel "https://www.youtube.com/@channel/shorts"

# Live streams / past streams only
python yt_transcripts.py channel "https://www.youtube.com/@channel/streams"
```

### Playlist URLs

1. Open the playlist on YouTube
2. Look at the address bar — the URL contains `list=` followed by the playlist ID
3. Copy the full URL: `https://www.youtube.com/playlist?list=PLxxxxxxxxxxxxxx`

```bash
python yt_transcripts.py playlist "https://www.youtube.com/playlist?list=PL8dPuuaLjXtNlUrzyH5r6jN9ulIgZBpdo"
```

You can also use a video URL that is part of the playlist — yt-dlp will detect and extract the full playlist:
```bash
python yt_transcripts.py playlist "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxxxxxx"
```

---

## 6. Quick Start & Expected Output

### Download without summarization

```bash
python yt_transcripts.py playlist \
  "https://www.youtube.com/playlist?list=PL8dPuuaLjXtNlUrzyH5r6jN9ulIgZBpdo" \
  --limit 3 --output-dir ./demo
```

Terminal output:
```
============================================================
MODE: Playlist  (playlist order)
URL : https://www.youtube.com/playlist?list=PL8dPuuaLjXtNlUrzyH5r6jN9ulIgZBpdo
  Fetching playlist video list...

Found 3 videos from playlist  https://...
Saving transcripts to: /Users/you/demo

[01/03] Crash Course Computer Science Preview
         -> saved  01_Crash_Course_Computer_Science_Preview.txt
[02/03] Early Computing: Crash Course Computer Science #1
         -> saved  02_Early_Computing_Crash_Course_Computer_Science_#1.txt
[03/03] Electronic Computing: Crash Course Computer Science #2
         -> saved  03_Electronic_Computing_Crash_Course_Computer_Science_#2.txt

============================================================
Complete.  Saved: 3  |  Skipped (exists): 0  |  No transcript: 0
Output directory: /Users/you/demo
```

### Resuming an interrupted run

Re-run the exact same command. Files already on disk are skipped:

```
[01/03] Crash Course Computer Science Preview
         -> already exists, skipping
[02/03] Early Computing: Crash Course Computer Science #1
         -> already exists, skipping
[03/03] Electronic Computing: Crash Course Computer Science #2
         -> saved  03_Electronic_Computing_Crash_Course_Computer_Science_#2.txt
```

### Adding summaries to an existing directory

```bash
python yt_transcripts.py summarize ./demo --style bullets
```

Terminal output:
```
============================================================
MODE: Summarize  (batch-summarize existing transcripts)
Directory : /Users/you/demo
Model     : claude-opus-4-7  |  Style: bullets

Found 3 transcript file(s) in /Users/you/demo
Model: claude-opus-4-7  |  Style: bullets

[01/03] Crash Course Computer Science Preview
         -> summarizing (4,218 chars)...
         -> updated  01_Crash_Course_Computer_Science_Preview.txt
[02/03] Early Computing: Crash Course Computer Science #1
         -> summarizing (14,391 chars)...
         -> map-reduce: 2 chunks x ~8,000 chars
         -> chunk 1/2... done
         -> chunk 2/2... done
         -> reduce step... done
         -> updated  02_Early_Computing_Crash_Course_Computer_Science_#1.txt
[03/03] Electronic Computing: Crash Course Computer Science #2
         -> summarizing (12,204 chars)...
         -> updated  03_Electronic_Computing_Crash_Course_Computer_Science_#2.txt

  Digest written -> /Users/you/demo/_digest.txt

============================================================
Complete.  Summarized: 3  |  Skipped (exists): 0  |  Errors: 0
Directory: /Users/you/demo
```

---

## 7. Modes

### `channel` mode

Downloads transcripts for every regular upload on a YouTube channel, numbered **oldest → newest**. File `01_` is always the channel's earliest upload.

```bash
python yt_transcripts.py channel URL [options]
```

**Examples:**

```bash
# All videos, defaults (./transcripts, English, no summary)
python yt_transcripts.py channel "https://www.youtube.com/@3blue1brown"

# First 50 oldest videos, custom directory
python yt_transcripts.py channel "https://www.youtube.com/@3blue1brown" \
  --output-dir ./3b1b --limit 50

# All videos with bullet-point summaries
python yt_transcripts.py channel "https://www.youtube.com/@3blue1brown" \
  --summarize --style bullets --summary-model claude-haiku-4-5

# German channel, prefer German transcripts with English fallback
python yt_transcripts.py channel "https://www.youtube.com/@maiLab" \
  --lang de --output-dir ./mailab

# Channel ID format
python yt_transcripts.py channel \
  "https://www.youtube.com/channel/UCYO_jab_esuFRV4b17AJtAw"
```

> **Large channels:** For channels with 500+ videos, fetching the video list can take 60–90 seconds as yt-dlp pages through YouTube's API. The transcript downloads then begin immediately after.

---

### `playlist` mode

Downloads transcripts for every video in a playlist, numbered in **playlist order**. `01_` is always the first video as the creator arranged it.

```bash
python yt_transcripts.py playlist URL [options]
```

**Examples:**

```bash
# Full playlist
python yt_transcripts.py playlist \
  "https://www.youtube.com/playlist?list=PL8dPuuaLjXtNlUrzyH5r6jN9ulIgZBpdo"

# First 10 videos with technical summaries, 2-second delay
python yt_transcripts.py playlist \
  "https://www.youtube.com/playlist?list=PLxxxxxx" \
  --limit 10 --summarize --style technical --delay 2.0

# Non-English playlist (Spanish preferred, English fallback)
python yt_transcripts.py playlist \
  "https://www.youtube.com/playlist?list=PLxxxxxx" \
  --lang es

# Slow download rate (reduces risk of rate limiting on large playlists)
python yt_transcripts.py playlist \
  "https://www.youtube.com/playlist?list=PLxxxxxx" \
  --delay 3.0
```

---

### `search` mode

Downloads transcripts for the top N YouTube search results, numbered in **SERP rank order**. `01_` is the highest-ranked result. Results reflect YouTube's ranking at query time from your current IP and session.

```bash
python yt_transcripts.py search "QUERY" [--limit 25|50|100] [options]
```

**Examples:**

```bash
# Top 25 results (default)
python yt_transcripts.py search "climate change explained"

# Top 50 with brief summaries
python yt_transcripts.py search "machine learning for beginners" \
  --limit 50 --summarize --style brief

# Top 100 in a named directory
python yt_transcripts.py search "stoic philosophy" \
  --limit 100 --output-dir ./stoicism_research

# Technical topic with technical summaries
python yt_transcripts.py search "transformer architecture explained" \
  --limit 25 --summarize --style technical \
  --summary-model claude-opus-4-7
```

> **Supported limit values:** `25`, `50`, `100`. Other values will work but may return fewer results than requested.

> **Search reproducibility:** YouTube search results vary by IP, region, login state, and time. Results are not guaranteed to be identical across runs.

---

### `summarize` mode

Batch-processes an existing directory of transcript `.txt` files **without re-downloading anything**. For each file, inserts a `SUMMARY` section between the `METADATA` and `TRANSCRIPT` sections. After processing all files, writes a `_digest.txt` master document.

Files that already contain a `SUMMARY` section are **skipped** unless you pass `--overwrite`.

```bash
python yt_transcripts.py summarize INPUT_DIR [options]
```

**Examples:**

```bash
# Summarize all files in ./transcripts (prose style, claude-opus-4-7)
python yt_transcripts.py summarize ./transcripts

# Bullet-point style with a faster, cheaper model
python yt_transcripts.py summarize ./transcripts \
  --style bullets --model claude-haiku-4-5

# Replace existing summaries with a different style
python yt_transcripts.py summarize ./transcripts \
  --style technical --overwrite

# Use OpenAI instead of Anthropic
python yt_transcripts.py summarize ./transcripts \
  --model gpt-4o-mini --style prose

# Quick brief summaries for triaging a large batch
python yt_transcripts.py summarize ./transcripts \
  --style brief --model claude-haiku-4-5
```

**When to use this mode instead of `--summarize` inline:**

- You already downloaded transcripts without `--summarize` and want to add summaries later
- You want to try a different style or model without re-downloading
- You want to run transcript downloads and summarization at different times (e.g., download now, summarize overnight)
- You want to add summaries only to a subset of files by removing others temporarily

---

## 8. Options Reference

### Shared options — `channel`, `playlist`, `search`

| Option | Default | Description |
|---|---|---|
| `--output-dir DIR` | `./transcripts` | Directory where transcript files are saved. Created if it does not exist. |
| `--limit N` | none (all) | Stop after N videos. For `search` mode, also controls how many results are fetched (25, 50, or 100). |
| `--lang CODE` | `en` | Preferred transcript language. ISO 639-1 code (e.g., `de`, `fr`, `ja`, `es`). Falls back to English, then any available language. |
| `--delay SECS` | `1.0` | Seconds to pause between requests. Increase if you encounter rate limiting. |
| `--summarize` | off | When set, generates an AI summary for each transcript immediately after downloading it. |
| `--summary-model M` | `claude-opus-4-7` | Which LLM to use. Anthropic models are used when the name contains `claude`; otherwise OpenAI. |
| `--style STYLE` | `prose` | Summary style. One of: `prose`, `bullets`, `technical`, `brief`. See [Summary Styles](#9-summary-styles). |

### Options — `summarize` subcommand

| Option | Default | Description |
|---|---|---|
| `input_dir` | — | **Required.** Path to a directory containing `.txt` transcript files. |
| `--model M` | `claude-opus-4-7` | Which LLM to use. |
| `--style STYLE` | `prose` | Summary style: `prose`, `bullets`, `technical`, `brief`. |
| `--overwrite` | off | Re-generate summaries for files that already have a `SUMMARY` section. Without this flag those files are skipped. |

### Language code reference

```bash
--lang en    # English (default)
--lang de    # German
--lang fr    # French
--lang es    # Spanish
--lang pt    # Portuguese
--lang ja    # Japanese
--lang ko    # Korean
--lang zh    # Chinese (Simplified)
--lang ru    # Russian
--lang ar    # Arabic
--lang hi    # Hindi
--lang it    # Italian
--lang nl    # Dutch
--lang pl    # Polish
--lang sv    # Swedish
--lang tr    # Turkish
```

The script tries the specified language first. If no transcript exists in that language it falls back in order: `en` → `en-US` → `en-GB` → any available transcript.

---

## 9. Summary Styles

### `prose` (default)

**Best for:** General reading, blog-style digests, sharing with non-technical audiences.

Structure: 3–5 paragraphs covering topic and context, main argument, supporting evidence, and conclusions. Journalistic tone with clear transitions. No bullet points.

**Example output:**
```
The video explores the history of early computing, beginning with mechanical
calculating machines in the 1800s and tracing the evolution through the first
programmable computers of the 1940s. Host Carrie Anne Philbin frames this
history as essential context for understanding modern software.

The central argument is that computing did not emerge from a single invention
but from decades of overlapping contributions...
```

---

### `bullets`

**Best for:** Quick reference, meeting notes, research triage, sharing with teams.

Structure: labeled sections with tight bullet points.

```
TOPIC
  Introduction to the history of early computing from mechanical calculators
  through the first electronic computers.

MAIN ARGUMENT
  Modern computing is the product of centuries of accumulated innovation, not
  a single invention, and understanding this history is essential context for
  working in technology.

KEY POINTS
  - Charles Babbage designed the Difference Engine (1820s) but never completed it
  - Ada Lovelace wrote what is considered the first algorithm in the 1840s
  - Vacuum tubes replaced mechanical relays in the 1940s, enabling electronic computation
  - ENIAC (1945) was among the first general-purpose electronic computers
  - Early computers filled entire rooms and required teams of operators

NOTABLE EXAMPLES / DATA
  - ENIAC used 18,000 vacuum tubes and consumed 150 kilowatts of power
  - The Colossus at Bletchley Park helped crack Nazi codes in WWII
  - IBM's first commercial computer shipped in 1952

TOOLS / RESOURCES MENTIONED
  - Crash Course Computer Science series (linked in description)

CONCLUSIONS
  - Computing history matters for understanding why modern systems are designed
    the way they are
  - Many computing "firsts" have contested or collaborative origins
```

---

### `technical`

**Best for:** Research notes, internal documentation, practitioner audiences.

Structure: Abstract → Methods/Approach → Technical Details → Limitations/Caveats → Key Takeaways. Preserves exact technical terminology.

**Example output:**
```
Abstract
  This video provides a survey of pre-digital and early electronic computing
  history from the 1820s to the late 1940s, aimed at a general audience
  beginning a computer science course.

Methods / Approach
  Chronological narrative covering mechanical, electromechanical, and early
  electronic computation milestones. Uses visual diagrams to illustrate how
  binary logic gates implement basic arithmetic operations.

Technical Details
  - Babbage Difference Engine: polynomial evaluation via method of differences
  - Relay-based computation: electromechanical switches with ~ms switching time
  - Vacuum tube logic: triode amplifiers operating as bistable switches
  - ENIAC specs: 18,000 vacuum tubes, 70,000 resistors, 10,000 capacitors
  - Memory: mercury delay lines and cathode ray tube storage in early machines
...
```

---

### `brief`

**Best for:** Triaging a large batch, executive summaries, one-line decisions on whether to read further.

Structure: A single paragraph of 40–70 words. Topic first, key insight last.

**Example output:**
```
This Crash Course Computer Science video traces the history of computing from
Charles Babbage's mechanical Difference Engine in the 1820s through the first
general-purpose electronic computers of the 1940s. The central insight is that
modern computing is the product of centuries of layered innovation, not a single
breakthrough, and understanding this lineage is essential context for anyone
working in software.
```

---

## 10. Output File Format

### File naming

Files are saved as zero-padded sequential `.txt` files:

```
01_Video_Title.txt        ← oldest (channel) / first (playlist) / #1 result (search)
02_Second_Video.txt
...
09_Ninth_Video.txt
10_Tenth_Video.txt        ← pads to 2 digits for ≤99 videos
...
100_Hundredth_Video.txt   ← pads to 3 digits for 100–999 videos
```

Title characters unsafe for filenames (`/ \ : * ? " < > |`) are replaced with `_`. Titles are truncated to 80 characters.

### File without summary

```
────────────────────────────────────────────────────────────
METADATA
────────────────────────────────────────────────────────────
Video ID:     dQw4w9WgXcQ
Title:        Example Video Title
Upload Date:  20231015
URL:          https://www.youtube.com/watch?v=dQw4w9WgXcQ
Position:     #1 of 47  (channel  https://www.youtube.com/@channel)

────────────────────────────────────────────────────────────
TRANSCRIPT
────────────────────────────────────────────────────────────
[full transcript text as a continuous paragraph — all caption segments
 joined with spaces, with [Music], [Applause], and similar tags
 preserved as YouTube delivers them]
```

> **Upload Date format:** `YYYYMMDD` when available from the flat-playlist metadata. For many videos in flat mode, yt-dlp may return `unknown` — the date is still available in the file's own metadata line.

### File with summary (inserted by `--summarize` or `summarize` subcommand)

The `SUMMARY` section is always between `METADATA` and `TRANSCRIPT`:

```
────────────────────────────────────────────────────────────
METADATA
────────────────────────────────────────────────────────────
Video ID:     dQw4w9WgXcQ
Title:        Example Video Title
Upload Date:  20231015
URL:          https://www.youtube.com/watch?v=dQw4w9WgXcQ
Position:     #1 of 47  (channel  https://www.youtube.com/@channel)

────────────────────────────────────────────────────────────
SUMMARY
────────────────────────────────────────────────────────────
[AI-generated summary in the requested style]

────────────────────────────────────────────────────────────
TRANSCRIPT
────────────────────────────────────────────────────────────
[full transcript text]
```

### `_digest.txt`

Written to the output directory after any summarization run. Contains every summary in sequence under a header:

```
============================================================
DIGEST  --  47 VIDEO SUMMARIES
Generated : 2026-05-04 04:27 UTC
Directory : /Users/you/transcripts
Model     : claude-opus-4-7
Style     : bullets
============================================================

[01/47]  Crash Course Computer Science Preview
URL: https://www.youtube.com/watch?v=tpIctyqH29Q
------------------------------------------------------------
TOPIC
  Introduction to the Crash Course Computer Science series...

KEY POINTS
  ...


[02/47]  Early Computing: Crash Course Computer Science #1
URL: https://www.youtube.com/watch?v=O5nskjZ_GoI
------------------------------------------------------------
...
```

The `_` prefix means this file sorts to the top of the directory in most file managers and is excluded from `summarize` mode's input file scan.

---

## 11. How Summarization Works

### Short transcripts (≤ 12,000 characters)

A single API call is made. The full transcript text is sent to the LLM with a style instruction:

```
[system prompt: summarization instructions for all 4 styles]

[user message]
Summarize the following YouTube video transcript using the bullets style.

[transcript text]
```

### Long transcripts (> 12,000 characters) — map-reduce

Most transcripts from videos longer than ~15 minutes exceed 12,000 characters. For these, the script uses a two-phase strategy to avoid discarding content:

```
Transcript (e.g. 40,000 chars)
       │
       ▼
  Split into chunks (~8,000 chars each at word boundaries)
       │
   ┌───┴───────────────────────┐
   ▼                           ▼
[Chunk 1 summary]    ...    [Chunk N summary]     ← MAP: N API calls
   │                           │
   └───────────────────────────┘
                │
                ▼
   Synthesize chunk summaries → final summary     ← REDUCE: 1 API call
```

The terminal output shows this in progress:

```
-> map-reduce: 5 chunks x ~8,000 chars
-> chunk 1/5... done
-> chunk 2/5... done
-> chunk 3/5... done
-> chunk 4/5... done
-> chunk 5/5... done
-> reduce step... done
```

### Claude-specific optimizations

Three optimizations are applied automatically when any `claude-*` model is selected:

**Streaming:** Responses are received via `.stream()` and collected with `.get_final_message()`. This prevents HTTP request timeouts that would otherwise occur with slow or long API responses.

**Prompt caching:** The system prompt (which contains the full style guide and quality standards) is tagged with `cache_control: ephemeral`. Anthropic caches it server-side for 5 minutes. Starting from the second video in a batch run, the prompt is served from cache rather than re-tokenized, saving roughly 400–600 input tokens per call. For a 100-video batch, this typically saves $0.10–$0.30 on Opus-tier models.

**Adaptive thinking:** Automatically enabled for `claude-opus-4-7`, `claude-opus-4-6`, and `claude-sonnet-4-6`. The model allocates internal reasoning time proportional to the complexity of the content. Thinking output is stripped before the summary is written to disk.

---

## 12. Ordering Guarantees

| Mode | `01_` represents | How order is determined |
|---|---|---|
| `channel` | Channel's oldest published video | yt-dlp returns newest-first; the list is reversed before numbering |
| `playlist` | First video in the playlist's defined order | yt-dlp preserves playlist position exactly |
| `search` | Highest-ranked result at query time | yt-dlp's `ytsearchN:` returns results in YouTube's native rank order |
| `summarize` | First file by filename sort | Files are sorted alphabetically, which matches the original numeric order |

**Channel ordering detail:**

YouTube's InnerTube API always returns channel videos newest-first and does not expose a sort-by-date-ascending option. The script fetches the entire list and reverses it in memory. This is accurate for channels that have not deleted, privated, or reordered videos. For channels with thousands of videos, the list fetch may take up to 90 seconds.

**Search ordering detail:**

YouTube search results are personalized. The same query from different IPs or logged-in accounts may return different rankings. The numbering in the output files reflects the ranking from your specific session at the time of the query.

---

## 13. Rate Limiting & Reliability

### Transcript API limits

The `youtube-transcript-api` library calls YouTube's caption endpoint directly. YouTube imposes rate limits, though thresholds are not publicly documented.

| Situation | Recommended `--delay` |
|---|---|
| Normal use (residential IP) | `1.0` (default) |
| Large batch (100+ videos) | `2.0` |
| Very large batch (500+ videos) | `3.0` |
| Cloud/datacenter IP | `3.0–5.0` (may still be blocked) |

### Resume support

Every run checks whether each output file already exists before processing. If a run is interrupted — by Ctrl+C, a network error, or a rate limit — simply re-run the exact same command. All previously completed files are skipped and the run continues from where it left off.

### LLM API limits

The LLM API has its own rate limits independent of the transcript API. If you encounter API rate limit errors during summarization:

1. **Increase `--delay`** to slow down the overall loop. Note this also slows transcript downloads; for `summarize` mode the delay applies between summary API calls.
2. **Use a lower-cost model** like `claude-haiku-4-5`, which has higher rate limits.
3. **Decouple the steps:** download transcripts first (no API key, fast), then run `summarize` separately.

### Network errors

Both `yt-dlp` and `youtube-transcript-api` have internal retry logic for transient network errors. Persistent failures on individual videos are reported and skipped so the batch continues.

---

## 14. Troubleshooting

### "no transcript available" for many videos

YouTube auto-generates captions for most videos, but some are unavailable due to:
- **Age-restricted content** — requires authentication (not supported without a browser cookie)
- **Live streams** (in-progress) — captions are not available until after the stream ends
- **Very short videos** or videos where the creator disabled captions
- **Music videos** — YouTube frequently disables captions on music content
- **Some foreign-language videos** — auto-generation quality may cause the API to return nothing

The script reports these and continues. Expect roughly 15–30% of videos on a typical channel to lack transcripts.

---

### "ERROR: yt-dlp not installed"

```bash
pip install yt-dlp
```

If `pip` installs it but it still fails, check which Python `pip` belongs to:
```bash
python3 -m pip install yt-dlp
```

---

### yt-dlp fails with "Sign in to confirm you're not a bot"

YouTube increasingly requires authentication for certain requests from scrapers. Solutions:

```bash
# Option 1: Pass your browser cookies to yt-dlp
# (The script doesn't expose this directly; you'd need to modify _YDL_FLAT_OPTS in the source)

# Option 2: Add cookies to the yt-dlp options dict in the script:
_YDL_FLAT_OPTS = {
    ...
    "cookiesfrombrowser": ("chrome",),   # or "firefox", "safari"
}
```

---

### "RequestBlocked" or "IpBlocked" from youtube-transcript-api

YouTube blocks cloud-provider IPs from the caption endpoint. If you're running the script on AWS, GCP, Azure, or similar:

- The script will fail to fetch transcripts even if yt-dlp succeeds
- Run from a residential IP or a VPN with residential exit nodes
- Alternatively, download transcripts locally and summarize in the cloud

---

### Anthropic: "AuthenticationError" / "invalid x-api-key"

```bash
# Check the key is set
echo $ANTHROPIC_API_KEY

# Ensure there are no quotes or whitespace
export ANTHROPIC_API_KEY="sk-ant-api03-..."   # no trailing spaces
```

---

### Anthropic: "OverloadedError" or "529"

Anthropic's API is temporarily overloaded. The script will surface this as a summarization error for the affected video and continue. Re-run with `summarize --overwrite` after the overload clears, or reduce concurrency by increasing `--delay`.

---

### OpenAI: "RateLimitError"

You have hit your OpenAI tier's rate limit. Increase `--delay` or use `gpt-4o-mini` which has higher limits than `gpt-4o`.

---

### Summaries are empty or very short

Possible causes:
- The transcript itself is very short (auto-generated captions on a short video may be sparse)
- The `brief` style intentionally produces 40–70 words — this is expected
- The LLM received a rate limit error mid-stream and returned partial output — re-run `summarize --overwrite` on the affected file

---

### `channel` mode only returns a few videos

Make sure you're using the full channel URL with the `/videos` tab, not a specific playlist:
```bash
# Correct
python yt_transcripts.py channel "https://www.youtube.com/@channel"

# This only downloads the featured playlist, not all uploads
python yt_transcripts.py channel "https://www.youtube.com/@channel/featured"
```

---

### Unicode errors in filenames on Windows

Video titles may contain characters that Windows filesystems reject. The `sanitize_filename` function replaces most of these, but some edge cases may remain. Set your terminal to UTF-8 encoding:

```powershell
$env:PYTHONIOENCODING="utf-8"
chcp 65001
```

---

## 15. Limitations

| Limitation | Detail |
|---|---|
| **No transcript = no file** | Videos without captions are skipped entirely. No `.txt` file is created. |
| **Captions only — no ASR** | The script uses YouTube's existing captions, not speech-to-text. If a video has no captions at all, no transcript can be retrieved. |
| **Auto-generated caption quality** | Auto-generated captions can be wrong, especially for accents, technical terms, or fast speech. Summaries are only as good as the caption quality. |
| **Channel ordering on edits** | If a creator deletes, privates, or reorders videos, the oldest-first ordering may be inaccurate. The script has no way to detect this. |
| **Search results are ephemeral** | YouTube search rankings change continuously. A batch downloaded today will have different ordering than one downloaded tomorrow. |
| **Search limit** | yt-dlp's `ytsearch` is capped at 100 results per call. There is no reliable way to fetch results beyond position 100 with this method. |
| **Private / age-restricted content** | Not accessible without browser cookies. The script does not attempt authentication. |
| **Transcript language** | The fallback chain is `requested lang → en → en-US → en-GB → any`. If a video only has, say, `de` captions and you request `ja`, you will get the German transcript. |
| **Rate of YouTube API changes** | yt-dlp regularly updates to match YouTube's internal API changes. If something breaks unexpectedly, `pip install --upgrade yt-dlp` often fixes it. |

---

## 16. Common Workflows

### Archive an entire channel

```bash
# Step 1: Download all transcripts (fast, no API key, resumes on interruption)
python yt_transcripts.py channel "https://www.youtube.com/@channel" \
  --output-dir ./channel_archive

# Step 2: Add summaries later (can run overnight for large channels)
python yt_transcripts.py summarize ./channel_archive \
  --model claude-haiku-4-5 \
  --style bullets
```

---

### Research a topic — search and triage

```bash
# 1. Grab the top 50 results on a topic
python yt_transcripts.py search "CRISPR gene therapy clinical trials" \
  --limit 50 --output-dir ./crispr \
  --summarize --style brief

# 2. Read _digest.txt to quickly triage which full transcripts matter
cat ./crispr/_digest.txt | less

# 3. Re-summarize promising ones with more detail
python yt_transcripts.py summarize ./crispr \
  --style technical --overwrite
```

---

### Process a course or lecture series

```bash
python yt_transcripts.py playlist \
  "https://www.youtube.com/playlist?list=PLxxxxxx" \
  --output-dir ./course_notes \
  --summarize --style bullets --summary-model claude-opus-4-7
```

Each lecture becomes `01_Lecture_1.txt`, `02_Lecture_2.txt`, … with the summary first, then the full transcript for reference.

---

### Separate download and summarization for cost control

Downloading transcripts is free. You can defer the LLM cost:

```bash
# Now — download only
python yt_transcripts.py playlist \
  "https://www.youtube.com/playlist?list=PLxxxxxx" \
  --output-dir ./playlist

# Later — summarize only what you decide you need
python yt_transcripts.py summarize ./playlist \
  --model claude-opus-4-7 \
  --style technical
```

---

### Re-summarize with a different style

```bash
# You have prose summaries but want bullets for a presentation
python yt_transcripts.py summarize ./transcripts \
  --style bullets --overwrite

# This rewrites the SUMMARY section in every file and regenerates _digest.txt
```

---

### Non-English content

```bash
# Spanish channel — prefer Spanish, fall back to English
python yt_transcripts.py channel "https://www.youtube.com/@ChannelName" \
  --lang es --output-dir ./spanish_transcripts

# Japanese playlist — prefer Japanese
python yt_transcripts.py playlist \
  "https://www.youtube.com/playlist?list=PLxxxxxx" \
  --lang ja
```

The LLM will summarize the transcript in whatever language it was fetched in. Summaries of non-English transcripts are generated in English by default (the system prompt is in English). If you need summaries in the source language, you would need to modify the `_SUMMARIZE_SYSTEM` and `_STYLE_PREFIXES` strings in the script.

---

### Minimal cost batch summarization

For large collections where quality is secondary to coverage:

```bash
python yt_transcripts.py summarize ./transcripts \
  --model claude-haiku-4-5 \
  --style brief

# claude-haiku-4-5 is ~10x cheaper than claude-opus-4-7
# brief style uses fewer output tokens
# Combined, this reduces cost by ~15-20x vs the defaults
```
