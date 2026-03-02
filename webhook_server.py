"""
Webhook server for Telegram bot using FastAPI and uvicorn.
This allows the bot to receive updates via webhooks instead of polling,
which is more suitable for serverless deployments like Render.com.
"""
import aiosqlite
import asyncio
import json
import logging
import mimetypes
import os
import re
import urllib.parse
import tempfile
from collections import OrderedDict
from pathlib import Path
from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from telegram import Update
from telegram.constants import FileSizeLimit
from telegram.ext import Application
from telegram.request import HTTPXRequest
import telegram.error

from configs.config import Config, ConfigError
# Import the bot setup functions
from main import (
    post_init,
    setup_bot_commands,
    start_command,
    menu_command,
    language_command,
    subscribe_command,
    unsubscribe_command,
    nuke_command,
    recategorize_command,
    users_command,
    block_command,
    unblock_command,
    send_command,
    broadcast_command,
    setcontact_command,
    clearcache_command,
    channel_post_handler,
    callback_query_handler,
    message_handler,
    error_handler
)
from handlers.admin import botusers_command, prunebots_command
from handlers.inline import handle_inline_query, handle_chosen_inline_result
from utils.webapp_security import verify_init_data, parse_init_data
from database import Database
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ChosenInlineResultHandler,
    filters
)
from utils.helpers import get_channel_id, get_channel_username, get_file_id_cache_size, get_bot_specific_file_id
from utils.categories import get_all_categories, get_category_display_name, get_subcategory_display_name, get_subcategories, EXCLUDED_FROM_ALL_PRODUCTS
from translations.translator import get_translated_string_async, translation_service

# Configure logging
from configs.logging_config import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

# Global registry for bot applications
# This allows the notification service to access all bot instances
_bot_applications = []
_catalog_db = Database()

# Media cache configuration
MEDIA_CACHE_DIR = Path(os.getenv("MEDIA_CACHE_DIR", "/tmp/media_cache"))
MEDIA_CACHE_MAX_SIZE_MB = int(os.getenv("MEDIA_CACHE_MAX_SIZE_MB", "500"))  # 500MB default
MEDIA_CACHE_TTL_HOURS = int(os.getenv("MEDIA_CACHE_TTL_HOURS", "24"))  # 24 hours default

# Create cache directory if it doesn't exist
MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)

class MediaCache:
    """Simple LRU file cache for media files with size and TTL management."""

    def __init__(self, cache_dir: Path, max_size_mb: int, ttl_hours: int):
        self.cache_dir = cache_dir
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.ttl_seconds = ttl_hours * 3600
        self.lock = asyncio.Lock()

    def _get_cache_path(self, file_id: str) -> Path:
        """Get cache file path for a file_id."""
        # Use hash to avoid filesystem issues with special characters
        import hashlib
        hash_name = hashlib.sha256(file_id.encode()).hexdigest()
        return self.cache_dir / hash_name

    async def get(self, file_id: str) -> Optional[Path]:
        """Get cached file path if it exists and is valid."""
        cache_path = self._get_cache_path(file_id)

        if not cache_path.exists():
            return None

        # Check if file is still within TTL
        try:
            file_age = asyncio.get_event_loop().time() - cache_path.stat().st_mtime
            if file_age > self.ttl_seconds:
                # File is expired, delete it
                cache_path.unlink()
                logger.debug(f"Cache expired for file_id {file_id[:20]}...")
                return None
        except (OSError, FileNotFoundError):
            return None

        # Update access time for LRU tracking
        cache_path.touch()
        logger.debug(f"Cache HIT for file_id {file_id[:20]}...")
        return cache_path

    async def set(self, file_id: str, source_path: Path) -> Path:
        """Store a file in the cache."""
        async with self.lock:
            cache_path = self._get_cache_path(file_id)

            # Copy file to cache
            import shutil
            shutil.copy2(source_path, cache_path)

            # Evict old files if cache is too large
            await self._evict_if_needed()

            logger.debug(f"Cache SET for file_id {file_id[:20]}...")
            return cache_path

    async def _evict_if_needed(self):
        """Evict oldest files if cache exceeds max size."""
        try:
            # Collect stats once per file to avoid redundant syscalls
            cache_files = []
            for f in self.cache_dir.iterdir():
                if f.is_file():
                    try:
                        st = f.stat()
                        cache_files.append((f, st.st_atime, st.st_size))
                    except (OSError, FileNotFoundError):
                        pass

            # Calculate total cache size
            total_size = sum(size for _, _, size in cache_files)

            # If under limit, nothing to do
            if total_size <= self.max_size_bytes:
                return

            # Sort by access time (oldest first)
            cache_files.sort(key=lambda x: x[1])

            # Evict oldest files until under limit
            for file_path, _, size in cache_files:
                if total_size <= self.max_size_bytes:
                    break

                try:
                    file_path.unlink()
                    total_size -= size
                    logger.debug(f"Evicted cache file: {file_path.name}")
                except (OSError, FileNotFoundError):
                    pass
        except Exception as e:
            logger.warning(f"Error during cache eviction: {e}")

    async def clear(self):
        """Clear all cached files."""
        async with self.lock:
            try:
                for file_path in self.cache_dir.iterdir():
                    if file_path.is_file():
                        file_path.unlink()
                logger.info("Media cache cleared")
            except Exception as e:
                logger.warning(f"Error clearing cache: {e}")

# Global media cache instance
_media_cache = MediaCache(MEDIA_CACHE_DIR, MEDIA_CACHE_MAX_SIZE_MB, MEDIA_CACHE_TTL_HOURS)

# Lightweight in-memory cache for resolved bot-specific file_ids.
# Keyed by (source_chat_id, source_message_id, file_index, bot_username) → resolved_file_id.
# Avoids repeated DB lookups across consecutive HTTP Range 206 requests for the same media.
# Bounded to _RESOLVED_FILE_ID_CACHE_MAX entries with LRU eviction.
_resolved_file_id_cache: OrderedDict = OrderedDict()
_RESOLVED_FILE_ID_CACHE_MAX = 2000


def _cache_resolved_file_id(key: tuple, file_id: str) -> None:
    """Insert a resolved file_id into the in-memory cache with LRU eviction."""
    if key in _resolved_file_id_cache:
        # Move to end (most recently used)
        _resolved_file_id_cache.move_to_end(key)
        _resolved_file_id_cache[key] = file_id
    else:
        # Add new entry
        _resolved_file_id_cache[key] = file_id
        # Evict oldest if over capacity (LRU)
        if len(_resolved_file_id_cache) > _RESOLVED_FILE_ID_CACHE_MAX:
            _resolved_file_id_cache.popitem(last=False)

def get_bot_applications() -> List[Application]:
    """Get all bot application instances."""
    return _bot_applications

def get_bot_usernames() -> List[str]:
    """Get list of bot usernames for all registered bots (normalized to lowercase)."""
    usernames = []
    for bot in _bot_applications:
        if hasattr(bot, 'bot') and hasattr(bot.bot, 'username') and bot.bot.username is not None:
            usernames.append(bot.bot.username.lower())
        else:
            # Log if a bot is missing username for debugging
            if hasattr(bot, 'bot'):
                logger.warning(f"Bot application has no username or username is None")
            else:
                logger.warning(f"Bot application has no 'bot' attribute")
    return usernames


