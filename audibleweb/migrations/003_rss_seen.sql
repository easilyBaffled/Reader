-- Track per-feed seen item GUIDs so first-subscribe floods 0 jobs.
CREATE TABLE rss_seen_items (
    feed_url TEXT NOT NULL,
    item_id  TEXT NOT NULL,
    seen_at  TEXT NOT NULL,
    PRIMARY KEY (feed_url, item_id)
);
