from audibleweb.lib.chunking import chunk_text


def test_chunk_text_empty_input_returns_empty_list():
    assert chunk_text("", level="paragraph") == []
    assert chunk_text("   \n\n  ", level="sentence") == []


def test_chunk_text_paragraph_level_splits_on_blank_lines():
    text = "Line one  with  double spaces.\nSecond line\n\nThird paragraph."

    chunks = chunk_text(text, level="paragraph")

    assert chunks == [
        "Line one with double spaces. Second line",
        "Third paragraph.",
    ]


def test_chunk_text_sentence_level_splits_on_sentence_boundaries():
    text = "First sentence. Second sentence! Third sentence?\n\nNew paragraph sentence."

    chunks = chunk_text(text, level="sentence")

    assert chunks == [
        "First sentence.",
        "Second sentence!",
        "Third sentence?",
        "New paragraph sentence.",
    ]


def test_chunk_text_merges_title_abbreviations():
    text = "Dr. Watson met Mr. Holmes at 5 p.m."

    chunks = chunk_text(text, level="sentence")

    assert chunks == ["Dr. Watson met Mr. Holmes at 5 p.m."]


def test_chunk_text_sentence_level_normalizes_whitespace():
    text = "Spread   across\nmultiple   lines."

    chunks = chunk_text(text, level="sentence")

    assert chunks == ["Spread across multiple lines."]
