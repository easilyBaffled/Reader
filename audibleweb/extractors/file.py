"""FileExtractor: PDF (PyMuPDF), TXT, Markdown file input (docs/design.md sec 2.1)."""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from .base import Article, ExtractionError, make_article

_SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md"}


class FileExtractor:
    name = "file"
    supported_inputs = ["file:pdf", "file:txt", "file:md"]

    def can_handle(self, input: str) -> bool:
        return Path(input).suffix.lower() in _SUPPORTED_SUFFIXES

    async def extract(self, input: str) -> Article:
        path = Path(input)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._extract_pdf(path)
        if suffix == ".md":
            return make_article(path.read_text(encoding="utf-8"))
        if suffix == ".txt":
            return make_article(path.read_text(encoding="utf-8"), title=path.stem)
        raise ExtractionError(f"Unsupported file type: {suffix or '(none)'}")

    def _extract_pdf(self, path: Path) -> Article:
        try:
            doc = fitz.open(path)
        except Exception as exc:
            raise ExtractionError(f"Could not open PDF: {exc}") from exc
        try:
            text = "\n\n".join(page.get_text().strip() for page in doc)
            title = doc.metadata.get("title") or path.stem
            author = doc.metadata.get("author") or None
        finally:
            doc.close()
        return make_article(text, title=title, author=author)
