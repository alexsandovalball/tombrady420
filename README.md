# Bradycadia Telegram Storefront

Bradycadia is a production Telegram multi-bot catalog backend with a FastAPI
webhook server, SQLite persistence, a Telegram WebApp storefront, inline mode
search, product notifications, SEO product/category pages, and admin tools.

The production target is Render. The live database should be on persistent disk,
for example:

```text
DB_PATH=/data/catalog.db
```

## Runtime

Use webhook mode in production:

```powershell
uvicorn webhook_server:app --host 0.0.0.0 --port $env:PORT
```

Use polling mode only for local single-bot development:

```powershell
python main.py
```

## Minimum Environment

```text
BOT_TOKEN=primary-token
ADMIN_IDS=8589411468
USE_WEBHOOK=true
WEBHOOK_URL=https://your-render-service.onrender.com
DB_PATH=/data/catalog.db
```

`STOREFRONT_DOMAIN` is only required when you have a custom domain. If it is
missing, the app derives public URLs from `WEBHOOK_URL`. On Render, `DB_PATH`
defaults to `/data/catalog.db` automatically when a persistent disk is mounted.

`BOT_TOKENS` is exclusive when set. Do not also rely on stale `BOT_TOKEN_N`
values. If `BOT_TOKENS` is absent, individual `BOT_TOKEN`, `BOT_TOKEN_`, and
`BOT_TOKEN_1..10` values are loaded.

## Reuse Checklist

For a fresh Render project:

1. Set `BOT_TOKEN`, `ADMIN_IDS`, `USE_WEBHOOK=true`, and `WEBHOOK_URL`.
2. Add either `CHANNEL_ID` or `CHANNEL_USERNAME` if the bot should ingest
   products from a channel.
3. Mount a persistent disk at `/data`.
4. Deploy, then open `https://your-render-url/setup-check`.
5. Open the new Telegram bot as each admin and press Start. Until that happens,
   Telegram may log `Chat not found` while setting admin commands.
6. Add products through the configured channel/admin flow. A brand-new database
   has zero products, so the WebApp opens but cannot show a catalog yet.

The app repairs Telegram webhooks automatically on startup. You normally do not
need to run a webhook setup script manually.

The browser admin dashboard is disabled by default so scanners cannot discover
the dashboard shell at `/admin`. Prefer Telegram `/adminstatus`. To expose the
browser dashboard intentionally, set both:

```text
ADMIN_DASHBOARD_ENABLED=true
ADMIN_DASHBOARD_TOKEN=long-random-private-token
```

## Project Layout

```text
admin/                 Optional browser dashboard shell, disabled by default
configs/               Environment, logging, SEO terms, Telegram IP ranges
database.py            SQLite schema, migrations, queries, registry tables
docs/                  Compact operator and implementation notes
handlers/              Telegram command, callback, product, inline handlers
middleware/            Rate limiting and request deduplication
notifications/         Notification constants
scripts/               Operational repair/doctor scripts
translations/          Bot text translations
utils/                 Shared helpers, media resolution, search, security
webapp/                Telegram WebApp and SEO page assets
main.py                Polling entry point and bot command setup
webhook_server.py      FastAPI app, startup, webhooks, API, SEO, media streaming
```

Test files were intentionally removed after a full verification pass to keep the
deployment package small. The last pre-cleanup run passed:

```text
229 passed
```

## Core Behavior

Products are ingested from the configured Telegram channel. The bot stores the
caption, primary media, album media, source message IDs, category, subcategory,
bot username, and notification state in SQLite.

Users browse with `/start`, `/menu`, category buttons, product buttons, free-text
search, and inline mode. Product IDs are stable SQLite IDs and are not reused
after deletion. This prevents old links, buttons, notifications, and SEO URLs
from pointing at a different future product.

Admins manage products, users, broadcasts, cache, SEO mappings, bot instances,
and global settings from Telegram commands. See [Admin Guide](docs/ADMIN_GUIDE.md).

The WebApp reads from the same catalog and supports direct product links,
category pages, related products, media galleries, subscription checks, user
tracking, and browser-friendly product/category pages. See
[Implementation Notes](docs/WEBAPP_IMPLEMENTATION.md).

## Webhook Identity

Each active bot resolves its Telegram bot ID on startup, maps that stable ID to a
`bot_registry` UUID, and registers a webhook at:

```text
/webhook/<bot_registry_uuid>
```

Webhook secret validation uses the registry-stored secret for that UUID. Legacy
numeric paths such as `/webhook/9` are treated as migration/orphan paths:

- known registry secret: route to the registry owner
- unknown Telegram-origin secret: log `[WEBHOOK_ORPHAN_DRAINED]` and return 200
- unknown non-Telegram request: reject

If an old removed token is known, delete its webhook with Telegram's
`deleteWebhook(drop_pending_updates=true)`. If the old token is unknown, the 200
drain behavior stops retries from consuming meaningful application work.

## Media and Cache

