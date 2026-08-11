"""
Admin handlers.
"""
import logging
import asyncio
import aiosqlite
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from configs.config import Config
from database import Database
try:
    from utils.cache_invalidation import invalidate_webapp_caches
except Exception as exc:
    logger = logging.getLogger(__name__)
    logger.warning("Cache invalidation unavailable; using no-op fallback: %s", exc)

    def invalidate_webapp_caches(reason: str = "") -> int:
        return 0
from utils.helpers import is_admin, get_channel_id, get_channel_username, escape_markdown_v1, resolve_admin_scope
from utils.safe_send import safe_send_message, safe_edit_message_text
from utils.search_console import describe_search_console_setup
from utils.decorators import command_admin_required
from middleware.rate_limiter import add_permanent_ip_block, remove_permanent_ip_block

logger = logging.getLogger(__name__)
db = Database()

# Constants
CAPTION_DISPLAY_LENGTH = 50  # Maximum length of caption to display in scan results


def _fmt_count(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_ms(value) -> str:
    try:
        return f"{float(value):.0f}ms"
    except (TypeError, ValueError):
        return "0ms"


def _format_admin_status_message(snapshot: dict) -> str:
    summary = snapshot.get("summary", {})
    seo = snapshot.get("seo", {})
    metrics = snapshot.get("metrics", {}).get("endpoints", {})
    runtime_bots = snapshot.get("runtime_bots", [])
    cache_state = snapshot.get("cache_state", {})
    process = snapshot.get("process", {})
    media_runtime = snapshot.get("media_runtime", {})
    telegram_fetch = process.get("telegram_fetch", {}) if isinstance(process, dict) else {}
    event_counts = snapshot.get("webapp_event_counts", [])
    bot_user_counts = snapshot.get("bot_user_counts", [])
    top_locations = snapshot.get("top_visitor_locations", [])
    indexnow = snapshot.get("indexnow", {})
    google_sitemap = seo.get("search_console_sitemap") or {}
    search_console_days = _fmt_count(seo.get("search_console_window_days") or 30)
    google_queries = seo.get("search_console_queries_window", seo.get("search_console_queries_7d"))
    google_clicks = seo.get("search_console_clicks_window", seo.get("search_console_clicks_7d"))
    google_impressions = seo.get("search_console_impressions_window", seo.get("search_console_impressions_7d"))
    google_trends_signals = seo.get("google_trends_signals", 0)
    google_trends_latest_seen = seo.get("google_trends_latest_seen") or "none yet"
    bot_health_summary = snapshot.get("bot_health_summary") or {}

    active_bots = [
        bot for bot in runtime_bots
        if bot.get("running") and bot.get("telegram_ok") and bot.get("webhook_ok") and bot.get("telegram_health") == "ok"
    ]
    unhealthy_bots = [
        bot for bot in runtime_bots
        if bot.get("telegram_health") not in {None, "ok", "webhook_warning"}
    ]
    webhook_warning_bots = [
        bot for bot in runtime_bots
        if bot.get("telegram_health") == "webhook_warning"
    ]
    setup_warning_bots = [
        bot for bot in runtime_bots
        if int(bot.get("startup_issue_count") or 0) > 0
    ]
    missing_bots = [bot for bot in runtime_bots if not bot.get("username")]

    lines = [
        "📊 Admin Status",
        "",
        "🤖 Bots",
        f"• Online: {_fmt_count(bot_health_summary.get('online', len(active_bots)))}/{_fmt_count(bot_health_summary.get('configured', len(runtime_bots)))} configured",
        f"• Down/unhealthy: {_fmt_count(bot_health_summary.get('down'))}/{_fmt_count(bot_health_summary.get('unhealthy', len(unhealthy_bots)))}",
        f"• Registry active: {_fmt_count(summary.get('bot_registry_active'))}",
    ]
    if missing_bots:
        lines.append(f"• Missing/unloaded: {_fmt_count(len(missing_bots))}")
    if unhealthy_bots:
        lines.append(f"• Unhealthy: {_fmt_count(len(unhealthy_bots))}")
    if webhook_warning_bots:
        lines.append(f"• Webhook warnings: {_fmt_count(len(webhook_warning_bots))}")
    if setup_warning_bots:
        lines.append(f"• Setup warnings: {_fmt_count(len(setup_warning_bots))}")

    if unhealthy_bots or webhook_warning_bots or setup_warning_bots:
        lines.extend(["", "🤖 Bot Health Details"])
        seen_bot_keys = set()
        detail_bots = []
        for bot in (unhealthy_bots + webhook_warning_bots + setup_warning_bots):
            key = bot.get("index")
            if key in seen_bot_keys:
                continue
            seen_bot_keys.add(key)
            detail_bots.append(bot)
        for bot in detail_bots[:10]:
            username = f"@{bot.get('username')}" if bot.get("username") else "not started"
            health = bot.get("telegram_health") or "unknown"
            error = bot.get("telegram_error") or bot.get("webhook_last_error") or ""
            pending = bot.get("webhook_pending")
            detail = f"• #{bot.get('index')} {username}: {health}"
            if pending is not None:
                detail += f", pending={_fmt_count(pending)}"
            if error:
                detail += f", {str(error)[:90]}"
            lines.append(detail)
            for issue in (bot.get("startup_issues") or [])[:3]:
                stage = issue.get("stage") or "setup"
                admin_id = issue.get("admin_id")
                issue_error = str(issue.get("error") or "")[:90]
                issue_line = f"  - {stage}"
                if admin_id:
                    issue_line += f" admin={admin_id}"
                if issue_error:
                    issue_line += f": {issue_error}"
                lines.append(issue_line)

    lines.extend([
        "",
        "👥 Users",
        f"• Total: {_fmt_count(summary.get('bot_users'))}",
        f"• Active 24h: {_fmt_count(summary.get('active_users_24h'))}",
        f"• Subscribed: {_fmt_count(summary.get('subscribed_users'))}",
        f"• Blocked: {_fmt_count(summary.get('blocked_users'))}",
        "",
        "📦 Catalog / Queues",
        f"• Products: {_fmt_count(summary.get('products'))}",
        f"• Pending notifications: {_fmt_count(summary.get('pending_notifications'))}",
        f"• Queued custom messages: {_fmt_count(summary.get('queued_custom_messages'))}",
        "",
        "🌐 WebApp / Inline",
        f"• Unique visitors 24h: {_fmt_count(summary.get('unique_visitors_24h'))}",
        f"• Website visits 24h: {_fmt_count(summary.get('website_visits_24h'))}",
        f"• WebApp opens 24h: {_fmt_count(summary.get('webapp_visits_24h'))}",
        f"• Known Telegram visitors 24h: {_fmt_count(summary.get('known_visitor_users_24h'))}",
        f"• Crawler samples 24h: {_fmt_count(summary.get('crawler_events_24h'))} "
        f"({_fmt_count(summary.get('search_crawler_events_24h'))} search crawlers)",
        f"• WebApp events 24h: {_fmt_count(summary.get('webapp_events_24h'))}",
        f"• Inline queries 24h: {_fmt_count(summary.get('inline_queries_24h'))}",
        f"• Inline chosen 24h: {_fmt_count(summary.get('inline_chosen_24h'))}",
        f"• WebApp searches 24h: {_fmt_count(summary.get('search_queries_24h'))}",
        f"• No-result searches 24h: {_fmt_count(summary.get('search_no_result_24h'))}",
        f"• SEO mappings: {_fmt_count(summary.get('seo_query_mappings'))}",
        f"• Google queries {search_console_days}d: {_fmt_count(google_queries)}",
        f"• Google clicks/impressions {search_console_days}d: {_fmt_count(google_clicks)}/{_fmt_count(google_impressions)}",
        f"• IndexNow: {'on' if indexnow.get('enabled', True) else 'off'} "
        f"(pending {_fmt_count(indexnow.get('pending'))}, "
        f"sent {_fmt_count(indexnow.get('submitted'))}, "
        f"failed {_fmt_count(indexnow.get('failed'))})",
        f"• Google sitemap: {google_sitemap.get('status') or 'never'}"
        + (f" at {google_sitemap.get('last_submitted_at')}" if google_sitemap.get("last_submitted_at") else ""),
        "",
        "⚙️ Caches",
        f"• File ID DB: {_fmt_count(summary.get('file_id_cache_entries'))}",
        f"• API memory: {_fmt_count(cache_state.get('api_results'))}",
        f"• Media info memory: {_fmt_count(cache_state.get('media_info'))}",
        f"• Resolved file IDs: {_fmt_count(cache_state.get('resolved_file_ids'))}",
        f"• Orphan legacy secrets: {_fmt_count(cache_state.get('orphan_legacy_secrets'))}",
        f"• Webhook 401 total: {_fmt_count(snapshot.get('webhook_401_total'))}",
        f"• RAM RSS: {_fmt_count(round((process.get('memory_rss_bytes') or 0) / (1024 * 1024), 1))} MB",
        f"• Media streams: {_fmt_count(media_runtime.get('active_streams'))}; "
        f"Telegram fetches active: {_fmt_count(telegram_fetch.get('active'))}",
        f"• Telegram fetch failures: {_fmt_count(telegram_fetch.get('failed'))}",
    ])

    if metrics:
        lines.extend(["", "⏱ API Latency"])
        for endpoint in ("api_products", "api_categories", "webhook_401"):
            stat = metrics.get(endpoint)
            if stat:
                lines.append(
                    f"• {endpoint}: count={_fmt_count(stat.get('count'))}, "
                    f"avg={_fmt_ms(stat.get('avg_ms'))}, p95={_fmt_ms(stat.get('p95_ms'))}"
                )

    if bot_user_counts:
        lines.extend(["", "👤 Top Bots By Users"])
        for item in bot_user_counts[:5]:
            lines.append(
                f"• @{item.get('bot_username')}: {_fmt_count(item.get('users'))} users "
                f"({_fmt_count(item.get('active_24h'))} active 24h)"
            )

    if top_locations:
        lines.extend(["", "📍 Top Visitor Locations"])
        for item in top_locations[:5]:
            place = ", ".join(
                part for part in [item.get("city"), item.get("region"), item.get("country")]
                if part and part != "Unknown"
            ) or "Unknown"
            lines.append(
                f"• {place}: {_fmt_count(item.get('visitors'))} visitor(s), "
                f"{_fmt_count(item.get('visits'))} visit(s)"
            )

    if event_counts:
        lines.extend(["", "🧭 Top WebApp Events"])
        for item in event_counts[:5]:
            lines.append(
                f"• {item.get('event_type')}/{item.get('action')}: {_fmt_count(item.get('count'))}"
            )

    lines.extend([
        "",
        "Tip: /clearcache only when media is stale. /prunebots is dry-run unless you add confirm.",
    ])
    return "\n".join(lines)


@command_admin_required
async def adminstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /adminstatus command - compact runtime, user, WebApp, and cache status."""
    try:
        snapshot = await db.get_admin_dashboard_snapshot(recent_limit=10)
        snapshot["seo"] = await db.get_search_intelligence_snapshot(limit=5)
        try:
            from webhook_server import (
                _runtime_bot_health_snapshots,
                _cache_state_snapshot,
                _runtime_process_snapshot,
                _media_runtime_snapshot,
                _webhook_401_counter,
                _indexnow_enabled,
                _indexnow_key,
                metrics,
            )
            snapshot["runtime_bots"] = await _runtime_bot_health_snapshots()
            snapshot["cache_state"] = _cache_state_snapshot()
            snapshot["process"] = _runtime_process_snapshot()
            snapshot["media_runtime"] = _media_runtime_snapshot()
            snapshot["webhook_401_total"] = _webhook_401_counter
            snapshot["metrics"] = metrics.snapshot()
            snapshot["indexnow"] = {
                **(snapshot.get("indexnow") or {}),
                "enabled": _indexnow_enabled(),
                "key_file_available": bool(_indexnow_key(create=False)),
            }
        except Exception as import_error:
            logger.debug("adminstatus using DB-only snapshot: %s", import_error)

        text = _format_admin_status_message(snapshot)
        await update.message.reply_text(text[:3900])
    except Exception as e:
        logger.error("Error in adminstatus command: %s", e, exc_info=True)
        await update.message.reply_text("❌ An error occurred while loading admin status.")


def _format_seo_target(item: dict) -> str:
    category = str(item.get("category") or "").strip()
    subcategory = str(item.get("subcategory") or "").strip()
    if category and subcategory:
        return f"{category} / {subcategory}"
    return category or "general catalog"


def _format_search_console_status(snapshot: dict) -> list[str]:
    setup = describe_search_console_setup(
        enabled=Config.SEARCH_CONSOLE_ENABLED,
        site_url=Config.SEARCH_CONSOLE_SITE_URL,
        service_account_json=Config.SEARCH_CONSOLE_SERVICE_ACCOUNT_JSON,
        service_account_file=Config.SEARCH_CONSOLE_SERVICE_ACCOUNT_FILE,
        oauth_client_json=Config.SEARCH_CONSOLE_OAUTH_CLIENT_JSON,
        oauth_client_file=Config.SEARCH_CONSOLE_OAUTH_CLIENT_FILE,
        oauth_token_json=Config.SEARCH_CONSOLE_OAUTH_TOKEN_JSON,
        oauth_token_file=Config.SEARCH_CONSOLE_OAUTH_TOKEN_FILE,
    )
    total_rows = int(snapshot.get("search_console_total_rows") or 0)
    window_rows = int(snapshot.get("search_console_queries_window") or 0)
    latest_source_date = snapshot.get("search_console_latest_source_date") or "none yet"
    latest_imported_at = snapshot.get("search_console_latest_imported_at") or "none yet"
    sitemap = snapshot.get("search_console_sitemap") or {}
    sitemap_status = sitemap.get("status") or "never"
    sitemap_attempt = sitemap.get("last_attempt_at") or "none yet"
    sitemap_submitted = sitemap.get("last_submitted_at") or "none yet"
    sitemap_error = sitemap.get("last_error") or ""

    if not setup.get("enabled"):
        status = "off"
        note = "Search Console importing is disabled."
    elif not setup.get("configured"):
        status = "not connected"
        note = "No usable Search Console credential was found."
    elif total_rows > 0:
        status = "connected and importing"
        if window_rows:
            note = "Google query rows are being stored."
        else:
            note = "Connected, but Google has no rows in the current lookback window."
    else:
        status = "configured, waiting for rows"
        note = "Credentials exist, but no Search Console rows have been saved yet."

    auth_source = setup.get("auth_source") or "none"
    interval_seconds = int(snapshot.get("seo_auto_learn_interval_seconds") or Config.SEO_AUTO_LEARN_INTERVAL_SECONDS)
    interval_minutes = max(1, round(interval_seconds / 60))
    max_matched = snapshot.get("seo_auto_learn_max_matched_count", Config.SEO_AUTO_LEARN_MAX_MATCHED_COUNT)
    max_per_run = snapshot.get("seo_auto_learn_max_per_run", Config.SEO_AUTO_LEARN_MAX_PER_RUN)
    score = snapshot.get("seo_auto_learn_score", Config.SEO_AUTO_LEARN_SCORE)
    suppression_hours = snapshot.get("seo_auto_learn_suppression_hours", Config.SEO_AUTO_LEARN_SUPPRESSION_HOURS)
    runtime = snapshot.get("runtime_status") or {}
    last_run = runtime.get("seo_auto_learn_last_run_at") or "pending"
    last_candidates = runtime.get("seo_auto_learn_last_candidates", 0)
    last_suppressed = runtime.get("seo_auto_learn_last_suppressed", 0)
    last_retired_noise = runtime.get("seo_auto_learn_last_retired_noise", 0)
    import_status = runtime.get("search_console_import_last_status") or "pending"
    import_property = runtime.get("search_console_import_last_property") or setup.get("site_url") or "not set"
    import_requests = runtime.get("search_console_import_last_requests", 0)
    import_media_rows = runtime.get("search_console_import_last_media_rows", 0)
    import_aggregates = runtime.get("search_console_import_last_aggregates", 0)
    import_mismatches = runtime.get("search_console_import_last_page_mismatches", 0)
    import_error = runtime.get("search_console_import_last_error") or ""

    return [
        "",
        "Google Search Console",
        f"• Status: {status}",
        f"• Auth: {auth_source}",
        f"• Last import health: {import_status}",
        f"• Property used: {import_property}",
        f"• API requests/aggregates: {_fmt_count(import_requests)}/{_fmt_count(import_aggregates)}",
        f"• Image/video rows: {_fmt_count(import_media_rows)}",
        f"• Page mismatches rejected: {_fmt_count(import_mismatches)}",
        f"• Latest Google data date: {latest_source_date}",
        f"• Last saved import: {latest_imported_at}",
        f"• Sitemap submit: {sitemap_status}",
        f"• Last sitemap attempt: {sitemap_attempt}",
        f"• Last sitemap accepted: {sitemap_submitted}",
        f"• Sitemap URL: {sitemap.get('sitemap_url') or Config.SEARCH_CONSOLE_SITEMAP_URL or 'not set'}",
        f"• Note: {note}",
        *( [f"• Sitemap note: {sitemap_error[:120]}"] if sitemap_error else [] ),
        *( [f"• Import note: {import_error[:120]}"] if import_error else [] ),
        "",
        "Auto-Learning Settings",
        f"• Runs every: {_fmt_count(interval_minutes)} min",
        f"• Match score: {score}",
        f"• Learns low-result searches: 0-{max_matched} result(s)",
        f"• Max new checks per run: {_fmt_count(max_per_run)}",
        f"• Weak-term cooldown: {_fmt_count(suppression_hours)}h",
        f"• Last learner run: {last_run}",
        f"• Last checked/paused: {_fmt_count(last_candidates)}/{_fmt_count(last_suppressed)}",
        f"• Off-audience mappings retired: {_fmt_count(last_retired_noise)}",
    ]


@command_admin_required
async def seostatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show SEO/search query intelligence for admin review."""
    try:
        snapshot = await db.get_search_intelligence_snapshot(limit=10)
        search_console_days = _fmt_count(snapshot.get("search_console_window_days") or 30)
        low_result_threshold = int(snapshot.get("low_result_threshold") or 0)
        low_result_label = f"0-{low_result_threshold}" if low_result_threshold > 0 else "0"
        google_trends_signals = snapshot.get("google_trends_signals")
        google_trends_latest_seen = snapshot.get("google_trends_latest_seen") or "none yet"
        lines = [
            "🔎 SEO Search Learning",
            "",
            "What this means: the bot watches website/WebApp searches, learns safe search-word mappings, and uses them to send future searches to the right menu area.",
            "",
            "Today",
            f"• Searches typed: {_fmt_count(snapshot.get('queries_24h'))}",
            f"• Searches with no result: {_fmt_count(snapshot.get('no_result_24h'))}",
            f"• Searches with {low_result_label} result(s): {_fmt_count(snapshot.get('low_result_24h'))}",
            f"• Total approved mappings: {_fmt_count(snapshot.get('approved_mappings'))}",
            f"• Made automatically: {_fmt_count(snapshot.get('auto_mappings'))}",
            f"• Google queries imported ({search_console_days}d): {_fmt_count(snapshot.get('search_console_queries_window'))}",
            f"• Google clicks/impressions ({search_console_days}d): {_fmt_count(snapshot.get('search_console_clicks_window'))}/{_fmt_count(snapshot.get('search_console_impressions_window'))}",
            f"• Google image/video impressions: {_fmt_count(snapshot.get('search_console_image_impressions_window'))}/{_fmt_count(snapshot.get('search_console_video_impressions_window'))}",
            f"• Google Trends signals: {_fmt_count(google_trends_signals)}",
            f"• Google Trends latest seen: {google_trends_latest_seen}",
            f"• Approved external signals: {_fmt_count(snapshot.get('external_signals'))}",
        ]
        lines.extend(_format_search_console_status(snapshot))

        top_queries = snapshot.get("top_queries") or []
        if top_queries:
            lines.extend(["", "Searches People Typed"])
            for item in top_queries[:5]:
                lines.append(
                    f"• {item.get('query')}: searched {_fmt_count(item.get('count'))}x, "
                    f"last found {_fmt_count(item.get('last_matched_count'))} product(s)"
                )

        search_console_queries = snapshot.get("top_search_console_queries") or []
        if search_console_queries:
            lines.extend(["", "Google Searches That Found The Site"])
            for item in search_console_queries[:5]:
                lines.append(
                    f"• {item.get('query')}: {_fmt_count(item.get('clicks'))} click(s), "
                    f"{_fmt_count(item.get('impressions'))} impression(s), avg position {item.get('avg_position')}"
                )

        opportunities = snapshot.get("search_console_opportunities") or []
        if opportunities:
            lines.extend(["", "Google Ranking Opportunities"])
            for item in opportunities[:5]:
                ctr_percent = round(float(item.get("ctr") or 0) * 100, 1)
                lines.append(
                    f"• {item.get('query')}: {_fmt_count(item.get('impressions'))} impression(s), "
                    f"{_fmt_count(item.get('clicks'))} click(s), CTR {ctr_percent}%, avg position {item.get('avg_position')}"
                )

        ctr_terms = snapshot.get("search_console_ctr_terms") or []
        if ctr_terms:
            lines.extend(["", "CTR Terms Being Learned"])
            for item in ctr_terms[:5]:
                ctr_percent = round(float(item.get("ctr") or 0) * 100, 1)
                lines.append(
                    f"• {item.get('query')} → {_format_seo_target(item)} "
                    f"({_fmt_count(item.get('impressions'))} impression(s), CTR {ctr_percent}%)"
                )

        recent_mappings = snapshot.get("recent_mappings") or []
        if recent_mappings:
            lines.extend(["", "New/Recently Used Search Mappings"])
            for item in recent_mappings[:8]:
                source = "auto" if item.get("source") == "auto_learn" else str(item.get("source") or "manual")
                hits = int(item.get("hits") or 0)
                hit_text = f", {_fmt_count(hits)} hit(s)" if hits else ""
                language = str(item.get("language") or "en").upper()
                lines.append(f"• [{language}] {item.get('term')} → {_format_seo_target(item)} ({source}{hit_text})")

        mapping_languages = snapshot.get("mapping_languages") or []
        search_languages = snapshot.get("search_languages_30d") or []
        if mapping_languages or search_languages:
            lines.extend(["", "Language Coverage"])
            if mapping_languages:
                mapping_summary = ", ".join(
                    f"{str(item.get('language') or 'en').upper()} {_fmt_count(item.get('count'))}"
                    for item in mapping_languages
                )
                lines.append(f"• Search mappings: {mapping_summary}")
            if search_languages:
                search_summary = ", ".join(
                    f"{str(item.get('language') or 'en').upper()} {_fmt_count(item.get('count'))}"
                    for item in search_languages
                )
                lines.append(f"• Searches in 30 days: {search_summary}")

        recent_external = snapshot.get("recent_external_signals") or []
        if recent_external:
            lines.extend(["", "New External/Google Signals"])
            for item in recent_external[:5]:
                lines.append(
                    f"• {item.get('term')} → {_format_seo_target(item)} "
                    f"({item.get('provider')}, confidence {item.get('confidence')})"
                )

        unmapped = snapshot.get("unmapped") or []
        if unmapped:
            lines.extend(["", "Still Being Studied"])
            for item in unmapped[:8]:
                best_matched = int(item.get("best_matched_count") or 0)
                if best_matched > 0:
                    result_text = f"best found {_fmt_count(best_matched)} product(s)"
                else:
                    result_text = "no products found"
                lines.append(
                    f"• {item.get('query')} ({_fmt_count(item.get('count'))} low/no-result search(es), {result_text})"
                )

        lines.extend([
            "",
            "Auto-learning runs in the background. No manual SEO import or approval command is needed.",
            "Search Console health above is based on saved imports, so it stays fast inside Telegram.",
            "Google Trends is ingested as a separate low-risk signal source and only promotes clear catalog matches.",
        ])
        await update.message.reply_text("\n".join(lines)[:3900])
    except Exception as e:
        logger.error("Error in seostatus command: %s", e, exc_info=True)
        await update.message.reply_text("❌ An error occurred while loading SEO status.")


async def build_users_bot_selection_menu():
    """
    Build bot selection menu for user management.
    Returns tuple of (keyboard, message_text, message_text_no_md, has_bots, bot_list)
    where bot_list is a list of (bot_username, count) tuples for single-bot auto-selection
    """
    active_bots, active_bot_list, untracked_count = await _get_active_bot_user_counts()

    if not active_bots:
        return None, None, None, False, []

    # Check for untracked users
    has_untracked = untracked_count > 0
    
    # Create complete bot list for auto-selection logic (active bots only)
    all_bots = [(entry["username"], entry["count"]) for entry in active_bot_list]
    if has_untracked:
        all_bots.append(("_untracked_", untracked_count))
    
    # Create keyboard with bot instances
    keyboard_buttons = []
    
    # Add active bots first
    for entry in active_bot_list:
        bot_username = entry["username"]
        count = entry["count"]
        display_text = f"✅ @{bot_username} ({count} users)"
        keyboard_buttons.append([
            InlineKeyboardButton(display_text, callback_data=f"viewusers|{bot_username}|1")
        ])
    
    # Add option to view users without bot_username
    if has_untracked:
        keyboard_buttons.append([
            InlineKeyboardButton(f"Untracked Users ({untracked_count})", callback_data="viewusers|_untracked_|1")
        ])
    
    keyboard = InlineKeyboardMarkup(keyboard_buttons)
    
    total_users = await db.get_users_count_by_bot()
    
    # Count all active bots (not just those with users)
    active_bots_count = len(active_bots)
    # Count active bots with users  
    active_bots_with_users_count = len(active_bot_list)
    
    message = f"📊 **User Management by Bot Instance**\n\n"
    message += f"Total Users: {total_users}\n"
    message += f"Active Bot Instances: {active_bots_count}\n"
    
    if active_bots_with_users_count < active_bots_count:
        bots_without_users = active_bots_count - active_bots_with_users_count
        message += f"ℹ️ {bots_without_users} active bot(s) have no users yet\n"
    
    message += f"\nSelect a bot to manage its users:"
    
    # Non-markdown version
    message_no_md = f"📊 User Management by Bot Instance\n\n"
    message_no_md += f"Total Users: {total_users}\n"
    message_no_md += f"Active Bot Instances: {active_bots_count}\n"
    
    if active_bots_with_users_count < active_bots_count:
        bots_without_users = active_bots_count - active_bots_with_users_count
        message_no_md += f"ℹ️ {bots_without_users} active bot(s) have no users yet\n"
    
    message_no_md += f"\nSelect a bot to manage its users:"
    
    return keyboard, message, message_no_md, True, all_bots


async def _get_active_bot_user_counts():
    """Return current runtime bots and their user counts.

    Runtime bot applications are the source of truth here. The database may
    contain historical users and identity records from switched/deleted bots,
    but those must not make old bots reappear in admin menus.
    """
    from webhook_server import get_bot_usernames

    active_bot_entries = []
    seen = set()
    for bot_index, bot_username in enumerate(get_bot_usernames()):
        if not bot_username:
            continue
        normalized = str(bot_username).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        active_bot_entries.append({
            "index": bot_index,
            "username": bot_username,
        })

    active_bots = [entry["username"] for entry in active_bot_entries]
    logger.info(
        "Active bots from webhook_server: %d - %s",
        len(active_bots),
        [f"Bot {entry['index']}: @{entry['username']}" for entry in active_bot_entries],
    )

    if active_bots:
        cleanup_stats = await db.forget_inactive_bot_memory(active_bots)
        if any(value for key, value in cleanup_stats.items() if key != "active_bots"):
            logger.info("Inactive bot memory cleanup from admin menu: %s", cleanup_stats)

    active_bot_list = []
    for entry in active_bot_entries:
        bot_username = entry["username"]
        count = await db.get_users_count_by_bot(bot_username)
        active_bot_list.append({
            **entry,
            "count": count,
        })

    untracked_count = await db.get_users_count_by_bot("_untracked_")
    return active_bots, active_bot_list, untracked_count


async def build_botusers_selection_menu(notice: str = ""):
    """Build the /botusers runtime-slot ordered bot selection menu."""
    active_bots, active_bot_list, untracked_count = await _get_active_bot_user_counts()
    if not active_bots:
        return None, None, None, False

    keyboard_buttons = []
    for entry in active_bot_list:
        bot_username = entry["username"]
        count = entry["count"]
        display_text = f"Bot {entry['index']}: @{bot_username} ({count} users)"
        keyboard_buttons.append([
            InlineKeyboardButton(display_text, callback_data=f"viewbotusers|{bot_username}|1")
        ])

    if untracked_count > 0:
        keyboard_buttons.append([
            InlineKeyboardButton(f"Untracked Users ({untracked_count})", callback_data="viewbotusers|_untracked_|1")
        ])

    keyboard = InlineKeyboardMarkup(keyboard_buttons)
    total_users = await db.get_users_count_by_bot()
    active_bots_count = len(active_bots)
    active_bots_with_users_count = sum(1 for entry in active_bot_list if entry["count"] > 0)

    message = "📊 **Bot Users by Instance**\n\n"
    message += f"Total Users: {total_users}\n"
    message += f"Active Bot Instances: {active_bots_count}\n"

    message_no_md = "📊 Bot Users by Instance\n\n"
    message_no_md += f"Total Users: {total_users}\n"
    message_no_md += f"Active Bot Instances: {active_bots_count}\n"

    if active_bots_with_users_count < active_bots_count:
        bots_without_users = active_bots_count - active_bots_with_users_count
        line = f"ℹ️ {bots_without_users} active bot(s) have no users yet\n"
        message += line
        message_no_md += line

    if notice:
        message += f"\n{notice}\n"
        message_no_md += f"\n{notice}\n"

    message += "\nSelect a bot to view its users:"
    message_no_md += "\nSelect a bot to view its users:"

    return keyboard, message, message_no_md, True


@command_admin_required
async def nuke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /nuke command - deletes ALL products (admin only) with double confirmation."""
    # First confirmation
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ Yes, I want to nuke", callback_data="nuke_confirm1")],
        [InlineKeyboardButton("❌ Cancel", callback_data="nuke_cancel")]
    ])
    
    total_count = await db.count_products()
    
    try:
        await update.message.reply_text(
            f"⚠️ **NUKE WARNING**\n\n"
            f"You are about to delete **{total_count}** product(s) from the catalog.\n\n"
            f"This action cannot be undone!\n\n"
            f"Are you sure you want to continue?",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except BadRequest as e:
        logger.warning(f"Markdown parse error in nuke_command, retrying without parse_mode: {e}")
        await update.message.reply_text(
            f"⚠️ NUKE WARNING\n\n"
            f"You are about to delete {total_count} product(s) from the catalog.\n\n"
            f"This action cannot be undone!\n\n"
            f"Are you sure you want to continue?",
            reply_markup=keyboard
        )


@command_admin_required
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /users command - view bot users grouped by bot instance with notification controls (admin only)."""
    try:
        # Build bot selection menu
        keyboard, message, message_no_md, has_bots, bot_list = await build_users_bot_selection_menu()
        
        if not has_bots:
            try:
                await update.message.reply_text(
                    "📊 **User Management**\n\n"
                    "No bot usernames found. Users may not have bot_username tracked yet.",
                    parse_mode="Markdown"
                )
            except BadRequest as e:
                logger.warning(f"Markdown parse error in users_command (no bots), retrying without parse_mode: {e}")
                await update.message.reply_text(
                    "📊 User Management\n\n"
                    "No bot usernames found. Users may not have bot_username tracked yet."
                )
            return
        
        # If only one bot/group exists, auto-select it for better UX
        if len(bot_list) == 1:
            bot_username = bot_list[0][0]
            await show_users_page(update, context, page=1, bot_username=bot_username)
            return
        
        # Show selection menu when multiple bots exist
        try:
            await update.message.reply_text(
                message,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except BadRequest as e:
            logger.warning(f"Markdown parse error in users_command, retrying without parse_mode: {e}")
            await update.message.reply_text(
                message_no_md,
                reply_markup=keyboard
            )
        
    except Exception as e:
        logger.error(f"Error in users command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while retrieving user list."
        )


async def show_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1, bot_username: str = None):
    """Display a page of users with notification toggle controls, optionally filtered by bot_username."""
    try:
        # Pagination settings
        per_page = 10
        offset = (page - 1) * per_page
        
        # Get paginated users and total count based on bot_username filter
        if bot_username:
            # Get users for specific bot
            total_users, page_users = await db.get_users_by_bot_paginated(bot_username, limit=per_page, offset=offset)
            
            # Set display name for header
            if bot_username == "_untracked_":
                bot_display_name = "Untracked Users"
            else:
                bot_display_name = f"@{bot_username}"
        else:
            # Get all users (backward compatibility)
            total_users, page_users = await db.get_users_paginated(limit=per_page, offset=offset)
            bot_display_name = "All Users"
        
        if total_users == 0:
            text = f"📭 No users found for {bot_display_name}."
            if update.callback_query:
                query = update.callback_query
                await safe_edit_message_text(
                    context.bot,
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    text=text
                )
            else:
                await update.message.reply_text(text)
            return
        
        total_pages = (total_users + per_page - 1) // per_page
        page = max(1, min(page, total_pages))
        
        # Build message text
        message_lines = [f"👥 **{bot_display_name}** (Page {page}/{total_pages}, {total_users} total)\n"]
        
        for i, user in enumerate(page_users, start=offset + 1):
            # Escape Markdown v1 characters
            username_raw = user.get('username')
            if username_raw:
                username = escape_markdown_v1(f"@{str(username_raw)}")
            else:
                username = "No username"
            first_name = escape_markdown_v1(str(user.get('first_name', 'N/A')))
            last_name = escape_markdown_v1(str(user.get('last_name', '')))
            full_name = f"{first_name} {last_name}".strip()
            
            # Notification status
            notif_enabled = user.get('notifications_enabled', 1) == 1
            notif_status = "🔔 ON" if notif_enabled else "🔕 OFF"
            
            # Blocked status
            is_blocked = user.get('is_blocked', 0) == 1
            blocked_status = "🚫 BLOCKED" if is_blocked else ""
            
            last_seen = user.get('last_seen', 'Unknown')
            if last_seen != 'Unknown':
                try:
                    dt = datetime.fromisoformat(last_seen)
                    last_seen = dt.strftime("%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    pass
            
            user_text = (
                f"\n{i}. **{full_name}** ({username})\n"
                f"   🆔 ID: `{user['user_id']}`\n"
                f"   📊 Interactions: {user.get('interaction_count', 0)}\n"
                f"   🕐 Last seen: {last_seen}\n"
                f"   📬 Notifications: {notif_status}"
            )
            
            # Only show bot username if not already filtering by bot
            if not bot_username:
                current_user_bot = user.get('bot_username')
                if current_user_bot:
                    escaped_bot_username = escape_markdown_v1(f"@{current_user_bot}")
                    user_text += f"\n   🤖 Bot: {escaped_bot_username}"
            
            # Add blocked status if applicable
            if blocked_status:
                user_text += f"\n   ⚠️ Status: {blocked_status}"
            
            message_lines.append(user_text)
        
        message_text = "\n".join(message_lines)
        
        # Build keyboard with individual toggle buttons
        keyboard_buttons = []
        for user in page_users:
            uid = user['user_id']
            notif_enabled = user.get('notifications_enabled', 1) == 1
            is_blocked = user.get('is_blocked', 0) == 1
            
            # Get user display name
            username = user.get('username') or user.get('first_name', 'Unknown')
            if len(username) > 12:
                username = username[:9] + "..."
            
            # Create callback data that includes bot_username if specified
            bot_param = f"|{bot_username}" if bot_username else ""
            
            # Create row with notification toggle and block/unblock button
            row_buttons = [
                InlineKeyboardButton(
                    f"{'🔕' if notif_enabled else '🔔'} {username}",
                    callback_data=f"toggle_notif|{uid}|{page}{bot_param}"
                )
            ]
            
            # Add block/unblock button
            if is_blocked:
                row_buttons.append(
                    InlineKeyboardButton("✅ Unblock", callback_data=f"unblock_user|{uid}|{page}{bot_param}")
                )
            else:
                row_buttons.append(
                    InlineKeyboardButton("🚫 Block", callback_data=f"block_user|{uid}|{page}{bot_param}")
                )
            
            keyboard_buttons.append(row_buttons)
        
        # Add pagination buttons
        nav_buttons = []
        if page > 1:
            prev_callback = f"users_page|{page-1}{bot_param}" if bot_username else f"users_page|{page-1}"
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=prev_callback))
        if page < total_pages:
            next_callback = f"users_page|{page+1}{bot_param}" if bot_username else f"users_page|{page+1}"
            nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=next_callback))
        
        if nav_buttons:
            keyboard_buttons.append(nav_buttons)
        
        # Add back button if filtering by bot
        if bot_username:
            keyboard_buttons.append([InlineKeyboardButton("⬅️ Back to Bot List", callback_data="users_back_to_bots")])
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        # Send or edit message
        if update.callback_query:
            query = update.callback_query
            try:
                await safe_edit_message_text(
                    context.bot,
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    text=message_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            except BadRequest as e:
                logger.warning(f"Markdown parse error in show_users_page, retrying without parse_mode: {e}")
                await safe_edit_message_text(
                    context.bot,
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    text=message_text,
                    reply_markup=keyboard
                )
        else:
            try:
                await update.message.reply_text(
                    message_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            except BadRequest as e:
                logger.warning(f"Markdown parse error in show_users_page, retrying without parse_mode: {e}")
                await update.message.reply_text(
                    message_text,
                    reply_markup=keyboard
                )
        
        logger.info(f"Displayed users page {page}/{total_pages} for {bot_display_name}")
        
    except Exception as e:
        logger.error(f"Error showing users page: {e}", exc_info=True)
        error_text = "❌ An error occurred while displaying users."
        if update.callback_query:
            query = update.callback_query
            await safe_edit_message_text(
                context.bot,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=error_text
            )
        else:
            await update.message.reply_text(error_text)


async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """Delete a product (admin only)."""
    user_id = update.effective_user.id
    query = update.callback_query
    chat_id = update.effective_chat.id if update.effective_chat else None
    admin_scope = resolve_admin_scope(update, context)

    async def _send_delete_feedback(text: str, keyboard=None) -> None:
        edit_result = None
        if query and query.message:
            edit_result = await safe_edit_message_text(
                context.bot,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=None,
            )
        if edit_result is None and chat_id:
            send_result = await safe_send_message(
                context.bot,
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=None,
            )
            if send_result is None:
                logger.error(
                    "Failed to deliver delete_product feedback",
                    extra={"product_id": product_id, "admin_id": user_id},
                )
    
    if not is_admin(user_id):
        if query:
            await query.answer("❌ Only administrators can delete products.", show_alert=True)
        return
    
    try:
        if query:
            await query.answer("Deleting product...")

        # Get product info before deletion
        product = await db.get_product(product_id)
        
        if not product:
            await _send_delete_feedback("❌ Product not found.")
            return
        
        # Delete from database (channel message will remain)
        deleted = await db.delete_product(product_id)
        
        if deleted:
            invalidate_webapp_caches(f"product {product_id} deleted")
            # Get user's previous page to return to
            catalog_page = await db.get_pagination_state(user_id, "catalog", None)
            back_page = catalog_page or 1
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to catalog", callback_data=f"menu|{back_page}")]
            ])
            await _send_delete_feedback("✅ Product deleted successfully!", keyboard)
            logger.info(
                "Product deleted",
                extra={"product_id": product_id, "admin_id": user_id, **admin_scope},
            )
        else:
            await _send_delete_feedback("❌ Failed to delete product.")
            
    except Exception as e:
        logger.error(
            "Error deleting product",
            extra={"product_id": product_id, "admin_id": user_id, "error": str(e), **admin_scope},
            exc_info=True,
        )
        if query:
            try:
                await query.answer("❌ An error occurred while deleting the product.", show_alert=True)
            except Exception:
                logger.warning("Unable to answer delete callback after exception", exc_info=True)
        await _send_delete_feedback("❌ An error occurred while deleting the product.")


