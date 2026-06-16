"""GitHubPagesPublisher: git push MP3 + regenerate feed.xml to gh-pages (docs/design.md sec 2.3, 6).

Flow per docs/design.md sec 6 "GitHub Pages Publisher Flow": maintain a shallow
clone of the gh-pages branch in `work_dir`, copy the stitched MP3 / regenerated
feed.xml into it, commit, and push. Push uses `--force` (sec 9: "Git push
conflict -> Force push (gh-pages is generated content)"). Git errors surface as
`GitHubPagesPublisherError` (sec 9: "Git push auth failure -> Fail at publish
stage. Audio preserved.") -- the source MP3 in data/audio/ is untouched on
failure since `publish()` only copies into `work_dir`.

Known limitation: the gh-pages branch must already exist on the remote.
Bootstrapping a brand-new branch is a one-time manual setup step, not handled
here.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from audibleweb.core.feed import FeedConfig, generate_feed, validate_feed
from audibleweb.publishers.base import Episode, episode_slug


class GitHubPagesPublisherError(Exception):
    """Raised when a git operation fails (docs/design.md sec 9)."""


class GitHubPagesPublisher:
    name = "github_pages"

    def __init__(
        self,
        repo: str,
        token: str,
        work_dir: Path | str,
        branch: str = "gh-pages",
        feed_config: FeedConfig | None = None,
        remote_url: str | None = None,
    ):
        self.repo = repo
        self.token = token
        self.work_dir = Path(work_dir)
        self.branch = branch
        self.feed_config = feed_config
        self._remote_url = remote_url or f"https://{token}@github.com/{repo}.git"
        owner, _, name = repo.partition("/")
        self.pages_base_url = f"https://{owner}.github.io/{name}"

    async def publish(self, episode: Episode, audio_path: Path) -> str:
        await self._ensure_clone()
        slug = episode_slug(episode.title, episode.published)
        audio_dir = self.work_dir / "audio"
        audio_dir.mkdir(exist_ok=True)
        shutil.copy2(audio_path, audio_dir / f"{slug}.mp3")
        await self._commit_and_push(f"Add episode: {episode.title}")
        return f"{self.pages_base_url}/audio/{slug}.mp3"

    async def update_feed(self, episodes: list[Episode]) -> str:
        await self._ensure_clone()
        xml = generate_feed(episodes, self.feed_config)
        validate_feed(xml)
        (self.work_dir / "feed.xml").write_text(xml, encoding="utf-8")
        await self._commit_and_push("Update feed.xml")
        return f"{self.pages_base_url}/feed.xml"

    async def publish_and_update_feed(
        self,
        episode: Episode,
        audio_path: Path,
        all_episodes: list[Episode],
    ) -> tuple[str, str]:
        """Atomic: stage MP3 + feed.xml, single commit + single push (reader-ksd)."""
        await self._ensure_clone()
        slug = episode_slug(episode.title, episode.published)
        audio_dir = self.work_dir / "audio"
        audio_dir.mkdir(exist_ok=True)
        shutil.copy2(audio_path, audio_dir / f"{slug}.mp3")
        xml = generate_feed(all_episodes, self.feed_config)
        validate_feed(xml)
        (self.work_dir / "feed.xml").write_text(xml, encoding="utf-8")
        await self._commit_and_push(f"Add episode: {episode.title}")
        return (
            f"{self.pages_base_url}/audio/{slug}.mp3",
            f"{self.pages_base_url}/feed.xml",
        )

    async def _ensure_clone(self) -> None:
        if (self.work_dir / ".git").is_dir():
            return
        self.work_dir.mkdir(parents=True, exist_ok=True)
        await self._run_git(
            "clone", "--depth", "1", "--branch", self.branch, self._remote_url, "."
        )

    async def _commit_and_push(self, message: str) -> None:
        await self._run_git("add", "-A")
        status = await self._run_git("status", "--porcelain")
        if not status.strip():
            return
        await self._run_git(
            "-c",
            "user.name=AudibleWeb",
            "-c",
            "user.email=audibleweb@localhost",
            "commit",
            "-m",
            message,
        )
        await self._run_git("push", "--force", "origin", self.branch)

    async def _run_git(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=self.work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise GitHubPagesPublisherError(
                f"git {args[0]} failed: {self._redact(stderr.decode().strip())}"
            )
        return stdout.decode()

    def _redact(self, text: str) -> str:
        return text.replace(self.token, "***") if self.token else text
