# Rate Limiting & Deduplication Strategy

This document describes the rate limiting, request deduplication, and monitoring
approach used by the TOM BRADY MEDIA BOT webhook server.

---

## Overview

The system is designed to support **legitimate user activity** — including rapid
media navigation (~4 second intervals) and quick page browsing — while protecting
against bot attacks and accidental duplicate processing.

---

## Layers of Protection

### 1. IP-Level Malicious Probe Blocking (`middleware/rate_limiter.py`)

The `GlobalRateLimiter` middleware immediately auto-blocks any IP that probes for
known malicious paths (e.g., `wp-login`, `wp-admin`, `.env`, `.git`, `xmlrpc.php`).

- **Normal traffic**: Unlimited (no token bucket throttling)
- **Malicious probes**: Instant auto-block for **1 hour** (configurable via `AUTO_BLOCK_DURATION` env var)
- **Webhook paths** (`/webhook`, `/webhook/*`): Always exempt — handled by Telegram IP whitelist

### 2. Non-Telegram Webhook IP Rate Limiting (`webhook_server.py`)

Requests to webhook endpoints from **non-Telegram IPs** are subject to a per-IP
token bucket limiter (`WebhookIPRateLimiter`). Legitimate Telegram servers are
whitelisted and never rate-limited.

- **Telegram IPs** (91.108.x.x, 149.154.x.x, etc.): Unlimited ✅
- **Other IPs**: 100 requests per 60-second window per IP

### 3. Callback Query Deduplication (`middleware/request_deduplicator.py`)

Prevents the same callback query from being processed twice within a short window.
This guards against Telegram retries and accidental double-taps.

| Setting | Default | Notes |
|---------|---------|-------|
| Dedup TTL | **5 seconds** | Configurable via `REQUEST_DEDUP_TTL` env var |
| Cache size | 50,000 entries | LRU eviction beyond this |

**Deduplication key**: `(bot_index, user_id, callback_data)`

> ⚠️ Navigation requests with **different parameters** (e.g., different page numbers
> or different subcategory names) are **never deduplicated** — they have distinct
> `callback_data` values.

### 4. Client-Side Debouncing (`webapp/app.js`)

The web frontend prevents redundant API calls through:

- **`loadingInProgress` flag**: Blocks a new products/categories load while one is
  already in progress (synchronous guard, race-condition safe in single-threaded JS)
- **`debouncedPageChange`**: 300ms debounce on pagination button clicks
- **`debouncedSearch`**: Configurable debounce on search input (see `CONFIG.SEARCH_DEBOUNCE_MS`)
- **`fetchJsonWithDedup`**: In-flight request deduplication — if the same URL is
  already being fetched, returns the existing Promise instead of creating a new request
- **`MediaRequestManager`**: Concurrent media request management with per-URL
  deduplication and a configurable concurrency limit

---

## What Is NOT Rate Limited

The following patterns are considered **legitimate user activity** and are
intentionally never blocked:

| Pattern | Example from logs | Why allowed |
|---------|-------------------|-------------|
| Rapid category browsing | 4–6 requests in 10 seconds | Normal navigation tempo |
| Subcategory pagination | `subcategory\|CONCENTRATES\|LIVE RESIN\|1` → `\|2` → `\|3` | User browsing pages |
| Media view + back navigation | `product\|1174` followed by `category\|SHROOMS\|1` | Natural UX flow |
| Multiple bots, same user | User interacts across bots | Multi-bot deployment is normal |

---

## Abuse Detection Signals

The following patterns indicate potential abuse and are monitored:

| Signal | Threshold | Response |
|--------|-----------|----------|
| Same IP probes malicious path | 1 attempt | Auto-block 1 hour |
| Non-Telegram IP on webhook endpoint | >100 req/min | HTTP 429 |
| Exact duplicate callback (same user, same data) | Within 5 seconds | Silently suppress (HTTP 200) |
| Bot success rate drops | <90% over 10+ requests | WARNING log |

---

## Monitoring & Alerting (`utils/monitoring.py`)

The `WebhookMonitor` tracks per-bot success/failure counters and average processing
latency. Data is available at the internal metrics endpoint:

```
GET /_internal/metrics
```

Example response:
```json
{
  "uptime_seconds": 3600.5,
  "webhook_401_total": 0,
  "endpoints": {
    "api_products": {"count": 240, "avg_ms": 180.3, "p95_ms": 420.1}
  },
  "webhook_monitor": {
    "uptime_seconds": 3600.5,
    "bots": {
      "3": {
        "success": 1240,
        "failure": 2,
        "total": 1242,
        "success_rate": 0.9984,
        "avg_duration_ms": 612.5,
        "last_seen": 1742312000.0
      }
    }
  }
}
```

### Log Correlation

Every webhook request is assigned a unique **request ID** (`request_id` prefix in
log lines), making it easy to trace a single update across all processing steps:

```
[a1b2c3d4] Bot 3: Processing callback query from user 7704335663: product|1174
[a1b2c3d4] Bot 3: Webhook request processed successfully
```

### Alert Thresholds (recommended)

| Metric | Alert Threshold | Action |
|--------|----------------|--------|
| `success_rate` per bot | < 90% | Investigate handler errors |
| `avg_duration_ms` | > 2000ms | Check for slow DB queries |
| `webhook_401_total` | > 10 in 1 minute | Possible secret token rotation issue |
| `p95_ms` (any endpoint) | > 500ms | Performance regression |

---

## Configuration

All tunables use existing environment variable patterns with sensible defaults.
**No new environment variables are required** to run the system.

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_REQUESTS` | `30` | Max requests per window (global rate limiter) |
| `RATE_LIMIT_WINDOW` | `60` | Window size in seconds |
| `AUTO_BLOCK_DURATION` | `3600` | Seconds to block malicious probe IPs |
| `REQUEST_DEDUP_TTL` | `5` | Seconds within which duplicate callbacks are suppressed |

---

## Architecture Diagram

```
Telegram Server (91.108.x.x)
       │
       ▼ POST /webhook/{bot_index}
┌──────────────────────────────┐
│  GlobalRateLimiter           │  ← Blocks malicious probe IPs
│  (middleware/rate_limiter.py)│
└──────────────┬───────────────┘
               │ (webhook paths exempt from IP rate limiting)
               ▼
┌──────────────────────────────┐
│  WebhookIPRateLimiter        │  ← Blocks non-Telegram IPs exceeding 100 req/min
│  (webhook_server.py)         │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  Secret Token Verification   │  ← Rejects requests without valid Telegram token
│  (webhook_server.py)         │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  RequestDeduplicator         │  ← Suppresses exact duplicate callback queries
│  (middleware/request_dedup…) │     within 5-second TTL window
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  Bot Update Processing       │  ← Actual handler execution
│  + WebhookMonitor recording  │
└──────────────────────────────┘
```
