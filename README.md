# Your Voice  ·  きみのこえ

Turn a **PDF**, a **webpage**, an **EPUB** or a text file into narrated audio —
either read word for word, or explained to you chapter by chapter.

Runs entirely on your machine. No API key, no account, no cost.

The web app is themed after the twilight of *Kimi no Na wa* — dusk indigo
falling to a coral horizon, with the red thread across the masthead. The
Python package and the command stay `audioreader`, because the deployed
service starts from that module name.

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

The page is a dusk scene with a narrator who
is wired to the audio rather than merely animated: a Web Audio analyser reads
the playing MP3 every frame, and the low-mid energy drives how wide her mouth
opens, so she lip-syncs to the actual narration. She blinks, breathes, follows
and says what she is doing beside the player. The bar meter is the live spectrum. All CSS and inline SVG — no
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
- `--backend anthropic` or `--backend openai`: real teaching — defines
  jargon, keeps the author's claims, written for the ear.

### Wiring up an explanation API

Both are called over plain HTTP from the standard library, so there is no
provider SDK to install and switching provider is one environment variable.
`/api/health` reports which engines the server can actually use, and the web
app greys out the ones with no key.

**Easiest: paste a key into the page.** Switch to *An explanation*, choose
Claude or an OpenAI-compatible engine, and a key field appears. The key is
kept in that browser only and sent with each request to your own backend,
which uses it for the call and never writes it down. Nothing to configure
on the server.

**Anthropic** - get a key at <https://console.anthropic.com/settings/keys>
(sign in, Create Key, add a little credit). For the server-wide setting:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:ANTHROPIC_MODEL = "claude-opus-5-5"   # optional, this is the default
```

Rough cost for a whole book or prep kit, explained chapter by chapter -
say 60,000 words in and 10,000 words of explanation out:

| Model | Input $/M | Output $/M | That book |
|---|---|---|---|
| `claude-opus-5-5` | $4 | $20 | about $0.60 |
| `claude-sonnet-5-5` | $2 | $10 | about $0.30 |
| `claude-haiku-5-5` | $0.10 | $0.50 | about $0.02 |

Opus is the default because it explains best; set `ANTHROPIC_MODEL`, or the
Model box in the page, to trade down.

### Free options

Narration is already free and needs no key at all - the voices cost nothing,
and the offline outline explains without any API. For a real explanation you
need a model, and several providers have a free tier:

| Provider | Free tier | Key from |
|---|---|---|
| **Groq** | yes, no card | <https://console.groq.com/keys> |
| **Google Gemini** | yes | <https://aistudio.google.com/apikey> |
| **OpenRouter** | models ending `:free` | <https://openrouter.ai/keys> |
| **Cerebras** | yes | <https://cloud.cerebras.ai> |
| **Mistral** | yes | <https://console.mistral.ai/api-keys> |
| **Ollama** | entirely free, runs on your machine | <https://ollama.com/download> |

Pick one from the **Provider** list in the page and the base URL fills itself
in. Press **list models** and it asks that provider what it currently offers,
putting any free ones first - model names change constantly, so the app asks
rather than shipping a list that goes stale. Rate limits apply on free tiers;
a provider answers 429 rather than charging you.

Ollama is the only one with no account at all, but it only works for a local
install - a backend on Render cannot reach your laptop.

**Anything OpenAI-compatible** — set the base URL and model to match:

| Provider | `OPENAI_BASE_URL` | typical `OPENAI_MODEL` |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` (default) | `gpt-4o-mini` |
| Groq (free tier) | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| OpenRouter | `https://openrouter.ai/api/v1` | any listed model |
| Together | `https://api.together.xyz/v1` | any listed model |
| Ollama (on your machine) | `http://localhost:11434/v1` | `llama3.1` |

```powershell
$env:OPENAI_API_KEY = "gsk_..."
$env:OPENAI_BASE_URL = "https://api.groq.com/openai/v1"
$env:OPENAI_MODEL = "llama-3.3-70b-versatile"
```

On Render, set the same names under **Environment** instead. Ollama only
works for a local install — a hosted backend cannot reach your localhost.

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

## Deploying: Render + Vercel

**Vercel alone cannot run this.** Its functions stop at 10s (60s on Pro), the
filesystem is read-only, and nothing survives between requests. One chapter
takes about a minute to narrate and the MP3 has to stay on disk until you've
listened to it. So the work is split:

| Where | What |
|---|---|
| **Render** | the Python backend — extraction, narration, the job queue |
| **Vercel** | the `web/` folder as a static site, calling that backend |

### 1 · Backend on Render

1. Render → **New** → **Blueprint** → pick this repo. It reads `render.yaml`.
2. Wait for the build, then open `https://<your-service>.onrender.com/api/health`.
   You should get `{"ok": true, ...}`.

The blueprint sets free-plan-sized limits: 25 MB uploads, 8 sections per job,
audio deleted after 45 minutes, work directory in `/tmp`.

### 2 · Frontend on Vercel

1. Vercel → **Add New** → **Project** → same repo. `vercel.json` already
   points the output at `web/`, so take the defaults.
2. Open the deployed page, click **⚙ backend** under the URL box, paste your
   Render URL, **Save**. It is remembered in that browser.

To bake the URL in for everyone instead, edit `web/config.js`:

```js
window.AUDIOREADER_API = "https://your-service.onrender.com";
```

### 3 · Lock it down

Once you know the Vercel domain, set this on Render → Environment, so other
sites cannot use your backend:

```
ALLOWED_ORIGINS = https://your-project.vercel.app
```

To make it private entirely, set `APP_TOKEN` to any random string on Render and
enter the same string under **⚙ backend** on the page.

### What to expect from the free tiers

- **Render free sleeps after 15 minutes idle.** The next request takes
  ~50 seconds to wake it. The page will look stuck; it isn't.
- **512 MB RAM.** A big PDF can run it out of memory. That's why uploads are
  capped at 25 MB and jobs at 8 sections.
- **Ephemeral disk.** A restart or redeploy throws away generated audio and
  in-progress jobs. Download what you want to keep.
- **The speech service may refuse datacenter IPs.** Edge TTS is free and
  unauthenticated, and it sometimes rate-limits or blocks cloud ranges. If
  hosted narration starts failing with 403s, that's why — the local install
  keeps working.
- **Anyone with the link can use it** unless you set `APP_TOKEN`. It fetches
  URLs on your server's behalf, so private addresses (localhost, `10.x`,
  `192.168.x`, and cloud metadata at `169.254.169.254`) are refused.

For a whole book, run it locally. The hosted copy is for articles and chapters.

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
