"""
Central emoji constants for consistent usage across the bot.
Provides emoji constants and helper functions for composing emoji-enhanced text.
"""

# Status and feedback emojis
OK = "✅"
ERROR = "❌"
WARNING = "⚠️"
INFO = "ℹ️"
CHECK = "✔️"
CROSS = "❎"

# Bot-specific emojis
GOAT = "🐐"
PHOTO = "📷"
VIDEO = "🎥"
DOCUMENT = "📄"
TRASH = "🗑️"
BELL = "🔔"
MUTED_BELL = "🔕"

# Navigation and UI emojis
BACK = "◀️"
FORWARD = "▶️"
UP = "🔼"
DOWN = "🔽"
HOME = "🏠"
SEARCH = "🔍"
MENU = "📋"
GEAR = "⚙️"
LANGUAGE = "🌐"

# Category emojis
FLOWER = "🌸"
EDIBLE = "🍬"
CONCENTRATE = "💎"
VAPE = "💨"
PREROLL = "🚬"
CARTRIDGE = "🔋"
ACCESSORY = "🎒"
OTHER = "📦"

# Admin emojis
ADMIN = "👑"
USER = "👤"
USERS = "👥"
BROADCAST = "📢"
STATS = "📊"
DELETE_ALL = "🗑️"
BLOCK = "🚫"
UNBLOCK = "🟢"

# Communication emojis
MESSAGE = "💬"
SEND = "📤"
RECEIVE = "📥"
ANNOUNCEMENT = "📣"

# Action emojis
ADD = "➕"
REMOVE = "➖"
EDIT = "✏️"
VIEW = "👁️"
REFRESH = "🔄"


def text_with(icon: str, text: str, position: str = "before") -> str:
    """
    Compose text with an emoji icon.
    
    Args:
        icon: The emoji to use
        text: The text content
        position: Where to place the emoji - "before" or "after"
        
    Returns:
        Formatted string with emoji
        
    Examples:
        >>> text_with(OK, "Success")
        "✅ Success"
        >>> text_with(ERROR, "Failed", position="after")
        "Failed ❌"
    """
    if position == "before":
        return f"{icon} {text}"
    else:
        return f"{text} {icon}"


def format_status(success: bool, text: str) -> str:
    """
    Format a status message with appropriate emoji.
    
    Args:
        success: Whether the status represents success
        text: The status text
        
    Returns:
        Formatted status string
        
    Examples:
        >>> format_status(True, "Operation completed")
        "✅ Operation completed"
        >>> format_status(False, "Operation failed")
        "❌ Operation failed"
    """
    icon = OK if success else ERROR
    return text_with(icon, text)


# Common UI patterns
CONFIRM_YES = f"{CHECK} Yes"
CONFIRM_NO = f"{CROSS} No"
BUTTON_BACK = f"{BACK} Back"
BUTTON_CANCEL = f"{ERROR} Cancel"
BUTTON_SUBSCRIBE = f"{BELL} Subscribe"
BUTTON_UNSUBSCRIBE = f"{MUTED_BELL} Unsubscribe"
