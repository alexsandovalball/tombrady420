"""
Inline query handler for Telegram Inline Mode.

Supports all bot instances (multi-bot deployment) without hardcoded usernames.
Bot username is derived from context.bot.username at runtime.
"""
import asyncio
import inspect
import logging
import time
from typing import List, Dict, Any, Optional, Tuple

from telegram import (
    Update,
    InlineQueryResult,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedVideo,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import ContextTypes

from database import Database
from configs.config import Config
from utils.deeplinks import build_bot_product_url, build_webapp_product_url
from utils.fuzzy_search import fuzzy_search_products
from utils.helpers import get_bot_username, escape_markdown_v1
from utils.media_resolver import MediaResolver

logger = logging.getLogger(__name__)
db = Database()

# ---------------------------------------------------------------------------
# Short-lived in-memory cache for get_all_products_for_search()
# This reduces SQLite load on rapid inline queries.
# ---------------------------------------------------------------------------
_PRODUCT_CACHE_TTL = 60  # seconds
_product_cache: Optional[Tuple[float, List[Dict[str, Any]]]] = None
_product_cache_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Per-user debouncing and task cancellation
# Maps user_id -> (task, timestamp) for in-flight inline query tasks
# ---------------------------------------------------------------------------
_DEBOUNCE_DELAY = 0.5  # 500ms debounce
_user_query_tasks: Dict[int, asyncio.Task] = {}
_user_query_tasks_lock = asyncio.Lock()

# Maximum number of inline results to return to the client.
# Centralised here so both the per-page slicer and the final answer cap
# stay in sync without touching helper builders individually.
INLINE_PAGE_SIZE = 10
_INLINE_PAGE_SIZE = INLINE_PAGE_SIZE  # Internal alias kept for backward compat


async def _get_cached_products() -> List[Dict[str, Any]]:
    """Return cached product list, refreshing if stale."""
    global _product_cache
    now = time.monotonic()
    async with _product_cache_lock:
        if _product_cache is not None and (now - _product_cache[0]) < _PRODUCT_CACHE_TTL:
            return _product_cache[1]
        products = await db.get_all_products_for_search()
        _product_cache = (now, products)
        return products


# ---------------------------------------------------------------------------
# Result builders
# ---------------------------------------------------------------------------

def _build_product_url(product_id: int, bot_username: str) -> Optional[str]:
    """Return the bot deep link for a product.

    ``bot_deep_link`` always points to the Telegram bot via ``t.me``.
    """
    return build_bot_product_url(product_id, bot_username)


def _build_product_webapp_url(product_id: int) -> Optional[str]:
    """Return the WebApp deep link for a product."""
    return build_webapp_product_url(Config.WEB_APP_URL, product_id)


async def _build_product_article(
    product: Dict[str, Any],
    bot_username: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> InlineQueryResult:
    """Build an inline result for a single product.

    For photo products, returns an ``InlineQueryResultCachedPhoto`` so that
    the image thumbnail is rendered directly in the inline picker.  For video
    products, returns an ``InlineQueryResultCachedVideo`` so the video preview
    appears in the picker.  If the product was indexed by a different bot
    instance (multi-bot deployment) we consult the persistent DB cache for a
    bot-specific file_id; if none is available we fall back to
    ``InlineQueryResultArticle`` (with a ``thumbnail_url`` when the storefront
    domain is configured).

    For products without media we always return an ``InlineQueryResultArticle``.
    """
    product_id = product.get("id")
    caption = product.get("caption") or ""
    category = product.get("category") or "Uncategorized"
    file_type = product.get("file_type") or ""

    # Truncate caption for display
    title = caption[:60] + "…" if len(caption) > 60 else caption
    if not title:
        title = f"Product #{product_id}"

    # Escape dynamic content for safe Markdown rendering
    title_escaped = escape_markdown_v1(title)
    category_escaped = escape_markdown_v1(category)

    description = f"Category: {category}"

    # Build the inline buttons using the correct domain for deep links
    bot_deep_link = _build_product_url(product_id, bot_username)
    webapp_deep_link = _build_product_webapp_url(product_id)

    buttons = []
    if bot_deep_link:
        buttons.append(InlineKeyboardButton("🔗 View in Bot", url=bot_deep_link))
    if webapp_deep_link:
        buttons.append(InlineKeyboardButton("🛍️ View in WebApp", url=webapp_deep_link))
    reply_markup = InlineKeyboardMarkup([buttons]) if buttons else None

    # Caption / message text shown when the result is shared (no ID shown to users)
    caption_text = (
        f"📦 *{title_escaped}*\n"
        f"📂 {category_escaped}"
    )

    # --- Attempt rich media result for photo/video products ---
    if file_type in ("photo", "video"):
        resolved_file_id = await _resolve_file_id_for_inline(product, context)
        if resolved_file_id:
            if file_type == "photo":
                return InlineQueryResultCachedPhoto(
                    id=f"product_{product_id}",
                    photo_file_id=resolved_file_id,
                    title=title,
                    description=description,
                    caption=caption_text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )
            else:  # video
                return InlineQueryResultCachedVideo(
                    id=f"product_{product_id}",
                    video_file_id=resolved_file_id,
                    title=title,
                    description=description,
                    caption=caption_text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )

    # --- Fallback: article with thumbnail from storefront ---
    # Use the storefront /api/catalog URL for the thumbnail so the image loads
    # quickly for the (up to INLINE_PAGE_SIZE) results shown.
    thumbnail_url: Optional[str] = None
    storefront_domain = Config.STOREFRONT_DOMAIN or (Config.WEBHOOK_URL or "").rstrip('/')
    if storefront_domain and file_type in ("photo", "video"):
        thumbnail_url = f"{storefront_domain.rstrip('/')}/api/catalog/{product_id}/media/0"

    return InlineQueryResultArticle(
        id=f"product_{product_id}",
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            message_text=caption_text,
            parse_mode="Markdown",
        ),
        reply_markup=reply_markup,
        thumbnail_url=thumbnail_url,
    )


async def _resolve_file_id_for_inline(
    product: Dict[str, Any],
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """Return a file_id usable by the current bot for the given product.

    Checks the persistent DB cache first; on a cache miss, triggers a
    message-forward via ``get_bot_specific_file_id`` to populate the cache
    and returns the resolved file_id.  Returns ``None`` only if forwarding
    explicitly fails or the product has no media.
    """
    if not product.get("file_id"):
        logger.debug(
            "No raw_file_id for product %s – will fall back to article result",
            product.get("id"),
        )
        return None

    current_bot = (context.bot.username or "").lower()
    result = await MediaResolver(context.bot, db).resolve_media_for_bot(product, current_bot, 0)
    if result.success:
        return result.file_id

    return None


def _build_webapp_article(bot_username: str, web_app_url: str) -> InlineQueryResultArticle:
    """Return an 'Open Shop / Bot' entry."""
    deep_link = f"https://t.me/{bot_username}?start=webapp"
    return InlineQueryResultArticle(
        id="open_webapp",
        title="🛍️ Open Shop",
        description="Open the full product catalog in the app",
        input_message_content=InputTextMessageContent(
            message_text=f"🛍️ [Open the Shop]({deep_link})",
            parse_mode="Markdown",
        ),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🤖 Open in Bot", url=deep_link),
        ]]),
    )


