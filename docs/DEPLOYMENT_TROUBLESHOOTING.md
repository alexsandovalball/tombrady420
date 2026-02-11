# Deployment Troubleshooting Guide

## Issue: Build Failure - "Could not read Username" / "No such file or directory: requirements.txt"

### Problem Description
If you're seeing errors like:
```
fatal: could not read Username for 'https://github.com': terminal prompts disabled
ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'
```

This indicates that Render.com is trying to clone from the **wrong repository URL**.

### Root Cause
The deployment service is configured to clone from an incorrect GitHub repository (e.g., `alexsandovalball/tombrady420` instead of `Tombrady007/tombrady420`), which causes:
1. Authentication failures when trying to clone
2. The build process continues without the actual code
3. `requirements.txt` is not found because the repository wasn't cloned

### Solution

#### Option 1: Update Render Dashboard (Recommended)
1. Log in to your [Render.com dashboard](https://dashboard.render.com)
2. Go to your web service
3. Navigate to **Settings** → **Build & Deploy**
4. Update the **Repository** setting to point to the correct repository:
   - **Correct**: `https://github.com/Tombrady007/tombrady420`
   - **Incorrect**: `https://github.com/alexsandovalball/tombrady420`
5. Click **Save Changes**
6. Trigger a new deployment by clicking **Manual Deploy** → **Deploy latest commit**

#### Option 2: Use render.yaml (Automatic Configuration)
This repository now includes a `render.yaml` file that Render.com will automatically detect and use for configuration.

1. Ensure your Render service is connected to the **correct repository**: `Tombrady007/tombrady420`
2. Render will automatically detect the `render.yaml` file
3. The configuration specifies:
   - Python 3.11.5 runtime
   - Build command: `pip install --upgrade pip && pip install -r requirements.txt`
   - Start command: `python webhook_server.py`
   - PORT: 10000 (Render's default)
   - USE_WEBHOOK: true

#### Option 3: Fork the Repository
If you need to deploy from your own repository:
1. Fork `Tombrady007/tombrady420` to your GitHub account
2. Create a new web service on Render.com
3. Connect **your forked repository**
4. Set the required environment variables (see below)
5. Deploy

### Required Environment Variables
Set these in the Render dashboard under **Environment**:

| Variable | Required | Example | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | ✅ Yes | `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz` | Bot token from @BotFather |
| `CHANNEL_ID` | ✅ Yes | `-1001234567890` | Your Telegram channel ID |
| `ADMIN_IDS` | ✅ Yes | `123456789,987654321` | Comma-separated admin user IDs |
| `WEBHOOK_URL` | ✅ Yes | `https://your-app.onrender.com` | Your Render app URL |
| `ORDER_CONTACT` | ⚠️ Recommended | `@yourusername` | Contact info shown to users |
| `USE_WEBHOOK` | Auto-set | `true` | Enable webhook mode (set by render.yaml) |
| `PRIMARY_ADMIN_ID` | Optional | `123456789` | Admin for categorization requests |
| `DB_PATH` | Optional | `/opt/render/project/data/catalog.db` | Database path (if using persistent disk) |

### Verifying the Deployment

After deploying, verify that:

1. **Build succeeds**: Check the build logs for:
   ```
   Successfully installed python-telegram-bot-...
   ```

2. **Server starts**: Check the deployment logs for:
   ```
   INFO:     Started server process
   INFO:     Uvicorn running on http://0.0.0.0:10000
   ```

3. **Bot is configured**: You should see:
   ```
   Bot configuration validated successfully
   Webhook set successfully
   ```

4. **Test the bot**: Send `/start` to your bot on Telegram

### Common Issues

#### Issue: "Repository not found" or "Authentication failed"
**Solution**: Make sure you're using the public repository URL or have granted Render access to your private repository.

#### Issue: "Webhook failed"
**Solution**: Make sure `WEBHOOK_URL` matches your Render app URL exactly (including https://).

#### Issue: "Database not found"
**Solution**: The database is created automatically. If using persistent disk, ensure `DB_PATH` points to the mounted disk location.

### Additional Resources
- [Render.com Documentation](https://render.com/docs)
- [Python on Render](https://render.com/docs/deploy-python)
- [Main README](README.md) - General bot documentation
- [Admin Guide](docs/ADMIN_GUIDE.md) - Admin features
