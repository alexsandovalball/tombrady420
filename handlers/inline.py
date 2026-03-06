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

from telegram import Update, InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from database import Database
from configs.config import Config
from utils.fuzzy_search import fuzzy_search_products
from utils.helpers import get_bot_username, escape_markdown_v1
from utils.categories import get_all_categories

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
_DEBOUNCE_DELAY = 0.1  # 100ms debounce
_user_query_tasks: Dict[int, asyncio.Task] = {}
_user_query_tasks_lock = asyncio.Lock()


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

def _build_product_article(
    product: Dict[str, Any],
    bot_username: str,
) -> InlineQueryResultArticle:
    """Build an InlineQueryResultArticle for a single product."""
    product_id = product.get("id")
    caption = product.get("caption") or ""
    category = product.get("category") or "Uncategorized"

    # Truncate caption for display
    title = caption[:60] + "…" if len(caption) > 60 else caption
    if not title:
        title = f"Product #{product_id}"

    # Escape dynamic content for safe Markdown rendering
    title_escaped = escape_markdown_v1(title)
    category_escaped = escape_markdown_v1(category)

    description = f"Category: {category}"

    # Build the inline button: prefer Web App URL if configured, else bot deep link
    web_app_url = Config.WEB_APP_URL
    if web_app_url:
        product_url = f"{web_app_url.rstrip('/')}/product/{product_id}"
        button_text = "🛍️ View in Web App"
    else:
        product_url = f"https://t.me/{bot_username}?start=product_{product_id}"
        button_text = "🛍️ View in bot"

    reply_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(button_text, url=product_url)
    ]])

    message_text = (
        f"📦 *{title_escaped}*\n"
        f"📂 {category_escaped}\n"
        f"🆔 ID: {product_id}"
    )

    return InlineQueryResultArticle(
        id=f"product_{product_id}",
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            message_text=message_text,
            parse_mode="Markdown",
        ),
        reply_markup=reply_markup,
    )


def _build_webapp_article(bot_username: str, web_app_url: str) -> InlineQueryResultArticle:
    """Return an 'Open WebApp / Shop' entry."""
    deep_link = f"https://t.me/{bot_username}?start=webapp"
    return InlineQueryResultArticle(
        id="open_webapp",
        title="🛍️ Open Shop",
        description="Open the full product catalog in the app",
        input_message_content=InputTextMessageContent(
            message_text=f"🛍️ [Open the Shop]({deep_link})",
            parse_mode="Markdown",
        ),
    )


def _build_browse_categories_article(bot_username: str) -> InlineQueryResultArticle:
    """Return a 'Browse categories' entry using switch-to-PM deep link."""
    deep_link = f"https://t.me/{bot_username}?start=categories"
    return InlineQueryResultArticle(
        id="browse_categories",
        title="📂 Browse Categories",
        description="Tap to open the bot and browse product categories",
        input_message_content=InputTextMessageContent(
            message_text=f"📂 [Browse categories in bot]({deep_link})",
            parse_mode="Markdown",
        ),
    )


def _build_inline_tips_article(bot_username: str) -> InlineQueryResultArticle:
    """Return inline search tips / how-to article."""
    bot_mention = f"@{bot_username}" if bot_username else "@bot"
    return InlineQueryResultArticle(
        id="inline_tips",
        title="💡 Inline Search Tips",
        description="Learn how to search effectively",
        input_message_content=InputTextMessageContent(
            message_text=(
                f"💡 **Inline Search Tips**\n\n"
                f"• Type product names: `{bot_mention} cartridge`\n"
                f"• Search by category: `cat:edibles` or `category:flower`\n"
                f"• Find by ID: `id:123`\n"
                f"• Keywords: `new` for new arrivals, `popular` for trending\n\n"
                f"Start typing to search!"
            ),
            parse_mode="Markdown",
        ),
    )


