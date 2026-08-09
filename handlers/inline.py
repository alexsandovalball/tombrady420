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
    InlineQueryResultPhoto,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from database import Database
from configs.config import Config
from utils.deeplinks import build_bot_product_url, build_webapp_product_url
from utils.fuzzy_search import fuzzy_search_products
from utils.helpers import get_bot_username, escape_markdown_v1, get_bot_specific_file_id

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
_DEBOUNCE_DELAY = 0.15
_user_query_tasks: Dict[int, asyncio.Task] = {}
_user_query_tasks_lock = asyncio.Lock()

# Maximum number of inline results to return to the client.
# Centralised here so both the per-page slicer and the final answer cap
# stay in sync without touching helper builders individually.
INLINE_PAGE_SIZE = 10
_INLINE_PAGE_SIZE = INLINE_PAGE_SIZE  # Internal alias kept for backward compat
# Keep inline startup snappy; rich media should degrade quickly rather than
# making Telegram wait too long before the picker opens.
_INLINE_MEDIA_RESOLVE_TIMEOUT = 0.8
_INLINE_RESULT_REJECTION_MARKERS = (
    "photo_invalid",
    "photo_url_invalid",
    "thumbnail_url_invalid",
    "wrong type of the web page content",
    "failed to get http url content",
    "video_invalid",
    "document_invalid",
    "wrong file identifier",
    "file identifier",
    "file_id",
)
_INLINE_BAD_MEDIA_TTL_SECONDS = 10 * 60
_INLINE_BAD_MEDIA_MAX = 512
_inline_bad_media_results: Dict[Tuple[str, str], float] = {}


def _consume_background_task(task: asyncio.Task) -> None:
    """Consume a shielded background task result so timeout paths stay quiet."""
    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        pass


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


def _product_public_id(product: Dict[str, Any]) -> int:
    """Return the visible reusable product number for links."""
    try:
        return int(product.get("public_id") or product.get("id") or product.get("product_id"))
    except (TypeError, ValueError):
        return int(product.get("id") or 0)


def _inline_result_rejected(exc: Exception) -> bool:
    if not isinstance(exc, BadRequest):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _INLINE_RESULT_REJECTION_MARKERS)


def _inline_article_thumbnail_url(
    product_id: Any,
    file_type: str,
    *,
    safe_only: bool = False,
) -> Optional[str]:
    """Return a thumbnail URL that Telegram can safely treat as an image."""
    if product_id is None:
        return None

    storefront_domain = Config.STOREFRONT_DOMAIN or (Config.WEBHOOK_URL or "").rstrip("/")
    if not storefront_domain:
        return None

    storefront_base = storefront_domain.rstrip("/")
    if not safe_only and file_type == "photo":
        return f"{storefront_base}/api/catalog/{product_id}/media/0"

    return f"{storefront_base}/og-image.png?v=20260805-football-emoji-canonical-v2"


def _inline_photo_media_url(product_id: Any, media_index: int = 0) -> Optional[str]:
    """Return an absolute photo URL Telegram can fetch for a cold inline result."""
    if product_id is None:
        return None

    storefront_domain = Config.STOREFRONT_DOMAIN or (Config.WEBHOOK_URL or "").rstrip("/")
    if not storefront_domain:
        return None

    storefront_base = storefront_domain.rstrip().rstrip("/")
    return f"{storefront_base}/api/catalog/{product_id}/media/{media_index}"


def _inline_media_quarantined(bot_username: str, product_id: Any) -> bool:
    """Return True when Telegram recently rejected this product as rich media."""
    if product_id is None:
        return False
    key = ((bot_username or "").lower(), str(product_id))
    marked_at = _inline_bad_media_results.get(key)
    if marked_at is None:
        return False
    if time.monotonic() - marked_at > _INLINE_BAD_MEDIA_TTL_SECONDS:
        _inline_bad_media_results.pop(key, None)
        return False
    return True


def _remember_inline_media_rejection(
    results: List[InlineQueryResult],
    bot_username: str,
) -> None:
    """Temporarily stop retrying rich inline media that Telegram rejected."""
    now = time.monotonic()
    bot_key = (bot_username or "").lower()
    marked = 0

    for result in results:
        if not isinstance(result, (InlineQueryResultCachedPhoto, InlineQueryResultCachedVideo, InlineQueryResultPhoto)):
            continue
        result_id = getattr(result, "id", "")
        if not result_id.startswith("product_"):
            continue
        product_id = result_id.removeprefix("product_")
        _inline_bad_media_results[(bot_key, product_id)] = now
        marked += 1

    while len(_inline_bad_media_results) > _INLINE_BAD_MEDIA_MAX:
        oldest_key = min(_inline_bad_media_results, key=_inline_bad_media_results.get)
        _inline_bad_media_results.pop(oldest_key, None)

    if marked:
        logger.info(
            "Quarantined %d inline rich-media result(s) after Telegram rejected the batch",
            marked,
        )