@command_admin_required
async def recategorize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /recategorize command - send categorization notifications for products needing (re)categorization (admin only).
    
    Includes:
    - Products with no category
    - Products with a category but missing subcategory (when subcategories are available for that category)
    """
    # Import at function level to avoid circular imports
    from utils.categories import get_subcategories
    from main import notify_admins_for_categorization
    
    user_id = update.effective_user.id
    
    try:
        # Send initial message
        status_msg = await update.message.reply_text(
            "🔄 Checking for products needing categorization...\n"
            "This may take a moment."
        )
        
        # Get all products
        all_products = await db.get_all_products_for_search()
        
        if not all_products:
            await status_msg.edit_text("📭 No products found.")
            return
        
        # Find uncategorized products (no category OR has category but missing required subcategory)
        uncategorized = []
        for p in all_products:
            # Product has no category at all
            if not p.get("category"):
                uncategorized.append(p)
            # Product has category but no subcategory, and category has subcategories available
            elif p.get("category") and not p.get("subcategory"):
                subcats = get_subcategories(p.get("category"))
                # If category has subcategories defined, this product needs recategorization
                if subcats:
                    uncategorized.append(p)
        
        if not uncategorized:
            await status_msg.edit_text(
                f"✅ All {len(all_products)} products are properly categorized!"
            )
            return
        
        # Send categorization notifications for products needing categorization
        notified = 0
        for product in uncategorized[:10]:  # Limit to 10 at a time to avoid spam
            try:
                await notify_admins_for_categorization(context, product["id"])
                notified += 1
                await asyncio.sleep(0.5)  # Small delay between notifications
            except Exception as e:
                logger.error(f"Failed to notify for product {product['id']}: {e}")
        
        # Send completion message
        categorized_count = len(all_products) - len(uncategorized)
        text = (
            f"📊 **Categorization Status**\n\n"
            f"📦 Total products: {len(all_products)}\n"
            f"✅ Properly categorized: {categorized_count}\n"
            f"❓ Needing categorization: {len(uncategorized)}\n"
            f"📤 Notifications sent: {notified}"
        )
        
        if len(uncategorized) > 10:
            text += f"\n\n⚠️ Only sent notifications for first 10 products.\nRun command again to process more."
        
        try:
            await status_msg.edit_text(text, parse_mode="Markdown")
        except BadRequest as e:
            logger.warning(f"Markdown parse error in recategorize_command, retrying without parse_mode: {e}")
            text_no_md = (
                f"📊 Categorization Status\n\n"
                f"📦 Total products: {len(all_products)}\n"
                f"✅ Properly categorized: {categorized_count}\n"
                f"❓ Needing categorization: {len(uncategorized)}\n"
                f"📤 Notifications sent: {notified}"
            )
            if len(uncategorized) > 10:
                text_no_md += f"\n\n⚠️ Only sent notifications for first 10 products.\nRun command again to process more."
            await status_msg.edit_text(text_no_md)
        logger.info(f"Admin {user_id} triggered recategorization - sent {notified} notifications")
        
    except Exception as e:
        logger.error(f"Error in recategorize command: {e}")
        await update.message.reply_text(
            "❌ An error occurred during recategorization."
        )


@command_admin_required
async def block_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /block command - block a user from using the bot (admin only).
    
    Usage: /block <user_id>
    Example: /block 123456789
    """
    user_id = update.effective_user.id
    
    # Check arguments
    if not context.args or len(context.args) < 1:
        try:
            await update.message.reply_text(
                "❌ **Invalid format**\n\n"
                "**Usage:** `/block <user_id>`\n\n"
                "**Example:** `/block 123456789`\n\n"
                "💡 Tip: Use `/users` to find user IDs",
                parse_mode="Markdown"
            )
        except BadRequest as e:
            logger.warning(f"Markdown parse error in block_command (invalid format), retrying without parse_mode: {e}")
            await update.message.reply_text(
                "❌ Invalid format\n\n"
                "Usage: /block <user_id>\n\n"
                "Example: /block 123456789\n\n"
                "💡 Tip: Use /users to find user IDs"
            )
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Prevent blocking admins
        if is_admin(target_user_id):
            await update.message.reply_text(
                "❌ Cannot block an admin user."
            )
            return
        
        # Block the user (also persists their known IPs to blocked_ips in DB)
        blocked_ips = await db.block_user(target_user_id)
        # Sync in-memory IP block registry so the webapp enforces the block immediately
        for ip in blocked_ips:
            add_permanent_ip_block(ip)
        
        ip_note = f"\n🌐 {len(blocked_ips)} IP address(es) also blocked." if blocked_ips else ""

        try:
            await update.message.reply_text(
                f"✅ **User Blocked**\n\n"
                f"🆔 User ID: `{target_user_id}`\n\n"
                f"This user can no longer use the bot.{ip_note}\n"
                f"Use `/unblock {target_user_id}` to unblock.",
                parse_mode="Markdown"
            )
        except BadRequest as e:
            logger.warning(f"Markdown parse error in block_command (success), retrying without parse_mode: {e}")
            await update.message.reply_text(
                f"✅ User Blocked\n\n"
                f"🆔 User ID: {target_user_id}\n\n"
                f"This user can no longer use the bot.{ip_note}\n"
                f"Use /unblock {target_user_id} to unblock."
            )
        logger.info(f"Admin {user_id} blocked user {target_user_id} (IPs blocked: {blocked_ips})")
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid user ID. Please provide a valid number."
        )
    except Exception as e:
        logger.error(f"Error in block command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while blocking the user."
        )


