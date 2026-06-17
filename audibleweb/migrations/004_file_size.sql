-- Store the stitched MP3 file size so feed.xml <enclosure length="..."> is
-- correct when rebuilding the feed from job history (reader-8f2.10).
ALTER TABLE jobs ADD COLUMN file_size_bytes INTEGER NOT NULL DEFAULT 0;