async def setup_application(bot_token: str, bot_index: int = 0):
    """Initialize and configure a bot application.
    
    Args:
        bot_token: The Telegram bot token
        bot_index: Index of the bot (0 for primary, 1+ for additional bots)
    """
    logger.info(f"Setting up bot application {bot_index}...")
    
    # Create application with optimized timeout settings
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=10.0,
        read_timeout=10.0,
        write_timeout=10.0,
        pool_timeout=5.0
    )
    
    bot_app = Application.builder().token(bot_token).request(request).build()
    
    # Register handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("menu", menu_command))
    bot_app.add_handler(CommandHandler("language", language_command))
    bot_app.add_handler(CommandHandler("nuke", nuke_command))
    bot_app.add_handler(CommandHandler("recategorize", recategorize_command))
    bot_app.add_handler(CommandHandler("users", users_command))
    bot_app.add_handler(CommandHandler("subscribe", subscribe_command))
    bot_app.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    bot_app.add_handler(CommandHandler("block", block_command))
    bot_app.add_handler(CommandHandler("unblock", unblock_command))
    bot_app.add_handler(CommandHandler("send", send_command))
    bot_app.add_handler(CommandHandler("broadcast", broadcast_command))
    bot_app.add_handler(CommandHandler("setcontact", setcontact_command))
    bot_app.add_handler(CommandHandler("clearcache", clearcache_command))
    bot_app.add_handler(CommandHandler("botusers", botusers_command))
    bot_app.add_handler(CommandHandler("prunebots", prunebots_command))
    
    # Channel post handler (for monitoring channel)
    # Only the primary bot (bot_index == 0) should monitor the channel and send categorization requests
    if bot_index == 0:
        channel_id = get_channel_id()
        channel_username = get_channel_username()
        
        if channel_id:
            # Use channel ID filter
            channel_filter = filters.Chat(chat_id=channel_id)
        elif channel_username:
            # Use channel username filter
            channel_filter = filters.Chat(username=channel_username.lstrip("@"))
        else:
            logger.warning("No CHANNEL_ID or CHANNEL_USERNAME set. Channel monitoring disabled.")
            channel_filter = None
        
        if channel_filter:
            bot_app.add_handler(
                MessageHandler(
                    channel_filter & filters.ChatType.CHANNEL,
                    channel_post_handler
                )
            )
            logger.info(f"Channel monitoring enabled for primary bot: {channel_id or channel_username}")
    else:
        logger.info(f"Channel monitoring DISABLED for secondary bot {bot_index} (only primary bot monitors channel)")
    
    # Callback query handler (for inline buttons)
    bot_app.add_handler(CallbackQueryHandler(callback_query_handler))

    # Inline query handler (for @bot search in any chat)
    bot_app.add_handler(InlineQueryHandler(handle_inline_query))
    bot_app.add_handler(ChosenInlineResultHandler(handle_chosen_inline_result))

    # Message handler (for search queries)
    bot_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            message_handler
        )
    )
    
    # Error handler
    bot_app.add_error_handler(error_handler)
    
    # Initialize the application
    # IMPORTANT: Must be called before any bot API methods (like set_my_commands)
    await bot_app.initialize()
    await bot_app.start()
    
    # Initialize database and set bot commands (only once for the primary bot)
    # NOTE: This must happen AFTER bot_app.initialize() to avoid "Frozen_method_invalid" errors
    if bot_index == 0:
        await post_init(bot_app)
    else:
        # For secondary bots, set commands
        await setup_bot_commands(bot_app.bot)
    
    # Set webhook
    webhook_url = Config.WEBHOOK_URL
    if webhook_url:
        # Primary bot uses /webhook, additional bots use /webhook/1, /webhook/2, etc.
        webhook_path = "/webhook" if bot_index == 0 else f"/webhook/{bot_index}"
        await bot_app.bot.set_webhook(
            url=f"{webhook_url}{webhook_path}",
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        logger.info(f"Webhook set for bot {bot_index}: {webhook_url}{webhook_path}")
    else:
        logger.warning("WEBHOOK_URL not set. Webhook may not work properly.")
    
    logger.info(f"Bot application {bot_index} initialized successfully")
    return bot_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown."""
    global _bot_applications
    
    # Startup
    logger.info("Starting webhook server...")
    
    # Validate configuration on startup
    try:
        Config.validate()
    except ConfigError as e:
        logger.error(str(e))
        raise
    
    # Initialize all bot applications in parallel for faster startup.
    # When BOT_TOKENS contains only one entry, asyncio.gather runs a single task —
    # fully backward-compatible with single-bot deployments.
    bot_apps = []
    bot_tokens = Config.BOT_TOKENS
    
    if not bot_tokens:
        logger.error("No bot tokens configured!")
        raise ConfigError("At least one bot token is required")
    
    logger.info(f"Initializing {len(bot_tokens)} bot instance(s) in parallel...")
    
    # Create setup tasks for all bots
    setup_tasks = [setup_application(token, idx) for idx, token in enumerate(bot_tokens)]
    
    bot_apps = []  # Initialize before gather for exception handler
    try:
        # Initialize all bots concurrently
        bot_apps = await asyncio.gather(*setup_tasks)
        logger.info(f"All {len(bot_apps)} bot(s) initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize bots: {e}", exc_info=True)
        # Clean up any successfully initialized bots
        for app in bot_apps:
            if app:
                try:
                    await app.stop()
                    await app.shutdown()
                except Exception:
                    pass
        raise
    
    # Store in global registry for notification service access
    _bot_applications = bot_apps
    
    app.state.bot_apps = bot_apps
    
    # Log bot usernames for debugging
    for idx, bot_app in enumerate(bot_apps):
        if hasattr(bot_app, 'bot') and hasattr(bot_app.bot, 'username'):
            logger.info(f"  Bot {idx}: @{bot_app.bot.username}")
    
    # Log cache status for multi-bot setups
    if len(bot_apps) > 1:
        cache_size = await get_file_id_cache_size()
        if cache_size == 0:
            logger.warning(
                f"File ID cache is empty. For secondary bots (bot 1-{len(bot_apps)-1}) to display media correctly, "
                f"admins must start a conversation with each bot by sending /start. "
                f"This allows the bots to cache bot-specific file IDs."
            )
        else:
            logger.info(f"File ID cache contains {cache_size} entries")
    
    yield
    
    # Shutdown
    logger.info("Shutting down bot applications...")
    for idx, bot_app in enumerate(app.state.bot_apps):
        try:
            await bot_app.stop()
            await bot_app.shutdown()
            logger.info(f"Bot {idx} shut down successfully")
        except Exception as e:
            logger.error(f"Error shutting down bot {idx}: {e}")
    
    # Clear global registry
    _bot_applications.clear()
    logger.info("All bot applications shut down successfully")


# Create FastAPI app with lifespan
app = FastAPI(
    title="Telegram Bot Webhook Server",
    lifespan=lifespan
)

# Add middleware to set cache headers for static files
from fastapi.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        
        # Add cache headers for static assets
        if request.url.path.startswith('/assets/'):
            # No cache for dynamic code files to ensure updates reflect immediately
            if request.url.path.endswith(('.js', '.css', '.html')):
                response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                response.headers['Pragma'] = 'no-cache'
                response.headers['Expires'] = '0'
            # Cache images for 1 day (can be cached longer as they rarely change)
            elif request.url.path.endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico')):
                response.headers['Cache-Control'] = 'public, max-age=86400'

        # Security headers for HTML responses
        content_type = response.headers.get('content-type', '')
        if 'text/html' in content_type:
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        return response

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all incoming requests for debugging and monitoring."""
    async def dispatch(self, request, call_next):
        # Log incoming request at DEBUG level to avoid flooding production logs
        client_host = request.client.host if request.client else "unknown"
        logger.debug(f"Incoming request: {request.method} {request.url.path} from {client_host}")
        
        # Process request
        response = await call_next(request)
        
        # Log response status at DEBUG level
        logger.debug(f"Response: {request.method} {request.url.path} - Status: {response.status_code}")
        
        return response

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(CacheControlMiddleware)

# Jinja2 templates for SSR/SEO pages
templates = Jinja2Templates(directory="webapp")

# Mount static assets (CSS, JS, images) under /assets.
# The root-relative path /assets works regardless of whether a custom domain is configured,
# because it is resolved server-side relative to the server's own origin — no absolute URL
# or CUSTOM_DOMAIN setting is required for static file serving to work correctly.
app.mount("/assets", StaticFiles(directory="webapp"), name="assets")

_STOREFRONT_BOT_USERNAME = os.getenv("BOT_USERNAME", "").lstrip("@")
_STOREFRONT_DOMAIN = os.getenv("STOREFRONT_DOMAIN", "https://topshelfcaliplug.com")


def _generate_slug(caption: str) -> str:
    """Generate a URL-safe slug from a product caption."""
    if not caption:
        return "product"
    first_line = caption.split('\n')[0].strip()
    slug = re.sub(r'[^a-z0-9]+', '-', first_line.lower()).strip('-')
    slug = slug[:60].rstrip('-')
    return slug or "product"


def _build_seo_context(*, title: str, desc: str, image: str = "", url: str = "",
                        product: Optional[dict] = None, deep_link: str = "",
                        keywords: str = "", category: str = "") -> dict:
    """Build template context dict for SSR SEO pages."""
    if product:
        product_url = url or _STOREFRONT_DOMAIN
        product_schema: dict = {
            "@context": "https://schema.org/",
            "@type": "Product",
            "name": title,
            "description": desc,
            "image": image,
            "url": product_url,
            "brand": {
                "@type": "Brand",
                "name": "Top Shelf Cali Plug",
            },
            "offers": {
                "@type": "Offer",
                "url": product_url,
                "availability": "https://schema.org/InStock",
                "seller": {
                    "@type": "Organization",
                    "name": "Top Shelf Cali Plug",
                },
            },
        }
        breadcrumb: dict = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": "Home",
                    "item": f"{_STOREFRONT_DOMAIN}/",
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": category or "Products",
                    "item": f"{_STOREFRONT_DOMAIN}/?category={urllib.parse.quote(category)}" if category else f"{_STOREFRONT_DOMAIN}/",
                },
                {
                    "@type": "ListItem",
                    "position": 3,
                    "name": title,
                    "item": product_url,
                },
            ],
        }
        schema = json.dumps({
            "@context": "https://schema.org/",
            "@graph": [product_schema, breadcrumb],
        })
    else:
        schema = json.dumps({
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "WebSite",
                    "name": "Top Shelf Cali Plug",
                    "url": _STOREFRONT_DOMAIN,
                    "description": desc,
                    "potentialAction": {
                        "@type": "SearchAction",
                        "target": f"{_STOREFRONT_DOMAIN}/?search={{search_term_string}}",
                        "query-input": "required name=search_term_string",
                    },
                },
                {
                    "@type": "Store",
                    "name": title,
                    "description": desc,
                    "url": _STOREFRONT_DOMAIN,
                    "image": image,
                    "contactPoint": {
                        "@type": "ContactPoint",
                        "contactType": "customer service",
                        "url": f"https://t.me/{_STOREFRONT_BOT_USERNAME}",
                    },
                },
            ],
        })
    default_keywords = "premium catalog, online shop, Telegram store, Top Shelf Cali Plug, buy online, product catalog"
    return {
        "seo_title": title,
        "seo_desc": desc,
        "seo_image": image,
        "seo_url": url,
        "seo_schema": schema,
        "seo_keywords": keywords or default_keywords,
        "telegram_deep_link": deep_link or f"https://t.me/{_STOREFRONT_BOT_USERNAME}",
    }



