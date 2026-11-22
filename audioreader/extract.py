"""Turn a PDF, webpage, EPUB or text file into a Document."""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

from .model import Document, Section, clean

UA = "Mozilla/5.0 (compatible; AudioReader/0.1; personal reading tool)"


def load(source: str, max_sections: int | None = None) -> Document:
    """Dispatch on what `source` looks like."""
    if re.match(r"^https?://", source, re.I):
        doc = from_web(source)
    else:
        p = Path(source).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"No such file: {p}")
        ext = p.suffix.lower()
        if ext == ".pdf":
            doc = from_pdf(p)
        elif ext == ".epub":
            doc = from_epub(p)
        elif ext in (".txt", ".md", ".markdown", ".rst"):
            doc = from_text(p)
        else:
            raise ValueError(f"Unsupported file type: {ext or '(none)'}")

    doc.sections = [s for s in doc.sections if s.words >= 20]
    if not doc.sections:
        raise ValueError("No readable text found in that source.")
    if max_sections:
        doc.sections = doc.sections[:max_sections]
    return doc


# ------------------------------------------------------------------------ PDF

def from_pdf(path: Path) -> Document:
    try:
        import pymupdf as fitz
    except ImportError:  # older PyMuPDF releases
        import fitz

    pdf = fitz.open(path)
    title = (pdf.metadata or {}).get("title") or path.stem
    pages = [pdf.load_page(i).get_text("text") for i in range(pdf.page_count)]

    # Prefer the embedded table of contents for chapter boundaries.
    toc = [t for t in (pdf.get_toc() or []) if t[2] and t[2] > 0]
    sections: list[Section] = []
    if len(toc) >= 2:
        # Keep the shallowest meaningful level so we get chapters, not subheads.
        top = min(t[0] for t in toc)
        marks = [t for t in toc if t[0] <= top + 1]
        for i, (_lvl, name, start) in enumerate(marks):
            end = marks[i + 1][2] - 1 if i + 1 < len(marks) else pdf.page_count
            body = clean("\n".join(pages[start - 1 : end]))
            if body:
                sections.append(Section(title=name.strip() or f"Part {i+1}", text=body))

    if not sections:
        sections = _chunk_pages(pages)

    pdf.close()
    if not sections:
        raise ValueError(
            "This PDF has no extractable text - it is probably a scan. "
            "Run OCR on it first (e.g. ocrmypdf) and try again."
        )
    return Document(title=title.strip(), source=str(path), kind="pdf", sections=sections)


def _chunk_pages(pages: list[str], per_chunk: int = 12) -> list[Section]:
    """Fallback for PDFs with no usable TOC: fixed page groups."""
    out = []
    for i in range(0, len(pages), per_chunk):
        body = clean("\n".join(pages[i : i + per_chunk]))
        if body:
            lo, hi = i + 1, min(i + per_chunk, len(pages))
            out.append(Section(title=f"Pages {lo}-{hi}", text=body))
    return out


# --------------------------------------------------------------------- WEBPAGE

def assert_public_url(url: str) -> None:
    """Refuse anything that is not a public http(s) host.

    When AudioReader is hosted, the URL box is an open door into whatever
    network the server sits in — cloud metadata endpoints, internal admin
    pages, localhost services. Resolve the name first and reject every
    address that is not publicly routable.
    """
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("Only http:// and https:// addresses are supported.")
    host = parts.hostname
    if not host:
        raise ValueError("That URL has no host in it.")

    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve {host}.") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError(
                f"{host} resolves to a private address ({ip}). "
                "Only public web pages can be read."
            )


def _pdf_at(url: str) -> bytes | None:
    """If the URL serves a PDF rather than a page, return its bytes.

    A link straight to a .pdf is common, and the HTML extractor makes
    nonsense of one, so check before parsing.
    """
    import urllib.request

    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Range": "bytes=0-1023"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            head = resp.read(1024)
    except Exception:
        return None

    if "application/pdf" not in ctype and not head.startswith(b"%PDF-"):
        return None

    full = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(full, timeout=180) as resp:
        return resp.read(80 * 1024 * 1024)


def from_web(url: str) -> Document:
    import tempfile

    import trafilatura

    if os.environ.get("ALLOW_PRIVATE_URLS", "").lower() not in ("1", "true", "yes"):
        assert_public_url(url)

    pdf_bytes = _pdf_at(url)
    if pdf_bytes:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(pdf_bytes)
            tmp = Path(fh.name)
        try:
            doc = from_pdf(tmp)
        finally:
            tmp.unlink(missing_ok=True)
        doc.source = url
        if doc.title.startswith("tmp"):  # temp filename is not a title
            doc.title = Path(urlparse(url).path).stem.replace("-", " ").strip() or url
        return doc

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise RuntimeError(f"Could not fetch {url}")

    body = trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    title = None
    try:
        meta = trafilatura.extract_metadata(downloaded)
        if meta:
            title = meta.title
    except Exception:
        pass
    title = title or urlparse(url).netloc

    if not body:
        raise ValueError("Could not find article text on that page.")

    return Document(
        title=title.strip(),
        source=url,
        kind="web",
        # trafilatura separates paragraphs with a single newline; keep them as breaks
        sections=_split_long(clean(body.replace("\n", "\n\n")), title),
    )


def _split_long(text: str, title: str, target_words: int = 1200) -> list[Section]:
    """Split a long flat article into listenable parts at paragraph breaks."""
    paras = [p for p in text.split("\n\n") if p.strip()]
    total = sum(len(p.split()) for p in paras)
    if total <= target_words * 1.5:
        return [Section(title=title, text=text)]

    # A single wall-of-text paragraph still has to be broken up: use sentences.
    expanded: list[str] = []
    for p in paras:
        if len(p.split()) <= target_words:
            expanded.append(p)
            continue
        buf2: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", p):
            buf2.append(sentence)
            if len(" ".join(buf2).split()) >= target_words // 2:
                expanded.append(" ".join(buf2))
                buf2 = []
        if buf2:
            expanded.append(" ".join(buf2))
    paras = expanded

    out, buf, count, n = [], [], 0, 1
    for p in paras:
        buf.append(p)
        count += len(p.split())
        if count >= target_words:
            out.append(Section(f"{title} - part {n}", "\n\n".join(buf)))
            buf, count, n = [], 0, n + 1
    if buf:
        out.append(Section(f"{title} - part {n}", "\n\n".join(buf)))
    return out


# ----------------------------------------------------------------------- EPUB

def from_epub(path: Path) -> Document:
    import ebooklib
    from bs4 import BeautifulSoup
    from ebooklib import epub

    book = epub.read_epub(str(path), options={"ignore_ncx": True})
    titles = book.get_metadata("DC", "title")
    title = titles[0][0] if titles else path.stem

    sections: list[Section] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        head = soup.find(["h1", "h2", "h3"])
        name = head.get_text(" ", strip=True) if head else ""
        body = clean(soup.get_text("\n\n"))
        if body:
            sections.append(Section(name or f"Chapter {len(sections)+1}", body))

    return Document(title=str(title).strip(), source=str(path), kind="epub", sections=sections)


# ------------------------------------------------------------------ PLAIN TEXT

def from_text(path: Path) -> Document:
    raw = path.read_text(encoding="utf-8", errors="replace")
    body = clean(raw)
    return Document(
        title=path.stem,
        source=str(path),
        kind="txt",
        sections=_split_long(body, path.stem),
    )
