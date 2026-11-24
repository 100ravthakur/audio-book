"""Command line interface: audioreader read | explain | info | voices | serve"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

from . import __version__
from .extract import load
from .model import Document
from .tts import DEFAULT_VOICE, PRESETS, list_voices, resolve_voice, speak

OUT_DIR = Path("out")


def _slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", text).strip()
    s = re.sub(r"[\s_]+", "-", s).lower()
    return (s[:limit].strip("-")) or "audio"


def _parse_range(spec: str, total: int) -> list[int]:
    """'1-3,7' -> [0,1,2,6] (1-based input, 0-based output)."""
    if not spec:
        return list(range(total))
    picked: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo = int(a) if a.strip() else 1
            hi = int(b) if b.strip() else total
        else:
            lo = hi = int(part)
        picked += [i - 1 for i in range(lo, hi + 1) if 1 <= i <= total]
    return sorted(set(picked))


def _bar(done: int, total: int, width: int = 24) -> str:
    filled = int(width * done / max(total, 1))
    return "[" + "#" * filled + "-" * (width - filled) + f"] {done}/{total}"


# --------------------------------------------------------------------- commands

def cmd_info(args) -> int:
    doc = load(args.source)
    print(f"\n  {doc.title}")
    print(f"  {doc.kind.upper()} - {doc.source}")
    print(f"  {len(doc.sections)} sections, {doc.words:,} words, "
          f"~{doc.est_minutes():.0f} min read aloud\n")
    for i, s in enumerate(doc.sections, 1):
        print(f"  {i:>3}. {s.title[:58]:<58} {s.words:>7,}w  ~{s.words/165:>4.0f}m")
    print()
    return 0


def cmd_voices(args) -> int:
    rows = asyncio.run(list_voices(args.filter or ""))
    if args.filter:
        print(f"\n  Voices matching '{args.filter}':\n")
    else:
        print("\n  Shortcuts you can pass to --voice:\n")
        for k, v in PRESETS.items():
            print(f"    {k:<10} {v}")
        print(f"\n  All {len(rows)} Edge voices "
              f"(narrow with: audioreader voices en-IN):\n")
    for r in rows:
        print(f"    {r['name']:<34} {r['gender']:<7} {r['tags'][:40]}")
    print()
    return 0


def _render(doc: Document, args, label: str) -> int:
    idx = _parse_range(args.sections, len(doc.sections))
    chosen = [doc.sections[i] for i in idx]
    if not chosen:
        print("No sections selected.", file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else OUT_DIR / _slug(doc.title)
    out_dir.mkdir(parents=True, exist_ok=True)
    voice = resolve_voice(args.voice)

    words = sum(s.words for s in chosen)
    print(f"\n  {doc.title}")
    print(f"  {label} - voice {voice} - rate {args.rate}")
    print(f"  {len(chosen)} section(s), {words:,} words, ~{words/165:.0f} min audio")
    print(f"  -> {out_dir}\n")

    started = time.time()
    written: list[Path] = []
    transcript: list[str] = []

    if args.single:
        text = "\n\n".join(f"{s.title}.\n\n{s.text}" for s in chosen)
        transcript.append(text)
        target = out_dir / f"{_slug(doc.title)}.mp3"
        print(f"  Narrating one file ({len(text):,} chars)...")
        speak(text, target, voice=voice, rate=args.rate, pitch=args.pitch,
              on_progress=lambda d, t: print(f"\r    {_bar(d, t)}", end="", flush=True))
        print()
        written.append(target)
    else:
        for n, (i, sec) in enumerate(zip(idx, chosen), 1):
            name = f"{i + 1:02d}-{_slug(sec.title, 40)}.mp3"
            target = out_dir / name
            transcript.append(f"## {sec.title}\n\n{sec.text}")
            print(f"  [{n}/{len(chosen)}] {sec.title[:46]:<46}", end="", flush=True)
            if target.exists() and args.resume:
                print(" skipped (exists)")
                written.append(target)
                continue
            speak(f"{sec.title}.\n\n{sec.text}", target, voice=voice,
                  rate=args.rate, pitch=args.pitch,
                  cue_path=target.with_suffix(".json"))
            print(f" {target.stat().st_size / 1024 / 1024:>5.1f} MB")
            written.append(target)

    playlist = out_dir / "playlist.m3u"
    playlist.write_text(
        "\n".join(["#EXTM3U"] + [p.name for p in written]), encoding="utf-8"
    )
    (out_dir / "transcript.md").write_text(
        f"# {doc.title}\n\nSource: {doc.source}\n\n" + "\n\n".join(transcript),
        encoding="utf-8",
    )

    total_mb = sum(p.stat().st_size for p in written) / 1024 / 1024
    print(f"\n  Done in {time.time() - started:.0f}s - "
          f"{len(written)} file(s), {total_mb:.1f} MB")
    print(f"  Playlist:   {playlist}")
    print(f"  Transcript: {out_dir / 'transcript.md'}\n")
    return 0


def cmd_read(args) -> int:
    return _render(load(args.source), args, "verbatim narration")


def cmd_explain(args) -> int:
    from .explain import explain_document

    doc = load(args.source)
    idx = _parse_range(args.sections, len(doc.sections))
    doc.sections = [doc.sections[i] for i in idx]
    args.sections = ""  # already narrowed

    print(f"\n  Explaining {len(doc.sections)} section(s) "
          f"via {args.backend} backend...")
    explained = explain_document(
        doc, target=args.target, backend=args.backend,
        on_progress=lambda d, t: print(f"\r    {_bar(d, t)}", end="", flush=True),
    )
    print()
    return _render(explained, args, f"explanation ({args.backend})")


def cmd_serve(args) -> int:
    import uvicorn

    print(f"\n  AudioReader web UI -> http://127.0.0.1:{args.port}\n")
    uvicorn.run("audioreader.server:app", host=args.host, port=args.port,
                log_level="warning")
    return 0


# ----------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audioreader",
        description="Turn a PDF, webpage, EPUB or text file into narrated audio.",
        epilog="Examples:\n"
               "  audioreader info book.pdf\n"
               "  audioreader read https://example.com/article --voice neerja\n"
               "  audioreader explain book.pdf --sections 1-3 --backend local\n"
               "  audioreader serve\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"audioreader {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_render_opts(sp):
        sp.add_argument("source", help="PDF / EPUB / .txt path, or an http(s) URL")
        sp.add_argument("--voice", default=DEFAULT_VOICE,
                        help="shortcut or full Edge voice id (default: neerja)")
        sp.add_argument("--rate", default="+0%", help="speed, e.g. +15%% or -10%%")
        sp.add_argument("--pitch", default="+0Hz", help="pitch, e.g. +2Hz")
        sp.add_argument("--sections", default="",
                        help="which sections, e.g. 1-3,7 (default: all)")
        sp.add_argument("--out", default="", help="output directory")
        sp.add_argument("--single", action="store_true",
                        help="one MP3 instead of one per section")
        sp.add_argument("--resume", action="store_true",
                        help="skip sections whose MP3 already exists")

    s = sub.add_parser("read", help="narrate the text word for word")
    add_render_opts(s)
    s.set_defaults(func=cmd_read)

    s = sub.add_parser("explain", help="explain it chapter by chapter, then narrate")
    add_render_opts(s)
    s.add_argument("--backend", choices=["claude", "local"], default="claude",
                   help="claude needs ANTHROPIC_API_KEY; local is offline")
    s.add_argument("--target", type=int, default=320,
                   help="words of explanation per section (default 320)")
    s.set_defaults(func=cmd_explain)

    s = sub.add_parser("info", help="show structure and length, synthesize nothing")
    s.add_argument("source")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("voices", help="list available voices")
    s.add_argument("filter", nargs="?", default="", help="e.g. en-IN, hi, Neerja")
    s.set_defaults(func=cmd_voices)

    s = sub.add_parser("serve", help="run the local web UI")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--host", default="127.0.0.1")
    s.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n  Stopped.\n", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\n  Error: {exc}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