@app.get("/robots.txt", response_class=Response)
async def serve_robots_txt():
    """Serve robots.txt to guide crawlers."""
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /webhook\n"
        "Disallow: /webhook/\n"
        "Disallow: /webapp\n"
        "Disallow: /webapp/\n"
        "\n"
        f"Sitemap: {_STOREFRONT_DOMAIN}/sitemap.xml\n"
    )
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml", response_class=Response)
async def serve_sitemap_xml():
    """Dynamically generate a sitemap.xml including homepage, category, and all product pages."""
    from datetime import date
    today = date.today().isoformat()
    products = await _catalog_db.get_all_products()

    urls = [
        (
            f"  <url>\n"
            f"    <loc>{_STOREFRONT_DOMAIN}/</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"    <changefreq>weekly</changefreq>\n"
            f"    <priority>1.0</priority>\n"
            f"  </url>"
        )
    ]
    for category in get_all_categories():
        urls.append(
            f"  <url>\n"
            f"    <loc>{_STOREFRONT_DOMAIN}/category/{urllib.parse.quote(category)}</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"    <changefreq>weekly</changefreq>\n"
            f"    <priority>0.9</priority>\n"
            f"  </url>"
        )
    for p in products:
        pid = p.get("id") or p.get("product_id")
        if pid is None:
            continue
        lastmod = p.get("created_at") or today
        if isinstance(lastmod, str) and "T" in lastmod:
            lastmod = lastmod.split("T")[0]
        elif not isinstance(lastmod, str):
            lastmod = today
        slug = _generate_slug(p.get("caption") or "")
        urls.append(
            f"  <url>\n"
            f"    <loc>{_STOREFRONT_DOMAIN}/product/{pid}/{slug}</loc>\n"
            f"    <lastmod>{lastmod}</lastmod>\n"
            f"    <changefreq>monthly</changefreq>\n"
            f"    <priority>0.8</priority>\n"
            f"  </url>"
        )

    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>"
    )
    return Response(content=sitemap, media_type="application/xml")


@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def serve_homepage(request: Request):
    """Serve the homepage with default Storefront SEO metadata."""
    ctx = _build_seo_context(
        title="Top Shelf Cali Plug – Premium Cannabis Storefront",
        desc="Browse our premium cannabis catalog. Order directly via Telegram.",
        url=_STOREFRONT_DOMAIN,
    )
    ctx["request"] = request
    return templates.TemplateResponse("index.html", ctx)


@app.get("/webapp/index.html", response_class=HTMLResponse)
@app.get("/webapp/", response_class=HTMLResponse)
@app.get("/webapp", response_class=HTMLResponse)
async def serve_webapp(request: Request):
    """Serve the WebApp for Telegram Mini Apps.

    This renders the same index.html template as the homepage, but at the
    /webapp/index.html path that Telegram bots reference via WEB_APP_URL.
    Keeps backward compatibility with the original webapp path while the
    clearnet storefront is served at /.
    """
    ctx = _build_seo_context(
        title="Top Shelf Cali Plug – Premium Cannabis Storefront",
        desc="Browse our premium cannabis catalog. Order directly via Telegram.",
        url=_STOREFRONT_DOMAIN,
    )
    ctx["request"] = request
    return templates.TemplateResponse("index.html", ctx)


