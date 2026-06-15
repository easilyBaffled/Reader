import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import pytest

from audibleweb.core.feed import (
    ITUNES,
    FeedConfig,
    FeedValidationError,
    generate_feed,
    validate_feed,
)
from audibleweb.publishers.base import Episode

CONFIG = FeedConfig(
    title="My Reading Feed",
    link="https://username.github.io/audibleweb",
    description="Articles converted to audio",
)


def _episode(
    title="Article Title", published=None, source_url="https://example.com/article"
):
    return Episode(
        title=title,
        published=published or datetime(2026, 6, 13, 10, 0, 0, tzinfo=UTC),
        duration_sec=1845,
        source_url=source_url,
        public_url="https://username.github.io/audibleweb/audio/2026-06-13-article-title.mp3",
        file_size_bytes=12345,
    )


# --- generate_feed -----------------------------------------------------------


def test_generate_feed_includes_channel_metadata():
    xml = generate_feed([], CONFIG)
    root = ET.fromstring(xml)

    assert root.tag == "rss"
    assert root.get("version") == "2.0"
    channel = root.find("channel")
    assert channel.find("title").text == CONFIG.title
    assert channel.find("link").text == CONFIG.link
    assert channel.find("description").text == CONFIG.description
    assert channel.find(ITUNES + "author").text == "AudibleWeb"


def test_generate_feed_item_fields_match_episode():
    episode = _episode()
    root = ET.fromstring(generate_feed([episode], CONFIG))
    item = root.find("channel").find("item")

    assert item.find("title").text == episode.title
    enclosure = item.find("enclosure")
    assert enclosure.get("url") == episode.public_url
    assert enclosure.get("length") == str(episode.file_size_bytes)
    assert enclosure.get("type") == "audio/mpeg"
    assert item.find("guid").text == episode.public_url
    assert item.find("guid").get("isPermaLink") == "true"
    assert item.find("pubDate").text == "Sat, 13 Jun 2026 10:00:00 GMT"
    assert item.find(ITUNES + "duration").text == "1845"
    assert item.find("link").text == episode.source_url
    assert item.find("description").text == f"Source: {episode.source_url}"


def test_generate_feed_orders_episodes_newest_first():
    older = _episode(title="Older", published=datetime(2026, 6, 1, tzinfo=UTC))
    newer = _episode(title="Newer", published=datetime(2026, 6, 13, tzinfo=UTC))

    root = ET.fromstring(generate_feed([older, newer], CONFIG))
    titles = [item.find("title").text for item in root.find("channel").findall("item")]

    assert titles == ["Newer", "Older"]


def test_generate_feed_without_source_url_uses_title_as_description():
    episode = _episode(source_url=None)
    root = ET.fromstring(generate_feed([episode], CONFIG))
    item = root.find("channel").find("item")

    assert item.find("link") is None
    assert item.find("description").text == episode.title


def test_generate_feed_includes_image_when_configured():
    config = FeedConfig(**{**vars(CONFIG), "image": "https://example.com/cover.jpg"})
    root = ET.fromstring(generate_feed([], config))
    image = root.find("channel").find(ITUNES + "image")

    assert image.get("href") == "https://example.com/cover.jpg"


# --- validate_feed -------------------------------------------------------------


def test_validate_feed_accepts_generated_feed():
    validate_feed(generate_feed([_episode()], CONFIG))  # no raise


def test_validate_feed_rejects_malformed_xml():
    with pytest.raises(FeedValidationError, match="not well-formed"):
        validate_feed("<rss><channel>")


def test_validate_feed_rejects_wrong_root():
    with pytest.raises(FeedValidationError, match="rss version"):
        validate_feed('<?xml version="1.0"?><feed version="2.0"><channel/></feed>')


def test_validate_feed_rejects_missing_channel():
    with pytest.raises(FeedValidationError, match="missing <channel>"):
        validate_feed('<?xml version="1.0"?><rss version="2.0"></rss>')


def test_validate_feed_rejects_missing_required_channel_tag():
    xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<title>t</title><link>l</link></channel></rss>"
    )
    with pytest.raises(FeedValidationError, match="missing required <description>"):
        validate_feed(xml)


def test_validate_feed_rejects_item_missing_required_tag():
    xml = generate_feed([_episode()], CONFIG)
    xml = xml.replace('<guid isPermaLink="true">', "<notguid>").replace(
        "</guid>", "</notguid>"
    )
    with pytest.raises(FeedValidationError, match="missing required <guid>"):
        validate_feed(xml)


def test_validate_feed_rejects_enclosure_missing_attribute():
    xml = generate_feed([_episode()], CONFIG)
    xml = xml.replace('type="audio/mpeg"', "")
    with pytest.raises(FeedValidationError, match="enclosure.*'type'"):
        validate_feed(xml)
