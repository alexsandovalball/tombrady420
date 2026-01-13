"""
Script to set the Telegram webhook URL for the bot.
Uses BOT_TOKEN and optional WEBHOOK_SECRET from environment variables.
"""
import os
import sys
import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def set_webhook():
    """Set the Telegram webhook URL."""
    # Get BOT_TOKEN from environment
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        print("Error: BOT_TOKEN environment variable is required", file=sys.stderr)
        sys.exit(1)
    
    # Get optional WEBHOOK_SECRET
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    
    # Build the webhook URL
    # Allow override via WEBHOOK_BASE_URL, default to the render.com deployment
    base_url = os.getenv("WEBHOOK_BASE_URL", "https://tombrady420mediabot.onrender.com")
    if webhook_secret:
        webhook_url = f"{base_url}/webhook/{webhook_secret}"
    else:
        # If no secret is provided, use base webhook path without token
        webhook_url = f"{base_url}/webhook"
        print("⚠️  WARNING: WEBHOOK_SECRET is not set.", file=sys.stderr)
        print("⚠️  The current webhook_server.py requires WEBHOOK_SECRET to be set.", file=sys.stderr)
        print("⚠️  The webhook will be set but may not work properly.", file=sys.stderr)
        print("", file=sys.stderr)
    
    # Telegram API endpoint
    api_url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
    
    # Prepare request data (using JSON body instead of query params for better special character handling)
    json_data = {
        "url": webhook_url
    }
    
    print(f"Setting webhook to: {webhook_url}")
    print(f"Making request to Telegram API...")
    
    try:
        # Make the request to Telegram API
        response = httpx.post(api_url, json=json_data, timeout=30.0)
        
        # Print the response
        print(f"\nStatus Code: {response.status_code}")
        print(f"Response:")
        print(response.text)
        
        # Check if successful
        if response.status_code == 200:
            response_data = response.json()
            if response_data.get("ok"):
                print("\n✅ Webhook set successfully!")
            else:
                print("\n❌ Failed to set webhook. Check the response above for details.")
                sys.exit(1)
        else:
            print(f"\n❌ Request failed with status code {response.status_code}")
            sys.exit(1)
            
    except httpx.RequestError as e:
        print(f"\n❌ Request error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    set_webhook()