@app.get("/product/{product_id}", response_class=HTMLResponse)
async def serve_product_page(product_id: int, request: Request):
    """Serve an individual product page with dynamic SEO metadata."""
    product = await _catalog_db.get_product(product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    # Build SEO fields from product data
    caption: str = product.get("caption") or ""
    # First line of caption as title, rest as description
    lines = [l.strip() for l in caption.splitlines() if l.strip()]
    seo_title = lines[0] if lines else f"Product #{product_id}"
    seo_desc = " ".join(lines[1:]) if len(lines) > 1 else seo_title

    # Prefer photo file_id represented as a media URL served by this server
    media_ids_json = product.get("media_ids") or "[]"
    try:
        media_ids = json.loads(media_ids_json) if isinstance(media_ids_json, str) else media_ids_json
    except Exception:
        media_ids = []
    seo_image = f"{_STOREFRONT_DOMAIN}/api/media/{product_id}/0" if media_ids else ""

    product_url = f"{_STOREFRONT_DOMAIN}/product/{product_id}/{_generate_slug(caption)}"
    deep_link = f"https://t.me/{_STOREFRONT_BOT_USERNAME}?start=product_{product_id}"

    ctx = _build_seo_context(
        title=seo_title,
        desc=seo_desc,
        image=seo_image,
        url=product_url,
        product=product,
        deep_link=deep_link,
        keywords=", ".join(filter(None, [
            product.get("category", ""),
            product.get("subcategory", ""),
            " ".join(caption.split()[:6]),
            "Top Shelf Cali Plug",
            "buy online",
            "Telegram store",
        ])),
        category=product.get("category", ""),
    )
    ctx["request"] = request
    return templates.TemplateResponse("index.html", ctx)


@app.get("/product/{product_id}/{slug}", response_class=HTMLResponse)
async def serve_product_page_with_slug(product_id: int, slug: str, request: Request):
    """Serve an individual product page using a slug URL — delegates to serve_product_page."""
    return await serve_product_page(product_id, request)


@app.get("/category/{category_name}", response_class=HTMLResponse)
async def serve_category_page(category_name: str, request: Request):
    """Serve a server-rendered category landing page for SEO crawlability."""
    # Case-insensitive match against canonical category names
    all_categories = get_all_categories()
    matched_category = next(
        (c for c in all_categories if c.lower() == category_name.lower()),
        None,
    )
    if matched_category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    display_name = get_category_display_name(matched_category)
    subcategories = get_subcategories(matched_category)

    # Fetch products for this category (limit 50 for the page)
    products = await _catalog_db.get_products_by_category(matched_category, limit=50)
    total = await _catalog_db.count_products_by_category(matched_category)

    # Get subcategory counts
    all_subcategories_data = await _catalog_db.get_all_subcategories_with_counts()
    subcategory_counts = {
        item["subcategory"]: item["count"]
        for item in all_subcategories_data.get(matched_category, [])
    }

    category_url = f"{_STOREFRONT_DOMAIN}/category/{urllib.parse.quote(matched_category)}"

    # Build keyword list from category + subcategory names
    subcat_names = ", ".join(subcategories) if subcategories else ""
    keywords = ", ".join(filter(None, [
        display_name,
        subcat_names,
        "Top Shelf Cali Plug",
        "buy online",
    ]))

    ctx = _build_seo_context(
        title=f"{display_name} | Top Shelf Cali Plug",
        desc=f"Browse our {display_name} collection. {total} products available. Order via Telegram.",
        url=category_url,
        keywords=keywords,
    )

    # Attach extra context for the category template
    ctx["request"] = request
    ctx["category_name"] = matched_category
    ctx["category_display"] = display_name
    ctx["subcategories"] = [
        {"name": s, "display": get_subcategory_display_name(s), "count": subcategory_counts.get(s, 0)}
        for s in subcategories
    ]
    ctx["products"] = [
        {
            "id": p.get("id") or p.get("product_id"),
            "caption": p.get("caption") or "",
            "category": p.get("category") or "",
            "subcategory": p.get("subcategory") or "",
            "subcategory_display": get_subcategory_display_name(p.get("subcategory")) if p.get("subcategory") else "",
            "slug": _generate_slug(p.get("caption") or ""),
        }
        for p in products
    ]
    ctx["total"] = total
    ctx["telegram_bot_url"] = f"https://t.me/{_STOREFRONT_BOT_USERNAME}"
    ctx["storefront_domain"] = _STOREFRONT_DOMAIN

    # Build ItemList + BreadcrumbList schema
    item_list_schema = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": display_name,
        "url": category_url,
        "numberOfItems": total,
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "url": f"{_STOREFRONT_DOMAIN}/product/{p['id']}/{p['slug']}",
                "name": (p["caption"].split("\n")[0].strip() if p["caption"] else f"Product #{p['id']}"),
            }
            for i, p in enumerate(ctx["products"])
        ],
    }
    breadcrumb_schema = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{_STOREFRONT_DOMAIN}/"},
            {"@type": "ListItem", "position": 2, "name": display_name, "item": category_url},
        ],
    }
    ctx["seo_schema"] = json.dumps({"@context": "https://schema.org", "@graph": [item_list_schema, breadcrumb_schema]})

    return templates.TemplateResponse("category.html", ctx)


def _get_primary_bot(request: Request, required: bool = True):
    """
    Return the primary bot instance or raise a 503 if unavailable and required.
    
    Args:
        request: FastAPI request object
        required: If True, raises HTTPException when bot is not available. If False, returns None.
    
    Returns:
        Bot instance or None if not required and not available
    """
    bot_apps = getattr(request.app.state, "bot_apps", [])
    if not bot_apps:
        if required:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot not initialized")
        return None
    return bot_apps[0].bot


def _get_validated_init_data(request: Request, allow_preview: bool = False) -> tuple:
    """
    Validate Telegram WebApp initData from header or query params.
    
    Args:
        request: FastAPI request object
        allow_preview: If True, allows missing/invalid initData for preview mode
    
    Returns:
        Tuple of (validated initData string, bot_index that matched).
        In preview mode with no valid auth returns ("", -1).
    """
    init_data = request.headers.get("X-Telegram-Init-Data")
    
    # If not in header, try query params with robust extraction
    if not init_data:
        # Use urllib to parse raw query string to avoid FastAPI/Starlette auto-decoding quirks
        # that might break the hash validation
        if request.url.query:
            try:
                qs = urllib.parse.parse_qs(request.url.query)
                if "init_data" in qs:
                    init_data = qs["init_data"][0]
            except (ValueError, KeyError, IndexError) as e:
                logger.debug(f"Failed to parse init_data from raw query string: {e}")
        
        # Fallback to standard FastAPI extraction if manual parsing failed
        if not init_data:
            init_data = request.query_params.get("init_data")
    
    if not init_data:
        if allow_preview:
            logger.debug("No initData provided, allowing preview mode access")
            return "", -1
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing init data")
    
    # Validate using any configured bot token
    bot_tokens = Config.BOT_TOKENS
    if not bot_tokens:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot token not configured")
    
    # Try to validate against each bot token
    for idx, token in enumerate(bot_tokens):
        if verify_init_data(init_data, token):
            if len(bot_tokens) > 1:
                logger.debug(f"InitData validated successfully with bot token {idx}")
            return init_data, idx

    # If none of the tokens validated successfully, handle the failure
    # Log a single warning with context about how many tokens were tried
    if allow_preview:
        logger.warning(
            "InitData validation failed for all %d configured bot token(s), allowing preview mode access",
            len(bot_tokens)
        )
        return "", -1

    logger.warning(
        "InitData validation failed: no match among %d configured bot token(s)",
        len(bot_tokens)
    )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid init data")


