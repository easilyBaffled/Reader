from pathlib import Path

import pytest

from audibleweb.config import AppConfig, load_config


@pytest.fixture
def clean_env(monkeypatch):
    for var in (
        "GITHUB_PAT",
        "JINA_API_KEY",
        "KOKORO_API_KEY",
        "LLM_API_KEY",
        "API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_defaults_when_no_files(tmp_path, clean_env):
    config = load_config(
        config_path=tmp_path / "missing.yaml", env_path=tmp_path / "missing.env"
    )

    assert config == AppConfig()


def test_loads_config_yaml_sections(tmp_path, clean_env):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
feed:
  title: "Custom Feed"
  description: "Custom description"
  cover: "custom.jpg"

voice:
  default: "am_adam"
  speed: 1.25

tts:
  engine: kokoro
  base_url: "http://kokoro.local:8880/v1"
  max_parallel: 8

publisher:
  type: local
  repo: "me/myfeed"
  branch: "pages"

extraction:
  jina_fallback: false
  rss_feeds: ["https://example.com/feed.xml"]
  rss_poll_interval: 1800

normalization:
  llm_enabled: false
  llm_base_url: "http://llm.local/v1"
  llm_model: "gpt-test"

server:
  host: "127.0.0.1"
  port: 8080
"""
    )

    config = load_config(config_path=config_path, env_path=tmp_path / "missing.env")

    assert config.feed.title == "Custom Feed"
    assert config.voice.default == "am_adam"
    assert config.voice.speed == 1.25
    assert config.tts.base_url == "http://kokoro.local:8880/v1"
    assert config.tts.max_parallel == 8
    assert config.publisher.type == "local"
    assert config.extraction.jina_fallback is False
    assert config.extraction.rss_feeds == ["https://example.com/feed.xml"]
    assert config.normalization.llm_model == "gpt-test"
    assert config.server.port == 8080


def test_partial_config_yaml_falls_back_to_defaults(tmp_path, clean_env):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("feed:\n  title: Only Title\n")

    config = load_config(config_path=config_path, env_path=tmp_path / "missing.env")

    assert config.feed.title == "Only Title"
    assert config.feed.description == "Articles converted to audio"
    assert config.voice == AppConfig().voice


def test_env_file_overrides_secrets(tmp_path, clean_env):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "GITHUB_PAT=ghp_test123\n"
        "JINA_API_KEY=jina_test\n"
        "KOKORO_API_KEY=kokoro_test\n"
        "LLM_API_KEY=llm_test\n"
        "API_KEY=server_test\n"
    )

    config = load_config(config_path=tmp_path / "missing.yaml", env_path=env_path)

    assert config.publisher.token == "ghp_test123"
    assert config.extraction.jina_api_key == "jina_test"
    assert config.tts.api_key == "kokoro_test"
    assert config.normalization.llm_api_key == "llm_test"
    assert config.server.api_key == "server_test"


def test_env_var_overrides_config_yaml_secret(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("extraction:\n  jina_api_key: from-yaml\n")
    monkeypatch.setenv("JINA_API_KEY", "from-env")
    for var in ("GITHUB_PAT", "KOKORO_API_KEY", "LLM_API_KEY", "API_KEY"):
        monkeypatch.delenv(var, raising=False)

    config = load_config(config_path=config_path, env_path=tmp_path / "missing.env")

    assert config.extraction.jina_api_key == "from-env"


def test_default_config_yaml_loads(clean_env):
    config = load_config(
        config_path=Path("config.yaml"), env_path=Path("nonexistent.env")
    )

    assert config.feed.title == "My Reading Feed"
    assert config.tts.engine == "kokoro"
    assert config.publisher.type == "github_pages"