@command_admin_required
async def unblock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unblock command - unblock a user (admin only).
    
    Usage: /unblock <user_id>
    Example: /unblock 123456789
    """
    user_id = update.effective_user.id
    
    # Check arguments
    if not context.args or len(context.args) < 1:
        try:
            await update.message.reply_text(
                "❌ **Invalid format**\n\n"
                "**Usage:** `/unblock <user_id>`\n\n"
                "**Example:** `/unblock 123456789`",
                parse_mode="Markdown"
            )
        except BadRequest as e:
            logger.warning(f"Markdown parse error in unblock_command (invalid format), retrying without parse_mode: {e}")
            await update.message.reply_text(
                "❌ Invalid format\n\n"
                "Usage: /unblock <user_id>\n\n"
                "Example: /unblock 123456789"
            )
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Unblock the user (also removes their IPs from blocked_ips in DB)
        unblocked_ips = await db.unblock_user(target_user_id)
        # Sync in-memory IP block registry
        for ip in unblocked_ips:
            remove_permanent_ip_block(ip)
        
        try:
            await update.message.reply_text(
                f"✅ **User Unblocked**\n\n"
                f"🆔 User ID: `{target_user_id}`\n\n"
                f"This user can now use the bot again.",
                parse_mode="Markdown"
            )
        except BadRequest as e:
            logger.warning(f"Markdown parse error in unblock_command (success), retrying without parse_mode: {e}")
            await update.message.reply_text(
                f"✅ User Unblocked\n\n"
                f"🆔 User ID: {target_user_id}\n\n"
                f"This user can now use the bot again."
            )
        logger.info(f"Admin {user_id} unblocked user {target_user_id} (IPs unblocked: {unblocked_ips})")
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid user ID. Please provide a valid number."
        )
    except Exception as e:
        logger.error(f"Error in unblock command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while unblocking the user."
        )


@command_admin_required
async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /send command - initiate broadcast to single user (admin only).
    
    New workflow:
    1. Ask for user ID
    2. Wait for user ID input
    3. Ask for message
    4. Wait for message input
    5. Show confirmation
    6. Send message
    """
    user_id = update.effective_user.id
    
    # Start the workflow - ask for user ID
    context.user_data['broadcast_mode'] = 'single_user'
    context.user_data['broadcast_step'] = 'awaiting_user_id'
    
    try:
        await update.message.reply_text(
            "📝 **Broadcast to Single User - Step 1 of 3**\n\n"
            "Please enter the user ID:",
            parse_mode="Markdown"
        )
    except BadRequest as e:
        logger.warning(f"Markdown parse error in send_command, retrying without parse_mode: {e}")
        await update.message.reply_text(
            "📝 Broadcast to Single User - Step 1 of 3\n\n"
            "Please enter the user ID:"
        )
    logger.info(f"Admin {user_id} started single user broadcast workflow")


