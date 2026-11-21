"""Pull plain text out of uploaded files (txt / md / docx / pdf / html)."""
from __future__ import annotations

import io
import re


def extract_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".docx"):
        return _docx(data)
    if name.endswith(".pdf"):
        return pdf_bytes_to_text(data)
    if name.endswith((".html", ".htm")):
        return html_to_text(data.decode("utf-8", "ignore"))
    return _decode(data)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "ignore")


def _docx(data: bytes) -> str:
    import docx  # python-docx

    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(p for p in parts if p is not None)


def pdf_bytes_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages[:200]:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - malformed page, keep going
            continue
    text = "\n\n".join(pages)
    text = re.sub(r"-\n(\w)", r"\1", text)            # de-hyphenate line breaks
    return re.sub(r"(?<![.\n])\n(?!\n)", " ", text)   # join wrapped lines


def html_to_text(html: str) -> str:
    try:
        import trafilatura

        out = trafilatura.extract(html, include_comments=False, include_tables=True, favor_recall=True)
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</h\d>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()
