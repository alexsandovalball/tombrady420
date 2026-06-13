# Admin Guide

This is the compact operator guide for the Bradycadia Telegram catalog.

## Admin Access

Admins are Telegram user IDs listed in `ADMIN_IDS`. Admin commands are installed
into each admin's private command menu at startup. Some hidden commands still
work when typed directly.

## Daily Commands

```text
/adminstatus      View runtime bot, WebApp, cache, webhook, and latency status
/seostatus        View automatic SEO search learning status
/users            View/manage users by bot instance
/botusers         Inspect users grouped by bot instance
/send             Send one user a direct message
/broadcast        Broadcast a custom message to users
/setcontact       Set order contact handle
/setlink          Set order/contact URL
/recategorize     Re-send category prompts for uncategorized products
/clearcache       Clear bot-specific file_id cache
/prunebots        Preview/remove users/products from inactive bot instances
/reinitbots       Recreate bot Application instances and webhooks
/list_broadcasts List group/channel broadcast destinations
/nuke             Delete all products with confirmation
```

Hidden but supported:

```text
/block <user_id>
/unblock <user_id>
/subscribe
/unsubscribe
```

## Product Workflow

1. Post product media and caption in the monitored channel.
2. The primary bot saves the product and album media.
3. Admin receives a category/subcategory prompt.
4. Once categorized, subscribed users are queued for notification.
5. Product appears in bot menus, inline mode, WebApp, and SEO pages.

Product IDs are stable and are not reused after deletion.

## User Management

Use `/users` for normal user work:

- view user ID, username, name, last seen, interaction count
- toggle notification subscription
- block or unblock a user
- page through users per bot instance

Use `/botusers` and `/prunebots` after token removals or bot replacement. Always
run the preview first, then confirm only when the active bot list looks correct.

Blocked users should be rejected in bot handlers and authenticated WebApp routes.
If a user has already opened a public SEO page, their browser can still request
public static/open-web routes unless you also know and block their IP.

Broadcast destinations that become unreachable are tracked per bot. A successful
product notification resets the counter; two consecutive failed product
notification attempts automatically disable that group/channel.

## SEO Search Learning

The bot automatically studies no-result WebApp searches and safely maps them to
existing catalog categories/subcategories when the match is confident. It also
learns translated variants for the supported WebApp languages: English, German,
Dutch, Italian, Spanish, and French.

Use `/seostatus` to see:

- searches typed on the site/WebApp in the last 24 hours
- searches that still returned no products
- recent mappings the bot made automatically
- where each learned search term now points in the catalog

Manual SEO import/approval commands are intentionally not exposed anymore.

IndexNow runs automatically in the background when the public storefront domain
is configured. It generates a key on the persistent disk, serves the key file
from the domain root, and submits changed product/category URLs in batches. Use
`/adminstatus` to check whether URLs are pending, submitted, or failing. You do
not need to manually submit every page after normal catalog updates.

## Cache Operations

`/clearcache` deletes persistent bot-specific Telegram `file_id` mappings.

Use it only when:

- media previews fail after token or bot changes
- a bot was newly added and old mappings are invalid
- an admin is deliberately repairing media resolution

Avoid routine use. Clearing it forces bots to rehydrate file IDs through Telegram
message forwarding, which makes the next media views slower.

Temporary WebApp media files and persistent disk media cache clean themselves.
Old cache entries are removed by age, idle time, and size pressure.

## Webhook Operations

Normal startup registers each bot at:

```text
/webhook/<registry_uuid>
```

If logs show `/webhook/9` with `[WEBHOOK_ORPHAN_DRAINED]`, Telegram is retrying
an old removed bot's webhook. If you still know that old bot token, run:

```text
https://api.telegram.org/bot<OLD_TOKEN>/deleteWebhook?drop_pending_updates=true
```

If the old token is gone, leave the 200 drain behavior in place.

Use `/reinitbots confirm` after changing tokens without redeploying. Use
`/reinitbots dryrun` to inspect expected webhook fingerprints.

## Admin Dashboard

Set:

```text
ADMIN_DASHBOARD_TOKEN=long-random-private-token
```

Open:

```text
https://your-domain.com/admin
```

Enter the token. The dashboard API is protected; the page shell alone contains
no private data.

## Database Maintenance

Render production database path should be:

```text
/data/catalog.db
```

Useful checks:

```powershell
python scripts/sqlite_doctor.py /data/catalog.db
python scripts/sqlite_doctor.py /data/catalog.db --rebuild-product-search
```

Routine deletes do not need `VACUUM`. SQLite WAL mode reuses free pages. Run
manual maintenance only during quiet periods.
