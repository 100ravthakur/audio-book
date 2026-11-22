# AudioReader

Turn a **PDF**, a **webpage**, an **EPUB** or a text file into narrated audio —
either read word for word, or explained to you chapter by chapter.

Runs entirely on your machine. No API key, no account, no cost.

---

## Install

```powershell
cd "S:\Projects for Future\AudioReader"
python -m pip install -r requirements.txt
```

## Use it — web app

```powershell
python -m audioreader serve
```

Open <http://127.0.0.1:8080>. Paste a URL or drop a PDF, pick the sections you
want, choose a voice, hit **Generate audio**. It plays in the page and
autoplays into the next section; each track has an `mp3` and a `text` link.

The page is an anime night-sky scene with a narrator character, **Yomi**, who
is wired to the audio rather than merely animated: a Web Audio analyser reads
the playing MP3 every frame, and the low-mid energy drives how wide her mouth
opens, so she lip-syncs to the actual narration. She blinks, breathes, follows
your cursor with her eyes, and tells you what she's doing in the speech bubble.
The bar meter beside her is the live spectrum. All CSS and inline SVG — no
images, no libraries, nothing fetched from the internet.

## Use it — command line

```powershell
python -m audioreader info "C:\books\atomic-habits.pdf"
```

```powershell
python -m audioreader read "https://paulgraham.com/greatwork.html" --voice neerja
```

```powershell
python -m audioreader explain "C:\books\atomic-habits.pdf" --sections 1-5 --backend local
```

Output lands in `out\<title>\` as one MP3 per chapter, plus `playlist.m3u`
(opens in VLC / any player) and `transcript.md`.

### Options

| Flag | What it does |
|---|---|
| `--voice` | `neerja`, `swara`, `prabhat`, … or any full Edge voice id |
| `--rate` | `-20%` … `+50%` — reading speed |
| `--pitch` | e.g. `+2Hz` |
| `--sections` | `1-3,7` — only those chapters |
| `--single` | one big MP3 instead of one per chapter |
| `--resume` | skip chapters already rendered (safe to re-run) |
| `--out` | output folder |
| `--backend` | *explain only:* `local` (offline) or `claude` (needs a key) |
| `--target` | *explain only:* words of explanation per chapter |

### Voices

```powershell
python -m audioreader voices en-IN
```

Shortcuts built in: `neerja`, `neerja+`, `prabhat` (Indian English),
`swara`, `madhur` (Hindi), `aarohi` (Marathi), `pallavi` (Tamil),
`shruti` (Telugu), `tanishaa` (Bengali), plus `aria`, `guy`, `sonia`.
Any of the ~320 Edge voices works by its full id.

---

## The two modes

**`read`** — speaks the text exactly as written. No network beyond the speech
service, no AI involved.

**`explain`** — rewrites each chapter as a spoken explanation first, then
narrates that. Two engines:

- `--backend local` (default in the web UI): picks the highest-signal
  sentences. Offline, instant, free, and rough.
- `--backend claude`: real teaching — defines jargon, keeps the author's
  claims, written for the ear. Needs `ANTHROPIC_API_KEY` in your environment:

  ```powershell
  $env:ANTHROPIC_API_KEY = "sk-ant-..."
  ```

---

## Notes and limits

- **Scanned PDFs** have no text layer; AudioReader will say so. Run OCR first
  (`ocrmypdf in.pdf out.pdf`) and try again.
- **Chapters** come from the PDF's own table of contents when it has one,
  otherwise it falls back to 12-page blocks.
- **Speed**: roughly 1 minute of audio per 4 seconds of processing, so a
  10-hour book takes about 40 minutes. Use `--resume` to pick up where you
  stopped.
- **Where the text goes**: the text of whatever you're reading is sent to
  Microsoft's Edge speech service to be spoken, and to Anthropic only if you
  choose `--backend claude`. Nothing else leaves the machine.
- Only read things you're entitled to read. This is a reading tool for your
  own material.

## Custom voices

The speech layer is one function — `speak_async` in `audioreader/tts.py`.
To use a cloned voice (XTTS, Piper, ElevenLabs), swap that function's body;
everything upstream is unchanged. Clone only your own voice, or someone's
who agreed to it.

## Layout

```
audioreader/
  extract.py   PDF / web / EPUB / txt  ->  Document(sections)
  model.py     data model + text cleanup for speech
  tts.py       chunking, synthesis, retries
  explain.py   Claude and offline summarizers
  cli.py       read | explain | info | voices | serve
  server.py    local web API
web/index.html the UI
```
