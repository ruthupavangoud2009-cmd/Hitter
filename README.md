# Hitter / GameBoost Middleman Bot

Discord bot with ticket panels, middleman tools, vouches, staff applications, DM subscriptions, vacation-role management and fill roles.

## Render deployment
1. Create a Render Web Service connected to this repository.
2. Build command: `pip install -r requirements.txt`
3. Start command: `python -u keep_alive.py`
4. Add environment variable `DISCORD_BOT_TOKEN` with your Discord bot token.
5. Optional: add `VOUCH_CHANNEL_ID` to override the configured vouch channel.

## Commands
Use `+help` or `/help` for a paginated command directory. Most bot commands support both prefix and slash forms.

## Discord Developer Portal
Enable **Server Members Intent** and **Message Content Intent** if the bot requires them. Ensure it has required permissions and its role is above roles it manages.

## Important
Render free services may sleep and are not guaranteed to stay online 24/7. Local JSON state may be lost on restarts/redeploys unless persistent storage is configured.
