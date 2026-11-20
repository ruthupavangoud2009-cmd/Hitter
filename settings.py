"""Your settings. Edit this file, then run bot_v2.py."""

import getpass
import os

# ============ CONFIG: fill these in ============
TOKEN = os.getenv("DISCORD_BOT_TOKEN") or getpass.getpass(
    "New middleman bot token (hidden): "
).strip()
if not TOKEN:
    raise RuntimeError("A new middleman bot token is required to run bot_v2.py.")
GUILD_ID = 1560209671703232563    
MIDDLEMAN_ROLE_ID = 1569631374204346368
TICKET_CATEGORY_ID = 1569631384333455432
LOG_CHANNEL_ID = 1569631488134352916
# Set this to the channel where customer and demo vouches should be posted.
VOUCH_CHANNEL_ID = int(os.getenv("VOUCH_CHANNEL_ID", "1569631434501521438"))
# Configure rank thresholds as (vouch count, role ID) pairs.
VOUCH_RANKS = []
# Role IDs listed here cannot submit or manage vouches.
VOUCH_BLOCKED_ROLE_IDS = set()
# ===============================================

# ============ PANEL LOOK: customize these ============
BRAND_NAME = "GameBoost Middleman Service"
SERVER_NAME = "GameBoost"
EMBED_COLOR = 0x38BDF8
BANNER_FILE = "banner671.gif"
LOGO_FILE = "logo.png"          

# Emojis. Plain ones work out of the box. To make them MOVE, replace them with
# an animated custom emoji code like "<a:bolt:123456789012345678>"
E_TITLE = "✅"
E_BOLT = "⚡"
E_ARROW = "➤"
E_BULLET = "•"
# =====================================================
APPLICATIONS_CHANNEL_ID = 1569631488134352916


