"""Text extraction for PDF, DOCX, TXT and Markdown uploads.

Files are validated by extension AND content signature, parsed in memory
(never written to disk) and returned as pages so citations can point to a
page number.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePath


class ExtractionError(ValueError):
    pass


SUPPORTED = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass
class Page:
    text: str
    page: int | None = None


def detect_type(filename: str, content: bytes) -> str:
    suffix = PurePath(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise ExtractionError(f"Unsupported file type '{suffix or filename}'. Upload PDF, DOCX, TXT or MD.")
    if suffix == ".pdf" and not content.startswith(b"%PDF"):
        raise ExtractionError("File does not look like a PDF")
    if suffix == ".docx" and not content.startswith(b"PK\x03\x04"):
        raise ExtractionError("File does not look like a DOCX document")
    if suffix in (".txt", ".md", ".markdown") and b"\x00" in content[:4096]:
        raise ExtractionError("Text file appears to be binary")
    return SUPPORTED[suffix]


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def _pdf(content: bytes) -> list[Page]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ExtractionError("Encrypted PDFs are not supported")
        pages = [Page(_clean(p.extract_text() or ""), i) for i, p in enumerate(reader.pages, start=1)]
    except PdfReadError as exc:
        raise ExtractionError(f"Could not read PDF: {exc}") from exc
    return [p for p in pages if p.text]


def _docx(content: bytes) -> list[Page]:
    import docx

    try:
        document = docx.Document(io.BytesIO(content))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ExtractionError("Could not read DOCX document") from exc
    parts: list[str] = []
    for para in document.paragraphs:
        if para.text.strip():
            prefix = (
                "## " if (para.style is not None and para.style.name.lower().startswith("heading")) else ""
            )
            parts.append(prefix + para.text.strip())
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    text = _clean("\n\n".join(parts))
    return [Page(text)] if text else []


def extract_pages(filename: str, content: bytes) -> list[Page]:
    mime = detect_type(filename, content)
    if mime == "application/pdf":
        pages = _pdf(content)
    elif mime.endswith("wordprocessingml.document"):
        pages = _docx(content)
    else:
        text = _clean(_decode(content))
        pages = [Page(text)] if text else []
    if not pages:
        raise ExtractionError("No extractable text found (scanned PDFs need OCR, which is not supported)")
    return pages
