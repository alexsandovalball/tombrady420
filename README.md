# 🌿 Top Shelf Cali Plug — Telegram Catalog Bot

A production-ready, multi-bot Telegram storefront for managing and browsing a media product catalog. Built on **python-telegram-bot v20** (async), **FastAPI**, and **aiosqlite**, with a Jinja2-rendered PWA storefront, 6-language automatic translation, and a queued notification delivery system.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [How It Works — End-to-End Flow](#how-it-works--end-to-end-flow)
3. [Product Catalog & Data Model](#product-catalog--data-model)
4. [Category Structure](#category-structure)
5. [User Commands](#user-commands)
6. [Admin Commands](#admin-commands)
7. [Notification System](#notification-system)
8. [Multi-Bot Support](#multi-bot-support)
9. [Translation System](#translation-system)
10. [WebApp / Storefront Frontend](#webapp--storefront-frontend)
11. [REST API Reference](#rest-api-reference)
12. [Fuzzy Search Engine](#fuzzy-search-engine)
13. [Security](#security)
14. [Configuration Reference](#configuration-reference)
15. [Project Structure](#project-structure)
16. [Quick Start](#quick-start)
17. [Deployment (Render.com)](#deployment-rendercom)

---

## Architecture Overview

```
Telegram Servers
      │ HTTPS webhook
      ▼
┌─────────────────────────────────────────────────────┐
│  FastAPI  (webhook_server.py)                       │
│  ├── POST /webhook          → primary bot           │
│  ├── POST /webhook/{n}      → bot n (multi-bot)     │
│  ├── GET  /api/*            → catalog REST API      │
│  ├── GET  /                 → SSR storefront (SEO)  │
│  ├── GET  /product/{id}/*   → SEO product pages     │
│  ├── GET  /category/{name}  → SEO category pages    │
│  └── GET  /assets/*         → static files          │
│                                                     │
│  python-telegram-bot Application (per bot token)    │
│  ├── CommandHandler, CallbackQueryHandler, ...      │
│  └── Channel post listener → product ingestion      │
└─────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────┐      ┌──────────────────────────────┐
│  SQLite DB  │      │  In-memory caches             │
│  (aiosqlite)│      │  ├── user language (5 min)    │
│             │      │  ├── order contact (5 min)    │
│  products   │      │  ├── order link    (5 min)    │
│  users      │      │  └── translation  (LRU 1000)  │
│  bot_users  │      └──────────────────────────────┘
│  bot_settings
│  notifications
│  bot_file_id_cache
└─────────────┘
```

**Two runtime modes:**

| Mode | Entry point | Use case |
|------|-------------|----------|
| Polling | `python main.py` | Local development, single-server |
| Webhook | `uvicorn webhook_server:app` | Production (Render.com, VPS) |

---

## How It Works — End-to-End Flow

### Product Ingestion (channel → catalog)

1. Admin posts a photo/video/media group to the configured Telegram **channel**.
2. The bot (added as channel admin) receives the channel post via webhook.
3. `main.py` → `process_channel_post()` extracts `file_id`, `caption`, and `file_type`.
4. For **media groups** (albums), messages are collected over a 5-second window (timer + asyncio lock) before processing as a single product.
5. `db.add_product()` assigns the **lowest available ID** (gap-filling recycling) and stores the product with `created_at = now()`.
6. The bot sends a **categorization prompt** to the admin via DM — an inline keyboard listing all categories and subcategories.
7. Admin selects the category/subcategory; the product is updated in the DB.
8. A **notification batch** is queued for all subscribed users (see [Notification System](#notification-system)).

### User Browsing (bot interaction)

1. User sends `/start` → bot registers user in `users` table (if new), sets language, shows main menu with a **Web App** button.
2. `/menu` renders an inline keyboard of categories with product counts.
3. Selecting a category shows subcategories (if any) or jumps straight to paginated product list (5 per page).
4. Selecting a product sends the media (photo/video/document) with caption and an **Order** button linking to the configured contact.
5. `/search` or any free-text message triggers fuzzy search against all product captions (rapidfuzz `partial_ratio`, cutoff 75).
6. Inline mode (`@bot_username query`) works globally in any chat.

### Notification delivery

Queued notification batches are processed sequentially by a background FIFO asyncio queue. Each batch delivers to all subscribed users in staggered groups of 10 (10-second inter-group delay) to stay below Telegram's rate limits. Delivery uses the **bot each user originally started** (multi-bot routing via `bot_file_id_cache`).

---

## Product Catalog & Data Model

### `products` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Gap-recycled sequential ID |
| `file_id` | TEXT | Telegram file_id (primary media) |
| `file_type` | TEXT | `photo`, `video`, `document`, `animation` |
| `caption` | TEXT | Full product caption from channel post |
| `category` | TEXT | Uppercase category key (e.g. `FLOWER`) |
| `subcategory` | TEXT | Uppercase subcategory key |
| `media_group_id` | TEXT | Album group ID (for multi-photo products) |
| `additional_media` | JSON | Array of `{file_id, file_type}` for extra album items |
| `created_at` | DATETIME | ISO timestamp; updated on recategorize |
| `bot_username` | TEXT | Username of the posting bot instance |
| `message_id` | INTEGER | Source channel message ID |

**ID recycling:** `get_next_free_id()` finds the lowest gap (e.g. if IDs 1,2,4 exist → returns 3). `VACUUM` is run after every deletion to reclaim space.

### `users` table

| Column | Description |
|--------|-------------|
| `user_id` | Telegram user ID (PK) |
| `username` | @handle |
| `first_name`, `last_name` | Display name parts |
| `language_code` | ISO-639-1 (e.g. `en`, `de`) |
| `is_subscribed` | Notification opt-in (default: 1) |
| `is_blocked` | Block flag — bot ignores all messages |
| `interaction_count` | Incremented on every message |
| `last_interaction` | Timestamp of last activity |
| `bot_username` | Which bot they first interacted with |

### `bot_settings` table

Key-value store for runtime-configurable settings:

| Key | Default | Set by |
|-----|---------|--------|
| `order_contact` | `@FLYAWAYPEP` | `/setcontact` |
| `order_link` | `https://t.me/FLYAWAYPEP` | `/setlink` |

### `notifications` table

Tracks each pending/sent notification per user per product to prevent duplicates.

### `bot_file_id_cache` table

Stores bot-specific `file_id` values for each media item. Telegram `file_id`s are bot-specific; this cache is populated via forwarding on first access so every bot instance can serve media independently.

---

## Category Structure

```
CARTRIDGES
  ├── AUTHENTICS
  └── REPLICAS

EDIBLES
  ├── FLOWER EDIBLES
  └── SHROOM EDIBLES

CONCENTRATES
  ├── SOLVENTLESS
  ├── LIVE RESIN AND EXTRACTS
  ├── OILS AND ISOLATES
  ├── HASH AND KIEF
  └── OTHERS

PREROLLS
  ├── FLOWER PREROLLS
  └── INFUSED FLOWER PREROLLS

SHROOMS            (no subcategories)

FLOWER
  ├── TOPSHELFCANDY
  ├── PREMIUMEXOTICS
  ├── EXOTICS
  ├── PREMIUMLIGHTDEPS
  ├── LIGHTDEPS
  ├── LIGHTASSIST
  ├── LOWS
  └── MOONROCKS AND SNOWCAPS

DATEDPROOFS        (excluded from "All Products")
CLIENTTOUCHDOWNS   (excluded from "All Products")
ANNOUNCEMENTS      (excluded from "All Products")
```

**Recategorization:** `/recategorize` finds products with a category but no subcategory (when subcategories exist) and re-sends the categorization prompt. On save, `created_at` is updated to `now()` so the product rises to the top of sorted lists.

---

## User Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message. Registers new users, shows language picker on first visit. Supports deep links: `/start product_{id}` opens a specific product; `/start inline` / `/start inline_help` shows inline-mode guide. |
| `/menu` | Category browser — inline keyboard with product counts per category. |
| `/language` | Language selection (English, German, Dutch, Italian, Spanish, French). Persists to DB. |
| `/subscribe` | Opt in to new-product notifications. |
| `/unsubscribe` | Opt out of notifications. |
| _(any text)_ | Fuzzy product search — min 2 characters. Returns paginated results. |

**Inline mode:** `@bot query` searches the catalog and returns up to 10 results as inline articles, each with caption preview and a **View Product** button.

---

## Admin Commands

All admin commands are gate-kept by user ID against `ADMIN_IDS` in `.env`.

| Command | What it does |
|---------|--------------|
| `/start` | Same as user — welcome message. |
| `/menu` | Browse catalog — same as users, but with delete buttons on products. |
| `/users` | Paginated user list (10/page). Shows ID, username, name, last seen, notification status, block status. Toggle notifications and block/unblock with inline buttons. |
| `/botusers` | Users grouped by which bot instance they used. Marks inactive bots with ⚠️. Delete individual users or bulk-delete all users from a specific bot. |
| `/send` | Two-step workflow: enter user ID → enter message text → bot delivers it. |
| `/broadcast` | Broadcast a message to all users. Confirmation required. Queued through the notification system to respect rate limits. |
| `/block <user_id>` | Block a user — all their messages are silently ignored. |
| `/unblock <user_id>` | Unblock a user. |
| `/setcontact` | Two-step: shows current contact → accepts new `@username` or phone. Saves to `bot_settings`. |
| `/setlink` | Two-step: shows current link → accepts `https://`, `http://`, or `tg://` URL. Saves to `bot_settings`. |
| `/recategorize` | Sends up to 10 categorization prompts for products missing a subcategory. Run repeatedly to process all. |
| `/clearcache` | Clears the `bot_file_id_cache` table. Use if media stops loading after changing bot tokens. |
| `/prunebots` | Preview (default) or confirm-delete of users and products associated with inactive/removed bot instances. |
| `/nuke` | Double-confirmed deletion of **all** products. Channel messages are untouched. |

**Category assignment flow (post-product):**
1. Admin receives a DM with the new product's caption and an inline keyboard.
2. Selecting a top-level category either assigns it directly (no subcategories) or shows a subcategory keyboard.
3. Selecting subcategory saves both. "Save without subcategory" is available for categories that have subcategories.
4. On save, notifications are queued for all subscribed users.

---

## Notification System

**Architecture:** `NotificationService` wraps an `asyncio.Queue`. All notification batches are enqueued and executed sequentially by a single background worker (`_queue_worker`), preventing concurrent batches from colliding.

**Per-product notification flow:**
1. Product is categorized → `notify_users_about_product()` is called.
2. A UUID batch ID is generated; a `pending_notifications` batch is created in the DB for all subscribed users.
3. The batch is enqueued. The queue worker dequeues it and processes in groups:
   - Group size: 10 users
   - Inter-group delay: 10 seconds
   - Max batch size fetched from DB: 100 notifications per iteration
4. For each user, the notification is sent via **their original bot** (looked up from `bot_users` table). Bot-specific `file_id` is fetched from `bot_file_id_cache` (populated by forwarding from channel on cache miss).
5. Duplicate suppression: each notification is tracked in `notifications` table; duplicates are removed before sending.
6. Unreachable users (Forbidden / "user not found" errors) are automatically deleted from the users table.
7. After the batch, a summary is sent to the primary admin: sent, attempted, rate-limited, duplicates removed.

**Rate limiting configuration** (in `utils/notifications.py`):
- Max 5 notifications per user per hour
- Stagger group size: 25 users / 2-second interval (~12.5 msg/s per bot, 42% of Telegram's 30/s hard limit)
- At 5,000 users per bot: ~6.7 min delivery time; all bots run in parallel so total time = single-bot time
- Custom message batch size: 20 / 1-second delay

**Safe-send wrappers** (`utils/safe_send.py`) handle all Telegram sends with:
- Up to 3 retries on `RetryAfter` (429) with bounded jitter (±50%, 1–60 s cap)
- Auto-deletion of users who return Forbidden or "user deactivated" errors
- Centralized `DELETED_USER_PATTERNS` constant for consistent error matching

---

## Multi-Bot Support

A single deployment can run **up to 11 bot tokens** simultaneously:

```
BOT_TOKEN     → /webhook      (primary)
BOT_TOKEN_1   → /webhook/1
BOT_TOKEN_2   → /webhook/2
…
BOT_TOKEN_10  → /webhook/10
```

Or supply all tokens in one variable: `BOT_TOKENS=token0,token1,token2`.

**How multi-bot works:**
- Each bot has its own `Application` instance initialized in parallel via `asyncio.gather`.
- All bots share the same SQLite database and handler logic.
- Users are tracked per-bot in `bot_users`; notifications are sent through the bot the user first used.
- `bot_file_id_cache` stores per-bot `file_id` mappings so each bot can serve the same media independently.
- Only the **primary bot** (index 0) runs the notification queue worker and channel listener, preventing duplicate deliveries.

---

## Translation System

**Supported languages:** English (default), German (`de`), Dutch (`nl`), Italian (`it`), Spanish (`es`), French (`fr`).

**Pipeline:**
1. All user-facing strings are defined in `translations/strings.py` → `get_strings()` returns an English dict.
2. `TranslationService` (`translations/translator.py`) wraps `GoogleTranslator` (deep-translator) with:
   - **Placeholder protection**: `{name}`, `{contact}` etc. are replaced with opaque markers (`XPLACEHOLDERX0X`) before translation and restored after, preventing corrupted format strings.
   - **Two-tier cache**: in-memory LRU (`BoundedCache`, 1000 entries) + DB-backed translation cache (persistent across restarts).
   - **Thread pool** for sync translations inside async context.
3. `get_translated_string_async(key, lang, **kwargs)` is the primary call site throughout handlers.
4. WebApp translations are handled client-side via a JS `t(key)` lookup table with the same 6 languages (no network call needed).

**Language persistence:** stored in `users.language_code`; cached in-memory per user for 5 minutes to avoid repeated DB reads.

---

## WebApp / Storefront Frontend

The `webapp/` directory serves a Telegram Mini App (via Telegram's WebApp API) **and** a public SEO-indexed clearnet storefront at the same URL.

### Pages

| Route | Description |
|-------|-------------|
| `/` | Storefront homepage — all categories with counts |
| `/product/{id}/{slug}` | Product detail page (SEO-friendly slug in URL) |
| `/category/{name}` | Category listing page |
| `/webapp`, `/webapp/index.html` | Telegram Mini App entry point |

### Frontend files

| File | Role |
|------|------|
| `webapp/index.html` | Main SPA shell — Jinja2 template with SEO meta tags, Open Graph, JSON-LD schema |
| `webapp/category.html` | Category listing Jinja2 template |
| `webapp/app.js` | Vanilla JS SPA — state machine, infinite scroll, media carousel, language switching, subscription toggle |
| `webapp/style.css` | Full-viewport doodle wallpaper (SVG, fixed, z-index 0), opaque `.container` (z-index 1), dark-mode support |

### Doodle wallpaper

A 400×400 px SVG tile is embedded as a `body::before` data-URL and tiles infinitely via `background-repeat: repeat`. It contains 28 stroke-only icons at 7% opacity (cannabis leaves, lighters, vape pens, bongs, mason jars, joints, smoke clouds, sparkles, capsule pills, crescent moons). The `.container` sits at `z-index: 1` with `background-color: var(--tg-theme-bg-color)` (fully opaque), so the pattern is visible only in the side gutters on wide screens and beneath the content area inside the Telegram client (which has no gutters). Dark mode swaps the stroke to `#5ab86e` via `@media (prefers-color-scheme: dark)`.

### SEO features
- Server-side Jinja2 rendering with per-page `<title>`, `<meta description>`, Open Graph, and JSON-LD `Product` / `LocalBusiness` schema
- Dynamic `sitemap.xml` including all product and category URLs
- `robots.txt` allowing crawlers on public pages, blocking `/api/` and `/webhook`

---

## REST API Reference

All endpoints that return product data accept an optional `lang` query parameter for on-the-fly translation. Endpoints marked **Auth Required** need a valid Telegram `initData` header (`X-Telegram-Init-Data`) or query parameter.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/categories` | Optional | All categories with subcategories and product counts |
| GET | `/api/products` | Optional | Products filtered by `category`, `subcategory`, `limit` (max 50), `offset` |
| GET | `/api/catalog` | Optional | Deprecated alias for `/api/products` |
| GET | `/api/catalog/{id}/media/{index}` | Optional | Single media item for a product (streamed) |
| GET | `/api/search?q=…` | Optional | Fuzzy search across all product captions |
| GET | `/api/user/subscription?user_id=…` | Required | Get notification subscription status |
| POST | `/api/user/subscription` | Required | Toggle notification subscription |
| GET | `/api/user/info` | Required | Current user's profile data |
| POST | `/api/user/track` | Optional | Register/update user in DB from WebApp open event |
| POST | `/api/webapp-logs` | Optional | Client-side diagnostic event logging |
| GET | `/health` | None | `{"status": "ok"}` liveness probe |
| POST | `/webhook` | Telegram HMAC | Primary bot update receiver |
| POST | `/webhook/{n}` | Telegram HMAC | Bot-n update receiver (multi-bot) |

**`/api/products` response shape:**
```json
{
  "items": [
    {
      "id": 42,
      "caption": "...",
      "category": "FLOWER",
      "subcategory": "EXOTICS",
      "subcategory_display": "Exotics",
      "created_at": "2025-01-15T10:30:00",
      "media_count": 3,
      "media_info": [{"file_id": "...", "file_type": "photo"}, ...]
    }
  ],
  "total": 128,
  "bot": "TopShelfBot",
  "order_contact": "@FLYAWAYPEP",
  "order_contact_username": "FLYAWAYPEP",
  "order_link": "https://t.me/FLYAWAYPEP"
}
```

---

## Fuzzy Search Engine

`utils/fuzzy_search.py` implements a two-stage ranking pipeline:

1. **Exact / substring match** (boosted): normalised query checked against normalised caption and category field.
2. **Fuzzy match** (fallback): uses `rapidfuzz.process.extract` with `fuzz.partial_ratio`, `score_cutoff=75`. Falls back to `difflib.SequenceMatcher` when rapidfuzz is not installed.

**Pre-processing:**
- Lowercase, strip punctuation, collapse whitespace.
- **Synonym expansion:** `cart` → `cartridge`, `weed` / `bud` → `flower`, `gummy` → `edibles`, `joint` → `pre-roll`, etc. (see `SEARCH_SYNONYMS` dict).

Results are returned exact-matches-first, then fuzzy-matches in descending similarity order.

---

## Security

### Webhook verification
Telegram signs every webhook request. python-telegram-bot verifies the signature automatically when the secret token is configured.

### WebApp initData verification
`utils/webapp_security.py` implements the [Telegram initData HMAC-SHA256 algorithm](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):
1. Parse `initData` as URL query string.
2. Sort parameters alphabetically (excluding `hash`).
3. Join as `key=value\n...` string.
4. `secret_key = HMAC-SHA256(b"WebAppData", bot_token)`
5. `computed_hash = HMAC-SHA256(secret_key, data_check_string)`
6. Constant-time compare with `hmac.compare_digest`.

In **multi-bot** setups, validation is attempted against each bot token in order; the first match wins.

### Input handling
- All SQL queries use parameterised statements (no string interpolation).
- Webhook endpoints accept only POST from Telegram IPs (enforced by Telegram's signature).
- API endpoints that mutate state require valid initData.

---

## Configuration Reference

All configuration is loaded from environment variables via `python-decouple`. Copy `configs/.env.example` to `.env` and fill in the required values.

*Either `CHANNEL_ID` or `CHANNEL_USERNAME` is required — use whichever you have available.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | ✅ | — | Primary bot token from @BotFather |
| `BOT_TOKENS` | — | — | Comma-separated list of all tokens (overrides individual vars) |
| `BOT_TOKEN_1` … `BOT_TOKEN_10` | — | — | Additional bot tokens (multi-bot) |
| `ADMIN_IDS` | ✅ | — | Comma-separated Telegram user IDs with admin access |
| `CHANNEL_ID` | ✅* | — | Numeric channel ID (e.g. `-1001234567890`) |
| `CHANNEL_USERNAME` | ✅* | — | Channel username (alternative to CHANNEL_ID) |
| `DB_PATH` | — | `catalog.db` | SQLite file path |
| `USE_WEBHOOK` | — | `false` | Set `true` for webhook mode |
| `WEBHOOK_URL` | ✅ (webhook) | — | Public HTTPS URL (e.g. `https://myapp.onrender.com`) |
| `WEB_APP_URL` | — | `{WEBHOOK_URL}/webapp/index.html` | Telegram Mini App URL |
| `ORDER_CONTACT` | — | `@FLYAWAYPEP` | Default order contact shown to users |
| `BOT_USERNAME` | — | — | Used for SEO deep-link generation |
| `STOREFRONT_DOMAIN` | — | `https://topshelfcaliplug.com` | Canonical domain for sitemap/SEO |
| `CUSTOM_DOMAIN` | — | — | Legacy alias for STOREFRONT_DOMAIN |

---

## Project Structure

```
tombrady420/
├── main.py                   # Bot entry point: polling + webhook setup, all handler registration
├── webhook_server.py         # FastAPI app: webhook endpoints, REST API, SSR storefront
├── database.py               # All SQLite operations (aiosqlite, connection pooling, in-memory caches)
├── emojis.py                 # Emoji constants used across bot messages
├── requirements.txt          # Python dependencies
├── pytest.ini                # Test configuration
│
├── configs/
│   ├── config.py             # ConfigClass: loads and validates all env vars
│   ├── logging_config.py     # Structured logging setup
│   └── .env.example          # Full annotated configuration reference
│
├── handlers/
│   ├── start.py              # /start, /subscribe, /unsubscribe + deep-link routing
│   ├── menu.py               # /menu, category/subcategory navigation, pagination
│   ├── search.py             # Free-text search handler, paginated results
│   ├── product_view.py       # Product media display, delete button (admins)
│   ├── language.py           # /language command, language callback
│   ├── admin.py              # All admin commands (see Admin Commands section)
│   └── inline.py             # Inline query handler, chosen inline result handler
│
├── utils/
│   ├── notifications.py      # NotificationService: FIFO queue, batch delivery, rate limiting
│   ├── safe_send.py          # Retry-safe wrappers for all Telegram send operations
│   ├── helpers.py            # Shared helpers: admin check, file_id extraction, media sending
│   ├── categories.py         # Category/subcategory definitions, display names, formatting
│   ├── fuzzy_search.py       # Two-stage fuzzy search with synonym expansion
│   ├── pagination.py         # Inline keyboard pagination utility
│   └── webapp_security.py    # Telegram WebApp initData HMAC verification
│
├── translations/
│   ├── strings.py            # All English UI strings (source of truth for translations)
│   ├── translator.py         # TranslationService: Google Translate + LRU + DB cache
│   └── language_config.py    # Supported language codes and display names
│
├── webapp/
│   ├── index.html            # Jinja2 SPA shell with SEO meta, Open Graph, JSON-LD
│   ├── category.html         # Jinja2 category listing template
│   ├── app.js                # Vanilla JS: state machine, API calls, UI rendering
│   └── style.css             # Doodle wallpaper, Telegram theme vars, dark mode
│
├── docs/
│   ├── ADMIN_GUIDE.md        # Detailed admin command walkthrough
│   ├── USER_GUIDE.md         # End-user guide
│   └── WEBAPP_IMPLEMENTATION.md  # WebApp implementation notes
│
├── scripts/
│   └── setup_webhooks.py     # Helper: register webhooks for all bot tokens
│
└── tests/
    ├── conftest.py            # Shared fixtures: in-memory DB, mock Telegram objects
    ├── test_database.py       # Database CRUD and cache tests
    ├── test_categories.py     # Category utility tests
    └── test_catalog_routing.py  # Webhook routing and API tests
```

---

## Quick Start

**Prerequisites:** Python 3.8+, a Telegram bot token from [@BotFather](https://t.me/botfather), a Telegram channel where the bot is admin.

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp configs/.env.example .env
# Edit .env — set BOT_TOKEN, CHANNEL_ID, ADMIN_IDS at minimum

# 3a. Run in polling mode (development)
python main.py

# 3b. Run in webhook mode (production)
USE_WEBHOOK=true WEBHOOK_URL=https://yourapp.onrender.com uvicorn webhook_server:app --host 0.0.0.0 --port 8000
```

**Run tests:**
```bash
pytest
```

---

## Deployment (Render.com)

1. Create a new **Web Service** pointing to this repository.
2. Set **Build Command:** `pip install -r requirements.txt`
3. Set **Start Command:** `uvicorn webhook_server:app --host 0.0.0.0 --port $PORT`
4. Add environment variables (at minimum: `BOT_TOKEN`, `ADMIN_IDS`, `CHANNEL_ID`, `USE_WEBHOOK=true`, `WEBHOOK_URL=https://<your-service>.onrender.com`).
5. Deploy. The lifespan handler in `webhook_server.py` automatically registers webhooks for all configured bot tokens on startup.
6. For **multiple bots**, add `BOT_TOKEN_1`, `BOT_TOKEN_2`, etc. and run `python scripts/setup_webhooks.py` once to register all webhook paths.

See `configs/.env.example` for the full annotated variable reference.

---

## Further Reading

- **[Admin Guide](docs/ADMIN_GUIDE.md)** — detailed walkthrough of every admin command
- **[User Guide](docs/USER_GUIDE.md)** — end-user instructions
- **[Configuration Reference](configs/.env.example)** — every environment variable documented inline
