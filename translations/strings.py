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
        
        # Subcategory names - CARTRIDGES
        "subcategory_authentics": "Authentics",
        "subcategory_replicas": "Replicas",
        
        # Subcategory names - EDIBLES
        "subcategory_flower_edibles": "Flower Edibles",
        "subcategory_shroom_edibles": "Shroom Edibles",
        
        # Subcategory names - CONCENTRATES
        "subcategory_snowcaps": "Snowcaps",
        "subcategory_moonrocks": "Moonrocks",
        "subcategory_hash_and_kief": "Hash and Kief",
        "subcategory_badder": "Badder",
        "subcategory_shatter": "Shatter",
        "subcategory_distillate": "Distillate",
        "subcategory_thcapowder": "THCa Powder",
        "subcategory_rso": "RSO",
        "subcategory_rosin": "Rosin",
        "subcategory_sugar": "Sugar",
        "subcategory_others": "Others",
        
        # Subcategory names - PREROLLS
        "subcategory_flower_prerolls": "Flower Pre-Rolls",
        "subcategory_infused_flower_prerolls": "Infused Flower Pre-Rolls",
        
        # Subcategory names - FLOWER
        "subcategory_topshelfcandy": "Top Shelf Candy",
        "subcategory_premiumexotics": "Premium Exotics",
        "subcategory_exotics": "Exotics",
        "subcategory_premiumlightdeps": "Premium Light Deps",
        "subcategory_lightdeps": "Light Deps",
        "subcategory_lightassist": "Light Assist",
        "subcategory_lows": "Lows",
        
        # Buttons
        "button_back": "« Back",
        "button_next_page": "Next »",
        "button_previous_page": "« Previous",
        "button_back_to_categories": "« Back to Categories",
        "button_back_to_menu": "« Back to Menu",
        "button_back_to_subcategories": "🔙 Back to Subcategories",
        "button_view_product": "👁️ View",
        "change_language": "🌐 Change Language",
        "button_refresh": "🔄 Refresh",
        "button_all_in_category": "📦 All {category}",
        
        # Search
        "search_results": "🔍 **Search Results**\n\nFound {count} products matching \"{query}\":",
        "no_results": "No products found matching \"{query}\".",
        "search_prompt": "💬 Type what you're looking for to search the catalog!",
        "search_min_chars": "🔍 Please enter at least 2 characters to search.",
        "catalog_empty": "📭 The catalog is empty. No products available yet.",
        "no_products_found": "❌ No products found for '{query}'. Try different keywords.",
        
        # Product view
        "product_info": "📦 **Product Details**\n\n{category}\n🆔 ID: {id}",
        "uncategorized": "Uncategorized",
        "order_contact_info": f"💬 DM {Config.ORDER_CONTACT} TO ORDER",
        
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
        
        # Catalog/Menu strings
        "showing_products": "Showing {current} of {total} products",
        "no_products_in_category": "📭 No products in this {context}.",
        "page_indicator": "Page {page}/{total_pages}",
        "select_subcategory": "Select a subcategory to browse products:",
        
        # Admin categorization strings
        "select_subcategory_or_save": "Select a subcategory or save without one:",
        "save_without_subcategory": "✅ Save without subcategory",
        "confirm_categorization": "Confirm categorization?",
        "product_categorized_successfully": "✅ Product #{product_id} categorized successfully!",
        "category_label": "📂 Category: {category}",
        "subcategory_label": "📁 Subcategory: {subcategory}",
        
        # Broadcast strings
        "broadcast_enter_user_id": "📝 **Broadcast to Single User - Step 1 of 3**\n\nPlease enter the user ID:",
        "broadcast_enter_message": "📝 **Broadcast to Single User - Step 2 of 3**\n\nPlease enter the message you want to send:",
        "broadcast_all_enter_message": "📝 **Broadcast to All Users - Step 1 of 2**\n\nPlease enter the message you want to broadcast:",
        "broadcast_confirm_single": "✅ **Broadcast to Single User - Confirmation**\n\n🆔 User ID: {user_id}\n📝 Message: {message}\n\nDo you want to send this message?",
        "broadcast_confirm_all": "✅ **Broadcast to All Users - Confirmation**\n\n📝 Message: {message}\n📊 Recipients: {count} users\n\nDo you want to send this broadcast?",
        "broadcast_cancelled": "❌ Broadcast cancelled.",
        "broadcast_sent_single": "✅ Message sent to user {user_id}!",
        "broadcast_sent_all": "✅ Broadcast queued for {count} users!",
        "invalid_user_id": "❌ Invalid user ID. Please enter a valid number.",
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