Telegram `file_id`s are bot-specific. Bradycadia stores canonical media identity
and bot-specific mappings so every bot can serve the same catalog. Missing
bot-specific IDs are rehydrated by forwarding source channel messages and then
deleting the temporary admin-chat copy.

Media streaming supports HTTP Range requests for WebApp video playback. Temporary
media files are cleaned automatically, and persistent disk cache can be enabled
for videos when Render disk space is available.

Cold Telegram downloads are limited separately from cached disk streams. On a
512 MB instance the runtime also sheds regenerable HTML/API/search caches at the
soft memory threshold and delays new cold downloads at the hard threshold instead
of risking an OOM restart. Defaults require no extra environment variables;
`MEDIA_DOWNLOAD_CONCURRENCY`, `RUNTIME_MEMORY_SOFT_MB`, and
`RUNTIME_MEMORY_HARD_MB` are available for deployment-specific tuning.

Useful maintenance commands:

```powershell
python scripts/sqlite_doctor.py /data/catalog.db
python scripts/sqlite_doctor.py /data/catalog.db --rebuild-product-search
python scripts/telegram_webhook_cleanup.py --help
```

## Inline Mode

Inline mode returns up to 10 closest products for the typed query. Results prefer
Telegram-native cached photo/video previews, while still carrying message text,
caption, category, and action buttons. No-media fallbacks use a visible storefront
thumbnail instead of blank-looking entries.

## SEO

The open web routes render product/category pages for crawlers:

```text
/
/product/<id>/<slug>
/category/<category>
/category/<category>/<subcategory>
/sitemap.xml
/robots.txt
/llms.txt
```

Legacy `/learn`, localized `/learn`, `/learn/<slug>`, and `/guides-feed.xml`
routes redirect back to the catalog home page so old indexed links do not 404.

Unavailable product links should fall back to a related category or the home
page rather than showing a blank JSON error.

Search expansion starts from `configs/seo_terms.json` and improves itself from
real WebApp searches. The background SEO learner maps repeated no-result
queries to existing catalog categories/subcategories only when confidence is
high, then adds curated and translated variants for the supported bot/WebApp
languages. Product names and catalog-derived brands stay dynamic so removed
inventory does not become a permanent keyword target. Use `/seostatus` to see
what people searched and what mappings were learned.

Category and subcategory pages use page-specific descriptions and pagination
metadata. Product pages publish `Product`/`Offer` structured data only when the
catalog contains a real product image and an explicit visible currency price;
otherwise they remain accurately described as catalog items rather than
inventing commerce data.

Google Trends discovery runs twice daily by default with a bounded batch. Trend
terms may use a slightly looser catalog score than internal searches, but they
must still contain cannabis or mushroom audience intent and normally need repeat
observations before promotion. All SEO maintenance is serialized and pauses
automatically while the process is under memory pressure.

Optional Search Console learning can import real Google query/click/impression
data for your own verified site. The recommended setup is OAuth with your normal
Search Console Google account:

```powershell
python scripts/search_console_oauth.py --client-json C:\path\to\oauth-client.json
```

Then place these two files on the Render persistent disk:

```text
/data/search-console-oauth-client.json
/data/search-console-oauth-token.json
```

The app auto-enables Search Console learning when both OAuth files exist. The
site URL is derived from `STOREFRONT_DOMAIN`, so `SEARCH_CONSOLE_ENABLED` and
`SEARCH_CONSOLE_SITE_URL` are only needed when you want to override defaults.
Service-account JSON is still supported for deployments where Search Console
accepts the service-account identity.

IndexNow is also enabled automatically when `STOREFRONT_DOMAIN` exists. The app
generates a persistent key at `/data/indexnow.key`, serves it from
`https://your-domain/<key>.txt`, queues canonical URLs when catalog content
changes, and submits them in bounded background batches to participating search
engines. This is intentionally change-based; page views do not submit URLs.
Check `/adminstatus` or the admin WebApp monitor for pending, submitted, and
failed IndexNow counts.

For a quick crawler-facing regression check against a running deployment:

```powershell
python scripts/seo_smoke_check.py https://your-custom-domain.com --product-id 123
```

The learner also supports an optional approved taxonomy export file via
`EXTERNAL_SEO_SIGNALS_FILE`. Use short terms and category mappings only; do not
paste copied Leafly/Weedmaps page descriptions into the project. See
`configs/external_seo_signals.example.json`.

## Render Notes

Use one persistent disk mounted at `/data`. Keep the database there, not in the
repo root. Render health checks hit `/`; the app also exposes `/health`.

Recommended start command:

```text
uvicorn webhook_server:app --host 0.0.0.0 --port $PORT
```

After deploy, check logs for:

```text
Bot startup complete successful=[...]
registry_resolved ... bot_id=<uuid> ... webhook_expected_fp=<fingerprint>
Webhook set/repaired ... /webhook/<uuid>
```

## Operator Rule of Thumb

Do not run `/clearcache` routinely. Use it only after token/bot changes, invalid
media behavior, or deliberate cache repair. Repeated cache clearing makes the
next user/admin media views slower because the bots must rebuild file IDs.
