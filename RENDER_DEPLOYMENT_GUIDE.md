# Render Deployment Guide

Complete step-by-step guide to deploy your Telegram bot to Render with zero downtime updates.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Initial Setup](#initial-setup)
- [Environment Variables](#environment-variables)
- [Deployment](#deployment)
- [Post-Deployment Testing](#post-deployment-testing)
- [Updating Configuration](#updating-configuration)
- [Troubleshooting](#troubleshooting)
- [Database Persistence](#database-persistence)
- [Monitoring](#monitoring)

## Prerequisites

### 1. Telegram Bot Setup

1. **Create a bot** with [@BotFather](https://t.me/botfather)
   - Send `/newbot` to @BotFather
   - Follow prompts to choose a name and username
   - **Save the bot token** (format: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

2. **Get your admin ID**
   - Message [@userinfobot](https://t.me/userinfobot)
   - **Save your user ID** (numeric, e.g., `123456789`)

3. **Create a public channel** (optional but recommended)
   - Create a channel in Telegram
   - Make it public (or keep private if bot is admin)
   - Add your bot as administrator with permissions:
     - ✅ Post messages
     - ✅ Delete messages
   - **Get channel ID**:
     - Forward a message from the channel to @userinfobot
     - Channel ID will be negative (e.g., `-1001234567890`)

### 2. GitHub Repository

1. **Fork or clone** this repository to your GitHub account
2. Ensure all files are committed
3. Your repo should contain:
   - `main.py`
   - `webhook_server.py`
   - `requirements.txt`
   - `database.py`
   - `config/` directory
   - `handlers/` directory
   - `utils/` directory

### 3. Render Account

1. Sign up at [Render.com](https://render.com) (free tier available)
2. Connect your GitHub account

## Initial Setup

### Step 1: Validate Your Configuration Locally (Optional)

Before deploying, test your configuration locally:

```bash
# 1. Copy .env.example to .env
cp .env.example .env

# 2. Edit .env with your values
# BOT_TOKEN=your_bot_token
# ADMIN_IDS=your_user_id
# CHANNEL_ID=-1001234567890

# 3. Install dependencies
pip install -r requirements.txt

# 4. Validate configuration
python validate_config.py --all

# 5. Test locally (polling mode)
python main.py
```

### Step 2: Generate render.yaml

The `validate_config.py` script can generate a `render.yaml` file:

```bash
python validate_config.py --render
```

This creates a `render.yaml` with all necessary configuration for Render.

## Environment Variables

Set these environment variables in Render Dashboard → Environment:

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `BOT_TOKEN` | Token from @BotFather | `123456789:ABCdefGHI...` |
| `ADMIN_IDS` | Comma-separated admin user IDs | `123456789,987654321` |
| `USE_WEBHOOK` | Enable webhook mode | `true` |
| `WEBHOOK_URL` | Your Render app URL | `https://yourapp.onrender.com` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CHANNEL_ID` | Channel for auto-ingestion | (none) |
| `DB_PATH` | Database file path | `/opt/render/project/src/data/catalog.db` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `REDIS_URL` | Redis connection URL | (none, uses in-memory) |
| `PREVIEW_TTL_SECONDS` | Preview TTL in seconds | `3600` |
| `DEBUG_LOG_UPDATES` | Log all updates | `false` |

## Deployment

### Option A: Using Render Dashboard (Recommended)

1. **Create a New Web Service**
   - Go to [Render Dashboard](https://dashboard.render.com/)
   - Click "New +" → "Web Service"

2. **Connect Repository**
   - Select "Build and deploy from a Git repository"
   - Connect your GitHub account if not already connected
   - Select your repository
   - Click "Connect"

3. **Configure Service**
   - **Name**: `telegram-bot` (or your preferred name)
   - **Region**: Choose closest to you (or Oregon for free tier)
   - **Branch**: `main` (or your default branch)
   - **Root Directory**: (leave blank)
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python webhook_server.py`
   - **Instance Type**: `Free` (or paid tier for better performance)

4. **Add Environment Variables**
   
   Click "Advanced" → "Add Environment Variable" for each:
   
   ```
   BOT_TOKEN = 123456789:ABCdefGHIjklMNOpqrsTUVwxyz
   ADMIN_IDS = 123456789
   CHANNEL_ID = -1001234567890
   USE_WEBHOOK = true
   WEBHOOK_URL = https://telegram-bot-XXXX.onrender.com
   LOG_LEVEL = INFO
   ```
   
   **Important**: For `WEBHOOK_URL`, use your Render app URL:
   - Wait until service is created to get the URL, OR
   - Use the format: `https://[your-service-name].onrender.com`

5. **Add Persistent Disk** (Important for Database)
   
   - Scroll to "Disk"
   - Click "Add Disk"
   - **Name**: `bot-data`
   - **Mount Path**: `/opt/render/project/src/data`
   - **Size**: `1 GB` (free tier)
   - Click "Save"

6. **Deploy**
   - Click "Create Web Service"
   - Render will:
     - Clone your repository
     - Install dependencies
     - Start your bot
     - This takes 2-3 minutes

### Option B: Using render.yaml (Infrastructure as Code)

1. **Generate render.yaml**
   ```bash
   python validate_config.py --render
   ```

2. **Commit and push**
   ```bash
   git add render.yaml
   git commit -m "Add Render deployment config"
   git push
   ```

3. **Deploy from Blueprint**
   - Go to [Render Dashboard](https://dashboard.render.com/)
   - Click "New +" → "Blueprint"
   - Connect repository
   - Render auto-detects `render.yaml`
   - Click "Apply"

4. **Set Secret Variables**
   
   After deployment, go to your service → Environment:
   - Set `BOT_TOKEN`
   - Set `ADMIN_IDS`
   - Set `CHANNEL_ID` (if using)
   - Set `WEBHOOK_URL` (copy from service URL)

## Post-Deployment Testing

### 1. Check Service Health

Once deployed, your Render app URL will be: `https://[service-name].onrender.com`

Test the health endpoint:

```bash
curl https://yourapp.onrender.com/health
# Expected: {"status":"ok"}
```

### 2. Check Logs

- Go to Render Dashboard → Your Service → "Logs" tab
- Look for:
  ```
  INFO Telegram Application started
  INFO Running startup tasks
  INFO Handlers initialized during startup tasks
  INFO Startup tasks complete
  ```

### 3. Test Bot Commands

1. **Start conversation**
   - Send `/start` to your bot on Telegram
   - Expected: Welcome message

2. **Browse catalog**
   - Send `/categories`
   - Expected: Category buttons

3. **Search**
   - Type any search query (e.g., "test")
   - Expected: Search results or "no results" message

4. **Admin commands** (if you're an admin)
   - Send `/admin_stats`
   - Expected: Statistics about products

### 4. Test Channel Ingestion (if configured)

1. Post a message with media in your channel
2. Check bot logs for ingestion confirmation
3. Browse catalog to see the new product

## Updating Configuration

### Changing Bot Token (2 minutes, zero downtime)

1. Get new token from @BotFather
2. Go to Render Dashboard → Your Service → "Environment"
3. Find `BOT_TOKEN` → Click "Edit"
4. Paste new token
5. Click "Save Changes"
6. Render auto-redeploys (30-60 seconds)

**No code changes needed!**

### Switching Channels (2 minutes)

1. Add bot to new channel as admin
2. Get new channel ID
3. Update `CHANNEL_ID` in Render → Environment
4. Save → Auto-redeploys

**Database preserves existing products!**

### Adding/Removing Admins (1 minute)

1. Get new admin IDs from @userinfobot
2. Update `ADMIN_IDS` in Render → Environment
   - Format: `123456789,987654321,111111111` (comma-separated, no spaces)
3. Save → Auto-redeploys

### Enabling Redis (Optional, for scalability)

1. Create a Redis instance (Render Redis or external)
2. Add `REDIS_URL` environment variable:
   - Format: `redis://:password@host:6379/0`
3. Save → Auto-redeploys

**Bot automatically uses Redis for callback storage!**

## Troubleshooting

### Common Issues

#### 1. "Application failed to start"

**Cause**: Missing environment variables or syntax error

**Fix**:
- Check Render logs for error message
- Verify all required env vars are set:
  - `BOT_TOKEN`
  - `ADMIN_IDS`
  - `USE_WEBHOOK=true`
  - `WEBHOOK_URL`
- Ensure `WEBHOOK_URL` matches your Render service URL

#### 2. "Webhook not receiving updates"

**Cause**: Telegram webhook not set correctly

**Fix**:
- Check logs for "Telegram Application started"
- Verify `WEBHOOK_URL` is correct (must be https://)
- Check webhook status:
  ```bash
  curl "https://api.telegram.org/bot<YOUR_TOKEN>/getWebhookInfo"
  ```
- If webhook URL is wrong, update `WEBHOOK_URL` env var

#### 3. "Bot not responding to commands"

**Cause**: Webhook URL mismatch or handlers not initialized

**Fix**:
- Check Render logs for errors
- Ensure bot is not running elsewhere (stop local instance)
- Verify you're messaging the correct bot
- Check that handlers initialized successfully in logs

#### 4. "Database resets on redeploy"

**Cause**: No persistent disk configured

**Fix**:
- Add persistent disk (see Deployment step 5)
- Ensure `DB_PATH` points to disk mount path:
  - `/opt/render/project/src/data/catalog.db`

#### 5. "Channel posts not detected"

**Cause**: Bot not admin in channel or wrong CHANNEL_ID

**Fix**:
- Verify bot is admin in channel
- Check bot has "Post messages" permission
- Verify `CHANNEL_ID` is correct (negative number)
- Check logs for channel_post events

### Debugging Steps

1. **Enable debug logging** (temporarily):
   ```
   Set LOG_LEVEL=DEBUG in Render → Environment
   Set DEBUG_LOG_UPDATES=true
   ```

2. **Check webhook info**:
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
   ```

3. **Test health endpoint**:
   ```bash
   curl https://yourapp.onrender.com/health
   ```

4. **Inspect database** (if you have shell access):
   ```bash
   python inspect_db.py top 10
   ```

5. **Review logs**:
   - Render Dashboard → Logs
   - Look for stack traces, errors, warnings

## Database Persistence

### Setting Up Persistent Storage

**Free tier** (1 GB included):
1. Render Dashboard → Your Service → "Disk"
2. Add disk with mount path: `/opt/render/project/src/data`
3. Ensure `DB_PATH=/opt/render/project/src/data/catalog.db`

**Important**: Without persistent disk, database resets on every deploy!

### Database Backups

1. **Manual backup** (via Render Shell):
   ```bash
   # Open Shell in Render Dashboard
   cp /opt/render/project/src/data/catalog.db /tmp/backup.db
   # Download via Render file browser or scp
   ```

2. **Automated backup** (add a cron job):
   ```bash
   # Create backup script
   echo "cp $DB_PATH $DB_PATH.backup-$(date +%Y%m%d)" > /opt/render/project/src/backup.sh
   ```

3. **Restore from backup**:
   ```bash
   cp /opt/render/project/src/data/catalog.db.backup-YYYYMMDD /opt/render/project/src/data/catalog.db
   # Restart service
   ```

## Monitoring

### Health Checks

Render automatically monitors your service via:
- HTTP health check on `/health` endpoint (returns 200 OK)
- Process health (restarts if crashed)

### Custom Monitoring

1. **Set up external monitoring** (e.g., UptimeRobot):
   - Monitor URL: `https://yourapp.onrender.com/health`
   - Alert if down

2. **Log monitoring**:
   - Check Render logs regularly
   - Set up alerts for ERROR level logs

3. **Bot statistics**:
   - Use `/admin_stats` command
   - Monitor product count, daily additions

### Performance Optimization

**Free tier limitations**:
- Spins down after 15 minutes of inactivity
- First request after spin-down takes 30-60 seconds

**Solutions**:
1. **Upgrade to paid tier** ($7/month):
   - Always-on instances
   - No spin-down
   - Better performance

2. **Keep-alive service** (free tier workaround):
   - Set up external ping service
   - Ping `/health` every 10 minutes
   - Keeps service warm

## Best Practices

### Security

1. **Never commit secrets**:
   - `.env` is in `.gitignore`
   - Set secrets in Render Dashboard only

2. **Rotate tokens periodically**:
   - Generate new bot token from @BotFather
   - Update in Render → Environment
   - Zero downtime rotation!

3. **Limit admin access**:
   - Only add trusted users to `ADMIN_IDS`
   - Remove old admins promptly

### Operational

1. **Test changes locally first**:
   ```bash
   USE_WEBHOOK=false python main.py
   ```

2. **Use staging environment**:
   - Create separate Render service for testing
   - Use different bot token
   - Test changes before production deploy

3. **Monitor logs after deploy**:
   - Check for errors/warnings
   - Verify startup sequence completes
   - Test all commands

4. **Keep dependencies updated**:
   ```bash
   pip list --outdated
   pip install --upgrade <package>
   # Update requirements.txt
   # Push to trigger redeploy
   ```

## Deployment Checklist

Use this checklist for every deployment:

```
Pre-Deployment:
[ ] Local testing completed (USE_WEBHOOK=false python main.py)
[ ] Configuration validated (python validate_config.py --all)
[ ] All secrets ready (BOT_TOKEN, ADMIN_IDS, CHANNEL_ID)
[ ] Database backups created (if updating schema)
[ ] Git changes committed and pushed

Deployment:
[ ] Render service created/updated
[ ] Environment variables set correctly
[ ] Persistent disk configured (mount: /opt/render/project/src/data)
[ ] Service deployed successfully
[ ] Logs show "Telegram Application started"
[ ] No ERROR level logs

Post-Deployment:
[ ] Health check passes (curl /health → 200 OK)
[ ] Bot responds to /start
[ ] /categories command works
[ ] Search functionality works
[ ] Admin commands work (if admin)
[ ] Channel ingestion works (if configured)
[ ] No errors in logs for 5 minutes

Monitoring:
[ ] External monitoring set up (optional)
[ ] Admin has access to Render dashboard
[ ] Backup strategy in place
[ ] Documentation updated
```

## Support

If you encounter issues:

1. **Check this guide** - Most issues are covered in Troubleshooting
2. **Review logs** - Render Dashboard → Logs tab
3. **Run validation** - `python validate_config.py --all`
4. **Check FORENSIC_ANALYSIS.md** - Known failure points documented
5. **Test locally** - Isolate Render-specific vs code issues

## Next Steps

- Review [FORENSIC_ANALYSIS.md](FORENSIC_ANALYSIS.md) for debugging methodology
- Read [OPTIMIZATION_SUMMARY.md](OPTIMIZATION_SUMMARY.md) for architecture details
- Check [PRODUCT_DISPLAY_FIXES.md](PRODUCT_DISPLAY_FIXES.md) for bug fix explanations

---

**Congratulations!** You now have a production-grade bot deployed with zero-downtime configuration updates.
