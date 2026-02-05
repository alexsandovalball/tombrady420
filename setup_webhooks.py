#!/usr/bin/env python3
"""
Helper script to configure webhooks for all bot instances
Run this after deploying to Render.com with multiple bot tokens
"""
import os
import sys
import requests

# Dynamically add project to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

def setup_webhooks():
    """Configure webhooks for all bot tokens"""
    from configs.config import Config
    
    # Load configuration
    Config._loaded = False
    Config._load_config()
    
    if not Config.WEBHOOK_URL:
        print("❌ WEBHOOK_URL is not set. Please set it in your environment.")
        return False
    
    if not Config.BOT_TOKENS:
        print("❌ No bot tokens found. Please set BOT_TOKEN or BOT_TOKEN_1, etc.")
        return False
    
    print("=" * 70)
    print("SETTING UP WEBHOOKS FOR ALL BOTS")
    print("=" * 70)
    print(f"Webhook base URL: {Config.WEBHOOK_URL}")
    print(f"Number of bots: {len(Config.BOT_TOKENS)}")
    print()
    
    success_count = 0
    fail_count = 0
    
    for idx, token in enumerate(Config.BOT_TOKENS):
        webhook_path = "/webhook" if idx == 0 else f"/webhook/{idx}"
        webhook_url = f"{Config.WEBHOOK_URL}{webhook_path}"
        
        print(f"Bot {idx}: Setting webhook...")
        print(f"  Token: {token[:15]}...")
        print(f"  Webhook: {webhook_url}")
        
        # Set webhook via Telegram Bot API
        api_url = f"https://api.telegram.org/bot{token}/setWebhook"
        params = {
            'url': webhook_url,
            'drop_pending_updates': True,
            'allowed_updates': ['message', 'callback_query', 'channel_post']
        }
        
        try:
            response = requests.post(api_url, json=params, timeout=10)
            result = response.json()
            
            if result.get('ok'):
                print(f"  ✅ Webhook set successfully!")
                success_count += 1
            else:
                print(f"  ❌ Failed: {result.get('description', 'Unknown error')}")
                fail_count += 1
        except Exception as e:
            print(f"  ❌ Error: {e}")
            fail_count += 1
        
        print()
    
    print("=" * 70)
    print(f"SUMMARY: {success_count} succeeded, {fail_count} failed")
    print("=" * 70)
    
    return fail_count == 0

if __name__ == '__main__':
    try:
        # Check if running in production
        if not os.getenv('BOT_TOKEN'):
            print("⚠️  This script should be run in your production environment")
            print("   where environment variables are set (e.g., on Render.com)")
            print()
            print("   Usage:")
            print("   1. SSH into your Render webservice, OR")
            print("   2. Set environment variables locally and run this script")
            sys.exit(1)
        
        success = setup_webhooks()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nAborted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