def _collect_media_ids(product: dict) -> List[str]:
    """Collect primary and additional file_ids for a product."""
    media_ids: List[str] = []
    
    primary_id = product.get("file_id")
    if primary_id:
        media_ids.append(primary_id)
    
    additional_raw = product.get("additional_file_ids")
    if additional_raw:
        try:
            additional_ids = json.loads(additional_raw)
            if isinstance(additional_ids, list):
                # Handle both string file_ids and (file_id, file_type) tuples
                for item in additional_ids:
                    if isinstance(item, str):
                        media_ids.append(item)
                    elif isinstance(item, (list, tuple)) and len(item) >= 1:
                        # Extract file_id from (file_id, file_type) tuple
                        media_ids.append(item[0])
        except Exception:
            logger.warning("Failed to parse additional_file_ids for product %s", product.get("id"))
    
    return media_ids


def _collect_media_info(product: dict) -> List[dict]:
    """Collect media information (id and type) for a product."""
    media_info: List[dict] = []
    
    # Add primary media
    primary_id = product.get("file_id")
    primary_type = product.get("file_type", "photo")
    if primary_id:
        media_info.append({"id": primary_id, "type": primary_type})
    
    # Add additional media
    additional_raw = product.get("additional_file_ids")
    if additional_raw:
        try:
            additional_ids = json.loads(additional_raw)
            if isinstance(additional_ids, list):
                for item in additional_ids:
                    if isinstance(item, str):
                        # Just file_id, assume photo
                        media_info.append({"id": item, "type": "photo"})
                    elif isinstance(item, (list, tuple)) and len(item) >= 2:
                        # (file_id, file_type) tuple
                        media_info.append({"id": item[0], "type": item[1]})
                    elif isinstance(item, (list, tuple)) and len(item) == 1:
                        # Just file_id in tuple
                        media_info.append({"id": item[0], "type": "photo"})
        except Exception:
            logger.warning("Failed to parse additional_file_ids for product %s", product.get("id"))
    
    return media_info


def _normalize_contact(contact: str) -> str:
    """Normalize @username contact for linking."""
    if not contact:
        return ""
    contact = contact.strip()
    return contact[1:] if contact.startswith("@") else contact


@app.get("/api/categories")
async def get_categories(request: Request, lang: str = "en"):
    """
    Get all categories with subcategories and product counts.
    Returns the hierarchical category structure matching the bot's menu.
    Supports translations via lang parameter.
    """
    _get_validated_init_data(request, allow_preview=True)
    
    # Get product counts for all categories
    category_counts = await _catalog_db.get_all_category_counts()
    
    # Get all subcategories with counts in a single query (optimization)
    all_subcategories = await _catalog_db.get_all_subcategories_with_counts()
    
    # Build category structure
    categories = []
    for category in get_all_categories():
        subcategories_list = get_subcategories(category)
        
        # Translate category display name
        # Translation keys follow pattern: "category_{category_name_lowercase}"
        # e.g., "category_cartridges", "category_edibles"
        category_display = get_category_display_name(category)
        if lang and lang != "en":
            try:
                # Try to get translation for category
                category_key = f"category_{category.lower()}"
                category_display = await get_translated_string_async(
                    category_key, 
                    lang,
                    default=category_display
                )
            except Exception as e:
                logger.debug(f"Could not translate category {category}: {e}")
                # Fall back to original display name
        
        category_data = {
            "name": category,
            "display_name": category_display,
            "count": category_counts.get(category, 0),
            "has_subcategories": len(subcategories_list) > 0,
            "subcategories": []
        }
        
        # Add subcategory counts if applicable
        if subcategories_list:
            subcategory_data = all_subcategories.get(category, [])
            count_dict = {item["subcategory"]: item["count"] for item in subcategory_data}
            
            for subcat in subcategories_list:
                # Use the same helper function that the bot uses for consistent translation
                subcat_display = get_subcategory_display_name(subcat, lang)
                
                category_data["subcategories"].append({
                    "name": subcat,
                    "display_name": subcat_display,
                    "count": count_dict.get(subcat, 0)
                })
        
        categories.append(category_data)
    
    # Add "All Products" count
    all_products_count = await _catalog_db.count_products_excluding_categories(EXCLUDED_FROM_ALL_PRODUCTS)
    
    # Return with a short cache hint: categories change infrequently so clients
    # can reuse the response for up to 60 seconds before revalidating.
    return Response(
        content=json.dumps({
            "categories": categories,
            "all_products_count": all_products_count
        }),
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=60"}
    )


@app.get("/api/products")
async def get_products(
    request: Request, 
    category: str = None, 
    subcategory: str = None, 
    limit: int = 24, 
    offset: int = 0,
    lang: str = "en"
):
    """
    Get products filtered by category and/or subcategory.
    If no filters provided, returns all products (excluding EXCLUDED_FROM_ALL_PRODUCTS).
    Supports translations via lang parameter.
    """
    _get_validated_init_data(request, allow_preview=True)
    bot = _get_primary_bot(request, required=False)
    
    # Bound limit for performance
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    
    # Fetch products based on filters
    if category and subcategory:
        products = await _catalog_db.get_products_by_category_and_subcategory(
            category, subcategory, limit=limit, offset=offset
        )
        total = await _catalog_db.count_products_by_category_and_subcategory(category, subcategory)
    elif category:
        products = await _catalog_db.get_products_by_category(category, limit=limit, offset=offset)
        total = await _catalog_db.count_products_by_category(category)
    else:
        # All products (excluding certain categories)
        products = await _catalog_db.get_all_products_excluding_categories(
            EXCLUDED_FROM_ALL_PRODUCTS, limit=limit, offset=offset
        )
        total = await _catalog_db.count_products_excluding_categories(EXCLUDED_FROM_ALL_PRODUCTS)

    order_contact = await _catalog_db.get_order_contact()
    order_contact_username = _normalize_contact(order_contact)
    order_link = await _catalog_db.get_order_link()

    items = []
    for product in products:
        media_info = _collect_media_info(product)
        
        # Get caption and translate if needed
        caption = product.get("caption") or ""
        if lang and lang != "en" and caption:
            try:
                # Translate caption using translation service
                caption = await translation_service.translate(caption, lang, source_lang="en")
            except Exception as e:
                logger.debug(f"Failed to translate caption for product {product.get('id')}: {e}")
                # Fall back to original caption
        
        items.append({
            "id": product.get("id"),
            "caption": caption,
            "category": product.get("category") or "",
            "subcategory": product.get("subcategory") or "",
            "subcategory_display": get_subcategory_display_name(product.get("subcategory"), lang) if product.get("subcategory") else "",
            "created_at": product.get("created_at"),
            "media_count": len(media_info),
            "media_info": media_info,  # Include full media info with types
        })
    
    return Response(
        content=json.dumps({
            "items": items,
            "total": total,
            "bot": bot.username if bot and getattr(bot, "username", None) else None,
            "order_contact": order_contact,
            "order_contact_username": order_contact_username,
            "order_link": order_link
        }),
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=30"}
    )


@app.get("/api/catalog")
async def get_catalog(request: Request, limit: int = 24, offset: int = 0):
    """
    Read-only catalog endpoint for the WebApp.
    Returns lightweight product metadata; media is streamed via the media endpoint.
    DEPRECATED: Use /api/products instead for better filtering support.
    """
    _get_validated_init_data(request)
    bot = _get_primary_bot(request)
    
    # Bound limit for performance
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    
    products = await _catalog_db.get_all_products(limit=limit, offset=offset)
    total = await _catalog_db.count_products()
    order_contact = await _catalog_db.get_order_contact()
    order_contact_username = _normalize_contact(order_contact)
    
    items = []
    for product in products:
        media_info = _collect_media_info(product)
        items.append({
            "id": product.get("id"),
            "caption": product.get("caption") or "",
            "category": product.get("category") or "",
            "subcategory": product.get("subcategory") or "",
            "subcategory_display": get_subcategory_display_name(product.get("subcategory")) if product.get("subcategory") else "",
            "created_at": product.get("created_at"),
            "media_count": len(media_info),
            "media_info": media_info,  # Include full media info with types
        })
    
    return {
        "items": items,
        "total": total,
        "bot": bot.username if bot and getattr(bot, "username", None) else None,
        "order_contact": order_contact,
        "order_contact_username": order_contact_username
    }




