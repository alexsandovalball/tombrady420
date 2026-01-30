# Product Notification System

## Overview

The Telegram bot now includes a comprehensive notification system that alerts subscribed users when new products are added and categorized. The system includes anti-spam rate limiting and admin controls to manage who receives notifications.

## Features

### For Users

#### Subscription Management
- **Automatic subscription**: Users are automatically subscribed to notifications when they first interact with the bot
- **Manual control**: Users can manage their subscription status using commands

#### Commands
- `/subscribe` - Enable product notifications
- `/unsubscribe` - Disable product notifications

#### Notification Format
When a new product is added and categorized, subscribed users receive:
- Category and subcategory information
- Product description preview
- "View Product" button for quick access
- "Unsubscribe" button for easy opt-out

### For Admins

#### Manual Product Addition
Admins can manually add products with the `/add_product` command:

**Usage:**
```
1. Send a photo/video/document with caption:
   /add_product <CATEGORY> <description>

Example:
   /add_product CARTRIDGES Premium Blue Dream Vape Cartridge
```

**Process:**
1. Send media with `/add_product` command in caption
2. If category has subcategories, bot shows selection menu
3. Product is saved with category/subcategory
4. All subscribed users are notified automatically

#### User Notification Management
Admins can control which users receive notifications via the `/users` command:

**Features:**
- View all bot users with pagination (10 per page)
- See notification status for each user (🔔 ON / 🔕 OFF)
- See blocked status (🚫 BLOCKED)
- Toggle notifications for individual users with one click
- Block/unblock users with inline buttons
- User information includes:
  - Username and full name
  - User ID
  - Interaction count
  - Last seen timestamp
  - Current notification status
  - Blocked status

**Usage:**
```
/users          # View first page
/users 2        # View page 2
```

Each user row has two buttons:
- **Notification toggle**: Click to enable/disable notifications for that user
- **Block/Unblock button**: Click to block or unblock the user from using the bot

#### User Blocking
Admins can block users from using the bot entirely:

**Commands:**
```
/block <user_id>     # Block a user
/unblock <user_id>   # Unblock a user
```

**Features:**
- Blocked users cannot use any bot features
- Blocked users see a message: "🚫 You have been blocked from using this bot"
- Admins cannot be blocked
- Blocked status is shown in `/users` command
- Can also block/unblock via inline buttons in `/users`

**Example:**
```
/block 123456789     # Block user with ID 123456789
/unblock 123456789   # Unblock the same user
```

#### Custom Messaging
Admins can send custom messages to users:

**Send to Specific User:**
```
/send <user_id> <message>
```

**Example:**
```
/send 123456789 Hello! Thank you for using our catalog.
```

**Broadcast to All Users:**
```
/broadcast <message>
```

**Example:**
```
/broadcast New products just arrived! Check them out with /menu
```

**Rate Limiting:**
- Maximum 3 custom messages per user per hour
- Messages delivered in batches with 5-second delays
- Blocked users are automatically excluded from broadcasts
- Admins are excluded from broadcasts

#### Admin Commands
- `/add_product` - Manually add a product with category
- `/users` - View and manage users (notifications, blocking)
- `/block <user_id>` - Block a user from using the bot
- `/unblock <user_id>` - Unblock a user
- `/send <user_id> <message>` - Send a custom message to a specific user
- `/broadcast <message>` - Send a message to all users
- `/stats` - View catalog statistics
- `/recategorize` - Send categorization prompts for uncategorized products
- `/nuke` - Delete all products (with double confirmation)

### Automatic Notifications

Notifications are sent automatically in two scenarios:

1. **When admin categorizes a product**: After a product is posted to the channel and an admin assigns it a category, all subscribed users are notified

2. **When admin manually adds a product**: Using `/add_product`, the product is immediately categorized and users are notified

## Rate Limiting

To comply with Telegram's anti-spam policies and provide a good user experience:

### Per-User Limits
- **Maximum**: 5 notifications per user per hour
- Users who reach the limit will not receive additional notifications until the hour window passes
- Limits are tracked per user independently

### Batch Processing
- Notifications are sent in batches of 10
- 3-second delay between batches to avoid rate limits
- Failed notifications are retried in the next processing cycle

### Automatic Handling
- If a user blocks the bot, they are automatically unsubscribed and blocked in system
- Telegram errors are logged but don't prevent other notifications
- Queue-based system ensures no notifications are lost

### Custom Message Limits
To prevent spam for custom messages:

- **Maximum**: 3 custom messages per user per hour
- Smaller batch size (5 messages) compared to product notifications (10)
- Longer delay between batches (5 seconds vs 3 seconds)
- Blocked users are automatically excluded
- Failed messages are retried in next processing cycle

## Technical Implementation

### Database Schema

#### bot_users table
Added columns:
- `notifications_enabled` (default: 1) - 1 = notifications enabled, 0 = notifications disabled
- `is_blocked` (default: 0) - 1 = user is blocked, 0 = user is active

#### notification_queue table
Tracks all product notifications to send:
- `id` - Queue entry ID
- `user_id` - Target user
- `product_id` - Product to notify about
- `sent_at` - Timestamp when sent (NULL if pending)
- `created_at` - When queued