async def _build_new_arrivals_articles(
    products: List[Dict[str, Any]],
    bot_username: str,
    context: ContextTypes.DEFAULT_TYPE,
    limit: int = 5,
) -> List[InlineQueryResult]:
    """Return newest products as inline results."""
    # Products are sorted by created_at DESC in DB, so first items are newest
    newest = products[:limit]
    return list(await asyncio.gather(*[_build_product_article(p, bot_username, context) for p in newest]))


async def _build_popular_articles(
    products: List[Dict[str, Any]],
    bot_username: str,
    context: ContextTypes.DEFAULT_TYPE,
    limit: int = 5,
) -> List[InlineQueryResult]:
    """Return trending / most-recently-added products as inline results."""
    # Simple heuristic: products are already sorted by created_at DESC in the DB query
    popular = products[:limit]
    return list(await asyncio.gather(*[_build_product_article(p, bot_username, context) for p in popular]))


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming inline queries with debouncing and task cancellation."""
    inline_query = update.inline_query
    if not inline_query:
        return

    user_id = inline_query.from_user.id

    # Cancel any previous in-flight query task for this user
    async with _user_query_tasks_lock:
        old_task = _user_query_tasks.get(user_id)
        if old_task and not old_task.done():
            old_task.cancel()
            logger.debug("Cancelled previous inline query task for user %s", user_id)

        # Create new task for this query
        task = asyncio.create_task(_process_inline_query(update, context))
        _user_query_tasks[user_id] = task

    # Wait for the task to complete (or be cancelled)
    try:
        await task
    except asyncio.CancelledError:
        logger.debug("Inline query task cancelled for user %s", user_id)
    finally:
        # Clean up completed task
        async with _user_query_tasks_lock:
            if _user_query_tasks.get(user_id) == task:
                del _user_query_tasks[user_id]


async def _process_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process an inline query with debouncing."""
    inline_query = update.inline_query
    if not inline_query:
        return

    # Debounce: wait a bit to see if user is still typing
    await asyncio.sleep(_DEBOUNCE_DELAY)

    start_time = time.monotonic()
    query = (inline_query.query or "").strip()
    user_id = inline_query.from_user.id
    bot_username = get_bot_username(context) or ""

    logger.info(
        "Inline query from user %s via bot @%s: %r",
        user_id,
        bot_username,
        query,
    )

    try:
        if query:
            results = await _handle_query_search(query, bot_username, context)
            next_offset = ""
            is_empty = False
        else:
            results = await _handle_empty_query(bot_username, context)
            next_offset = ""
            is_empty = True

        # Calculate latency
        latency_ms = int((time.monotonic() - start_time) * 1000)
        results_count = len(results)

        # Log to analytics with performance metrics
        try:
            await db.log_inline_query(
                user_id=user_id,
                query=query,
                bot_username=bot_username,
                latency_ms=latency_ms,
                results_count=results_count,
                is_empty_query=is_empty,
            )
        except Exception as exc:
            logger.warning("Failed to log inline query: %s", exc)

        # switch_pm drives users into the bot for full browsing.
        # Conditionally pass switch_pm_* only if the installed version supports them.
        _answer_params = inspect.signature(inline_query.answer).parameters
        _extra: Dict[str, Any] = {}
        if "switch_pm_text" in _answer_params and "switch_pm_parameter" in _answer_params:
            _extra["switch_pm_text"] = "📋 Open Full Catalog"
            _extra["switch_pm_parameter"] = "inline"

        # Enforce the global page-size cap before answering.
        # This prevents helper builders (empty query, category browse, etc.)
        # from accidentally sending more results than the configured limit.
        # Verify locally: perform an inline query that used to return many rows
        # and confirm the picker shows at most INLINE_PAGE_SIZE entries.
        total_results = len(results)
        if total_results > INLINE_PAGE_SIZE:
            logger.debug(
                "Trimming inline results from %d to %d (INLINE_PAGE_SIZE cap)",
                total_results,
                INLINE_PAGE_SIZE,
            )
            results = results[:INLINE_PAGE_SIZE]
            next_offset = ""  # pagination disabled when cap is enforced; implement robust offsets in a follow-up if needed

        # Cache settings:
        # - is_personal=False: Results are identical for all users (no per-user personalization)
        # - cache_time=30: Cache for 30 seconds to balance freshness and load
        # NOTE: If adding language-based strings or per-user features, set is_personal=True
        await inline_query.answer(
            results=results,
            cache_time=30,
            is_personal=False,
            next_offset=next_offset,
            **_extra,
        )
    except Exception as exc:
        logger.error("Error answering inline query: %s", exc, exc_info=True)
        # Answer with empty results to avoid Telegram timeout
        try:
            await inline_query.answer(results=[], cache_time=5)
        except Exception:
            pass


