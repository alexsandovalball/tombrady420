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

The project now uses one shared visual system, but two browser shells:

```text
Public storefront shell:
  webapp/localized_home.html
  webapp/category.html
  webapp/product.html
  webapp/learn.html
  webapp/learn_index.html

Telegram Mini App shell:
  webapp/index.html
  webapp/app.js

Shared styling/assets:
  webapp/style.css
```

The Mini App is intentionally kept because it still owns Telegram-specific
behavior that the public storefront does not need:

- Telegram WebApp user detection
- authenticated session exchange via `/api/webapp-session`
- dynamic category/product/search navigation without full page loads
- signed admin monitor loading for Telegram admins
- direct product deep-link handling inside Telegram

The public storefront remains SEO-first and crawlable. The Mini App should keep
using the same styling direction so both surfaces feel consistent, but it is
still a distinct shell.

`category.html` and `product.html` own the main public browsing and media-view
experience. Category card previews prefer lightweight previews. Full video
streams should be touched only when a product is opened or the user plays media.

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

### Visitor analytics and guide publishing

`visitor_events` separates human page views from search crawlers, social link
previews, SEO tools, and automation. The admin monitor's visitor/location/page
headlines use human page views only; crawler traffic appears in its own sampled
section. Raw events are retained for 45 days by default and then rolled into
daily aggregates.

The guide worker turns qualified WebApp searches and Search Console demand into
source-backed drafts. It never publishes them automatically. Telegram admins
review each draft in the home-page Admin Monitor and choose Publish or Reject.
Published guides enter `/learn`, matching product/category internal links, the
sitemap, the guide RSS feed, and the IndexNow queue. Source material is limited
to the curated public-health list in `utils/guide_research.py`; competitor pages
are not scraped or copied.

## Production Checks

After deploy, verify:

- active bot count matches intended tokens
- webhook URLs are UUID paths, not numeric paths
- stale `/webhook/9` deliveries return 200 with `[WEBHOOK_ORPHAN_DRAINED]`
- `/health` returns OK
- `/admin/api/overview` requires the admin dashboard token
- direct product WebApp links open the expected product
- inline mode returns 10 closest matches with previews and text
