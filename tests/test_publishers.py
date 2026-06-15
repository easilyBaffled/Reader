import asyncio
import subprocess
from datetime import UTC, datetime

import pytest

from audibleweb.core.feed import FeedConfig, validate_feed
from audibleweb.publishers.base import Episode, episode_slug
from audibleweb.publishers.github_pages import (
    GitHubPagesPublisher,
    GitHubPagesPublisherError,
)
from audibleweb.publishers.local import LocalPublisher


def run(coro):
    return asyncio.run(coro)


FEED_CONFIG = FeedConfig(
    title="My Reading Feed", link="https://example.com", description="d"
)


def _episode(**overrides):
    defaults = dict(
        title="Article Title",
        published=datetime(2026, 6, 13, 10, 0, 0, tzinfo=UTC),
        duration_sec=120,
        source_url="https://source.example/article",
        public_url="https://example.com/audio/2026-06-13-article-title.mp3",
        file_size_bytes=1024,
    )
    defaults.update(overrides)
    return Episode(**defaults)


# --- episode_slug --------------------------------------------------------------


def test_episode_slug_sanitizes_title():
    slug = episode_slug("Hello, World! 2026", datetime(2026, 6, 13, tzinfo=UTC))
    assert slug == "2026-06-13-hello-world-2026"


def test_episode_slug_falls_back_to_date_when_title_has_no_alnum():
    slug = episode_slug("!!!", datetime(2026, 6, 13, tzinfo=UTC))
    assert slug == "2026-06-13"


# --- LocalPublisher --------------------------------------------------------------


def test_local_publisher_publish_copies_audio_file(tmp_path):
    audio_src = tmp_path / "src.mp3"
    audio_src.write_bytes(b"audio-bytes")
    publisher = LocalPublisher(tmp_path / "data", "http://localhost:5000", FEED_CONFIG)

    url = run(publisher.publish(_episode(), audio_src))

    assert url == "http://localhost:5000/audio/2026-06-13-article-title.mp3"
    dest = tmp_path / "data" / "audio" / "2026-06-13-article-title.mp3"
    assert dest.read_bytes() == b"audio-bytes"


def test_local_publisher_strips_trailing_slash_from_base_url(tmp_path):
    audio_src = tmp_path / "src.mp3"
    audio_src.write_bytes(b"audio-bytes")
    publisher = LocalPublisher(tmp_path / "data", "http://localhost:5000/", FEED_CONFIG)

    url = run(publisher.publish(_episode(), audio_src))

    assert url == "http://localhost:5000/audio/2026-06-13-article-title.mp3"


def test_local_publisher_update_feed_writes_valid_feed(tmp_path):
    publisher = LocalPublisher(tmp_path / "data", "http://localhost:5000", FEED_CONFIG)

    url = run(publisher.update_feed([_episode()]))

    assert url == "http://localhost:5000/feed.xml"
    feed_path = tmp_path / "data" / "feed.xml"
    validate_feed(feed_path.read_text())  # no raise


# --- GitHubPagesPublisher ----------------------------------------------------------


def _git(*args: str, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def bare_repo(tmp_path):
    """A local bare repo with a seeded gh-pages branch, standing in for the GitHub remote."""
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    subprocess.run(
        ["git", "init", "--bare", "-b", "gh-pages", str(bare)],
        check=True,
        capture_output=True,
    )
    _git("init", "-b", "gh-pages", cwd=seed)
    (seed / "README.md").write_text("seed")
    _git("add", "-A", cwd=seed)
    _git(
        "-c",
        "user.name=seed",
        "-c",
        "user.email=seed@test",
        "commit",
        "-m",
        "init",
        cwd=seed,
    )
    _git("remote", "add", "origin", str(bare), cwd=seed)
    _git("push", "origin", "gh-pages", cwd=seed)
    return bare


def _publisher(work_dir, bare_repo, repo="testuser/testrepo", token=""):
    return GitHubPagesPublisher(
        repo=repo,
        token=token,
        work_dir=work_dir,
        feed_config=FEED_CONFIG,
        remote_url=str(bare_repo),
    )


def _show(bare_repo, path: str) -> bytes:
    result = subprocess.run(
        ["git", "--git-dir", str(bare_repo), "show", f"gh-pages:{path}"],
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_publish_copies_audio_and_pushes_to_remote(tmp_path, bare_repo):
    audio_src = tmp_path / "src.mp3"
    audio_src.write_bytes(b"fake mp3 data")
    publisher = _publisher(tmp_path / "clone", bare_repo)

    url = run(publisher.publish(_episode(), audio_src))

    assert (
        url == "https://testuser.github.io/testrepo/audio/2026-06-13-article-title.mp3"
    )
    assert _show(bare_repo, "audio/2026-06-13-article-title.mp3") == b"fake mp3 data"


def test_update_feed_writes_validates_and_pushes_to_remote(tmp_path, bare_repo):
    publisher = _publisher(tmp_path / "clone", bare_repo)

    url = run(publisher.update_feed([_episode()]))

    assert url == "https://testuser.github.io/testrepo/feed.xml"
    validate_feed(_show(bare_repo, "feed.xml").decode())  # no raise


def test_clone_reused_across_calls(tmp_path, bare_repo):
    audio_src = tmp_path / "src.mp3"
    audio_src.write_bytes(b"fake mp3 data")
    publisher = _publisher(tmp_path / "clone", bare_repo)

    run(publisher.publish(_episode(), audio_src))
    run(publisher.update_feed([_episode()]))

    # both pushes landed on top of the same clone
    assert _show(bare_repo, "audio/2026-06-13-article-title.mp3") == b"fake mp3 data"
    validate_feed(_show(bare_repo, "feed.xml").decode())


def test_update_feed_no_op_when_nothing_changed(tmp_path, bare_repo):
    publisher = _publisher(tmp_path / "clone", bare_repo)

    run(publisher.update_feed([_episode()]))
    run(publisher.update_feed([_episode()]))  # second call: nothing to commit, no error


def test_clone_failure_raises_redacted_error(tmp_path):
    publisher = GitHubPagesPublisher(
        repo="testuser/testrepo",
        token="secrettoken",
        work_dir=tmp_path / "clone",
        feed_config=FEED_CONFIG,
        remote_url="/nonexistent-path-secrettoken",
    )
    audio_src = tmp_path / "src.mp3"
    audio_src.write_bytes(b"fake mp3 data")

    with pytest.raises(GitHubPagesPublisherError) as exc_info:
        run(publisher.publish(_episode(), audio_src))

    message = str(exc_info.value)
    assert "secrettoken" not in message
    assert "***" in message


def test_pages_base_url_derived_from_repo(tmp_path, bare_repo):
    publisher = _publisher(tmp_path / "clone", bare_repo, repo="alice/her-feed")
    assert publisher.pages_base_url == "https://alice.github.io/her-feed"
