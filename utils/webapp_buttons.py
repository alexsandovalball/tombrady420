"""Helpers for Telegram Mini App launch buttons."""

from __future__ import annotations

from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from configs.config import Config
from translations.translator import get_translated_string_async
from utils.deeplinks import build_webapp_product_url, normalize_webapp_url


def build_webapp_button(
    text: str,
    *,
    product_id: Optional[int] = None,
) -> Optional[InlineKeyboardButton]:
    """Return a Telegram Mini App button when the WebApp URL is configured."""
    if product_id is None:
        url = normalize_webapp_url(Config.WEB_APP_URL)
    else:
        url = build_webapp_product_url(Config.WEB_APP_URL, product_id)

    if not url:
        return None

    return InlineKeyboardButton(text, web_app=WebAppInfo(url=url))


async def build_welcome_keyboard(
    user_lang: str,
    *,
    is_subscribed: bool,
) -> InlineKeyboardMarkup:
    """Build the shared welcome keyboard with both Mini App and in-bot entry points."""
    open_shop_text = await get_translated_string_async("open_shop", user_lang)
    browse_in_bot_text = await get_translated_string_async("browse_in_bot", user_lang)
    change_language_text = await get_translated_string_async("change_language", user_lang)

    keyboard_buttons: list[list[InlineKeyboardButton]] = []

    webapp_button = build_webapp_button(open_shop_text)
    if webapp_button:
        keyboard_buttons.append([webapp_button])

    keyboard_buttons.append([
        InlineKeyboardButton(browse_in_bot_text, callback_data="categories")
    ])
    keyboard_buttons.append([
        InlineKeyboardButton(change_language_text, callback_data="open_language_settings")
    ])

    if not is_subscribed:
        resubscribe_text = await get_translated_string_async("resubscribe_notifications", user_lang)
        keyboard_buttons.append([
            InlineKeyboardButton(resubscribe_text, callback_data="toggle_notifications")
        ])

    return InlineKeyboardMarkup(keyboard_buttons)


async def build_catalog_keyboard(user_lang: str) -> InlineKeyboardMarkup:
    """Build a compact navigation keyboard with Mini App access where available."""
    browse_in_bot_text = await get_translated_string_async("browse_in_bot", user_lang)
    keyboard_buttons: list[list[InlineKeyboardButton]] = []

    webapp_button = build_webapp_button(await get_translated_string_async("open_shop", user_lang))
    if webapp_button:
        keyboard_buttons.append([webapp_button])

    keyboard_buttons.append([
        InlineKeyboardButton(browse_in_bot_text, callback_data="categories")
    ])
    return InlineKeyboardMarkup(keyboard_buttons)