def _build_top_categories_article(bot_username: str) -> InlineQueryResultArticle:
    """Return top categories article."""
    categories = get_all_categories()[:5]  # Show top 5 categories
    category_list = "\n".join([f"• {cat}" for cat in categories])

    return InlineQueryResultArticle(
        id="top_categories",
        title="📂 Top Categories",
        description="Browse by category",
        input_message_content=InputTextMessageContent(
            message_text=(
                f"📂 **Top Categories**\n\n"
                f"{category_list}\n\n"
                f"Try searching: `cat:cartridges` or just type a category name!"
            ),
            parse_mode="Markdown",
        ),
    )


def _build_new_arrivals_articles(
    products: List[Dict[str, Any]],
    bot_username: str,
    limit: int = 5,
) -> List[InlineQueryResultArticle]:
    """Return newest products as articles."""
    # Products are sorted by created_at DESC in DB, so first items are newest
    newest = products[:limit]
    return [_build_product_article(p, bot_username) for p in newest]


def _build_popular_articles(
    products: List[Dict[str, Any]],
    bot_username: str,
    limit: int = 5,
) -> List[InlineQueryResultArticle]:
    """Return trending / most-recently-added products as articles."""
    # Simple heuristic: products are already sorted by created_at DESC in the DB query
    popular = products[:limit]
    return [_build_product_article(p, bot_username) for p in popular]


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
            results = await _handle_query_search(query, bot_username)
            is_empty = False
        else:
            results = await _handle_empty_query(bot_username)
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

        # Cache settings:
        # - is_personal=False: Results are identical for all users (no per-user personalization)
        # - cache_time=30: Cache for 30 seconds to balance freshness and load
        # NOTE: If adding language-based strings or per-user features, set is_personal=True
        await inline_query.answer(
            results=results,
            cache_time=30,
            is_personal=False,
            **_extra,
        )
    except Exception as exc:
        logger.error("Error answering inline query: %s", exc, exc_info=True)
        # Answer with empty results to avoid Telegram timeout
        try:
            await inline_query.answer(results=[], cache_time=5)
        except Exception:
            pass


async def _handle_empty_query(bot_username: str) -> List[InlineQueryResultArticle]:
    """Return default results for an empty inline query with enhanced discoverability."""
    results: List[InlineQueryResultArticle] = []

    # 1. Inline search tips (how to use)
    if bot_username:
        results.append(_build_inline_tips_article(bot_username))

    # 2. Open WebApp / Shop entry
    web_app_url = Config.WEB_APP_URL
    if web_app_url and bot_username:
        results.append(_build_webapp_article(bot_username, web_app_url))

    # 3. Browse categories entry
    if bot_username:
        results.append(_build_browse_categories_article(bot_username))

    # 4. Top categories overview
    if bot_username:
        results.append(_build_top_categories_article(bot_username))

    # 5. New arrivals / trending items
    try:
        products = await _get_cached_products()
        # Add new arrivals (first 3 products)
        results.extend(_build_new_arrivals_articles(products, bot_username, limit=3))
        # Add popular/trending (next 3 products)
        results.extend(_build_popular_articles(products, bot_username, limit=3))
    except Exception as exc:
        logger.warning("Could not fetch products for inline empty query: %s", exc)

    return results


async def _handle_query_search(
    query: str,
    bot_username: str,
) -> List[InlineQueryResultArticle]:
    """
    Return fuzzy-search results for a non-empty query.

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
                # Filter products by category (fuzzy match)
                matched = [
                    p for p in products
                    if category_query in (p.get("category") or "").lower()
                ][:50]
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
            matched = products[:10]  # First 10 newest products

        # Popular keyword
        elif query_lower == "popular":
            matched = products[:10]  # First 10 products (heuristic)

        # Default: fuzzy search
        else:
            # Lower score_cutoff to 60 for broader matches, relying on two-stage ranking
            matched = fuzzy_search_products(products, query, score_cutoff=60, limit=50)

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

    return [_build_product_article(p, bot_username) for p in matched[:50]]


async def handle_chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ChosenInlineResult events for analytics."""
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
