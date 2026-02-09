-- Migration: Add per-bot media file_id caching table
-- Date: 2026-02-09
-- Description: Creates a table to cache bot-specific Telegram file_ids for media resources.
--              This solves the "Wrong file identifier" error when secondary bots try to
--              send media uploaded by a different bot. Telegram file_ids are bot-scoped,
--              so each bot needs its own cached file_id for each media resource.

CREATE TABLE IF NOT EXISTS media_file_ids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_key TEXT NOT NULL,
    resource_id INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(bot_key, resource_id)
);

-- Index for fast lookup by (bot_key, resource_id)
CREATE INDEX IF NOT EXISTS idx_media_file_ids_bot_resource ON media_file_ids(bot_key, resource_id);

-- Index for cleanup/maintenance queries
CREATE INDEX IF NOT EXISTS idx_media_file_ids_updated_at ON media_file_ids(updated_at);
