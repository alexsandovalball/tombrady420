"""Helpers for Telegram Mini App launch buttons."""

from __future__ import annotations

from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

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
) -> ReplyKeyboardMarkup:
    """Build the shared welcome keyboard using Telegram's more reliable reply-keyboard WebApp button."""
    open_shop_text = await get_translated_string_async("open_shop", user_lang)
    webapp_url = normalize_webapp_url(Config.WEB_APP_URL)

    keyboard_buttons: list[list[KeyboardButton]] = []
    if webapp_url:
        keyboard_buttons.append([
            KeyboardButton(open_shop_text, web_app=WebAppInfo(url=webapp_url))
        ])

    keyboard_buttons.append([
        KeyboardButton("/menu"),
        KeyboardButton("/language"),
    ])

    if not is_subscribed:
        keyboard_buttons.append([
            KeyboardButton("/subscribe"),
        ])

    return ReplyKeyboardMarkup(
        keyboard_buttons,
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
    )


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
