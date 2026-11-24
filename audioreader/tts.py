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


# --------------------------------------------------------- mp3 frame duration
#
# Word cues are timed from their own chunk's start, so to lay chunks end to end
# we need each one's exact length. Counting MPEG frames is exact; adding up the
# word timings instead would drift over a long chapter.

_BITRATES_V2_L3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
_BITRATES_V1_L3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
_RATES = {0: [44100, 48000, 32000], 2: [22050, 24000, 16000], 3: [11025, 12000, 8000]}


def mp3_duration(data: bytes) -> float:
    """Length in seconds, by walking the MPEG frame headers."""
    i, total, n = 0, 0.0, len(data)
    if data[:3] == b"ID3":                       # skip a leading ID3v2 tag
        sz = int.from_bytes(data[6:10], "big")
        i = 10 + ((sz & 0x7F) | ((sz >> 1) & 0x3F80)
                  | ((sz >> 2) & 0x1FC000) | ((sz >> 3) & 0xFE00000))
    while i + 4 <= n:
        if data[i] != 0xFF or (data[i + 1] & 0xE0) != 0xE0:
            i += 1
            continue
        ver = (data[i + 1] >> 3) & 0x03          # 0=MPEG2.5, 2=MPEG2, 3=MPEG1
        layer = (data[i + 1] >> 1) & 0x03        # 1 = Layer III
        bri = (data[i + 2] >> 4) & 0x0F
        sri = (data[i + 2] >> 2) & 0x03
        pad = (data[i + 2] >> 1) & 0x01
        if layer != 1 or ver == 1 or bri in (0, 15) or sri == 3:
            i += 1
            continue
        rate = _RATES[0 if ver == 3 else (2 if ver == 2 else 3)][sri]
        kbps = (_BITRATES_V1_L3 if ver == 3 else _BITRATES_V2_L3)[bri]
        if not rate or not kbps:
            i += 1
            continue
        samples = 1152 if ver == 3 else 576
        total += samples / rate
        i += max((samples // 8) * kbps * 1000 // rate + pad, 4)
    return total


async def _synth_cued(text: str, voice: str, rate: str, pitch: str,
                      volume: str) -> tuple[bytes, list]:
    """Same as _synth_chunk, but also collects per-word timings."""
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch,
                                volume=volume, boundary="WordBoundary")
    audio, cues = bytearray(), []
    async for part in comm.stream():
        if part["type"] == "audio":
            audio.extend(part["data"])
        elif part["type"] == "WordBoundary":
            cues.append([round(part["offset"] / 1e7, 3),      # 100ns ticks
                         round(part["duration"] / 1e7, 3),
                         part["text"]])
    return bytes(audio), cues


async def speak_async(
    text: str,
    out_path: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
    on_progress: Callable[[int, int], None] | None = None,
    retries: int = 3,
    cue_path: Path | None = None,
) -> Path:
    """Synthesize `text` to an MP3 at `out_path`.

    With `cue_path`, also writes {"text": ..., "cues": [[start, length, word]]}
    — the exact words spoken and when each one is said, which is what lets the
    reader follow along and lets you click a word to jump there.
    """
    chunks = split_for_tts(text)
    if not chunks:
        raise ValueError("Nothing to speak.")

    voice_id = resolve_voice(voice)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cues: list = []
    elapsed = 0.0

    # MP3 frames concatenate cleanly, so no ffmpeg needed.
    with out_path.open("wb") as fh:
        for i, chunk in enumerate(chunks, 1):
            last_err: Exception | None = None
            for attempt in range(retries):
                try:
                    if cue_path:
                        audio, part_cues = await _synth_cued(
                            chunk, voice_id, rate, pitch, volume)
                        for start, length, word in part_cues:
                            cues.append([round(elapsed + start, 3), length, word])
                        elapsed += mp3_duration(audio)
                    else:
                        audio = await _synth_chunk(
                            chunk, voice_id, rate, pitch, volume)
                    fh.write(audio)
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

    if cue_path:
        import json

        cue_path.write_text(
            json.dumps({"text": " ".join(chunks), "cues": cues}, ensure_ascii=False),
            encoding="utf-8",
        )
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