@command_admin_required
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /broadcast command - initiate broadcast to all users (admin only).
    
    New workflow:
    1. Ask for message
    2. Wait for message input
    3. Show confirmation
    4. Send broadcast
    """
    user_id = update.effective_user.id
    
    # Start the workflow - ask for message
    context.user_data['broadcast_mode'] = 'all_users'
    context.user_data['broadcast_step'] = 'awaiting_message'
    
    try:
        await update.message.reply_text(
            "📝 **Broadcast to All Users - Step 1 of 2**\n\n"
            "Please enter the message you want to broadcast:",
            parse_mode="Markdown"
        )
    except BadRequest as e:
        logger.warning(f"Markdown parse error in broadcast_command, retrying without parse_mode: {e}")
        await update.message.reply_text(
            "📝 Broadcast to All Users - Step 1 of 2\n\n"
            "Please enter the message you want to broadcast:"
        )
    logger.info(f"Admin {user_id} started broadcast to all users workflow")


@command_admin_required
async def handle_broadcast_workflow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the multi-step broadcast workflow for admins."""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    broadcast_mode = context.user_data.get('broadcast_mode')
    broadcast_step = context.user_data.get('broadcast_step')
    
    if broadcast_mode == 'single_user':
        if broadcast_step == 'awaiting_user_id':
            # Step 1: Received user ID, now ask for message
            try:
                target_user_id = int(message_text.strip())
                
                # Check if user exists and is not blocked
                is_blocked = await db.is_user_blocked(target_user_id)
                if is_blocked:
                    try:
                        await update.message.reply_text(
                            f"❌ Cannot send message to blocked user `{target_user_id}`.\n\n"
                            "Broadcast cancelled. Use /send to start again.",
                            parse_mode="Markdown"
                        )
                    except BadRequest as e:
                        logger.warning(f"Markdown parse error in handle_broadcast_workflow (blocked), retrying without parse_mode: {e}")
                        await update.message.reply_text(
                            f"❌ Cannot send message to blocked user {target_user_id}.\n\n"
                            "Broadcast cancelled. Use /send to start again."
                        )
                    context.user_data.clear()
                    return
                
                # Store user ID and move to next step
                context.user_data['target_user_id'] = target_user_id
                context.user_data['broadcast_step'] = 'awaiting_message'
                
                try:
                    await update.message.reply_text(
                        f"📝 **Broadcast to Single User - Step 2 of 3**\n\n"
                        f"🆔 User ID: `{target_user_id}`\n\n"
                        f"Please enter the message you want to send:",
                        parse_mode="Markdown"
                    )
                except BadRequest as e:
                    logger.warning(f"Markdown parse error in handle_broadcast_workflow (step 2), retrying without parse_mode: {e}")
                    await update.message.reply_text(
                        f"📝 Broadcast to Single User - Step 2 of 3\n\n"
                        f"🆔 User ID: {target_user_id}\n\n"
                        f"Please enter the message you want to send:"
                    )
                logger.info(f"Admin {user_id} entered user ID: {target_user_id}")
                
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid user ID. Please enter a valid number.\n\n"
                    "Broadcast cancelled. Use /send to start again."
                )
                context.user_data.clear()
                
        elif broadcast_step == 'awaiting_message':
            # Step 2: Received message, show confirmation
            target_user_id = context.user_data.get('target_user_id')
            context.user_data['broadcast_message'] = message_text
            context.user_data['broadcast_step'] = 'awaiting_confirmation'
            
            # Show confirmation with inline buttons
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm & Send", callback_data=f"broadcast_confirm_single|{target_user_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")]
            ])
            
            # Truncate message before escaping
            truncated_message = message_text[:200]
            if len(message_text) > 200:
                truncated_message += "..."
            
            # Escape message preview for Markdown
            message_preview = escape_markdown_v1(truncated_message)
            
            try:
                await update.message.reply_text(
                    f"✅ **Broadcast to Single User - Confirmation**\n\n"
                    f"🆔 User ID: `{target_user_id}`\n"
                    f"📝 Message: {message_preview}\n\n"
                    f"Do you want to send this message?",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            except BadRequest as e:
                logger.warning(f"Markdown parse error in broadcast confirmation (single), retrying without parse_mode: {e}")
                await update.message.reply_text(
                    f"✅ Broadcast to Single User - Confirmation\n\n"
                    f"🆔 User ID: {target_user_id}\n"
                    f"📝 Message: {truncated_message}\n\n"
                    f"Do you want to send this message?",
                    reply_markup=keyboard
                )
            logger.info(f"Admin {user_id} awaiting confirmation for single user broadcast")
            
    elif broadcast_mode == 'all_users':
        if broadcast_step == 'awaiting_message':
            # Step 1: Received message, show confirmation
            context.user_data['broadcast_message'] = message_text
            context.user_data['broadcast_step'] = 'awaiting_confirmation'
            
            # Get user count
            users = await db.get_all_users()
            user_count = len([u for u in users if not u.get('is_blocked', 0) and not is_admin(u['user_id'])])
            
            # Show confirmation with inline buttons
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm & Broadcast", callback_data="broadcast_confirm_all")],
                [InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")]
            ])
            
            # Truncate message before escaping
            truncated_message = message_text[:200]
            if len(message_text) > 200:
                truncated_message += "..."
            
            # Escape message preview for Markdown
            message_preview = escape_markdown_v1(truncated_message)
            
            try:
                await update.message.reply_text(
                    f"✅ **Broadcast to All Users - Confirmation**\n\n"
                    f"📝 Message: {message_preview}\n"
                    f"📊 Recipients: {user_count} users\n\n"
                    f"Do you want to send this broadcast?",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            except BadRequest as e:
                logger.warning(f"Markdown parse error in broadcast confirmation (all), retrying without parse_mode: {e}")
                await update.message.reply_text(
                    f"✅ Broadcast to All Users - Confirmation\n\n"
                    f"📝 Message: {truncated_message}\n"
                    f"📊 Recipients: {user_count} users\n\n"
                    f"Do you want to send this broadcast?",
                    reply_markup=keyboard
                )
            logger.info(f"Admin {user_id} awaiting confirmation for broadcast to all users")


@command_admin_required
async def setcontact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setcontact command - set the order contact username (admin only)."""
    user_id = update.effective_user.id
    _clear_setlink_state(context)
    
    # Get current contact
    current_contact = await db.get_order_contact()
    
    # Escape the current contact for markdown v1
    escaped_contact = escape_markdown_v1(current_contact) if current_contact else "Not set"
    
    try:
        await update.message.reply_text(
            f"📝 **Set Order Contact**\n\n"
            f"Current contact: {escaped_contact}\n\n"
            f"Please enter the new contact username (e.g., @username):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="setcontact_cancel")]
            ]),
            parse_mode="Markdown"
        )
    except BadRequest as e:
        logger.warning(f"Markdown parse error in setcontact_command, retrying without parse_mode: {e}")
        await update.message.reply_text(
            f"📝 Set Order Contact\n\n"
            f"Current contact: {current_contact if current_contact else 'Not set'}\n\n"
            f"Please enter the new contact username (e.g., @username):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="setcontact_cancel")]
            ])
        )
    
    # Store state for next message
    context.user_data['awaiting_contact'] = True
    logger.info(f"Admin {user_id} initiated setcontact command")


@command_admin_required
async def handle_setcontact_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the contact username input from admin."""
    user_id = update.effective_user.id
    
    if not context.user_data.get('awaiting_contact'):
        return
    
    contact = update.message.text.strip()

    if contact.lower() in {"/cancel", "cancel"}:
        context.user_data['awaiting_contact'] = False
        await update.message.reply_text("Cancelled contact setup.")
        return
    
    # Validate format
    if not contact.startswith('@'):
        await update.message.reply_text(
            "❌ Invalid format. Contact must start with '@' (e.g., @username)\n\n"
            "Please try again or use /setcontact to restart."
        )
        return
    
    # Save to database
    await db.set_order_contact(contact)
    invalidate_webapp_caches("order contact updated")
    
    # Clear state
    context.user_data['awaiting_contact'] = False
    
    # Escape contact for markdown v1
    escaped_contact = escape_markdown_v1(contact)
    
    try:
        await update.message.reply_text(
            f"✅ Order contact updated successfully!\n\n"
            f"New contact: {escaped_contact}\n\n"
            f"All users will now be directed to DM {escaped_contact} to place orders.",
            parse_mode="Markdown"
        )
    except BadRequest as e:
        logger.warning(f"Markdown parse error in handle_setcontact_input, retrying without parse_mode: {e}")
        await update.message.reply_text(
            f"✅ Order contact updated successfully!\n\n"
            f"New contact: {contact}\n\n"
            f"All users will now be directed to DM {contact} to place orders."
        )
    logger.info(f"Admin {user_id} updated order contact to: {contact}")


@command_admin_required
async def setlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open the multi-link contacts manager (admin only)."""
    user_id = update.effective_user.id

    context.user_data["awaiting_contact"] = False
    context.user_data.pop("setlink_pending_label", None)
    context.user_data["awaiting_link"] = False
    context.user_data["setlink_mode"] = "menu"
    links = await db.get_order_links()
    await _send_setlink_menu(update.message, links)
    logger.info(f"Admin {user_id} initiated setlink command")


@command_admin_required
async def handle_setlink_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text replies for the multi-link contacts manager."""
    user_id = update.effective_user.id

    mode = context.user_data.get("setlink_mode")
    if not context.user_data.get('awaiting_link') or mode not in {"label", "url"}:
        return

    value = update.message.text.strip()

    if value.lower() in {"/cancel", "cancel"}:
        _clear_setlink_state(context)
        await update.message.reply_text("Cancelled link setup.", reply_markup=_setlink_menu_keyboard(await db.get_order_links()))
        return

    if mode == "label":
        label = _clean_order_link_label(value)
        if not label:
            await update.message.reply_text(
                "Please send a short button name, for example:\n"
                "• Telegram\n"
                "• Signal\n"
                "• WhatsApp\n\n"
                "Send cancel to stop."
            )
            return
        context.user_data["setlink_pending_label"] = label
        context.user_data["setlink_mode"] = "url"
        await update.message.reply_text(
            f"Nice. Now send the URL for \"{label}\".\n\n"
            "Examples:\n"
            "• https://t.me/username\n"
            "• https://signal.me/#eu/...\n"
            "• https://wa.me/1234567890\n\n"
            "Send cancel to stop.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="setlink|cancel")]])
        )
        return

    label = context.user_data.get("setlink_pending_label")
    if not label:
        context.user_data["setlink_mode"] = "label"
        await update.message.reply_text("Please send the button name first.")
        return

    if not _is_valid_order_link_url(value):
        await update.message.reply_text(
            "That link does not look valid. It must start with http://, https://, or tg://.\n\n"
            "Please send the URL again, or send cancel to stop."
        )
        return

    links = await db.get_order_links()
    links.append({"label": label, "url": value})
    await db.set_order_links(links)
    invalidate_webapp_caches("order link updated")
    _clear_setlink_state(context)

    await update.message.reply_text(
        f"Added extra home screen button:\n\n{label}\n{value}",
        reply_markup=_setlink_menu_keyboard(links),
        disable_web_page_preview=True,
    )
    logger.info("Admin %s added order link label=%r url=%r", user_id, label, value)


def _clean_order_link_label(label: str) -> str:
    label = " ".join(str(label or "").strip().split())
    if not label or len(label) > 40:
        return ""
    return label


def _is_valid_order_link_url(link: str) -> bool:
    link = str(link or "").strip()
    return link.startswith(("http://", "https://", "tg://"))


def _clear_setlink_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_link"] = False
    context.user_data.pop("setlink_mode", None)
    context.user_data.pop("setlink_pending_label", None)


def _format_order_links(links: list[dict]) -> str:
    if not links:
        return "No extra home screen links are set yet."
    lines = []
    for index, item in enumerate(links, start=1):
        lines.append(f"{index}. {item.get('label') or 'Link'}\n   {item.get('url') or ''}")
    return "\n".join(lines)


def _setlink_menu_keyboard(links: list[dict]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("Add link", callback_data="setlink|add")]]
    if links:
        rows.append([InlineKeyboardButton("Remove link", callback_data="setlink|remove_menu")])
        rows.append([InlineKeyboardButton("Clear all", callback_data="setlink|clear_confirm")])
    rows.append([InlineKeyboardButton("Done", callback_data="setlink|done")])
    return InlineKeyboardMarkup(rows)


