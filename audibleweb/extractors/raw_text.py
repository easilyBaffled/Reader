"""RawTextExtractor: pasted/POSTed raw text input (docs/design.md sec 2.1)."""

from __future__ import annotations

from .base import Article, make_article


class RawTextExtractor:
    name = "raw_text"
    supported_inputs = ["text"]

    def can_handle(self, input: str) -> bool:
        # Catch-all: raw text is selected explicitly (input_type="text"), never
        # auto-detected, so this must rank last in any can_handle-based dispatch.
        return True

    async def extract(self, input: str) -> Article:
        return make_article(input)
