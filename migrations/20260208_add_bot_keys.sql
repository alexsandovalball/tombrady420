-- Migration: Add multi-bot support columns
-- Date: 2026-02-08
-- Description: Adds nullable bot key columns for multi-bot synchronized broadcasts
--              These columns enable tracking which bot a user prefers and which bot
--              should deliver queued messages/notifications.

-- Add last_bot_key to bot_users table
-- Stores the environment variable name of the bot the user last interacted with
-- Example values: 'BOT_TOKEN', 'BOT_TOKEN_1', 'BOT_TOKEN_2'
-- NULL for existing users (backward compatible)
ALTER TABLE bot_users ADD COLUMN last_bot_key TEXT;

-- Add target_bot_key to custom_message_queue table
-- Stores which bot process should deliver this queued message
-- NULL for existing queued messages (will fall back to bot_username filtering)
ALTER TABLE custom_message_queue ADD COLUMN target_bot_key TEXT;

-- Add target_bot_key to notification_queue table
-- Stores which bot process should deliver this notification
-- NULL for existing notifications (will fall back to bot_username filtering)
ALTER TABLE notification_queue ADD COLUMN target_bot_key TEXT;

-- No data migration needed - all columns are nullable
-- Existing deployments will continue to work with NULL values
-- New interactions will populate these fields automatically