@app.get("/api/catalog/{product_id}/media/{media_index}")
async def stream_catalog_media(request: Request, product_id: int, media_index: int):
    """
    Stream a single media file for a product without exposing the bot token.
    Uses file caching and efficient streaming to optimize performance.
    Returns metadata for files that cannot be streamed (too large).

    Args:
        product_id: Product ID
        media_index: Index of media in product's media array
    """
    _get_validated_init_data(request, allow_preview=True)
    bot = _get_primary_bot(request)

    # Define size limits
    WEB_STREAMING_LIMIT = 50 * 1024 * 1024  # 50MB - absolute max

    product = await _catalog_db.get_product(product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    media_info = _collect_media_info(product)
    if media_index < 0 or media_index >= len(media_info):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")

    # Note: We validate against media_info length which should match media_ids
    # This ensures we have both the media type info and the file_id
    media_ids = _collect_media_ids(product)
    file_id = media_ids[media_index]
    media_type_info = media_info[media_index]
    is_video = media_type_info.get('type') == 'video'

    # Resolve bot-specific file_id for multi-bot scenarios
    # This ensures the file_id works with the current bot instance even if
    # the file was originally indexed by a different bot
    source_chat_id = product.get('chat_id')
    source_message_id = product.get('message_id')

    # For additional media in media groups, we need to get the correct message_id
    if media_index > 0:
        additional_message_ids_json = product.get('additional_message_ids')
        if additional_message_ids_json:
            try:
                additional_message_ids = json.loads(additional_message_ids_json)
                if media_index - 1 < len(additional_message_ids):
                    source_message_id = additional_message_ids[media_index - 1]
            except (json.JSONDecodeError, TypeError, IndexError) as e:
                logger.warning(
                    f"Failed to parse additional_message_ids JSON for product {product_id}, "
                    f"media_index {media_index}. Data: {additional_message_ids_json}. Error: {e}"
                )

    if source_chat_id and source_message_id:
        try:
            # Try to get bot-specific file_id
            file_type = media_type_info.get('type', 'document')
            bot_username = bot.username or ""
            _res_key = (source_chat_id, source_message_id, 0, bot_username)
            # Check lightweight in-memory cache first to avoid DB lookup on repeated
            # Range 206 requests for the same media item.
            bot_specific_file_id = _resolved_file_id_cache.get(_res_key)
            if bot_specific_file_id is None:
                bot_specific_file_id = await get_bot_specific_file_id(
                    bot,
                    source_chat_id,
                    source_message_id,
                    file_type,
                    file_index=0  # file_index within this specific message (always 0 since each Telegram message contains exactly one media item)
                )
                if bot_specific_file_id:
                    _cache_resolved_file_id(_res_key, bot_specific_file_id)

            if bot_specific_file_id:
                logger.debug(
                    f"Using bot-specific file_id for product {product_id}, media {media_index}: "
                    f"original={file_id[:20]}..., bot_specific={bot_specific_file_id[:20]}..."
                )
                file_id = bot_specific_file_id
            else:
                logger.debug(
                    f"Bot-specific file_id not available for product {product_id}, media {media_index}, "
                    f"using original file_id"
                )
        except Exception as e:
            logger.warning(
                f"Error getting bot-specific file_id for product {product_id}, media {media_index}: {e}. "
                f"Falling back to original file_id"
            )
    else:
        logger.debug(
            f"Product {product_id} missing chat_id or message_id, cannot resolve bot-specific file_id"
        )

    # Structured logging for diagnostics
    logger.debug(
        f"Media request: product_id={product_id}, media_index={media_index}, "
        f"is_video={is_video}, file_id={file_id[:20]}..."
    )

    # Check cache first for significant performance improvement
    cached_file_path = await _media_cache.get(file_id)
    file_path = None
    file_size = 0
    temp_file_to_cleanup = None

    if cached_file_path:
        logger.debug(f"Serving from cache: product {product_id}, media {media_index}")
        file_path = cached_file_path
        file_size = file_path.stat().st_size
    else:
        # Not in cache, need to download from Telegram
        try:
            # Try to get file info - this can fail for files >20MB
            try:
                tg_file = await bot.get_file(file_id)
                file_size = tg_file.file_size or 0

                # Log file size retrieved
                logger.debug(
                    f"File info retrieved: product_id={product_id}, media_index={media_index}, "
                    f"file_id={file_id[:20]}..., file_size={file_size} bytes "
                    f"({file_size / (1024*1024):.2f} MB)"
                )
            except telegram.error.BadRequest as e:
                if "file is too big" in str(e).lower():
                    # File >20MB - cannot use getFile API
                    logger.warning(
                        f"File too large for getFile API "
                        f"(product {product_id}, media {media_index})"
                    )
                    return Response(
                        content=json.dumps({
                            "error": "file_too_large",
                            "message": "File exceeds Telegram Bot API limit.",
                            "can_download": False
                        }),
                        media_type="application/json",
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                    )
                else:
                    # Re-raise other BadRequest errors
                    raise

            # If file is too large for web streaming, return error
            if file_size > WEB_STREAMING_LIMIT:
                logger.warning(
                    "File too large for web streaming - product %s, media %s, size %s bytes",
                    product_id, media_index, file_size
                )
                return Response(
                    content=json.dumps({
                        "error": "file_too_large",
                        "file_size": file_size,
                        "message": "File is too large to stream. Please request it in the bot chat.",
                        "can_download": False
                    }),
                    media_type="application/json",
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                )

            # Download the file via Bot API to temp location
            temp_input_path = None
            try:
                # Create temporary file for download
                with tempfile.NamedTemporaryFile(mode='wb', suffix='.mp4' if is_video else '', delete=False) as temp_file:
                    temp_input_path = temp_file.name

                # Download directly to disk (streaming from Telegram)
                await tg_file.download_to_drive(custom_path=temp_input_path)
                logger.debug(f"Downloaded file to disk for product {product_id}, size={file_size / (1024*1024):.2f}MB")

                # Store in cache for future requests
                file_path = await _media_cache.set(file_id, Path(temp_input_path))
                temp_file_to_cleanup = temp_input_path  # Mark for cleanup after response
                logger.debug(f"File cached: product {product_id}, media {media_index}")

            except telegram.error.BadRequest as e:
                # Cleanup temp file if download failed
                if temp_input_path:
                    try:
                        Path(temp_input_path).unlink()
                    except (OSError, FileNotFoundError):
                        pass
                raise

        except telegram.error.BadRequest as e:
            if "File is too big" not in str(e):
                logger.exception("Unable to fetch file for product %s: %s", product_id, e)
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Media unavailable")
            # If it's "File is too big", it should have been handled above
            raise
        except Exception as e:
            logger.exception("Unable to fetch file for product %s: %s", product_id, e)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Media unavailable")

    # Determine media type
    media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    # For videos, ensure correct content type for browser compatibility
    if is_video and media_type == "application/octet-stream":
        media_type = "video/mp4"

    content_length = file_size if file_size > 0 else file_path.stat().st_size

    # Final log: summarize what we're serving
    logger.debug(
        f"Serving media: product_id={product_id}, media_index={media_index}, "
        f"is_video={is_video}, file_size={content_length} bytes "
        f"({content_length / (1024*1024):.2f} MB), media_type={media_type}"
    )

    # Handle HTTP Range requests for proper video seeking/streaming
    range_header = request.headers.get("range")
    if range_header:
        try:
            # Parse range header (e.g., "bytes=0-1024")
            range_spec = range_header.replace("bytes=", "")
            range_parts = range_spec.split("-")
            start = int(range_parts[0]) if range_parts[0] else 0
            end = int(range_parts[1]) if range_parts[1] else content_length - 1
            end = min(end, content_length - 1)

            # Stream the requested range efficiently
            async def stream_range():
                with open(file_path, 'rb') as f:
                    f.seek(start)
                    remaining = end - start + 1
                    chunk_size = 8192
                    while remaining > 0:
                        chunk = f.read(min(chunk_size, remaining))
                        if not chunk:
                            break
                        yield chunk
                        remaining -= len(chunk)

                # Cleanup temp file after streaming if needed
                if temp_file_to_cleanup:
                    try:
                        Path(temp_file_to_cleanup).unlink()
                    except (OSError, FileNotFoundError):
                        pass

            return StreamingResponse(
                stream_range(),
                status_code=206,
                media_type=media_type,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{content_length}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(end - start + 1),
                    "Cache-Control": "public, max-age=3600"  # Extended cache for better performance
                }
            )
        except (ValueError, IndexError) as e:
            logger.warning(f"Invalid range header: {range_header}, error: {e}")
            pass  # Fall through to full response

    # Stream the full file efficiently (no loading into memory)
    async def stream_file():
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                yield chunk

        # Cleanup temp file after streaming if needed
        if temp_file_to_cleanup:
            try:
                Path(temp_file_to_cleanup).unlink()
            except (OSError, FileNotFoundError):
                pass

    return StreamingResponse(
        stream_file(),
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=3600",  # Extended cache for better performance
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length)
        }
    )


