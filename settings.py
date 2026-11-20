"""Bot configuration. Set the token using DISCORD_BOT_TOKEN in your environment."""
import os

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN environment variable. Set it in Render Environment.")

GUILD_ID = 1560209671703232563
MIDDLEMAN_ROLE_ID = 1569631374204346368
TICKET_CATEGORY_ID = 1569631384333455432
LOG_CHANNEL_ID = 1569631488134352916
VOUCH_CHANNEL_ID = int(os.getenv("VOUCH_CHANNEL_ID", "1569631434501521438"))
VOUCH_RANKS = []
VOUCH_BLOCKED_ROLE_IDS = set()
BRAND_NAME = "GameBoost Middleman Service"
SERVER_NAME = "GameBoost"
EMBED_COLOR = 0x38BDF8
BANNER_FILE = "banner671.gif"
LOGO_FILE = "logo.png"
E_TITLE = "✅"
E_BOLT = "⚡"
E_ARROW = "➤"
E_BULLET = "•"
APPLICATIONS_CHANNEL_ID = 1569631488134352916
