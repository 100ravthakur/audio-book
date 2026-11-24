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
        # Table markup. Old sites lay a whole article out in one table cell,
        # so judge by length: long means prose, short means a navigation box.
        if "|" in s:
            if re.fullmatch(r"[|\s:\-\u2013\u2014]+", s):
                continue
            cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
            joined = " ".join(cells)
            # Prose runs long or ends in a full stop; a navigation cell
            # ("Part of a series of articles on the") does neither.
            if len(joined.split()) < 14 and not joined.rstrip().endswith((".", "!", "?")):
                continue
            s = joined
        out_lines.append(s)

    text = "\n".join(out_lines)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Said aloud, an abbreviation is a stumble, and its full stop also splits the
# sentence in the wrong place. Spell them out.
_ABBREV = [
    (r"\be\.\s?g\.", "for example"), (r"\bi\.\s?e\.", "that is"),
    (r"\betc\.", "and so on"), (r"\bvs\.?\b", "versus"),
    (r"\bapprox\.", "approximately"), (r"\bcf\.", "compare"),
    (r"\bFig\.", "Figure"), (r"\bNo\.\s*(?=\d)", "Number "),
    (r"\bca\.\s*(?=\d)", "around "), (r"\bpp\.\s*(?=\d)", "pages "),
    (r"\bp\.\s*(?=\d)", "page "),
]

_CURRENCY = [
    (r"\$\s?([\d][\d,.]*)", r"\1 dollars"), (r"₹\s?([\d][\d,.]*)", r"\1 rupees"),
    (r"€\s?([\d][\d,.]*)", r"\1 euros"), (r"£\s?([\d][\d,.]*)", r"\1 pounds"),
    (r"¥\s?([\d][\d,.]*)", r"\1 yen"),
]

_SYMBOLS = {
    "%": " percent ", "&": " and ", "×": " times ", "÷": " divided by ",
    "±": " plus or minus ", "°": " degrees ", "≈": " roughly ", "≤": " at most ",
    "≥": " at least ", "≠": " not equal to ", "→": " to ", "=": " equals ",
    "$": " dollars ", "₹": " rupees ", "€": " euros ", "£": " pounds ",
}


_OPENERS = ("equals", "plus", "times", "divided by", "at most", "at least",
            "roughly", "percent", "not equal to", "where ,")
_DANGLING = {"is", "or", "and", "the", "a", "an", "of", "to", "equals", "where",
             "with", "by", "for", "equivalently", "that", "than", "as", "in"}


def _is_debris(sentence: str) -> bool:
    """True for formula wreckage left behind when symbols are stripped out.

    Maths on a page is usually an image or MathML, so the extractor drops it
    and leaves things like "An exact formula for the payment () is or
    equivalently". Reading that aloud is worse than skipping it.
    """
    s = sentence.strip()
    low = s.lower()
    words = s.split()
    if len(words) < 4:
        return True

    # "= monthly interest rate" became "equals monthly interest rate".
    if low.startswith(_OPENERS):
        return True
    # A clause left hanging because the maths after it vanished.
    if low.rstrip(".,;:").split()[-1] in _DANGLING and not s.endswith((".", "!", "?")):
        return True
    # Code and spreadsheet formulas: PMT(rate, nper, pv), snake_case names.
    if re.search(r"[A-Za-z_]+\s*\([A-Za-z_]+\s*,", s) or re.search(r"\w+_\w+", s):
        return True

    singles = sum(1 for w in words if len(w.strip(".,;:()")) <= 1)
    if singles / len(words) > 0.25:
        return True
    letters = sum(c.isalpha() or c.isspace() for c in s)
    return letters / max(len(s), 1) < 0.62


def strip_for_speech(text: str) -> str:
    """Turn extracted text into something worth hearing."""
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"\[\s*\d+\s*\]", "", text)                      # citations
    text = re.sub(r"\[\s*(edit|citation needed)\s*\]", "", text, flags=re.I)

    for pat, rep in _ABBREV:
        text = re.sub(pat, rep, text)
    for pat, rep in _CURRENCY:
        text = re.sub(pat, rep, text)

    kept = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            kept.append("")
            continue
        if re.fullmatch(r"[\s|:\-–—_=*+·•]{2,}", s):               # rules, leaders
            continue
        if re.fullmatch(r"(v\s*t\s*e|edit|references|external links|see also)",
                        s, flags=re.I):
            continue
        kept.append(re.sub(r"^[\-–—•*]\s+", "", s))                # bullet leaders
    text = "\n".join(kept)

    for sym, word in _SYMBOLS.items():
        text = text.replace(sym, word)

    text = re.sub(r"\(\s*\)", " ", text)        # parens emptied by stripped maths
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\bU\.\s?S\.", "US", text)   # or the full stop splits the sentence
    text = re.sub(r"\bU\.\s?K\.", "UK", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Finally drop whole sentences that survived only as wreckage.
    out = []
    for para in text.split("\n\n"):
        good = [s for s in re.split(r"(?<=[.!?])\s+", para.strip())
                if s and not _is_debris(s)]
        if good:
            out.append(" ".join(good))
    return "\n\n".join(out).strip()
