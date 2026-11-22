"""Shared data model: every input source becomes a Document."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Section:
    """One chapter / article / logical chunk of a document."""

    title: str
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass
class Document:
    title: str
    source: str
    kind: str  # pdf | web | epub | txt
    sections: list[Section] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(s.words for s in self.sections)

    @property
    def full_text(self) -> str:
        return "\n\n".join(f"{s.title}\n\n{s.text}" for s in self.sections)

    def est_minutes(self, wpm: int = 165) -> float:
        return self.words / wpm


# ---------------------------------------------------------------- text cleanup

_LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\u2018": "'", "\u2019": "'", "\u201c": '"',
    "\u201d": '"', "\u2013": "-", "\u2014": " - ", "\u2026": "...",
    "\u00a0": " ", "\u200b": "",
}


def clean(raw: str) -> str:
    """Make extracted text safe and pleasant to read aloud."""
    if not raw:
        return ""
    for bad, good in _LIGATURES.items():
        raw = raw.replace(bad, good)

    # Join words broken across a line by a hyphen: "exam-\nple" -> "example"
    raw = re.sub(r"(\w)-\n(\w)", r"\1\2", raw)
    # A single newline inside a paragraph is a soft wrap, not a break.
    raw = re.sub(r"(?<![.!?:;\n])\n(?!\n)", " ", raw)

    out_lines = []
    for line in raw.split("\n"):
        s = line.strip()
        if not s:
            out_lines.append("")
            continue
        # Drop bare page numbers and "Page 12 of 300" running headers.
        if re.fullmatch(r"(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?", s, re.I):
            continue
        # Drop lines that are mostly figure/table noise or dot leaders.
        if re.fullmatch(r"[.\-_\u2022\s]{4,}", s):
            continue
        out_lines.append(s)

    text = "\n".join(out_lines)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_for_speech(text: str) -> str:
    """Remove things a listener cannot hear usefully."""
    text = re.sub(r"https?://\S+", "link", text)
    text = re.sub(r"\[\d+\]", "", text)        # citation markers
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
