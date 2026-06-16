-- Add heartbeat_at to jobs so stall detection can surface stuck in-progress jobs.
ALTER TABLE jobs ADD COLUMN heartbeat_at TEXT;
