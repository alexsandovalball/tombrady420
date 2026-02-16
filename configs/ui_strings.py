"""
Central UI text constants for consistent user interface across all bot instances.

This module provides all user-facing text blocks, button labels, and message templates
to ensure consistency across deployments and make updates easier.
"""

import emojis

# ============================================================================
# ADMIN COMMANDS
# ============================================================================

ADMIN_COMMANDS_TEXT = (
    f"\n\n{emojis.ADMIN} Admin Commands:\n"
    "• /start - Welcome message\n"
    "• /menu - View catalog\n"
    "• /users - Manage users\n"
    "• /send - Send message to user\n"
    "• /broadcast - Broadcast to all users\n"
    "• /setcontact - Set order contact\n"
    "• /recategorize - Recategorize products\n"
    "• /nuke - Delete all products"
)

# ============================================================================
# COMMON BUTTON LABELS
# ============================================================================

# Navigation buttons
BTN_BACK = f"{emojis.BACK} Back"
BTN_CANCEL = f"{emojis.ERROR} Cancel"
BTN_CONFIRM = f"{emojis.OK} Confirm"
BTN_YES = f"{emojis.CHECK} Yes"
BTN_NO = f"{emojis.CROSS} No"

# Action buttons
BTN_DELETE = f"{emojis.TRASH} Delete"
BTN_EDIT = f"{emojis.EDIT} Edit"
BTN_VIEW = f"{emojis.VIEW} View"
BTN_REFRESH = f"{emojis.REFRESH} Refresh"

# Subscription buttons
BTN_SUBSCRIBE = f"{emojis.BELL} Subscribe"
BTN_UNSUBSCRIBE = f"{emojis.MUTED_BELL} Unsubscribe"

# Product actions
BTN_DELETE_PRODUCT = f"{emojis.TRASH} Delete this product"
BTN_VIEW_CATALOG = f"{emojis.MENU} View Catalog"

# Language/Settings
BTN_CHANGE_LANGUAGE = f"{emojis.LANGUAGE} Change Language"
BTN_SELECT_LANGUAGE = f"{emojis.LANGUAGE} Select Your Language"

# Admin buttons
BTN_SEND_NOTIFICATIONS = f"{emojis.OK} Yes, send notifications"
BTN_SKIP_NOTIFICATIONS = f"{emojis.ERROR} No, skip notifications"

# ============================================================================
# STATUS MESSAGES
# ============================================================================

# Success messages
MSG_SUCCESS = f"{emojis.OK} Success"
MSG_SENT_SUCCESSFULLY = f"{emojis.OK} **Message Sent Successfully!**"
MSG_NUKE_CANCELLED = f"{emojis.OK} Nuke cancelled. No products were deleted."
MSG_BROADCAST_CANCELLED = f"{emojis.ERROR} Broadcast cancelled."
MSG_NOTIFICATIONS_SENT = f"{emojis.OK} **Notifications sent!**"
MSG_CATEGORIZATION_COMPLETE = f"{emojis.OK} **Categorization complete**"

# Error messages
MSG_ERROR = f"{emojis.ERROR} Error"
MSG_INVALID_LANGUAGE = f"{emojis.ERROR} Invalid language"
MSG_MESSAGE_NOT_FOUND = f"{emojis.ERROR} Message not found. Please start over."
MSG_SEND_FAILED = f"{emojis.ERROR} Failed to send message. Check logs for details."
MSG_DELETE_ERROR = f"{emojis.ERROR} An error occurred while deleting products. Check logs for details."
MSG_ADMIN_ONLY = f"{emojis.ERROR} Only admins can perform this action."
MSG_ADMIN_ONLY_CATEGORIZE = f"{emojis.ERROR} Only admins can categorize products."
MSG_ADMIN_ONLY_NOTIFICATIONS = f"{emojis.ERROR} Only admins can manage notifications."
MSG_ADMIN_ONLY_NUKE = f"{emojis.ERROR} Only admins can nuke."

# Warning messages
MSG_FINAL_WARNING = f"{emojis.WARNING} **FINAL WARNING**"

# ============================================================================
# MENU LABELS
# ============================================================================

MENU_CATEGORIES = {
    "FLOWER": f"{emojis.MENU} FLOWER",
    "EDIBLES": f"{emojis.MENU} EDIBLES",
    "CONCENTRATES": f"{emojis.MENU} CONCENTRATES",
    "VAPES": f"{emojis.MENU} VAPES",
    "PREROLLS": f"{emojis.MENU} PREROLLS",
    "CARTRIDGES": f"{emojis.MENU} CARTRIDGES",
    "ACCESSORIES": f"{emojis.MENU} ACCESSORIES",
    "OTHER": f"{emojis.MENU} OTHER",
}

# ============================================================================
# SEARCH MESSAGES
# ============================================================================

MSG_SEARCH_RESULTS = f"{emojis.SEARCH} Search Results"
MSG_NO_RESULTS = f"{emojis.INFO} No results found"

# ============================================================================
# NOTIFICATION MESSAGES
# ============================================================================

def format_notification_prompt(product_name: str) -> str:
    """Format the notification sending prompt for a product."""
    return (
        f"{emojis.BELL} **Send Notifications?**\n\n"
        f"Product: *{product_name}*\n\n"
        "Would you like to notify subscribed users about this product?"
    )


def format_categorization_result(product_name: str, category: str, subcategory: str = None) -> str:
    """Format the categorization completion message."""
    msg = f"{MSG_CATEGORIZATION_COMPLETE}\n\nProduct: *{product_name}*\nCategory: *{category}*"
    if subcategory:
        msg += f"\nSubcategory: *{subcategory}*"
    return msg


def format_notification_result(sent_count: int, product_name: str) -> str:
    """Format the notification sending result message."""
    return (
        f"{MSG_NOTIFICATIONS_SENT}\n\n"
        f"Notified **{sent_count}** users about:\n*{product_name}*"
    )


def format_message_sent_result(username: str, user_id: int) -> str:
    """Format the message sent confirmation."""
    return (
        f"{MSG_SENT_SUCCESSFULLY}\n\n"
        f"Recipient: {username} (ID: {user_id})"
    )


def format_nuke_warning(product_count: int) -> str:
    """Format the nuke warning message."""
    return (
        f"{MSG_FINAL_WARNING}\n\n"
        f"You are about to delete **{product_count}** products.\n"
        "This action cannot be undone!\n\n"
        "Are you absolutely sure?"
    )
