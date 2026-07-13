# Implementation Notes

This file summarizes the current backend/WebApp design without the historical
debug notes.

## Backend

`webhook_server.py` owns production startup:

- initializes SQLite
- starts every Telegram bot Application
- resolves each bot's registry UUID and webhook secret
- registers Telegram webhooks
- serves bot webhooks, WebApp APIs, media, SEO pages, and admin dashboard APIs

`main.py` owns polling mode and shared Telegram command setup.

`database.py` owns schema creation, migrations, product/user/settings queries,
notification queues, bot registry identity, media registry, WebApp event storage,
and product-search FTS maintenance.

## WebApp APIs

Public/preview-readable catalog routes:

```text
GET /api/categories
GET /api/products
GET /api/products/{product_id}
GET /api/search
GET /api/catalog/{product_id}/media/{media_index}
GET /media/products/{product_id}/{media_index}
```

Authenticated user routes:

```text
POST /api/webapp-session
GET  /api/user/subscription
POST /api/user/subscription
GET  /api/user/info
POST /api/user/track
POST /api/webapp-logs
```

Telegram `initData` is verified for user-specific writes. Preview mode is allowed
for read-only catalog and logging paths so open-web product/category pages work.

## Frontend

`webapp/app.js` drives:

- Telegram WebApp user detection
- category/subcategory browsing
- direct product deep links
- product media gallery
- video preview frame cache
- session-token based API calls
- multilingual UI strings
- WebApp event logging

Category card previews prefer lightweight previews. Full video streams should be
touched only when a product is opened or the user plays media.

## Media

The media path is:

1. product stores source `file_id`, type, source chat/message, and album metadata
2. resolver validates whether the current bot can use the cached file ID
3. cross-bot cache misses are rehydrated from source channel messages
4. WebApp streams media through FastAPI with Range support
5. temp and persistent cache cleanup removes old/idle/oversized entries

Persistent video cache is recommended on Render when disk is available. A
700 MB cap is a practical balance for a 1 GB disk with `/data/catalog.db`.

## Inline Mode

`handlers/inline.py` returns at most 10 closest products. It uses strict
catalog-focused fuzzy search rather than broad SEO expansion. Cached media
results include both preview media and text content so the inline picker does
not split into "media without caption" versus "caption without media" behavior.

## SEO

The open-web routes render product/category HTML, metadata, related products,
media thumbnails, canonical links, multilingual `hreflang` pages, sitemap,
robots, favicon, and Open Graph
assets. Deleted or missing product URLs should fall back to the product category
when possible, otherwise to the home page.

SEO search-term expansion lives in:

```text
configs/seo_terms.json
utils/seo_terms.py
utils/seo_autolearn.py
handlers/admin.py   (/seostatus)
```

Do not create pages full of disconnected keyword stuffing. Keep search terms
mapped to real categories/products and expose them through useful category,
learn, or product pages. The automatic learner approves only confident mappings
to existing catalog taxonomy and expands them into supported bot/WebApp
languages.

## Production Checks

After deploy, verify:

- active bot count matches intended tokens
- webhook URLs are UUID paths, not numeric paths
- stale `/webhook/9` deliveries return 200 with `[WEBHOOK_ORPHAN_DRAINED]`
- `/health` returns OK
- `/admin/api/overview` requires the admin dashboard token
- direct product WebApp links open the expected product
- inline mode returns 10 closest matches with previews and text
