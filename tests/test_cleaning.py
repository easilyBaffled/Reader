from audibleweb.lib.cleaning import apply_pronunciation_overrides, clean_text


def test_clean_text_fixes_curly_double_quotes():
    assert clean_text("“Hello”") == '"Hello"'


def test_clean_text_fixes_curly_single_quotes():
    assert clean_text("it’s") == "it's"


def test_clean_text_fixes_ellipsis():
    assert clean_text("Wait…") == "Wait..."


def test_clean_text_lowercases_all_caps_words():
    assert clean_text("The NASA report") == "The nasa report"


def test_clean_text_preserves_mixed_case():
    assert clean_text("Hello World") == "Hello World"


def test_clean_text_combined():
    result = clean_text("“NASA confirms…”")
    assert result == '"nasa confirms..."'


def test_apply_pronunciation_overrides_basic():
    result = apply_pronunciation_overrides(
        "Read the README file", {"README": "read me"}
    )
    assert result == "Read the read me file"


def test_apply_pronunciation_overrides_case_insensitive():
    result = apply_pronunciation_overrides("The API endpoint", {"api": "A P I"})
    assert result == "The A P I endpoint"


def test_apply_pronunciation_overrides_whole_word_only():
    result = apply_pronunciation_overrides("SQLite database", {"SQL": "sequel"})
    assert result == "SQLite database"


def test_apply_pronunciation_overrides_empty_dict():
    text = "unchanged text"
    assert apply_pronunciation_overrides(text, {}) == text


def test_apply_pronunciation_overrides_empty_text():
    assert apply_pronunciation_overrides("", {"word": "replacement"}) == ""
