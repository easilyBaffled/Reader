"""Stage 1 + Stage 3 text cleaning for TTS preprocessing."""

import re

_PUNCTUATION_REPLACEMENTS = {
    "“": '"',
    "”": '"',
    "„": '"',
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "…": "...",
}

_ALL_CAPS_PATTERN = re.compile(r"\b[A-Z]{2,}\b")


def clean_text(text: str) -> str:
    """Stage 1: fix non-standard punctuation and lowercase ALL CAPS words."""
    for old, new in _PUNCTUATION_REPLACEMENTS.items():
        text = text.replace(old, new)

    text = _ALL_CAPS_PATTERN.sub(lambda m: m.group(0).lower(), text)
    return text


def apply_pronunciation_overrides(text: str, pronunciation: dict[str, str]) -> str:
    """Stage 3: replace words/phrases using whole-word matching from pronunciation dict."""
    for word, replacement in pronunciation.items():
        pattern = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
        text = pattern.sub(replacement, text)
    return text