@app.get("/api/search")
async def search_products(
    request: Request,
    q: str = "",
    limit: int = 24,
    offset: int = 0
):
    """
    Search products by query string (searches caption, category, and subcategory).
    Excludes products from Dated Proofs, Client Touchdowns, and Announcements categories.
    Optimized server-side search to reduce client-side processing.
    """
    _get_validated_init_data(request, allow_preview=True)
    
    if not q or len(q) < 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Query must be at least 2 characters")
    
    # Bound limit for performance
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    
    # Categories to exclude from search results
    excluded_categories = ['DATEDPROOFS', 'CLIENTTOUCHDOWNS', 'ANNOUNCEMENTS']
    
    try:
        # Use database search for efficiency
        query_lower = q.lower()
        
        # Search across all products
        async with aiosqlite.connect(_catalog_db.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Apply the same pragmas used by the optimized DB connection to ensure
            # consistent performance: WAL mode allows concurrent reads, 10 MB page cache
            # reduces disk I/O, and NORMAL sync is safe under WAL.
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=30000")
            await db.execute("PRAGMA cache_size=-10000")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("PRAGMA temp_store=MEMORY")
            # Build placeholders for excluded categories
            placeholders = ','.join('?' * len(excluded_categories))
            
            # Count total matching results (excluding specific categories)
            count_query = f"""
                SELECT COUNT(*) as count FROM products 
                WHERE (LOWER(caption) LIKE ? 
                OR LOWER(category) LIKE ? 
                OR LOWER(subcategory) LIKE ?)
                AND (category IS NULL OR category = '' OR category NOT IN ({placeholders}))
            """
            search_pattern = f"%{query_lower}%"
            
            async with db.execute(
                count_query, 
                (search_pattern, search_pattern, search_pattern, *excluded_categories)
            ) as cursor:
                count_row = await cursor.fetchone()
                total = count_row["count"] if count_row else 0
            
            # Fetch matching products with pagination (excluding specific categories)
            search_query = f"""
                SELECT * FROM products 
                WHERE (LOWER(caption) LIKE ? 
                OR LOWER(category) LIKE ? 
                OR LOWER(subcategory) LIKE ?)
                AND (category IS NULL OR category = '' OR category NOT IN ({placeholders}))
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            
            products = []
            async with db.execute(
                search_query, 
                (search_pattern, search_pattern, search_pattern, *excluded_categories, limit, offset)
            ) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    products.append(dict(row))
        
        order_contact = await _catalog_db.get_order_contact()
        order_contact_username = _normalize_contact(order_contact)
        
        items = []
        for product in products:
            media_info = _collect_media_info(product)
            items.append({
                "id": product.get("id"),
                "caption": product.get("caption") or "",
                "category": product.get("category") or "",
                "subcategory": product.get("subcategory") or "",
                "subcategory_display": get_subcategory_display_name(product.get("subcategory")) if product.get("subcategory") else "",
                "created_at": product.get("created_at"),
                "media_count": len(media_info),
                "media_info": media_info,
            })
        
        return {
            "items": items,
            "total": total,
            "query": q,
            "order_contact": order_contact,
            "order_contact_username": order_contact_username
        }
        
    except Exception as e:
        logger.exception(f"Search failed for query '{q}': {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Search failed")

@app.get("/api/user/subscription")
async def get_user_subscription(request: Request, user_id: int):
    """Get user subscription status."""
    _get_validated_init_data(request, allow_preview=False)  # Require auth for user data
    
    try:
        async with aiosqlite.connect(_catalog_db.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT is_subscribed FROM bot_users WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {"is_subscribed": bool(row["is_subscribed"])}
                else:
                    # User not in database, default to subscribed
                    return {"is_subscribed": True}
    except Exception as e:
        logger.exception(f"Error getting user subscription: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get subscription status")


@app.post("/api/user/subscription")
async def update_user_subscription(request: Request):
    """Update user subscription status."""
    _get_validated_init_data(request, allow_preview=False)  # Require auth for user data
    
    try:
        data = await request.json()
        user_id = data.get("user_id")
        is_subscribed = data.get("is_subscribed", True)
        
        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id required")
        
        async with aiosqlite.connect(_catalog_db.db_path) as db:
            # Check if user exists
            async with db.execute("SELECT user_id FROM bot_users WHERE user_id = ?", (user_id,)) as cursor:
                exists = await cursor.fetchone()
            
            if exists:
                # Update existing user
                await db.execute(
                    "UPDATE bot_users SET is_subscribed = ? WHERE user_id = ?",
                    (1 if is_subscribed else 0, user_id)
                )
            else:
                # Create new user record
                await db.execute(
                    "INSERT INTO bot_users (user_id, is_subscribed) VALUES (?, ?)",
                    (user_id, 1 if is_subscribed else 0)
                )
            
            await db.commit()
            
        logger.info(f"User {user_id} subscription updated to: {is_subscribed}")
        return {"success": True, "is_subscribed": is_subscribed}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating user subscription: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update subscription")


@app.post("/api/webapp-logs")
async def webapp_logs(request: Request):
    """
    Accept and log webapp events for diagnostics and analytics.
    Logs include user interactions, API calls, video playback events, and errors.
    """
    try:
        # Allow preview mode for logging (doesn't require full auth)
        _get_validated_init_data(request, allow_preview=True)
        
        data = await request.json()
        logs = data.get("logs", [])
        
        if not logs:
            return {"success": True, "message": "No logs to process"}
        
        # Log each event with structured format
        for log_entry in logs:
            timestamp = log_entry.get("timestamp", "")
            user_id = log_entry.get("user_id", "unknown")
            username = log_entry.get("username", "unknown")
            event_type = log_entry.get("event_type", "unknown")
            action = log_entry.get("action", "")
            status = log_entry.get("status", "")
            error = log_entry.get("error", "")
            details = log_entry.get("details", {})
            
            # Format log message with full context
            log_msg = f"[WebApp] user_id={user_id} username={username} event={event_type} action={action} status={status}"
            if error:
                log_msg += f" error={error}"
            if details:
                log_msg += f" details={json.dumps(details)}"
            
            # Log at appropriate level based on status
            if status == "error" or error:
                logger.error(log_msg)
            elif status == "warning":
                logger.warning(log_msg)
            else:
                logger.info(log_msg)
        
        return {"success": True, "processed": len(logs)}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error processing webapp logs: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to process logs")


@app.get("/api/user/info")
async def get_user_info(request: Request):
    """
    Get user information from Telegram initData.
    This endpoint extracts user_id and other info from the initData header,
    providing a reliable way for the webapp to get user details even when
    tg.initDataUnsafe is not available in the frontend.
    """
    try:
        # Allow preview mode but prefer authenticated data
        init_data, _ = _get_validated_init_data(request, allow_preview=True)
        
        if not init_data:
            # No initData available
            return {
                "authenticated": False,
                "user_id": None,
                "username": None,
                "first_name": None,
                "last_name": None
            }
        
        # Parse user info from initData
        parsed_data = parse_init_data(init_data)
        user_data = parsed_data.get("user")
        
        if not user_data:
            return {
                "authenticated": False,
                "user_id": None,
                "username": None,
                "first_name": None,
                "last_name": None
            }
        
        # Extract user information
        user_id = user_data.get("id")
        username = user_data.get("username")
        first_name = user_data.get("first_name")
        last_name = user_data.get("last_name")
        language_code = user_data.get("language_code")
        
        return {
            "authenticated": True,
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "language_code": language_code
        }
    except Exception as e:
        logger.exception(f"Error getting user info: {e}")
        # Return unauthenticated response instead of error
        return {
            "authenticated": False,
            "user_id": None,
            "username": None,
            "first_name": None,
            "last_name": None
        }


@app.post("/api/user/track")
async def track_webapp_user(request: Request):
    """
    Track a webapp user in the bot_users database.
    Called when the webapp opens to ensure user is registered in the bot's
    user database for analytics and tracking purposes.
    """
    try:
        init_data, bot_index = _get_validated_init_data(request, allow_preview=True)
        
        data = await request.json()
        user_id = data.get("user_id")
        username = data.get("username")
        first_name = data.get("first_name")
        last_name = data.get("last_name")
        
        # Try to extract user info from initData if not in request body
        if not user_id and init_data:
            parsed_data = parse_init_data(init_data)
            user_data = parsed_data.get("user")
            if user_data:
                user_id = user_data.get("id")
                username = username or user_data.get("username")
                first_name = first_name or user_data.get("first_name")
                last_name = last_name or user_data.get("last_name")
        
        if not user_id:
            return {"success": False, "message": "No user_id available"}
        
        # Track user in the bot_users database, using the bot that validated the request
        bot_apps = getattr(request.app.state, "bot_apps", [])
        bot_username = None
        if bot_apps:
            if 0 <= bot_index < len(bot_apps):
                # Use the specific bot that validated this request
                bot_app = bot_apps[bot_index]
            else:
                # bot_index == -1: preview/unauthenticated mode, fall back to primary bot
                bot_app = bot_apps[0]
            if hasattr(bot_app, 'bot') and hasattr(bot_app.bot, 'username'):
                bot_username = bot_app.bot.username
        
        await _catalog_db.track_user(
            user_id=int(user_id),
            username=username,
            first_name=first_name,
            last_name=last_name,
            bot_username=bot_username
        )
        
        logger.info(f"[WebApp] Tracked user {user_id} (username={username}) in bot_users database")
        
        return {
            "success": True,
            "user_id": user_id,
            "tracked": True
        }
        
    except Exception as e:
        logger.exception(f"Error tracking webapp user: {e}")
        return {"success": False, "message": "Failed to track user"}


@app.post("/webhook")
async def webhook(request: Request):
    """Handle incoming webhook updates from Telegram for the primary bot (bot 0)."""
    logger.info("Received webhook request for primary bot")
    
    # Check if bots are initialized
    if not hasattr(request.app.state, "bot_apps") or not request.app.state.bot_apps:
        logger.error("Bot applications not initialized")
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    
    try:
        # Parse the update
        json_data = await request.json()
        # Use the first bot (primary bot at index 0)
        bot_app = request.app.state.bot_apps[0]
        update = Update.de_json(json_data, bot_app.bot)
        
        # Log update details
        if update.message:
            logger.info(f"Processing message update from user {update.message.from_user.id}")
        elif update.callback_query:
            logger.info(f"Processing callback query from user {update.callback_query.from_user.id}: {update.callback_query.data}")
        elif update.channel_post:
            logger.info(f"Processing channel post from chat {update.channel_post.chat.id}")
        else:
            logger.info(f"Processing update type: {update.update_id}")
        
        # Process the update
        await bot_app.process_update(update)
        
        logger.info("Webhook request processed successfully for primary bot")
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error processing webhook update for primary bot: {e}", exc_info=True)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.post("/webhook/{bot_index}")
async def webhook_indexed(request: Request, bot_index: int):
    """Handle incoming webhook updates from Telegram for additional bot instances.
    
    Note: The primary bot (bot 0) is accessed via /webhook, not /webhook/0.
    This endpoint handles bots 1, 2, 3, etc.
    """
    logger.info(f"Received webhook request for bot {bot_index}")
    
    # Check if bots are initialized
    if not hasattr(request.app.state, "bot_apps") or not request.app.state.bot_apps:
        logger.error("Bot applications not initialized")
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    
    # Validate bot index (must be >= 1 for this endpoint)
    if bot_index >= len(request.app.state.bot_apps):
        logger.error(f"Invalid bot index: {bot_index}. Available bots: 0-{len(request.app.state.bot_apps) - 1}")
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    
    if bot_index == 0:
        logger.warning("Bot 0 should use /webhook endpoint, not /webhook/0. Redirecting...")
        # Still process it for convenience, but log the warning
    
    try:
        # Parse the update
        json_data = await request.json()
        bot_app = request.app.state.bot_apps[bot_index]
        update = Update.de_json(json_data, bot_app.bot)
        
        # Log update details
        if update.message:
            logger.info(f"Bot {bot_index}: Processing message update from user {update.message.from_user.id}")
        elif update.callback_query:
            logger.info(f"Bot {bot_index}: Processing callback query from user {update.callback_query.from_user.id}: {update.callback_query.data}")
        elif update.channel_post:
            logger.info(f"Bot {bot_index}: Processing channel post from chat {update.channel_post.chat.id}")
        else:
            logger.info(f"Bot {bot_index}: Processing update type: {update.update_id}")
        
        # Process the update
        await bot_app.process_update(update)
        
        logger.info(f"Webhook request processed successfully for bot {bot_index}")
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error processing webhook update for bot {bot_index}: {e}", exc_info=True)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.get("/health")
@app.head("/health")
async def health():
    """Health check endpoint for deployment platforms."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment or default to 8000
    port = int(os.getenv("PORT", "8000"))
    
    logger.info(f"Starting webhook server on port {port}")
    
    # Run the server with access logs enabled
    uvicorn.run(
        "webhook_server:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True  # Explicitly enable access logs
    )