async def _build_product_article(
    product: Dict[str, Any],
    bot_username: str,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    force_article: bool = False,
    allow_rehydrate: bool = True,
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
    public_product_id = _product_public_id(product)
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
    bot_deep_link = _build_product_url(public_product_id, bot_username)
    webapp_deep_link = _build_product_webapp_url(public_product_id)

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
    media_quarantined = _inline_media_quarantined(bot_username, product_id)
    if not force_article and not media_quarantined and file_type in ("photo", "video"):
        resolved_file_id = await _resolve_file_id_for_inline(
            product,
            context,
            allow_rehydrate=allow_rehydrate,
        )
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
                    show_caption_above_media=False,
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
                    show_caption_above_media=False,
                )

    # Cold-start fallback for photo products: still return a media result.
    # Telegram will fetch this URL once, which warms the existing media cache,
    # and the sent inline message remains photo + caption/buttons instead of
    # degrading to a caption-only article.
    if not force_article and not media_quarantined and file_type == "photo":
        photo_url = _inline_photo_media_url(product_id)
        if photo_url:
            return InlineQueryResultPhoto(
                id=f"product_{product_id}",
                photo_url=photo_url,
                thumbnail_url=photo_url,
                title=title,
                description=description,
                caption=caption_text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
                show_caption_above_media=False,
            )

    # --- Fallback: article with a Telegram-safe thumbnail from storefront ---
    # Article thumbnails must be images. Photo products can use their public
    # preview URL; videos/documents use the default image to avoid Telegram
    # rejecting the entire inline answer with Photo_invalid/Document_invalid.
    thumbnail_url = _inline_article_thumbnail_url(
        product_id,
        file_type,
        safe_only=force_article or media_quarantined,
    )

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
    *,
    allow_rehydrate: bool = True,
) -> Optional[str]:
    """Return a file_id usable by the current bot for the given product.

    This intentionally follows the older, stable inline path: same-bot
    products use their original file_id immediately; cross-bot products use
    the typed bot-specific cache and only rehydrate when that cache is empty.
    """
    raw_file_id = product.get("file_id")
    if not raw_file_id:
        logger.debug(
            "No raw_file_id for product %s – will fall back to article result",
            product.get("id"),
        )
        return None

    product_bot = (product.get("bot_username") or "").lower()
    current_bot = (context.bot.username or "").lower()

    if product_bot and current_bot and product_bot == current_bot:
        return raw_file_id

    chat_id = product.get("chat_id")
    message_id = product.get("message_id")
    file_type = product.get("file_type")
    if chat_id and message_id and current_bot and file_type in ("photo", "video"):
        try:
            cached = await db.get_bot_file_id(
                chat_id,
                message_id,
                0,
                current_bot,
                file_type=file_type,
            )
            if cached:
                return cached

            if not allow_rehydrate:
                return None

            rehydrate_task = asyncio.create_task(
                get_bot_specific_file_id(
                    context,
                    chat_id,
                    message_id,
                    file_type,
                    file_index=0,
                ),
                name=f"inline_media_rehydrate:{product.get('id')}",
            )
            rehydrate_task.add_done_callback(_consume_background_task)
            resolved = await asyncio.wait_for(
                asyncio.shield(rehydrate_task),
                timeout=_INLINE_MEDIA_RESOLVE_TIMEOUT,
            )
            if resolved:
                return resolved
        except asyncio.TimeoutError:
            logger.info(
                "Inline media rehydrate timed out for product %s; using article fallback",
                product.get("id"),
            )
        except Exception as exc:
            logger.warning("Error resolving file_id for inline: %s", exc, exc_info=True)

    return None


