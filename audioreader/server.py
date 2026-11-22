"""Local web UI. Run with: audioreader serve  (or python -m audioreader serve)

Everything stays on this machine: uploads go to a work directory, audio is
written next to it, nothing is sent anywhere except the speech service.
"""
from __future__ import annotations

import asyncio
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import __version__
from .extract import load
from .model import Document
from .tts import DEFAULT_VOICE, PRESETS, list_voices, resolve_voice, speak_async

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
WORK = ROOT / "out" / "_web"
WORK.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD = 120 * 1024 * 1024  # 120 MB

app = FastAPI(title="AudioReader", version=__version__)

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
    tracks: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------- routes

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (WEB / "index.html").read_text(encoding="utf-8")


@app.get("/api/voices")
async def api_voices(q: str = "") -> dict:
    rows = await list_voices(q)
    return {"presets": PRESETS, "voices": rows}


@app.post("/api/analyze")
async def api_analyze(
    url: str = Form(""),
    file: UploadFile | None = File(None),
) -> JSONResponse:
    """Read the source and return its structure. No audio yet."""
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
                    raise HTTPException(413, "File larger than 120 MB.")
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


@app.post("/api/render")
async def api_render(payload: dict) -> dict:
    doc_id = payload.get("doc_id", "")
    doc = DOCS.get(doc_id)
    if not doc:
        raise HTTPException(404, "That document is no longer loaded. Analyze again.")

    wanted = payload.get("sections") or list(range(1, len(doc.sections) + 1))
    picked = [(n, doc.sections[n - 1]) for n in wanted
              if 1 <= n <= len(doc.sections)]
    if not picked:
        raise HTTPException(400, "No sections selected.")

    mode = payload.get("mode", "read")
    backend = payload.get("backend", "local")
    voice = resolve_voice(payload.get("voice") or DEFAULT_VOICE)
    rate = payload.get("rate") or "+0%"
    target = int(payload.get("target") or 320)

    job = Job(id=uuid.uuid4().hex[:12], title=doc.title, total=len(picked))
    JOBS[job.id] = job
    out_dir = WORK / job.id
    out_dir.mkdir(parents=True, exist_ok=True)

    asyncio.create_task(
        _run_job(job, doc, picked, out_dir, mode, backend, voice, rate, target)
    )
    return {"job_id": job.id, "total": job.total}


async def _run_job(job, doc, picked, out_dir, mode, backend, voice, rate, target):
    from .explain import explain_section

    try:
        for n, sec in picked:
            text = sec.text
            if mode == "explain":
                text = await asyncio.to_thread(
                    explain_section, sec, doc.title, target, backend
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
        raise HTTPException(404, "Unknown job.")
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
async def api_cleanup() -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    DOCS.clear()
    JOBS.clear()
    return {"ok": True}