async def _send_setlink_menu(message, links: list[dict], notice: str = ""):
    text = (
        "Set Extra Home Screen Links\n\n"
        "These buttons appear on the WebApp home screen. They do not change the DM TO ORDER button; /setcontact controls that Telegram contact.\n\n"
        f"{_format_order_links(links)}"
    )
    if notice:
        text = f"{notice}\n\n{text}"
    await message.reply_text(text, reply_markup=_setlink_menu_keyboard(links), disable_web_page_preview=True)


async def _edit_setlink_menu(query, links: list[dict], notice: str = ""):
    text = (
        "Set Extra Home Screen Links\n\n"
        "These buttons appear on the WebApp home screen. They do not change the DM TO ORDER button; /setcontact controls that Telegram contact.\n\n"
        f"{_format_order_links(links)}"
    )
    if notice:
        text = f"{notice}\n\n{text}"
    try:
        await query.edit_message_text(text, reply_markup=_setlink_menu_keyboard(links), disable_web_page_preview=True)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def handle_setlink_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, query, user_id: int, parts: list[str]):
    """Handle /setlink inline button actions."""
    if not is_admin(user_id):
        await query.answer("Admins only.", show_alert=True)
        return

    action = parts[1] if len(parts) > 1 else "menu"
    links = await db.get_order_links()

    if action == "add":
        context.user_data["awaiting_link"] = True
        context.user_data["setlink_mode"] = "label"
        context.user_data.pop("setlink_pending_label", None)
        await query.message.reply_text(
            "Send the button name for this link.\n\n"
            "Examples:\n"
            "• Telegram\n"
            "• Signal\n"
            "• WhatsApp\n\n"
            "Send cancel to stop.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="setlink|cancel")]])
        )
        return

    if action == "remove_menu":
        if not links:
            await _edit_setlink_menu(query, links, "There are no links to remove.")
            return
        rows = [
            [InlineKeyboardButton(f"Remove {item.get('label') or f'Link {index + 1}'}", callback_data=f"setlink|remove|{index}")]
            for index, item in enumerate(links)
        ]
        rows.append([InlineKeyboardButton("Back", callback_data="setlink|menu")])
        await query.edit_message_text(
            "Choose the home screen link to remove:",
            reply_markup=InlineKeyboardMarkup(rows),
            disable_web_page_preview=True,
        )
        return

    if action == "remove" and len(parts) > 2:
        try:
            index = int(parts[2])
        except ValueError:
            index = -1
        if 0 <= index < len(links):
            removed = links.pop(index)
            await db.set_order_links(links)
            invalidate_webapp_caches("order link removed")
            await _edit_setlink_menu(query, links, f"Removed: {removed.get('label') or 'Link'}")
            logger.info("Admin %s removed order link label=%r", user_id, removed.get("label"))
        else:
            await _edit_setlink_menu(query, links, "That link no longer exists.")
        return

    if action == "clear_confirm":
        if not links:
            await _edit_setlink_menu(query, links, "There are no links to clear.")
            return
        await query.edit_message_text(
            "Clear all extra home screen links?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Yes, clear all", callback_data="setlink|clear_yes")],
                [InlineKeyboardButton("Back", callback_data="setlink|menu")],
            ]),
        )
        return

    if action == "clear_yes":
        await db.set_order_links([])
        invalidate_webapp_caches("order links cleared")
        _clear_setlink_state(context)
        await _edit_setlink_menu(query, [], "All contact links cleared.")
        logger.info("Admin %s cleared all order links", user_id)
        return

    if action in {"cancel", "done"}:
        _clear_setlink_state(context)
        if action == "done":
            await _edit_setlink_menu(query, links, "Saved.")
        else:
            await query.message.reply_text("Cancelled link setup.", reply_markup=_setlink_menu_keyboard(links))
        return

    await _edit_setlink_menu(query, links)


