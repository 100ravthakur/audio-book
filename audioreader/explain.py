"""Explain mode: rewrite a section as a spoken explanation instead of reading it.

Engines
-------
local      no network, no key. Picks the highest-signal sentences. Rough.
anthropic  Claude. Needs ANTHROPIC_API_KEY.
openai     Anything that speaks the OpenAI chat-completions protocol, which
           is most of them. Needs OPENAI_API_KEY, and OPENAI_BASE_URL if it
           is not OpenAI itself:

             OpenAI      (default)   https://api.openai.com/v1
             Groq        (free tier) https://api.groq.com/openai/v1
             OpenRouter              https://openrouter.ai/api/v1
             Together                https://api.together.xyz/v1
             Ollama      (local)     http://localhost:11434/v1

Both are called over plain HTTP from the standard library, so no provider
SDK is installed and switching provider is an environment variable.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .model import Document, Section

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
OPENAI_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
TIMEOUT = int(os.environ.get("EXPLAIN_TIMEOUT", "120"))

ALIASES = {"claude": "anthropic", "": "local", None: "local"}

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


def available() -> dict[str, object]:
    """Which engines this instance can actually use, for /api/health."""
    return {
        "local": True,
        "byok": True,       # the page may supply a key for one request
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "anthropic_model": ANTHROPIC_MODEL,
        "openai_model": OPENAI_MODEL,
        "openai_base": OPENAI_BASE,
    }


# ------------------------------------------------------------------ transport

def _post(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        # urllib's default user-agent is blocked outright by some providers'
        # bot protection (Groq's Cloudflare answers 1010 before reading the key).
        headers={
            "Content-Type": "application/json",
            "User-Agent": "AudioReader/0.1",
            "Accept": "application/json",
            **headers,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"The explanation API returned {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach the explanation API: {exc.reason}") from None
    except json.JSONDecodeError:
        raise RuntimeError("The explanation API did not return JSON.") from None


def _check_base(url: str) -> None:
    """A caller-supplied base URL must not point back into our own network."""
    from .extract import assert_public_url

    if not url.startswith("https://"):
        raise RuntimeError("The API base URL must start with https://")
    assert_public_url(url)


def _anthropic(system: str, user: str, creds: dict | None = None) -> str:
    creds = creds or {}
    key = creds.get("key") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "Explain mode with Claude needs ANTHROPIC_API_KEY. Set it, pick the "
            "OpenAI-compatible engine, or use the offline outline."
        )
    data = _post(
        ANTHROPIC_URL,
        {
            "model": creds.get("model") or ANTHROPIC_MODEL,
            "max_tokens": 2000,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("The explanation API returned an empty answer.")
    return text


def _openai(system: str, user: str, creds: dict | None = None) -> str:
    creds = creds or {}
    key = creds.get("key") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "Explain mode with an OpenAI-compatible API needs OPENAI_API_KEY "
            "(and OPENAI_BASE_URL if you are not using OpenAI itself)."
        )
    base = OPENAI_BASE
    if creds.get("base_url"):
        base = creds["base_url"].rstrip("/")
        _check_base(base)
    data = _post(
        f"{base}/chat/completions",
        {
            "model": creds.get("model") or OPENAI_MODEL,
            "max_tokens": 2000,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        {"Authorization": f"Bearer {key}"},
    )
    try:
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError):
        raise RuntimeError(f"Unexpected reply from the explanation API: {str(data)[:200]}") from None
    if not text:
        raise RuntimeError("The explanation API returned an empty answer.")
    return text


# -------------------------------------------------------------------- entry

def explain_section(section: Section, doc_title: str, target: int = 320,
                    backend: str = "local", creds: dict | None = None) -> str:
    """`creds` lets the caller pass their own key for this request only.

    Nothing about it is stored or logged; it lives for the length of the call.
    """
    backend = ALIASES.get(backend, backend)
    if backend == "local":
        return _extractive(section, target)

    system = SYSTEM.format(target=target)
    user = (
        f"Document: {doc_title}\n"
        f"Section: {section.title}\n\n"
        f"---\n{section.text[:120_000]}\n---"
    )
    if backend == "anthropic":
        return _anthropic(system, user, creds)
    if backend == "openai":
        return _openai(system, user, creds)
    raise RuntimeError(f"Unknown explanation engine: {backend}")


def explain_document(doc: Document, target: int = 320, backend: str = "local",
                     on_progress=None, creds: dict | None = None) -> Document:
    out = Document(title=doc.title, source=doc.source, kind=doc.kind, sections=[])
    for i, sec in enumerate(doc.sections, 1):
        out.sections.append(
            Section(sec.title, explain_section(sec, doc.title, target, backend, creds))
        )
        if on_progress:
            on_progress(i, len(doc.sections))
    return out


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
