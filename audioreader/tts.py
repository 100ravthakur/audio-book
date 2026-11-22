"""Speech synthesis. Default engine is edge-tts: free, no API key, natural."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Callable

from .model import strip_for_speech

# A short, curated list. `audioreader voices` prints every voice Edge offers.
PRESETS: dict[str, str] = {
    # English (India)
    "neerja":   "en-IN-NeerjaNeural",            # female, warm, Indian English
    "neerja+":  "en-IN-NeerjaExpressiveNeural",  # female, more expressive
    "prabhat":  "en-IN-PrabhatNeural",           # male, Indian English
    # Hindi
    "swara":    "hi-IN-SwaraNeural",             # female
    "madhur":   "hi-IN-MadhurNeural",            # male
    # Other Indian languages
    "aarohi":   "mr-IN-AarohiNeural",            # Marathi, female
    "pallavi":  "ta-IN-PallaviNeural",           # Tamil, female
    "shruti":   "te-IN-ShrutiNeural",            # Telugu, female
    "tanishaa": "bn-IN-TanishaaNeural",          # Bengali, female
    # Elsewhere
    "aria":     "en-US-AriaNeural",
    "guy":      "en-US-GuyNeural",
    "sonia":    "en-GB-SoniaNeural",
}

DEFAULT_VOICE = "neerja"
MAX_CHARS = 2200  # keep each request to the service comfortably small


def resolve_voice(name: str) -> str:
    return PRESETS.get(name.lower(), name)


def split_for_tts(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Break text into chunks at sentence boundaries, never mid-sentence."""
    text = strip_for_speech(text)
    if not text:
        return []

    sentences = re.split(r"(?<=[.!?\u0964])\s+", text)
    chunks, buf = [], ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        while len(s) > limit:  # pathological run-on: split at a comma or space
            cut = s.rfind(",", 0, limit)
            if cut < limit // 2:
                cut = s.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(s[:cut].strip())
            s = s[cut:].strip()
        if len(buf) + len(s) + 1 > limit:
            if buf:
                chunks.append(buf.strip())
            buf = s
        else:
            buf = f"{buf} {s}".strip()
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


async def _synth_chunk(text: str, voice: str, rate: str, pitch: str, volume: str) -> bytes:
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume)
    audio = bytearray()
    async for part in comm.stream():
        if part["type"] == "audio":
            audio.extend(part["data"])
    return bytes(audio)


async def speak_async(
    text: str,
    out_path: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
    on_progress: Callable[[int, int], None] | None = None,
    retries: int = 3,
) -> Path:
    """Synthesize `text` to an MP3 at `out_path`."""
    chunks = split_for_tts(text)
    if not chunks:
        raise ValueError("Nothing to speak.")

    voice_id = resolve_voice(voice)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # MP3 frames concatenate cleanly, so no ffmpeg needed.
    with out_path.open("wb") as fh:
        for i, chunk in enumerate(chunks, 1):
            last_err: Exception | None = None
            for attempt in range(retries):
                try:
                    fh.write(await _synth_chunk(chunk, voice_id, rate, pitch, volume))
                    last_err = None
                    break
                except Exception as exc:  # transient network / service hiccup
                    last_err = exc
                    await asyncio.sleep(1.5 * (attempt + 1))
            if last_err:
                raise RuntimeError(
                    f"Speech failed on chunk {i}/{len(chunks)}: {last_err}"
                ) from last_err
            if on_progress:
                on_progress(i, len(chunks))
    return out_path


def speak(text: str, out_path: Path, **kw) -> Path:
    return asyncio.run(speak_async(text, out_path, **kw))


async def list_voices(filter_text: str = "") -> list[dict]:
    import edge_tts

    voices = await edge_tts.list_voices()
    f = filter_text.lower()
    rows = [
        {
            "name": v["ShortName"],
            "gender": v["Gender"],
            "locale": v["Locale"],
            "tags": ", ".join(
                (v.get("VoiceTag") or {}).get("VoicePersonalities", []) or []
            ),
        }
        for v in voices
        if not f
        or f in v["ShortName"].lower()
        or f in v["Locale"].lower()
    ]
    return sorted(rows, key=lambda r: r["name"])