@command_admin_required
async def clearcache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clearcache command - clear the file ID cache (admin only)."""
    user_id = update.effective_user.id
    
    try:
        # Import the cache clearing function
        from utils.helpers import clear_file_id_cache
        
        # Clear the database cache
        deleted_count = await clear_file_id_cache()

        # Also clear in-memory caches in webhook_server
        try:
            from webhook_server import clear_in_memory_caches
            cleared_memory = clear_in_memory_caches()
            memory_msg = f"\nCleared {cleared_memory} in-memory cache entries."
        except ImportError:
            memory_msg = ""
        
        try:
            await update.message.reply_text(
                f"✅ **File ID cache cleared successfully!**\n\n"
                f"Removed {deleted_count} database cache entries.{memory_msg}\n"
                f"Media will be re-fetched on next access.\n"
                f"Persistent media metadata and block records were preserved.",
                parse_mode="Markdown"
            )
        except BadRequest as e:
            logger.warning(f"Markdown parse error in clearcache_command, retrying without parse_mode: {e}")
            await update.message.reply_text(
                f"✅ File ID cache cleared successfully!\n\n"
                f"Removed {deleted_count} database cache entries.{memory_msg}\n"
                f"Media will be re-fetched on next access.\n"
                f"Persistent media metadata and block records were preserved."
            )
        logger.info(f"Admin {user_id} cleared the file ID cache ({deleted_count} entries)")
        
    except Exception as e:
        logger.error(f"Error in clearcache command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while clearing the cache."
        )


@command_admin_required
async def botusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /botusers command - view users grouped by bot username (admin only)."""
    try:
        keyboard, message, message_no_md, has_bots = await build_botusers_selection_menu()

        if not has_bots:
            try:
                await update.message.reply_text(
                    "📊 **Bot Users Summary**\n\n"
                    "No active bot instances are loaded right now.",
                    parse_mode="Markdown"
                )
            except BadRequest as e:
                logger.warning(f"Markdown parse error in botusers_command (no bots), retrying without parse_mode: {e}")
                await update.message.reply_text(
                    "📊 Bot Users Summary\n\n"
                    "No active bot instances are loaded right now."
                )
            return

        try:
            await update.message.reply_text(
                message,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except BadRequest as e:
            logger.warning(f"Markdown parse error in botusers_command, retrying without parse_mode: {e}")
            await update.message.reply_text(
                message_no_md,
                reply_markup=keyboard
            )
        
    except Exception as e:
        logger.error(f"Error in botusers command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while retrieving bot users."
        )


async def show_bot_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_username: str, page: int = 1):
    """Display users for a specific bot with delete options."""
    try:
        query = update.callback_query
        await query.answer()
        
        # Pagination settings
        per_page = 10
        offset = (page - 1) * per_page
        
        # Get users for this bot using the new paginated method
        total_count, users = await db.get_users_by_bot_paginated(bot_username, limit=per_page, offset=offset)
        
        if bot_username == "_untracked_":
            display_name = "Untracked Users"
        else:
            display_name = f"@{bot_username}"
        
        if not users:
            try:
                await query.edit_message_text(
                    f"📊 **{display_name}**\n\n"
                    f"No users found.",
                    parse_mode="Markdown"
                )
            except BadRequest as e:
                logger.warning(f"Markdown parse error in show_bot_users_page (no users), retrying without parse_mode: {e}")
                await query.edit_message_text(
                    f"📊 {display_name}\n\n"
                    f"No users found."
                )
            return
        
        # Calculate total pages
        total_pages = (total_count + per_page - 1) // per_page
        
        # Build user list message
        message = f"📊 **{display_name}** (Page {page}/{total_pages})\n"
        message += f"Total: {total_count} users\n\n"
        
        keyboard_buttons = []
        
        for user in users:
            user_id = user["user_id"]
            username = user.get("username") or "N/A"
            first_name = user.get("first_name") or "Unknown"
            last_name = user.get("last_name") or ""
            name = f"{first_name} {last_name}".strip()
            
            # Escape user-provided strings for Markdown
            escaped_username = escape_markdown_v1(username) if username != "N/A" else "N/A"
            escaped_name = escape_markdown_v1(name)
            
            notif_status = "🔔" if user.get("notifications_enabled", 0) else "🔕"
            block_status = " [BLOCKED]" if user.get("is_blocked", 0) else ""
            
            message += f"• ID: `{user_id}`\n"
            message += f"  User: @{escaped_username if username != 'N/A' else 'none'}\n"
            message += f"  Name: {escaped_name}\n"
            message += f"  Status: {notif_status}{block_status}\n"
            
            # Determine delete button label - show username or name instead of user_id
            if username != "N/A":
                delete_label = f"🗑️ Delete @{username}"
            elif name:
                delete_label = f"🗑️ Delete {name}"
            else:
                delete_label = f"🗑️ Delete {user_id}"
            
            # Add delete button for each user
            keyboard_buttons.append([
                InlineKeyboardButton(
                    delete_label,
                    callback_data=f"deleteuser|{user_id}|{bot_username}|{page}"
                )
            ])
            
            message += "\n"
        
        # Add bulk delete button
        keyboard_buttons.append([
            InlineKeyboardButton(
                f"⚠️ Delete All {total_count} Users from {display_name}",
                callback_data=f"deleteallbot|{bot_username}"
            )
        ])
        
        # Add pagination buttons
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"viewbotusers|{bot_username}|{page-1}"))
        nav_buttons.append(InlineKeyboardButton("🔙 Back to Bots", callback_data="backto_botlist"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"viewbotusers|{bot_username}|{page+1}"))
        
        keyboard_buttons.append(nav_buttons)
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        try:
            await safe_edit_message_text(
                context.bot,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=message,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except BadRequest as e:
            logger.warning(f"Markdown parse error in show_bot_users_page, retrying without parse_mode: {e}")
            await safe_edit_message_text(
                context.bot,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=message,
                reply_markup=keyboard
            )
        
    except Exception as e:
        logger.error(f"Error showing bot users page: {e}", exc_info=True)
        await query.answer("An error occurred", show_alert=True)


@command_admin_required
async def prunebots_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /prunebots command - prune users from inactive/deleted bots (admin only)."""
    user_id = update.effective_user.id
    
    try:
        # Get active bot usernames from webhook server
        from webhook_server import get_bot_usernames
        active_bots = get_bot_usernames()
        
        if not active_bots:
            try:
                await update.message.reply_text(
                    "⚠️ **Warning**: No active bots found!\n\n"
                    "Cannot proceed with pruning.",
                    parse_mode="Markdown"
                )
            except BadRequest as e:
                logger.warning(f"Markdown parse error in prunebots_command (no bots), retrying without parse_mode: {e}")
                await update.message.reply_text(
                    "⚠️ Warning: No active bots found!\n\n"
                    "Cannot proceed with pruning."
                )
            return
        
        # Check if this is a dry run (default) or actual pruning
        dry_run = True
        if context.args and context.args[0].lower() == "confirm":
            dry_run = False
        
        # Perform pruning
        stats = await db.prune_inactive_bot_users(active_bots, dry_run=dry_run)
        
        if dry_run:
            if stats['users'] == 0:
                # Escape bot usernames for Markdown
                escaped_bots = "\n• ".join(f"@{escape_markdown_v1(bot)}" for bot in sorted(active_bots))
                try:
                    await update.message.reply_text(
                        "✅ **All Good!**\n\n"
                        f"No users found associated with inactive bots.\n"
                        f"Active bots: {len(active_bots)}\n"
                        f"• {escaped_bots}",
                        parse_mode="Markdown"
                    )
                except BadRequest as e:
                    logger.warning(f"Markdown parse error in prunebots_command (all good), retrying without parse_mode: {e}")
                    await update.message.reply_text(
                        "✅ All Good!\n\n"
                        f"No users found associated with inactive bots.\n"
                        f"Active bots: {len(active_bots)}\n"
                        f"• " + "\n• ".join(f"@{bot}" for bot in sorted(active_bots))
                    )
            else:
                # Create confirmation keyboard
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Yes, Prune Now", callback_data="confirm_prunebots")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_prunebots")]
                ])
                
                # Escape bot usernames for Markdown
                escaped_bots = "\n• ".join(f"@{escape_markdown_v1(bot)}" for bot in sorted(active_bots))
                try:
                    await update.message.reply_text(
                        f"🔍 **Inactive Bot Users Found**\n\n"
                        f"The following would be deleted:\n"
                        f"• Users: {stats['users']}\n"
                        f"• Products: {stats['products']}\n"
                        f"• Pending Notifications: {stats['notifications']}\n"
                        f"• Custom Messages: {stats['custom_messages']}\n\n"
                        f"Active bots ({len(active_bots)}):\n"
                        f"• {escaped_bots}\n\n"
                        f"⚠️ This action cannot be undone!\n\n"
                        f"To confirm, use: `/prunebots confirm`",
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                except BadRequest as e:
                    logger.warning(f"Markdown parse error in prunebots_command (preview), retrying without parse_mode: {e}")
                    await update.message.reply_text(
                        f"🔍 Inactive Bot Users Found\n\n"
                        f"The following would be deleted:\n"
                        f"• Users: {stats['users']}\n"
                        f"• Products: {stats['products']}\n"
                        f"• Pending Notifications: {stats['notifications']}\n"
                        f"• Custom Messages: {stats['custom_messages']}\n\n"
                        f"Active bots ({len(active_bots)}):\n"
                        f"• " + "\n• ".join(f"@{bot}" for bot in sorted(active_bots)) + "\n\n"
                        f"⚠️ This action cannot be undone!\n\n"
                        f"To confirm, use: /prunebots confirm",
                        reply_markup=keyboard
                    )
                logger.info(f"Admin {user_id} previewed bot pruning: {stats}")
        else:
            if stats['users'] == 0:
                try:
                    await update.message.reply_text(
                        "✅ **No Changes Made**\n\n"
                        "No users found associated with inactive bots.",
                        parse_mode="Markdown"
                    )
                except BadRequest as e:
                    logger.warning(f"Markdown parse error in prunebots_command (no changes), retrying without parse_mode: {e}")
                    await update.message.reply_text(
                        "✅ No Changes Made\n\n"
                        "No users found associated with inactive bots."
                    )
            else:
                # Escape bot usernames for Markdown
                escaped_bots = "\n• ".join(f"@{escape_markdown_v1(bot)}" for bot in sorted(active_bots))
                try:
                    await update.message.reply_text(
                        f"✅ **Pruning Complete**\n\n"
                        f"Deleted:\n"
                        f"• Users: {stats['users']}\n"
                        f"• Products: {stats['products']}\n"
                        f"• Pending Notifications: {stats['notifications']}\n"
                        f"• Custom Messages: {stats['custom_messages']}\n\n"
                        f"Remaining active bots ({len(active_bots)}):\n"
                        f"• {escaped_bots}",
                        parse_mode="Markdown"
                    )
                except BadRequest as e:
                    logger.warning(f"Markdown parse error in prunebots_command (complete), retrying without parse_mode: {e}")
                    await update.message.reply_text(
                        f"✅ Pruning Complete\n\n"
                        f"Deleted:\n"
                        f"• Users: {stats['users']}\n"
                        f"• Products: {stats['products']}\n"
                        f"• Pending Notifications: {stats['notifications']}\n"
                        f"• Custom Messages: {stats['custom_messages']}\n\n"
                        f"Remaining active bots ({len(active_bots)}):\n"
                        f"• " + "\n• ".join(f"@{bot}" for bot in sorted(active_bots))
                    )
                logger.info(f"Admin {user_id} pruned inactive bot users: {stats}")
        
    except Exception as e:
        logger.error(f"Error in prunebots command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while pruning bot users."
        )


@command_admin_required
async def reinitbots_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reinitbots command — admin-only bot reinitialisation.

    Usage:
      /reinitbots           — show confirmation instructions
      /reinitbots dryrun    — show current & previous webhook secret prefixes (no changes)
      /reinitbots confirm   — stop and recreate all bot Applications

    This is useful when a Telegram bot token has been replaced or rotated.
    The reinitialisation reads the current Config.BOT_TOKENS values and calls
    setup_application() for each token so new webhooks are registered.
    """
    args = context.args or []
    subcommand = args[0].lower() if args else ""

    if subcommand == "dryrun":
        # Show current and previous webhook secret prefixes per bot index.
        # Deferred import to avoid circular import at module load time.
        try:
            from webhook_server import _webhook_secret_history, _runtime_index_to_registry_id
            from configs.config import Config
            from database import Database
        except ImportError as e:
            await update.message.reply_text(f"❌ Could not import webhook_server: {e}")
            return

        tokens = getattr(Config, "BOT_TOKENS", []) or []
        if not tokens:
            await update.message.reply_text("⚠️ No BOT_TOKENS configured.")
            return

        lines = ["🔍 *Webhook Secret Dry-Run* (no changes made)\n"]
        registry_db = Database()
        for idx, token in enumerate(tokens):
            registry_id = _runtime_index_to_registry_id.get(idx)
            registry_row = await registry_db.get_bot_registry_by_id(registry_id) if registry_id else None
            current_secret = (registry_row or {}).get("webhook_secret") or ""
            current_prefix = current_secret[:8]

            history = _webhook_secret_history.get(registry_id, [None, None])
            prev_secret = history[0]  # [previous, current]; always a list due to default
            prev_prefix = prev_secret[:8] if prev_secret else "none"

            lines.append(
                f"Bot {idx}: registry=`{registry_id or 'unavailable'}` | current prefix=`{current_prefix or 'unavailable'}` | prev prefix=`{prev_prefix}`"
            )
        try:
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("\n".join(lines))
        return

    if subcommand == "confirm":
        # Acknowledge immediately; the reinit runs in the background so that
        # the Telegram reply is returned before the current bot app is restarted.
        await update.message.reply_text(
            "🔄 Bot reinitialisation scheduled. All bot instances will be stopped "
            "and recreated from the current token configuration. Check service logs "
            "for progress and any errors."
        )
        try:
            # Deferred import to avoid circular import at module load time.
            from webhook_server import reinitialize_bots

            async def _run_reinit():
                try:
                    # Pass None for fastapi_app: the Telegram handler doesn't have a
                    # reference to the FastAPI app instance.  reinitialize_bots will
                    # update _bot_applications (the module-level global), but
                    # app.state.bot_apps won't be refreshed.  On the next webhook
                    # request FastAPI will still use the old state reference.
                    # Operators can restart the service to fully synchronize state,
                    # or call reinitialize_bots with the FastAPI app from a custom
                    # startup/admin HTTP endpoint.
                    result = await reinitialize_bots(None)
                    logger.info("Background reinitialisation complete: %s", result)
                except Exception:
                    logger.exception("Background reinitialisation failed")

            asyncio.create_task(_run_reinit())
        except ImportError:
            # Running in polling mode — reinitialize_bots is not meaningful outside
            # webhook mode, but we won't crash the command.
            logger.warning("reinitbots_command: webhook_server.reinitialize_bots not importable (polling mode?)")
            await update.message.reply_text(
                "⚠️ Reinitialisation is only supported in webhook mode. "
                "No action was taken."
            )
        except Exception as exc:
            logger.error("reinitbots_command: failed to schedule reinit: %s", exc, exc_info=True)
            await update.message.reply_text(
                f"❌ Failed to schedule reinitialisation: {exc}"
            )
        return

    # No args or unexpected args — show instructions.
    await update.message.reply_text(
        "⚠️ *Bot Reinitialisation*\n\n"
        "This command stops all running bot instances and recreates them from "
        "the current `BOT_TOKENS` configuration.  Use it after replacing a "
        "Telegram bot token.\n\n"
        "• `/reinitbots dryrun` — show expected webhook secret prefixes (no changes)\n"
        "• `/reinitbots confirm` — stop and recreate all bots",
        parse_mode="Markdown",
    )



# ---------------------------------------------------------------------------
# Broadcast management commands
# ---------------------------------------------------------------------------

async def _is_chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True if the command sender is an administrator of the current chat."""
    try:
        member = await context.bot.get_chat_member(
            update.effective_chat.id, update.effective_user.id
        )
        return member.status in (
            member.ADMINISTRATOR,
            member.OWNER,
        )
    except Exception as e:
        logger.warning(f"Could not verify chat admin status: {e}")
        return False


async def enable_broadcasts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /enable_broadcasts – opt the current group/channel in for broadcasts.

    Only works in non-private chats.  Any member of the group or channel can
    run this command.
    """
    chat = update.effective_chat
    user_id = update.effective_user.id

    if chat.type == "private":
        await update.message.reply_text(
            "ℹ️ This command can only be used inside a group or channel."
        )
        return

    bot_username = context.bot.username
    chat_id = chat.id
    chat_type = chat.type
    title = chat.title or str(chat_id)

    # Ensure the chat is registered (handles the case where the bot was added
    # before this feature existed and no my_chat_member update was processed)
    await db.upsert_broadcast_chat(
        bot_username=bot_username,
        chat_id=chat_id,
        chat_type=chat_type,
        title=title,
        added_by_user_id=user_id,
    )

    updated = await db.set_broadcast_chat_enabled(bot_username, chat_id, True)
    if updated:
        logger.info(
            f"enable_broadcasts: @{bot_username} enabled broadcasting to "
            f"{chat_type} '{title}' (chat_id={chat_id}) by user {user_id}"
        )
        await update.message.reply_text(
            f"✅ Broadcasts enabled for this {chat_type}.\n"
            "New product notifications will be posted here."
        )
    else:
        await update.message.reply_text(
            "⚠️ Could not enable broadcasts. Please try again later."
        )


async def disable_broadcasts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /disable_broadcasts – opt the current group/channel out of broadcasts.

    Only works in non-private chats.  Any member of the group or channel can
    run this command.
    """
    chat = update.effective_chat
    user_id = update.effective_user.id

    if chat.type == "private":
        await update.message.reply_text(
            "ℹ️ This command can only be used inside a group or channel."
        )
        return

    bot_username = context.bot.username
    chat_id = chat.id
    title = chat.title or str(chat_id)

    updated = await db.set_broadcast_chat_enabled(bot_username, chat_id, False)
    if updated:
        logger.info(
            f"disable_broadcasts: @{bot_username} disabled broadcasting to "
            f"'{title}' (chat_id={chat_id}) by user {user_id}"
        )
        await update.message.reply_text(
            "🔕 Broadcasts disabled for this chat."
        )
    else:
        await update.message.reply_text(
            "ℹ️ This chat was not registered as a broadcast destination."
        )


@command_admin_required
async def list_broadcasts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list_broadcasts – show all broadcast destinations for this bot (admin only)."""
    bot_username = context.bot.username
    chats = await db.get_broadcast_chats(bot_username)

    if not chats:
        await update.message.reply_text(
            f"📭 No broadcast destinations registered for @{bot_username}.\n\n"
            "Add the bot to a group or channel with admin rights and then run "
            "/enable_broadcasts there."
        )
        return

    lines = [f"📡 *Broadcast destinations for @{bot_username}*\n"]
    for entry in chats:
        status = "✅" if entry["enabled"] else "⭕"
        title = entry["title"] or str(entry["chat_id"])
        lines.append(
            f"{status} `{entry['chat_id']}` – {entry['chat_type']} – {title}"
        )

    lines.append(
        "\n✅ = enabled  ⭕ = disabled\n"
        "Run /enable_broadcasts or /disable_broadcasts inside the chat to change."
    )

    try:
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception:
        # Fallback without markdown
        plain = "\n".join(lines).replace("*", "").replace("`", "")
        await update.message.reply_text(plain)
