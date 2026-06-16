"""Plugin discovery: scans plugins/{extractors,engines,publishers}/ on startup.

Drop a .py file in plugins/{extractors,engines,publishers}/ implementing the
relevant Protocol (Extractor/TTSEngine/Publisher). No entry points or other
registration needed — the PluginRegistry picks it up automatically.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
from pathlib import Path

from audibleweb.engines.base import TTSEngine
from audibleweb.engines.kokoro import KokoroEngine
from audibleweb.extractors.base import Extractor
from audibleweb.extractors.file import FileExtractor
from audibleweb.extractors.raw_text import RawTextExtractor
from audibleweb.extractors.web import WebExtractor
from audibleweb.publishers.base import Publisher
from audibleweb.publishers.github_pages import GitHubPagesPublisher
from audibleweb.publishers.local import LocalPublisher

logger = logging.getLogger(__name__)

_BUILTIN_EXTRACTOR_CLASSES: list[type] = [WebExtractor, FileExtractor, RawTextExtractor]
_BUILTIN_ENGINE_CLASSES: list[type] = [KokoroEngine]
_BUILTIN_PUBLISHER_CLASSES: list[type] = [GitHubPagesPublisher, LocalPublisher]


def _is_plugin_class(cls: type, proto: type) -> bool:
    """Structural Protocol check: cls has all attrs in proto and is not itself a Protocol."""
    if cls is proto:
        return False
    if "__protocol_attrs__" in cls.__dict__:
        return False
    attrs: frozenset[str] = getattr(proto, "__protocol_attrs__", frozenset())
    return bool(attrs) and all(hasattr(cls, attr) for attr in attrs)


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        logger.exception("Failed to load plugin module %s", path)
        return None
    return module


def _discover_classes(plugin_dir: Path, proto: type) -> list[type]:
    """Return all classes in plugin_dir/*.py that structurally implement proto."""
    if not plugin_dir.is_dir():
        return []
    found: list[type] = []
    for py_file in sorted(plugin_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        module = _load_module(py_file)
        if module is None:
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != module.__name__:
                continue  # skip classes imported from other modules
            if _is_plugin_class(obj, proto):
                found.append(obj)
                logger.debug("Plugin discovered: %s from %s", obj.__name__, py_file)
    return found


class PluginRegistry:
    """Built-in + user plugin classes for each plugin type.

    Built-in classes are pre-loaded on init. Call load() to add user plugins
    from the plugins/ directory.
    """

    def __init__(self) -> None:
        self.extractor_classes: list[type] = list(_BUILTIN_EXTRACTOR_CLASSES)
        self.engine_classes: dict[str, type] = {
            cls.name: cls  # type: ignore[attr-defined]
            for cls in _BUILTIN_ENGINE_CLASSES
        }
        self.publisher_classes: dict[str, type] = {
            cls.name: cls  # type: ignore[attr-defined]
            for cls in _BUILTIN_PUBLISHER_CLASSES
        }

    def load(self, plugins_dir: Path) -> None:
        """Discover user plugins from plugins_dir/{extractors,engines,publishers}/."""
        for cls in _discover_classes(plugins_dir / "extractors", Extractor):
            self.extractor_classes.append(cls)
        for cls in _discover_classes(plugins_dir / "engines", TTSEngine):
            name = getattr(cls, "name", None)
            if name:
                self.engine_classes[name] = cls
        for cls in _discover_classes(plugins_dir / "publishers", Publisher):
            name = getattr(cls, "name", None)
            if name:
                self.publisher_classes[name] = cls
