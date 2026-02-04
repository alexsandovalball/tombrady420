"""
Translatable strings for the bot.
All user-facing messages should be defined here.
"""
from configs.config import Config

# Base strings in English - these will be translated on the fly
def get_strings():
    """Get translatable strings with config values."""
    return {
        # Start command
        "welcome": f"👋 Welcome, {{name}}!\n\nI'm your media product catalog bot. I help you browse and search products from our catalog.\n\nUse /menu to view all products or simply type what you're looking for!  DM {Config.ORDER_CONTACT} TO ORDER",
        "notifications_enabled": "🔔 **Notifications Enabled!**\n\nYou will now receive notifications when new products are added to the catalog.\n\nUse /unsubscribe to stop receiving notifications.",
        "notifications_disabled": "🔕 **Notifications Disabled**\n\nYou will no longer receive notifications about new products.\n\nUse /subscribe to enable notifications again.",
        
        # Menu
        "product_categories": "📂 **Product Categories**\n\nSelect a category to browse products:",
        "all_products": "📦 All Products",
        "view_catalog": "📋 View Catalog",
        "resubscribe_notifications": "🔔 Resubscribe to Notifications",
        
        # Category names
        "category_cartridges": "🛒 Cartridges",
        "category_edibles": "🍫 Edibles",
        "category_concentrates": "💎 Concentrates",
        "category_prerolls": "🚬 Pre-Rolls",
        "category_shrooms": "🍄 Shrooms",
        "category_flower": "🌸 Flower",
        "category_datedproofs": "📅 Dated Proofs",
        "category_clienttouchdowns": "✈️ Client Touchdowns",
        "category_announcements": "📢 Announcements",
        
        # Buttons
        "button_back": "« Back",
        "button_next_page": "Next »",
        "button_previous_page": "« Previous",
        "button_back_to_categories": "« Back to Categories",
        "button_back_to_menu": "« Back to Menu",
        "button_view_product": "👁️ View",
        
        # Search
        "search_results": "🔍 **Search Results**\n\nFound {count} products matching \"{query}\":",
        "no_results": "No products found matching \"{query}\".",
        "search_prompt": "💬 Type what you're looking for to search the catalog!",
        
        # Product view
        "product_info": "📦 **Product Details**\n\n{category}\n🆔 ID: {id}",
        "uncategorized": "Uncategorized",
        
        # Admin
        "admin_stats": "📊 **Bot Statistics**\n\n{stats}",
        "product_deleted": "✅ Product deleted successfully.",
        "user_blocked": "🚫 User {user_id} has been blocked.",
        "user_unblocked": "✅ User {user_id} has been unblocked.",
        
        # Errors
        "error_occurred": "❌ An error occurred. Please try again.",
        "no_permission": "❌ You don't have permission to use this command.",
        "product_not_found": "❌ Product not found.",
        
        # Language settings
        "language_settings": "🌐 **Language Settings**\n\nSelect your preferred language:",
        "language_changed": "✅ Language changed to {language}",
        "current_language": "Current language: {language}",
    }

# Initialize strings
STRINGS = get_strings()


def get_string(key: str, **kwargs) -> str:
    """
    Get a string by key with optional formatting.
    
    Args:
        key: The string key
        **kwargs: Format arguments for the string
    
    Returns:
        Formatted string or key if not found
    """
    string = STRINGS.get(key, key)
    if kwargs:
        try:
            return string.format(**kwargs)
        except KeyError:
            return string
    return string