async def _build_product_results(
    products: List[Dict[str, Any]],
    bot_username: str,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    force_article: bool = False,
    allow_rehydrate: bool = True,
) -> List[InlineQueryResult]:
    built = await asyncio.gather(
        *[
            _build_product_article(
                product,
                bot_username,
                context,
                force_article=force_article,
                allow_rehydrate=allow_rehydrate,
            )
            for product in products
        ],
        return_exceptions=True,
    )

    results: List[InlineQueryResult] = []
    for product, result in zip(products, built):
        if isinstance(result, Exception):
            logger.warning(
                "Skipping inline product %s after result build failure: %s",
                product.get("id"),
                result,
            )
            continue
        results.append(result)
    return results


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
    *,
    force_article: bool = False,
    allow_rehydrate: bool = True,
) -> List[InlineQueryResult]:
    """Return newest products as inline results."""
    # Products are sorted by created_at DESC in DB, so first items are newest
    newest = products[:limit]
    return await _build_product_results(
        newest,
        bot_username,
        context,
        force_article=force_article,
        allow_rehydrate=allow_rehydrate,
    )


async def _build_popular_articles(
    products: List[Dict[str, Any]],
    bot_username: str,
    context: ContextTypes.DEFAULT_TYPE,
    limit: int = 5,
    *,
    force_article: bool = False,
    allow_rehydrate: bool = True,
) -> List[InlineQueryResult]:
    """Return trending / most-recently-added products as inline results."""
    # Simple heuristic: products are already sorted by created_at DESC in the DB query
    popular = products[:limit]
    return await _build_product_results(
        popular,
        bot_username,
        context,
        force_article=force_article,
        allow_rehydrate=allow_rehydrate,
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming inline queries with debouncing and task cancellation."""
    inline_query = update.inline_query
    if not inline_query:
        return

    user_id = inline_query.from_user.id
    if await db.is_user_blocked(user_id):
        logger.warning("Blocked user %s attempted inline query access", user_id)
        await inline_query.answer(results=[], cache_time=60, is_personal=True)
        return

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
        try:
            await inline_query.answer(
                results=results,
                cache_time=30,
                is_personal=False,
                next_offset=next_offset,
                **_extra,
            )
        except BadRequest as exc:
            if not _inline_result_rejected(exc):
                raise

            logger.warning(
                "Inline answer rejected by Telegram (%s); retrying with safe article results",
                exc,
            )
            _remember_inline_media_rejection(results, bot_username)
            if query:
                fallback_results = await _handle_query_search(
                    query,
                    bot_username,
                    context,
                    force_article=True,
                )
            else:
                fallback_results = await _handle_empty_query(
                    bot_username,
                    context,
                    force_article=True,
                )

            await inline_query.answer(
                results=fallback_results[:INLINE_PAGE_SIZE],
                cache_time=5,
                is_personal=False,
                next_offset="",
                **_extra,
            )

        # Log after answering so analytics never slows down the picker opening.
        latency_ms = int((time.monotonic() - start_time) * 1000)
        results_count = len(results)
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
    except Exception as exc:
        logger.error("Error answering inline query: %s", exc, exc_info=True)
        # Answer with empty results to avoid Telegram timeout
        try:
            await inline_query.answer(results=[], cache_time=5)
        except Exception:
            pass


async def _handle_empty_query(
    bot_username: str,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    force_article: bool = False,
) -> List[InlineQueryResult]:
    """Return default results for an empty inline query with enhanced discoverability."""
    results: List[InlineQueryResult] = []

    # Keep inline mode useful without showing the removed browse/tips/category articles.
    web_app_url = Config.WEB_APP_URL
    if web_app_url and bot_username:
        results.append(_build_webapp_article(bot_username, web_app_url))

    try:
        products = await _get_cached_products()
        # Add new arrivals (first 3 products)
        results.extend(
            await _build_new_arrivals_articles(
                products,
                bot_username,
                context,
                limit=3,
                force_article=force_article,
                allow_rehydrate=False,
            )
        )
        # Add popular/trending (next 3 products – skip new-arrivals slice to avoid duplicate IDs)
        results.extend(
            await _build_popular_articles(
                products[3:],
                bot_username,
                context,
                limit=3,
                force_article=force_article,
                allow_rehydrate=False,
            )
        )
    except Exception as exc:
        logger.warning("Could not fetch products for inline empty query: %s", exc)

    return results


async def _handle_query_search(
    query: str,
    bot_username: str,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    force_article: bool = False,
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
            matched = fuzzy_search_products(
                products,
                query,
                score_cutoff=60,
                limit=INLINE_PAGE_SIZE,
                include_seo_terms=False,
                rank_by_score=True,
            )

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
    return await _build_product_results(
        page_items,
        bot_username,
        context,
        force_article=force_article,
        allow_rehydrate=False,
    )


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
    if await db.is_user_blocked(user_id):
        logger.warning("Blocked user %s attempted chosen inline result access", user_id)
        return

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
