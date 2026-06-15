import asyncio

import fitz
import pytest

from audibleweb.extractors.base import ExtractionError, derive_title, make_article
from audibleweb.extractors.file import FileExtractor
from audibleweb.extractors.raw_text import RawTextExtractor

LONG_TEXT = "This is a sufficiently long sentence for extraction tests. " * 3


def run(coro):
    return asyncio.run(coro)


# --- base.py: derive_title / make_article -----------------------------------


def test_derive_title_uses_first_non_empty_line():
    assert derive_title("\n\n  Hello World  \nBody text.") == "Hello World"


def test_derive_title_strips_markdown_heading_markers():
    assert derive_title("## My Heading\nBody text.") == "My Heading"


def test_derive_title_falls_back_to_untitled_for_blank_text():
    assert derive_title("   \n  \n") == "Untitled"


def test_derive_title_truncates_long_lines():
    line = "x" * 150
    title = derive_title(line)
    assert title.endswith("…")
    assert len(title) == 100


def test_make_article_computes_word_count_and_default_title():
    article = make_article(LONG_TEXT)

    assert article.text == LONG_TEXT.strip()
    assert article.word_count == len(LONG_TEXT.split())
    assert article.title.startswith("This is a sufficiently long sentence")
    assert article.source_url is None
    assert article.author is None
    assert article.published is None


def test_make_article_rejects_short_text():
    with pytest.raises(ExtractionError, match="No extractable content"):
        make_article("too short")


# --- raw_text.py --------------------------------------------------------------


def test_raw_text_extractor_can_handle_anything():
    extractor = RawTextExtractor()

    assert extractor.can_handle("anything at all") is True
    assert extractor.can_handle("") is True


def test_raw_text_extractor_extract_returns_article():
    extractor = RawTextExtractor()

    article = run(extractor.extract(LONG_TEXT))

    assert article.text == LONG_TEXT.strip()
    assert article.source_url is None


def test_raw_text_extractor_extract_rejects_short_input():
    extractor = RawTextExtractor()

    with pytest.raises(ExtractionError, match="No extractable content"):
        run(extractor.extract("hi"))


# --- file.py: can_handle ------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("article.pdf", True),
        ("article.txt", True),
        ("article.md", True),
        ("article.PDF", True),
        ("article.docx", False),
        ("no_extension", False),
    ],
)
def test_file_extractor_can_handle(filename, expected):
    extractor = FileExtractor()

    assert extractor.can_handle(filename) is expected


# --- file.py: extract (.txt) ---------------------------------------------------


def test_file_extractor_extract_txt_uses_filename_as_title(tmp_path):
    path = tmp_path / "my-article.txt"
    path.write_text(LONG_TEXT, encoding="utf-8")

    article = run(FileExtractor().extract(str(path)))

    assert article.title == "my-article"
    assert article.text == LONG_TEXT.strip()
    assert article.word_count == len(LONG_TEXT.split())


# --- file.py: extract (.md) -----------------------------------------------------


def test_file_extractor_extract_md_derives_title_from_heading(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text(f"# My Markdown Title\n\n{LONG_TEXT}", encoding="utf-8")

    article = run(FileExtractor().extract(str(path)))

    assert article.title == "My Markdown Title"
    assert LONG_TEXT.strip() in article.text


# --- file.py: extract (.pdf) -----------------------------------------------------


def test_file_extractor_extract_pdf(tmp_path):
    path = tmp_path / "doc.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), LONG_TEXT)
    doc.set_metadata({"title": "PDF Title", "author": "PDF Author"})
    doc.save(path)
    doc.close()

    article = run(FileExtractor().extract(str(path)))

    assert article.title == "PDF Title"
    assert article.author == "PDF Author"
    assert "sufficiently long sentence" in article.text


# --- file.py: error handling ------------------------------------------------------


def test_file_extractor_extract_unsupported_suffix_raises(tmp_path):
    path = tmp_path / "article.docx"
    path.write_text(LONG_TEXT, encoding="utf-8")

    with pytest.raises(ExtractionError, match="Unsupported file type"):
        run(FileExtractor().extract(str(path)))


def test_file_extractor_extract_missing_file_raises(tmp_path):
    path = tmp_path / "missing.txt"

    with pytest.raises(FileNotFoundError):
        run(FileExtractor().extract(str(path)))


def test_file_extractor_extract_short_txt_raises(tmp_path):
    path = tmp_path / "short.txt"
    path.write_text("too short", encoding="utf-8")

    with pytest.raises(ExtractionError, match="No extractable content"):
        run(FileExtractor().extract(str(path)))
