"""Explain mode: rewrite a section as a spoken explanation instead of reading it.

Two backends:
  claude - uses the Anthropic API (needs ANTHROPIC_API_KEY). Real teaching.
  local  - no network, no key: extractive outline. Rough but free and instant.
"""
from __future__ import annotations

import os
import re

from .model import Document, Section

MODEL = "claude-sonnet-5-5"

SYSTEM = """You are narrating an audiobook-style explanation. You will be given one \
chapter or section of a document. Explain it to a listener who cannot see the page.

Rules:
- Write for the ear: short sentences, plain words, no headings, no bullet points,
  no markdown, no "in this chapter we will".
- Open with one sentence naming the single idea of this section.
- Then explain the reasoning and the concrete examples that matter. Keep the
  author's actual claims; do not invent facts, numbers or quotes.
- Use spoken transitions ("here's why that matters", "so", "the catch is").
- Define jargon the first time it appears.
- Close with one sentence the listener should remember.
- Target about {target} words. Output only the narration text."""


def explain_section(section: Section, doc_title: str, target: int = 320,
                    backend: str = "claude") -> str:
    if backend == "local":
        return _extractive(section, target)
    return _claude(section, doc_title, target)


def _claude(section: Section, doc_title: str, target: int) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "Explain mode needs ANTHROPIC_API_KEY set, or pass --backend local "
            "for the offline outline."
        )
    import anthropic

    client = anthropic.Anthropic()
    body = section.text[:120_000]
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM.format(target=target),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Document: {doc_title}\n"
                    f"Section: {section.title}\n\n"
                    f"---\n{body}\n---"
                ),
            }
        ],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


# --------------------------------------------------- offline extractive digest

_STOP = set(
    """a an the and or but if then than that this these those of in on at to for from by
    with as is are was were be been being it its he she they them his her their we you i
    not no so such which who whom what when where how there here also very more most can
    could would should may might will just only own same too s t don now""".split()
)


def _extractive(section: Section, target: int) -> str:
    """Pick the highest-signal sentences by word frequency. No network."""
    text = section.text
    sentences, seen = [], set()
    for s in re.split(r"(?<=[.!?])\s+", text):
        s = s.strip()
        key = re.sub(r"\W+", " ", s.lower()).strip()
        if len(s.split()) > 6 and key not in seen:   # skip repeated boilerplate
            seen.add(key)
            sentences.append(s)
    if not sentences:
        return text[: target * 6]

    freq: dict[str, int] = {}
    for w in re.findall(r"[a-z']+", text.lower()):
        if w not in _STOP and len(w) > 2:
            freq[w] = freq.get(w, 0) + 1
    if not freq:
        return " ".join(sentences[:6])

    peak = max(freq.values())
    scored = []
    for i, s in enumerate(sentences):
        words = re.findall(r"[a-z']+", s.lower())
        if not words:
            continue
        score = sum(freq.get(w, 0) / peak for w in words) / (len(words) ** 0.5)
        if i < 3:
            score *= 1.25  # opening sentences usually frame the section
        scored.append((score, i, s))

    scored.sort(reverse=True)
    picked, count = [], 0
    for _score, i, s in scored:
        picked.append((i, s))
        count += len(s.split())
        if count >= target:
            break
    picked.sort()

    lead = f"{section.title}. Here are the key points."
    return lead + " " + " ".join(s for _i, s in picked)


def explain_document(doc: Document, target: int = 320, backend: str = "claude",
                     on_progress=None) -> Document:
    out = Document(
        title=doc.title, source=doc.source, kind=doc.kind, sections=[]
    )
    for i, sec in enumerate(doc.sections, 1):
        out.sections.append(
            Section(sec.title, explain_section(sec, doc.title, target, backend))
        )
        if on_progress:
            on_progress(i, len(doc.sections))
    return out