async def _handle_empty_query(bot_username: str, context: ContextTypes.DEFAULT_TYPE) -> List[InlineQueryResult]:
    """Return default results for an empty inline query with enhanced discoverability."""
    results: List[InlineQueryResult] = []

    # Keep inline mode useful without showing the removed browse/tips/category articles.
    web_app_url = Config.WEB_APP_URL
    if web_app_url and bot_username:
        results.append(_build_webapp_article(bot_username, web_app_url))

    try:
        products = await _get_cached_products()
        # Add new arrivals (first 3 products)
        results.extend(await _build_new_arrivals_articles(products, bot_username, context, limit=3))
        # Add popular/trending (next 3 products – skip new-arrivals slice to avoid duplicate IDs)
        results.extend(await _build_popular_articles(products[3:], bot_username, context, limit=3))
    except Exception as exc:
        logger.warning("Could not fetch products for inline empty query: %s", exc)

    return results


async def _handle_query_search(
    query: str,
    bot_username: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> List[InlineQueryResult]:
    """Return fuzzy-search results for a non-empty query (no pagination).

    Returns at most ``INLINE_PAGE_SIZE`` results.  Pagination is disabled.

    Supports query prefixes:
    - cat:name or category:name -> search by category
    - id:123 -> find product by ID
    - new -> show new arrivals
    - popular -> show popular items
    """
    try:
        products = await _get_cached_products()

        # Check for query prefixes
        query_lower = query.lower().strip()

        # Category search: cat:name or category:name
        if query_lower.startswith("cat:") or query_lower.startswith("category:"):
            category_query = query_lower.split(":", 1)[1].strip() if ":" in query_lower else ""
            if category_query:
                matched = [
                    p for p in products
                    if category_query in (p.get("category") or "").lower()
                ]
            else:
                matched = []

        # ID search: id:123
        elif query_lower.startswith("id:"):
            product_id_str = query_lower.split(":", 1)[1].strip() if ":" in query_lower else ""
            try:
                product_id = int(product_id_str)
                matched = [p for p in products if p.get("id") == product_id]
            except ValueError:
                matched = []

        # New arrivals keyword
        elif query_lower == "new":
            matched = products  # sorted by created_at DESC

        # Popular keyword
        elif query_lower == "popular":
            matched = products

        # Default: fuzzy search
        else:
            matched = fuzzy_search_products(products, query, score_cutoff=60, limit=None)

    except Exception as exc:
        logger.error("Error searching products for inline query: %s", exc)
        matched = []

    if not matched:
        # Return a friendly 'no results' article
        # Escape user query to prevent Markdown injection
        query_escaped = escape_markdown_v1(query)
        return [
            InlineQueryResultArticle(
                id="no_results",
                title="❌ No products found",
                description=f'No results for "{query}"',
                input_message_content=InputTextMessageContent(
                    message_text=f"❌ No products found for *{query_escaped}*.",
                    parse_mode="Markdown",
                ),
            )
        ]

    # Cap before building articles so we don't do unnecessary async work.
    page_items = matched[:INLINE_PAGE_SIZE]
    results = list(await asyncio.gather(*[_build_product_article(p, bot_username, context) for p in page_items]))
    return results


async def handle_chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ChosenInlineResult events.

    Logs the selection for analytics and sends a private follow-up message to
    the user who chose the result.  The follow-up includes a media preview
    (photo when available) and buttons to open the product in the web app and
    in the bot – all without any external state store (no Redis required).
    """
    chosen = update.chosen_inline_result
    if not chosen:
        return

    user_id = chosen.from_user.id
    result_id = chosen.result_id
    query = chosen.query or ""
    bot_username = get_bot_username(context) or ""

    logger.info(
        "Chosen inline result: user=%s bot=@%s result_id=%r query=%r",
        user_id,
        bot_username,
        result_id,
        query,
    )

    try:
        await db.log_chosen_inline_result(
            user_id=user_id,
            result_id=result_id,
            query=query,
            bot_username=bot_username,
        )
    except Exception as exc:
        logger.warning("Failed to log chosen inline result: %s", exc)

    # Send a private follow-up message for product selections so the user gets
    # a media preview and action buttons in their own chat with the bot.
    if not result_id.startswith("product_"):
        return

    try:
        product_id = int(result_id.split("_", 1)[1])
    except (ValueError, IndexError):
        return

    try:
        product = await db.get_product(product_id)
        if not product:
            return

        caption = product.get("caption") or ""
        title = caption[:60] + "…" if len(caption) > 60 else caption
        title_escaped = escape_markdown_v1(title)
        category_escaped = escape_markdown_v1(product.get("category") or "Uncategorized")
        caption_text = f"📦 *{title_escaped}*\n📂 {category_escaped}"

        # Build action buttons using correct domain
        bot_deep_link = _build_product_url(product_id, bot_username)
        webapp_deep_link = _build_product_webapp_url(product_id)
        buttons = []
        if bot_deep_link:
            buttons.append(InlineKeyboardButton("🔗 View in Bot", url=bot_deep_link))
        if webapp_deep_link:
            buttons.append(InlineKeyboardButton("🛍️ View in WebApp", url=webapp_deep_link))
        reply_markup = InlineKeyboardMarkup([buttons]) if buttons else None

        file_id = product.get("file_id")
        file_type = product.get("file_type") or ""
        media_sent = False

        if file_id and file_type in ("photo", "video"):
            try:
                # If the product was indexed by a different bot instance, attempt
                # to resolve a file_id that is valid for the current bot.
                product_bot = product.get("bot_username")
                current_bot_username = bot_username or context.bot.username
                if product_bot and current_bot_username and product_bot.lower() != current_bot_username.lower():
                    logger.debug(
                        "Product %s indexed by different bot (%s), resolving for %s",
                        product_id, product_bot, current_bot_username,
                    )
                    bot_specific_id = await get_bot_specific_file_id(
                        context,
                        product.get("chat_id"),
                        product.get("message_id"),
                        file_type,
                        file_index=0,
                    )
                    if bot_specific_id:
                        file_id = bot_specific_id
                        logger.debug("Resolved bot-specific file_id for product %s", product_id)
                    else:
                        logger.warning(
                            "Failed to resolve bot-specific file_id for product %s - will attempt with original file_id",
                            product_id,
                        )

                # Validate file_id before attempting send.
                if not file_id:
                    logger.error(
                        "file_id is None for product %s (file_type=%s), cannot send media",
                        product_id, file_type,
                    )
                elif file_type == "photo":
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=file_id,
                        caption=caption_text,
                        parse_mode="Markdown",
                        reply_markup=reply_markup,
                        disable_notification=True,
                    )
                    media_sent = True
                    logger.debug("Sent photo for product %s to user %s", product_id, user_id)
                elif file_type == "video":
                    await context.bot.send_video(
                        chat_id=user_id,
                        video=file_id,
                        caption=caption_text,
                        parse_mode="Markdown",
                        reply_markup=reply_markup,
                        disable_notification=True,
                    )
                    media_sent = True
                    logger.debug("Sent video for product %s to user %s", product_id, user_id)
            except Exception as media_error:
                logger.error(
                    "Failed to send media preview for product %s: %s (file_id=%s, file_type=%s)",
                    product_id, media_error, file_id, file_type, exc_info=True,
                )

        if not media_sent:
            await context.bot.send_message(
                chat_id=user_id,
                text=caption_text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
                disable_notification=True,
            )
    except Exception as exc:
        # Common failure: user hasn't started the bot yet – silently ignore.
        logger.debug("Could not send post-selection message to user %s: %s", user_id, exc)
