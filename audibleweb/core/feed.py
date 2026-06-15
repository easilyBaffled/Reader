"""RSS 2.0 + iTunes podcast feed generation and validation (docs/design.md sec 6, 9)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import format_datetime

from audibleweb.publishers.base import Episode

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ITUNES = "{" + ITUNES_NS + "}"

ET.register_namespace("itunes", ITUNES_NS)

REQUIRED_CHANNEL_TAGS = ("title", "link", "description")
REQUIRED_ITEM_TAGS = (
    "title",
    "enclosure",
    "guid",
    "pubDate",
    "description",
    ITUNES + "duration",
)
REQUIRED_ENCLOSURE_ATTRS = ("url", "length", "type")


class FeedValidationError(Exception):
    """Raised when generated feed.xml fails RSS 2.0 + iTunes validation (docs/design.md sec 9:
    "Generated XML invalid -> Validate before push. Invalid = fail, don't push.")."""


@dataclass
class FeedConfig:
    title: str
    link: str
    description: str
    author: str = "AudibleWeb"
    image: str | None = None


def generate_feed(episodes: list[Episode], config: FeedConfig) -> str:
    """Build feed.xml from job history (docs/design.md sec 6). Newest episode first."""
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = config.title
    ET.SubElement(channel, "link").text = config.link
    ET.SubElement(channel, "description").text = config.description
    ET.SubElement(channel, ITUNES + "author").text = config.author
    if config.image:
        ET.SubElement(channel, ITUNES + "image", {"href": config.image})

    for episode in sorted(episodes, key=lambda e: e.published, reverse=True):
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = episode.title
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": episode.public_url,
                "length": str(episode.file_size_bytes),
                "type": "audio/mpeg",
            },
        )
        ET.SubElement(item, "guid", {"isPermaLink": "true"}).text = episode.public_url
        ET.SubElement(item, "pubDate").text = format_datetime(
            episode.published, usegmt=True
        )
        ET.SubElement(item, ITUNES + "duration").text = str(int(episode.duration_sec))
        if episode.source_url:
            ET.SubElement(item, "link").text = episode.source_url
            ET.SubElement(item, "description").text = f"Source: {episode.source_url}"
        else:
            ET.SubElement(item, "description").text = episode.title

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        rss, encoding="unicode"
    )


def validate_feed(xml_text: str) -> None:
    """Structural validation against RSS 2.0 + iTunes (docs/design.md sec 6, 9).

    Checks well-formedness, required <rss>/<channel> elements, and that each
    <item> carries the fields feed.xml's spec example requires (title,
    enclosure w/ url+length+type, guid, pubDate, description, itunes:duration).
    Not a full XSD schema validation.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FeedValidationError(f"feed.xml is not well-formed XML: {exc}") from exc

    if root.tag != "rss" or root.get("version") != "2.0":
        raise FeedValidationError('feed.xml root must be <rss version="2.0">')

    channel = root.find("channel")
    if channel is None:
        raise FeedValidationError("feed.xml missing <channel>")

    for tag in REQUIRED_CHANNEL_TAGS:
        if channel.find(tag) is None:
            raise FeedValidationError(f"feed.xml <channel> missing required <{tag}>")

    for item in channel.findall("item"):
        for tag in REQUIRED_ITEM_TAGS:
            if item.find(tag) is None:
                raise FeedValidationError(f"feed.xml <item> missing required <{tag}>")
        enclosure = item.find("enclosure")
        for attr in REQUIRED_ENCLOSURE_ATTRS:
            if not enclosure.get(attr):
                raise FeedValidationError(
                    f"feed.xml <enclosure> missing '{attr}' attribute"
                )
