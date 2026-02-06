# Testing Persistence and Broadcast Features

This document describes how to test the new persistence and broadcast confirmation features.

## Features Added

### 1. PicklePersistence
- **webhook_server.py**: Each bot instance has its own persistence file (`bot_persistence_0.pkl`, `bot_persistence_1.pkl`, etc.)
- **main.py**: Single persistence file (`bot_persistence.pkl`) for polling mode
- **Purpose**: Maintains state between webhook requests, allowing multi-step workflows like `/broadcast` to work correctly

### 2. Enhanced Broadcast Confirmations
- **Queued Message**: Shows immediately after confirming broadcast
- **Completed Message**: Shows after all messages have been delivered

## Testing Instructions

### Test 1: Verify Persistence Files Are Created

**For Webhook Mode:**
1. Start the webhook server
2. Check that persistence files are created:
   ```bash
   ls -la bot_persistence_*.pkl
   ```
3. Expected files:
   - `bot_persistence_0.pkl` (primary bot)
   - `bot_persistence_1.pkl`, `bot_persistence_2.pkl`, etc. (if multiple bots configured)

**For Polling Mode:**
1. Start main.py
2. Check that persistence file is created:
   ```bash
   ls -la bot_persistence.pkl
   ```

### Test 2: Verify /broadcast Workflow Persists State

**Steps:**
1. Send `/broadcast` command to the bot
2. Wait for prompt: "Please send the message you want to broadcast"
3. Send a test message (e.g., "Test broadcast message")
4. Verify you receive a confirmation request
5. Click "✅ Confirm Broadcast"

**Expected Behavior:**
- In webhook mode, the state should persist between each step
- You should see two messages:
  - **First**: "✅ Broadcast Queued Successfully!" with message count and preview
  - **Second**: "✅ Broadcast Completed!" with total delivered and time taken

### Test 3: Verify /send Workflow Persists State

**Steps:**
1. Send `/send` command to the bot
2. Follow the prompts:
   - Enter user ID
   - Enter message
3. Confirm the send

**Expected Behavior:**
- State should persist between each step
- Message should be sent successfully

### Test 4: Verify Broadcast Confirmation Messages

**Setup:**
1. Have at least 2 non-admin users in the database
2. Admin sends `/broadcast` command

**Expected Output:**

**Message 1 (Queued):**
```
✅ Broadcast Queued Successfully!

📨 Messages queued: 2
📝 Message: Test broadcast message

🚀 Messages are being delivered now with rate-limiting...
```

**Message 2 (Completed) - Appears after delivery:**
```
✅ Broadcast Completed!

📊 Total delivered: 2
⏱️ Time taken: 5s
✅ All messages have been sent.
```

### Test 5: Verify State Persistence Across Restarts

**Steps:**
1. Start bot and send `/broadcast`
2. Get to the message input step
3. Stop the bot (simulate crash or restart)
4. Start the bot again
5. Send a message

**Expected Behavior:**
- With persistence: State is restored, workflow continues
- Without persistence (old behavior): State is lost, message is treated as search query

## Troubleshooting

### Persistence Files Not Created
- Check file permissions in the working directory
- Verify the bot is using the correct configuration
- Check logs for "Persistence enabled" messages

### State Not Persisting
- Verify persistence files exist and are being updated (check modification time)
- Ensure `.gitignore` includes `*.pkl` to prevent committing these files
- Check for errors in logs related to pickle serialization

### Broadcast Completion Message Not Received
- Verify admin user ID is correctly passed to `broadcast_custom_message()`
- Check logs for "Broadcast completion notification sent" message
- Ensure the background task is not being terminated prematurely
- Verify bot has permission to send messages to admin

## Log Messages to Look For

### Persistence Initialization
```
INFO - Persistence enabled for bot 0: bot_persistence_0.pkl
INFO - Persistence enabled: bot_persistence.pkl
```

### Broadcast Flow
```
INFO - Admin {user_id} started broadcast to all users workflow
INFO - [Notifications] Queued {count} custom messages for broadcast
INFO - Broadcast completion notification sent to admin {user_id}
```

### Error Handling
```
ERROR - Error in background broadcast completion task: {error}
```

## Notes

- Persistence files are excluded from git (added to `.gitignore`)
- Each bot instance maintains separate state
- Background tasks use fire-and-forget pattern for async processing
- Completion notifications are sent via background task to avoid blocking
