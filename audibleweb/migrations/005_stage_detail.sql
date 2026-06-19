-- Per-stage progress detail (e.g. "Cloning gh-pages branch...", "Fetching
-- https://..."), shown under the progress bar so users can see what's
-- actually happening within a stage, not just which of the 4 broad stages.
ALTER TABLE jobs ADD COLUMN stage_detail TEXT;
