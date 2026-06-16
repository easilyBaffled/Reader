"""Configuration loading: .env (secrets) -> config.yaml (settings) (docs/design.md sec 8).

Per CLAUDE.md's config hierarchy, config.yaml supplies app settings (sections
and field names match docs/design.md sec 8's documented shape exactly, with
missing file/sections/fields falling back to the defaults below). .env
supplies secrets that config.yaml deliberately leaves blank -- GitHub PAT,
API keys, LLM endpoint key -- via the env vars in _ENV_OVERRIDES. A set env
var always wins over a config.yaml value for these secret fields.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_ENV_PATH = Path(".env")


@dataclass
class FeedConfig:
    title: str = "My Reading Feed"
    description: str = "Articles converted to audio"
    cover: str = "cover.jpg"


@dataclass
class VoiceConfig:
    default: str = "af_heart"
    speed: float = 1.0


@dataclass
class TTSConfig:
    engine: str = "kokoro"
    base_url: str = "http://localhost:8880/v1"
    max_parallel: int = 4
    api_key: str = ""  # KOKORO_API_KEY in .env


@dataclass
class PublisherConfig:
    type: str = "github_pages"
    repo: str = "username/audibleweb-feed"
    branch: str = "gh-pages"
    token: str = ""  # GITHUB_PAT in .env
    max_episodes: int = 0  # 0 = unlimited; rotate out oldest when exceeded
    max_size_mb: int = 900  # GitHub Pages recommended limit; 0 = no check


@dataclass
class ExtractionConfig:
    jina_fallback: bool = True
    jina_api_key: str = ""  # JINA_API_KEY in .env
    rss_feeds: list[str] = field(default_factory=list)
    rss_poll_interval: int = 3600


@dataclass
class NormalizationConfig:
    llm_enabled: bool = True
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""  # LLM_API_KEY in .env


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 5000
    api_key: str = ""  # API_KEY in .env


@dataclass
class LoggingConfig:
    log_path: str = ""  # empty = no file logging; set in config.yaml for prod
    log_level: str = "INFO"
    max_bytes: int = 10_000_000  # 10 MB per file
    backup_count: int = 5


@dataclass
class AppConfig:
    feed: FeedConfig = field(default_factory=FeedConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    publisher: PublisherConfig = field(default_factory=PublisherConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    normalization: NormalizationConfig = field(default_factory=NormalizationConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# env var -> (AppConfig section attr, section field) for secrets left blank in
# config.yaml (docs/design.md sec 8: ".env -- secrets (GitHub PAT, API keys,
# LLM endpoint)").
_ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "GITHUB_PAT": ("publisher", "token"),
    "JINA_API_KEY": ("extraction", "jina_api_key"),
    "KOKORO_API_KEY": ("tts", "api_key"),
    "LLM_API_KEY": ("normalization", "llm_api_key"),
    "API_KEY": ("server", "api_key"),
}


def load_config(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    env_path: str | Path = DEFAULT_ENV_PATH,
) -> AppConfig:
    """Load env_path then config_path, merge into an AppConfig.

    Both files are optional -- a missing config.yaml yields an all-defaults
    AppConfig, and a missing .env is simply skipped by load_dotenv.
    """
    load_dotenv(env_path, override=False)

    raw: dict = {}
    config_path = Path(config_path)
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}

    config = AppConfig(
        feed=FeedConfig(**(raw.get("feed") or {})),
        voice=VoiceConfig(**(raw.get("voice") or {})),
        tts=TTSConfig(**(raw.get("tts") or {})),
        publisher=PublisherConfig(**(raw.get("publisher") or {})),
        extraction=ExtractionConfig(**(raw.get("extraction") or {})),
        normalization=NormalizationConfig(**(raw.get("normalization") or {})),
        server=ServerConfig(**(raw.get("server") or {})),
        logging=LoggingConfig(**(raw.get("logging") or {})),
    )

    for env_var, (section, attr) in _ENV_OVERRIDES.items():
        value = os.environ.get(env_var)
        if value:
            setattr(getattr(config, section), attr, value)

    return config