#### custom_message_queue table
Tracks all custom messages to send:
- `id` - Queue entry ID
- `user_id` - Target user
- `message_text` - Message content
- `sent_at` - Timestamp when sent (NULL if pending)
- `created_at` - When queued

### Notification Flow

```
1. Product is categorized (by admin or via /add_product)
   ↓
2. Get all subscribed users (excluding admins)
   ↓
3. Queue notification for each user
   ↓
4. Process queue with rate limiting
   ↓
5. Send notifications in batches
   ↓
6. Mark as sent or handle errors
```

### Rate Limit Implementation

**Product Notifications:**
```python
MAX_NOTIFICATIONS_PER_HOUR = 5
NOTIFICATION_BATCH_SIZE = 10
BATCH_DELAY_SECONDS = 3
```

**Custom Messages:**
```python
MAX_CUSTOM_MESSAGES_PER_HOUR = 3
CUSTOM_MESSAGE_BATCH_SIZE = 5
CUSTOM_MESSAGE_DELAY_SECONDS = 5
```

For each user:
1. Check recent notifications/messages in last 60 minutes
2. If count < limit, send message
3. If count ≥ limit, skip (will retry later)
4. Track sent messages for rate limit enforcement

## Admin Best Practices

### User Blocking

**When to Block:**
- Users spamming the bot
- Abusive or inappropriate behavior
- Test accounts no longer needed
- Users who violated terms of service

**How to Block:**
- Use `/block <user_id>` command
- Or click "🚫 Block" button in `/users` interface
- User immediately loses access to all bot functions

**Important Notes:**
- Cannot block admin users
- Blocked users automatically excluded from broadcasts
- Can unblock anytime with `/unblock <user_id>`

### Custom Messaging

**Best Practices:**
- Use broadcasts sparingly to avoid user fatigue
- Personalize direct messages when possible
- Respect the 3 messages/hour limit per user
- Don't send marketing messages too frequently
- Provide value in each message

**Example Use Cases:**
- Announce special promotions
- Send important updates about catalog
- Welcome new premium users
- Follow up on specific inquiries
- Alert about new product categories

### Managing User Notifications

**Strategy 1: Opt-In Only**
- Disable notifications for all new users by default
- Manually enable only for verified/premium users
- Use `/users` to toggle as needed

**Strategy 2: Selective Filtering**
- Let users subscribe naturally
- Use `/users` to disable problem users or spammers
- Re-enable if user requests

### Adding Products

**Best Practice:**
1. Add products via `/add_product` during low-traffic hours to avoid notification spam
2. Categorize channel products promptly to maintain user engagement
3. Use descriptive captions to make notifications valuable

### Monitoring

**Check regularly:**
- `/users` - Monitor user engagement and notification status
- `/stats` - Track product growth
- Bot logs - Review notification delivery status

## Troubleshooting

### Notifications Not Sending

**Check:**
1. User has notifications enabled (`/users` command)
2. User hasn't blocked the bot
3. Product is categorized (uncategorized products don't trigger notifications)
4. User hasn't hit rate limit (5/hour)

**Solutions:**
- Toggle user notifications off and on in `/users`
- Wait for rate limit window to pass
- Check bot logs for errors

### Too Many Notifications

**Solutions:**
1. Use `/users` to disable notifications for specific users
2. Adjust rate limits in `utils/notifications.py`:
   - Reduce `MAX_NOTIFICATIONS_PER_HOUR`
   - Increase `BATCH_DELAY_SECONDS`
3. Add products during off-peak hours

### User Can't Subscribe

**Check:**
1. User must interact with bot first (`/start`)
2. User must not have blocked the bot
3. Database permissions are correct

**Solution:**
- Ask user to send `/start` first
- Admin can manually enable in `/users`

## Configuration

### Rate Limit Customization

Edit `utils/notifications.py`:

```python
# Maximum notifications per user per hour
MAX_NOTIFICATIONS_PER_HOUR = 5  # Adjust as needed

# Notifications to send in one batch
NOTIFICATION_BATCH_SIZE = 10  # Increase for faster processing

# Delay between batches (seconds)
BATCH_DELAY_SECONDS = 3  # Increase to be more conservative
```

### Notification Message Customization

Edit `utils/notifications.py`, method `_send_notification_batch`:

```python
message_text = (
    f"🆕 **New Product Available!**\n\n"
    f"📂 Category: {category_text}\n"
    f"📝 {caption_preview}\n\n"
    f"Use /menu to browse the catalog!"
)
```

## Security Considerations

1. **Admin-only controls**: Only configured admins can:
   - Add products via `/add_product`
   - Toggle user notifications via `/users`
   - Access admin commands

2. **User privacy**: 
   - Users can opt-out anytime via `/unsubscribe`
   - Blocked users are auto-unsubscribed
   - No personal data is shared in notifications

3. **Rate limiting**:
   - Prevents bot from being flagged as spam
   - Protects users from notification fatigue
   - Maintains Telegram API compliance

## Future Enhancements

Potential improvements:
- Category-specific subscriptions (e.g., only FLOWER notifications)
- Daily digest mode (batch notifications once per day)
- Custom notification schedules per user
- Analytics dashboard for notification engagement
- A/B testing different notification formats
