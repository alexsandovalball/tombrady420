# Broadcast Guide – Group & Channel Notifications

This document explains how to configure your Telegram bot(s) to send product
notifications to groups and channels in addition to individual private-message
subscribers.

---

## Overview

Each bot instance can maintain a list of **broadcast destinations** – groups or
channels where the bot has posting rights.  Destinations are stored in the
`broadcast_chats` database table and are **disabled by default** (opt-in model)
to prevent unsolicited messaging.

When a new product is categorised and the notification pipeline runs, the system:

1. Sends a private-message notification to every subscribed user (existing behaviour).
2. **Sends the product to every *enabled* broadcast destination** for each bot instance.

---

## How to add a group or channel

### Groups and Supergroups

1. **Add the bot** to the group as a regular member or administrator.
2. On being added the bot automatically registers the group as a broadcast
   destination (disabled).
3. A **chat administrator** (or global bot admin) runs `/enable_broadcasts`
   inside the group to opt in.

```
/enable_broadcasts
```

The bot replies with a confirmation.  From that point on every new categorised
product is posted to the group.

To opt out, run `/disable_broadcasts` in the group.

### Channels

1. **Add the bot as an administrator** of the channel with at least the
   *Post Messages* permission.
2. The bot detects this via the `my_chat_member` update and registers the channel.
3. Because you cannot run commands in a channel directly, use one of these
   approaches to enable broadcasts:
   - Run `/enable_broadcasts` in the **linked discussion group** (if the channel
     has one and the bot is a member of that group too).
   - Or use the **global admin command** `/list_broadcasts` in the bot's private
     chat to see the channel's `chat_id`, then run
     `/enable_broadcasts` inside any chat where the bot is present and the
     channel is already registered.
   - Alternatively, temporarily enable the channel's linked discussion group
     and run the command there.

> **Tip:** The easiest workflow is to create a linked discussion group for the
> channel.  Add the bot to both.  Enable broadcasts in the discussion group if
> desired, and separately in the channel by using the linked group.

---

## Commands reference

| Command | Where | Who can use |
|---|---|---|
| `/enable_broadcasts` | Group / supergroup | Chat admin or global bot admin |
| `/disable_broadcasts` | Group / supergroup | Chat admin or global bot admin |
| `/list_broadcasts` | Any chat | Global bot admin only |

### `/enable_broadcasts`

Opts the current chat in for product broadcast notifications.  If the chat is
not yet registered (e.g. bot was added before this feature was deployed), it is
automatically registered first.

### `/disable_broadcasts`

Opts the current chat out.  The destination remains in the database and can be
re-enabled later.

### `/list_broadcasts`

Shows all known broadcast destinations for the current bot instance, both
enabled and disabled.  Admin-only.

```
📡 Broadcast destinations for @yourbot

✅ -1001234567890 – supergroup – My Cannabis Group
⭕ -1009876543210 – channel   – My Channel

✅ = enabled  ⭕ = disabled
Run /enable_broadcasts or /disable_broadcasts inside the chat to change.
```

---

## Multi-bot setups

Each bot instance manages its own broadcast destinations independently.  A
group that adds **Bot A** will only receive notifications via **Bot A**.  To
receive notifications via **Bot B** you must add **Bot B** to the group as well
and run `/enable_broadcasts` again (the command is recognised per-bot).

Use `/list_broadcasts` inside the private chat of each bot to see that bot's
destinations.

---

## Security and spam considerations

* Broadcasts are **opt-in**.  No messages are sent to a chat until an admin
  explicitly runs `/enable_broadcasts`.
* Only **chat administrators** can opt a chat in or out, preventing ordinary
  members from subscribing or unsubscribing on behalf of the whole group.
* Global bot admins (configured via `ADMIN_IDS`) can also run the commands
  from any chat.
* If the bot is removed from a chat or loses posting rights, the destination is
  automatically disabled and will no longer receive broadcasts.
* Telegram's per-second rate limits are respected: a 0.5 s delay is inserted
  between each destination send.  If a send returns a permanent error
  (Forbidden, chat not found, not enough rights) the destination is disabled
  automatically.

---

## Database schema

```sql
CREATE TABLE broadcast_chats (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_username     TEXT    NOT NULL,
    chat_id          INTEGER NOT NULL,
    chat_type        TEXT    NOT NULL,   -- group | supergroup | channel
    title            TEXT,
    enabled          INTEGER NOT NULL DEFAULT 0,
    added_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    added_by_user_id INTEGER,
    UNIQUE(bot_username, chat_id)
);
```

The table is created automatically during `db.init_db()` on first startup.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Bot added to group but no entry in `/list_broadcasts` | Bot was added before `my_chat_member` handler was deployed | Run `/enable_broadcasts` in the group; it registers the chat automatically |
| `/enable_broadcasts` returns "Only chat administrators can enable broadcasts here." | Sender is not a group admin | Have a group admin run the command |
| Channel does not appear in `/list_broadcasts` | Bot was not added as an admin with *Post Messages* | Make the bot a channel admin with at least the *Post Messages* permission |
| Broadcasts stop arriving | Bot was kicked or lost rights | Re-add bot with correct permissions and run `/enable_broadcasts` again |
