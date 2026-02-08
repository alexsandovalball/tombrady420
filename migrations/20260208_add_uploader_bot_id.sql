-- Migration: Add cross-bot media delivery support
-- Date: 2026-02-08
-- Description: Adds uploader_bot_id and storage_url columns to products table
--              to enable secondary bots to deliver media originally uploaded by other bots.
--              When a secondary bot encounters "Wrong file identifier" error,
--              it can download the media from the uploader bot and re-upload it.

-- Add uploader_bot_id to products table
-- Stores the environment variable name of the bot that originally uploaded this media
-- Example values: 'BOT_TOKEN', 'BOT_TOKEN_1', 'BOT_TOKEN_2'
-- NULL for existing products (backward compatible - fallback will be skipped)
ALTER TABLE products ADD COLUMN uploader_bot_id TEXT;

-- Add storage_url to products table (optional, for future caching)
-- Stores a persistent URL (e.g., S3, cloud storage) where media bytes are cached
-- This allows serving media without re-downloading from Telegram on every fallback
-- NULL for products not yet cached
ALTER TABLE products ADD COLUMN storage_url TEXT;

-- No data migration needed - all columns are nullable
-- Existing products will continue to work, but won't trigger fallback (no uploader_bot_id)
-- New products will populate uploader_bot_id automatically when saved
