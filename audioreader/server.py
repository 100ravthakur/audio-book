"""Web API + local UI.

Run locally:   audioreader serve
Run hosted:    uvicorn audioreader.server:app --host 0.0.0.0 --port $PORT

Configuration (all optional, read from the environment):
  ALLOWED_ORIGINS   comma-separated origins allowed to call the API ("*" default)
  APP_TOKEN         if set, every /api call must send  X-App-Token: <value>
  MAX_UPLOAD_MB     largest file accepted            (default 120)
  MAX_SECTIONS      most sections per render job     (default 0 = no limit)
  JOB_TTL_MIN       delete a finished job's audio after N minutes (default 60)
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import __version__
from .extract import load
from .model import Document
from .tts import DEFAULT_VOICE, PRESETS, list_voices, resolve_voice, speak_async

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
# Hosted filesystems are usually read-only outside the temp dir.
WORK = Path(os.environ.get("WORK_DIR") or (ROOT / "out" / "_web"))
try:
    WORK.mkdir(parents=True, exist_ok=True)
except OSError:
    WORK = Path("/tmp/audioreader")
    WORK.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "120")) * 1024 * 1024
MAX_SECTIONS = int(os.environ.get("MAX_SECTIONS", "0"))
JOB_TTL = int(os.environ.get("JOB_TTL_MIN", "60")) * 60
APP_TOKEN = os.environ.get("APP_TOKEN", "")
ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app = FastAPI(title="AudioReader", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_credentials=False,   # no cookies: a wildcard origin is safe here
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

DOCS: dict[str, Document] = {}
JOBS: dict[str, "Job"] = {}


@dataclass
class Job:
    id: str
    title: str
    total: int
    done: int = 0
    state: str = "running"  # running | done | error
    message: str = ""
    created: float = field(default_factory=time.time)
    tracks: list[dict] = field(default_factory=list)


def check_token(token: str | None) -> None:
    if APP_TOKEN and token != APP_TOKEN:
        raise HTTPException(401, "Missing or wrong X-App-Token.")


def sweep() -> None:
    """Drop expired jobs and their audio so a long-lived instance stays small."""
    now = time.time()
    for jid, job in list(JOBS.items()):
        if job.state != "running" and now - job.created > JOB_TTL:
            shutil.rmtree(WORK / jid, ignore_errors=True)
            JOBS.pop(jid, None)
    for doc_id in list(DOCS):
        if len(DOCS) > 40:
            DOCS.pop(doc_id, None)


# ---------------------------------------------------------------------- routes

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (WEB / "index.html").read_text(encoding="utf-8")


@app.get("/config.js")
async def config_js() -> Response:
    path = WEB / "config.js"
    body = path.read_text(encoding="utf-8") if path.exists() else ""
    return Response(body, media_type="application/javascript")


@app.get("/api/health")
async def health() -> dict:
    from .explain import available

    return {
        "ok": True,
        "version": __version__,
        "auth": bool(APP_TOKEN),
        "max_upload_mb": MAX_UPLOAD // 1024 // 1024,
        "max_sections": MAX_SECTIONS or None,
        "jobs": len(JOBS),
        "explain": available(),
    }


@app.get("/api/voices")
async def api_voices(q: str = "") -> dict:
    rows = await list_voices(q)
    return {"presets": PRESETS, "voices": rows}


@app.post("/api/analyze")
async def api_analyze(
    url: str = Form(""),
    file: UploadFile | None = File(None),
    x_app_token: str | None = Header(None),
) -> JSONResponse:
    """Read the source and return its structure. No audio yet."""
    check_token(x_app_token)
    sweep()

    source: str
    if file and file.filename:
        suffix = Path(file.filename).suffix.lower()
        if suffix not in (".pdf", ".epub", ".txt", ".md"):
            raise HTTPException(400, f"Unsupported file type: {suffix or 'unknown'}")
        dest = WORK / f"{uuid.uuid4().hex}{suffix}"
        size = 0
        with dest.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        413, f"File larger than {MAX_UPLOAD // 1024 // 1024} MB."
                    )
                out.write(chunk)
        source = str(dest)
    elif url.strip():
        source = url.strip()
    else:
        raise HTTPException(400, "Give a URL or choose a file.")

    try:
        doc = await asyncio.to_thread(load, source)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc

    doc_id = uuid.uuid4().hex[:12]
    DOCS[doc_id] = doc
    return JSONResponse(
        {
            "doc_id": doc_id,
            "title": doc.title,
            "kind": doc.kind,
            "words": doc.words,
            "minutes": round(doc.est_minutes()),
            "sections": [
                {"n": i + 1, "title": s.title, "words": s.words,
                 "minutes": round(s.words / 165, 1)}
                for i, s in enumerate(doc.sections)
            ],
        }
    )


@app.get("/api/section/{doc_id}/{n}")
async def api_section(doc_id: str, n: int, x_app_token: str | None = Header(None)) -> dict:
    """The extracted text of one section, for the reading pane."""
    check_token(x_app_token)
    doc = DOCS.get(doc_id)
    if not doc:
        raise HTTPException(404, "That document is no longer loaded. Analyze again.")
    if not 1 <= n <= len(doc.sections):
        raise HTTPException(404, "No such section.")
    sec = doc.sections[n - 1]
    return {"n": n, "title": sec.title, "words": sec.words, "text": sec.text[:200_000]}


@app.post("/api/render")
async def api_render(payload: dict, x_app_token: str | None = Header(None)) -> dict:
    check_token(x_app_token)
    sweep()

    doc = DOCS.get(payload.get("doc_id", ""))
    if not doc:
        raise HTTPException(
            404,
            "That document is no longer loaded — the server restarted, or it "
            "has been idle a while. Press Analyze again.",
        )

    wanted = payload.get("sections") or list(range(1, len(doc.sections) + 1))
    picked = [(n, doc.sections[n - 1]) for n in wanted if 1 <= n <= len(doc.sections)]
    if not picked:
        raise HTTPException(400, "No sections selected.")
    if MAX_SECTIONS and len(picked) > MAX_SECTIONS:
        raise HTTPException(
            400,
            f"This instance renders at most {MAX_SECTIONS} sections at a time. "
            f"Pick fewer, or run AudioReader locally for the whole book.",
        )

    mode = payload.get("mode", "read")
    backend = payload.get("backend", "local")
    voice = resolve_voice(payload.get("voice") or DEFAULT_VOICE)
    rate = payload.get("rate") or "+0%"
    target = int(payload.get("target") or 320)

    # The caller may bring their own API key for this request. It is held only
    # for the length of the job, never written to disk and never logged.
    creds = {
        "key": (payload.get("api_key") or "").strip(),
        "base_url": (payload.get("base_url") or "").strip(),
        "model": (payload.get("model") or "").strip(),
    }
    creds = {k: v for k, v in creds.items() if v} or None

    job = Job(id=uuid.uuid4().hex[:12], title=doc.title, total=len(picked))
    JOBS[job.id] = job
    out_dir = WORK / job.id
    out_dir.mkdir(parents=True, exist_ok=True)

    asyncio.create_task(
        _run_job(job, doc, picked, out_dir, mode, backend, voice, rate, target, creds)
    )
    return {"job_id": job.id, "total": job.total}


async def _run_job(job, doc, picked, out_dir, mode, backend, voice, rate, target,
                   creds=None):
    from .explain import explain_section

    try:
        for n, sec in picked:
            text = sec.text
            if mode == "explain":
                text = await asyncio.to_thread(
                    explain_section, sec, doc.title, target, backend, creds
                )
            path = out_dir / f"{n:03d}.mp3"
            await speak_async(f"{sec.title}.\n\n{text}", path, voice=voice, rate=rate)
            (out_dir / f"{n:03d}.txt").write_text(text, encoding="utf-8")
            job.tracks.append(
                {
                    "n": n,
                    "title": sec.title,
                    "url": f"/api/audio/{job.id}/{n:03d}",
                    "text_url": f"/api/text/{job.id}/{n:03d}",
                    "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
                }
            )
            job.done += 1
        job.state = "done"
    except Exception as exc:
        job.state = "error"
        job.message = str(exc)


@app.get("/api/job/{job_id}")
async def api_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        # Jobs live in memory, so a restart (a deploy, or a free-plan instance
        # waking from sleep) loses them. Say so rather than "unknown".
        raise HTTPException(
            404,
            "That job is gone — the server restarted while it was running. "
            "Nothing is lost on your side: press Generate again.",
        )
    return {
        "state": job.state,
        "done": job.done,
        "total": job.total,
        "message": job.message,
        "title": job.title,
        "tracks": job.tracks,
    }


def _safe_file(job_id: str, name: str, suffix: str) -> Path:
    if not job_id.isalnum() or not name.isdigit():
        raise HTTPException(400, "Bad path.")
    path = (WORK / job_id / f"{name}{suffix}").resolve()
    if not str(path).startswith(str(WORK.resolve())) or not path.exists():
        raise HTTPException(404, "Not found.")
    return path


@app.get("/api/audio/{job_id}/{name}")
async def api_audio(job_id: str, name: str) -> FileResponse:
    return FileResponse(_safe_file(job_id, name, ".mp3"), media_type="audio/mpeg")


@app.get("/api/text/{job_id}/{name}")
async def api_text(job_id: str, name: str) -> FileResponse:
    return FileResponse(_safe_file(job_id, name, ".txt"), media_type="text/plain")


@app.post("/api/cleanup")
async def api_cleanup(x_app_token: str | None = Header(None)) -> dict:
    check_token(x_app_token)
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    DOCS.clear()
    JOBS.clear()
    return {"ok": True}
