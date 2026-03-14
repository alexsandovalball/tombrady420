"""
Notification system constants — rate limits, batch sizes, and delivery policy knobs.

All tunable values live here so they can be imported by any sub-module without
creating import cycles.
"""

# ---------------------------------------------------------------------------
# Per-user rate limits
# ---------------------------------------------------------------------------

MAX_NOTIFICATIONS_PER_HOUR = 5       # Maximum product notifications per user per hour
MAX_CUSTOM_MESSAGES_PER_HOUR = 3     # Maximum custom messages per user per hour

# ---------------------------------------------------------------------------
# Batch and queue processing
# ---------------------------------------------------------------------------

NOTIFICATION_BATCH_SIZE = 25         # Users per delivery batch
BATCH_DELAY_SECONDS = 1              # Delay between batches within a group (seconds)
PENDING_NOTIFICATIONS_BATCH_LIMIT = 100  # DB rows claimed per processing cycle

CUSTOM_MESSAGE_BATCH_SIZE = 20       # Users per custom-message batch
CUSTOM_MESSAGE_DELAY_SECONDS = 1     # Delay between custom-message batches (seconds)

# ---------------------------------------------------------------------------
# Staggered delivery
# ---------------------------------------------------------------------------

ENABLE_STAGGERED_DELIVERY = True     # Stagger sends to avoid Telegram spam flags

# 2s between groups → ~12.5 msg/s per bot (42% of Telegram's 30 msg/s hard limit).
# At 5,000 users/bot: ceil(5000/25)=200 groups × 2s = ~6.7 min total delivery time.
# At 45,000 users across 9 bots (parallel): same ~6.7 min (bots run concurrently).
STAGGER_GROUP_SIZE = 25              # Number of users per stagger group
STAGGER_INTERVAL_SECONDS = 2        # Seconds between consecutive stagger groups

TELEGRAM_MSGS_PER_SECOND_HARD_LIMIT = 30   # Telegram documented hard limit per bot
RATE_LIMIT_SAFETY_FACTOR = 0.42            # Current utilization ratio (12.5/30)

# ---------------------------------------------------------------------------
# Per-bot token-bucket rate limiter
# ---------------------------------------------------------------------------

BOT_SEND_RATE_PER_SEC = 10   # Steady-state message rate per bot (tokens/second)
BOT_SEND_BURST = 20           # Maximum burst per bot (tokens)

# ---------------------------------------------------------------------------
# Concurrency cap
# ---------------------------------------------------------------------------

MAX_CONCURRENT_BOT_WORKERS = 5   # Maximum bot workers running simultaneously

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

FILE_ID_PREVIEW_LENGTH = 20   # Characters shown when logging file IDs
