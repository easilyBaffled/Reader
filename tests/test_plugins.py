"""Tests for plugin discovery (reader-8f2.13)."""

import asyncio
import textwrap
from pathlib import Path

import pytest

from audibleweb.extractors.base import Article
from audibleweb.extractors.file import FileExtractor
from audibleweb.extractors.raw_text import RawTextExtractor
from audibleweb.extractors.web import WebExtractor
from audibleweb.engines.kokoro import KokoroEngine
from audibleweb.plugins import PluginRegistry, _discover_classes, _is_plugin_class
from audibleweb.publishers.github_pages import GitHubPagesPublisher
from audibleweb.publishers.local import LocalPublisher
from audibleweb.extractors.base import Extractor
from audibleweb.engines.base import TTSEngine
from audibleweb.publishers.base import Publisher


# ---------------------------------------------------------------------------
# _is_plugin_class
# ---------------------------------------------------------------------------


def test_is_plugin_class_rejects_protocol_itself():
    assert not _is_plugin_class(Extractor, Extractor)
    assert not _is_plugin_class(TTSEngine, TTSEngine)
    assert not _is_plugin_class(Publisher, Publisher)


def test_is_plugin_class_accepts_builtin_implementations():
    assert _is_plugin_class(WebExtractor, Extractor)
    assert _is_plugin_class(FileExtractor, Extractor)
    assert _is_plugin_class(RawTextExtractor, Extractor)
    assert _is_plugin_class(KokoroEngine, TTSEngine)
    assert _is_plugin_class(LocalPublisher, Publisher)
    assert _is_plugin_class(GitHubPagesPublisher, Publisher)


def test_is_plugin_class_rejects_incomplete_class():
    class Incomplete:
        name = "x"

    assert not _is_plugin_class(Incomplete, Extractor)


# ---------------------------------------------------------------------------
# PluginRegistry — built-ins
# ---------------------------------------------------------------------------


def test_registry_includes_builtin_extractors():
    registry = PluginRegistry()
    assert WebExtractor in registry.extractor_classes
    assert FileExtractor in registry.extractor_classes
    assert RawTextExtractor in registry.extractor_classes


def test_registry_includes_builtin_engines():
    registry = PluginRegistry()
    assert "kokoro" in registry.engine_classes
    assert registry.engine_classes["kokoro"] is KokoroEngine


def test_registry_includes_builtin_publishers():
    registry = PluginRegistry()
    assert "local" in registry.publisher_classes
    assert "github_pages" in registry.publisher_classes


# ---------------------------------------------------------------------------
# Plugin discovery — user extractor dropped into plugins/extractors/
# ---------------------------------------------------------------------------

FIXTURE_EXTRACTOR_SRC = textwrap.dedent("""\
    from __future__ import annotations
    from audibleweb.extractors.base import Article, make_article

    class FixtureExtractor:
        name = "fixture"
        supported_inputs = ["fixture"]

        def can_handle(self, input: str) -> bool:
            return input.startswith("fixture:")

        async def extract(self, input: str) -> Article:
            content = input.removeprefix("fixture:") or "default content"
            return make_article("x" * 100 + " " + content, title="Fixture")
""")


@pytest.fixture()
def plugin_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plugins"
    (d / "extractors").mkdir(parents=True)
    (d / "engines").mkdir()
    (d / "publishers").mkdir()
    (d / "extractors" / "fixture.py").write_text(FIXTURE_EXTRACTOR_SRC)
    return d


def test_discover_classes_finds_fixture_extractor(plugin_dir: Path):
    found = _discover_classes(plugin_dir / "extractors", Extractor)
    names = [cls.name for cls in found]
    assert "fixture" in names


def test_registry_load_adds_plugin_extractor(plugin_dir: Path):
    registry = PluginRegistry()
    registry.load(plugin_dir)

    names = [cls.name for cls in registry.extractor_classes]
    assert "fixture" in names
    assert "web" in names  # built-ins still present


def test_registry_load_empty_dir_no_crash(tmp_path: Path):
    empty = tmp_path / "plugins"
    registry = PluginRegistry()
    registry.load(empty)  # dirs don't exist — must not raise
    assert WebExtractor in registry.extractor_classes


def test_discovered_extractor_is_usable(plugin_dir: Path):
    registry = PluginRegistry()
    registry.load(plugin_dir)

    (FixtureCls,) = [cls for cls in registry.extractor_classes if cls.name == "fixture"]
    extractor = FixtureCls()

    assert extractor.can_handle("fixture:hello")
    assert not extractor.can_handle("http://example.com")

    article = asyncio.run(extractor.extract("fixture:hello"))
    assert isinstance(article, Article)
    assert article.title == "Fixture"


def test_discover_skips_dunder_files(plugin_dir: Path):
    (plugin_dir / "extractors" / "__helpers.py").write_text("class Helper: pass\n")
    found = _discover_classes(plugin_dir / "extractors", Extractor)
    class_names = [cls.__name__ for cls in found]
    assert "Helper" not in class_names


def test_discover_skips_imported_classes(plugin_dir: Path):
    src = textwrap.dedent("""\
        from audibleweb.extractors.web import WebExtractor as _Web

        class LocalPlugin:
            name = "local_plugin"
            supported_inputs = ["x"]
            def can_handle(self, input: str) -> bool: return True
            async def extract(self, input: str): ...
    """)
    (plugin_dir / "extractors" / "local_plugin.py").write_text(src)
    found = _discover_classes(plugin_dir / "extractors", Extractor)
    class_names = [cls.__name__ for cls in found]
    assert "WebExtractor" not in class_names
    assert "LocalPlugin" in class_names
