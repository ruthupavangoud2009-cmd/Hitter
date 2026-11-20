import asyncio
import datetime
import io
import json
import os
import re

import discord
from discord import app_commands
from discord.ext import commands

from settings import *

# Safe defaults if these aren't in settings.py yet
VOUCH_CHANNEL_ID = globals().get("VOUCH_CHANNEL_ID", 0)
VOUCH_RANKS = globals().get("VOUCH_RANKS", [])  # list of (vouches needed, role ID)
HITTER_ROLE_ID = 1569631375173091368
VOUCH_BLOCKED_ROLE_ID = 1569631376045510728
VACATION_KEEP_ROLE_IDS = {HITTER_ROLE_ID, VOUCH_BLOCKED_ROLE_ID}
SUPPORT_CATEGORY_ID = 1569631384333455432
FILLABLE_ROLE_IDS = {
    1569631349365538936,
    1569631322576527480,
    1569631323377897533,
    1569631324602507284,
    1569631325479243806,
    1569631326246805564,
    1569631338691301467,
    1569631339898998845,
    1569631342004805662,
    1569631342872895508,
    1569631343816744971,
    1569631345347399730,
    1569631346358222888,
    1569631347255935076,
    1569631348161781850,
    1569631351681060915,
    1569631369339080745,
    1569631372627279993,
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DM_SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "dm_subscribers.json")
DM_SUBSCRIBE_LOG_CHANNEL_ID = 1569631532686245928
VACATION_ROLES_FILE = os.path.join(BASE_DIR, "vacation_roles.json")
_dm_subscriber_lock = asyncio.Lock()
_vacation_lock = asyncio.Lock()
_support_ticket_lock = asyncio.Lock()


def get_banner_path():
    for directory in (BASE_DIR, os.path.dirname(BASE_DIR)):
        path = os.path.join(directory, BANNER_FILE)
        if os.path.isfile(path):
            return path
    return os.path.join(BASE_DIR, BANNER_FILE)


def get_banner_file():
    banner_path = get_banner_path()
    if not os.path.isfile(banner_path):
        return None
    return discord.File(banner_path, filename=BANNER_FILE)


def attach_banner(embed: discord.Embed):
    banner = get_banner_file()
    if banner is None:
        return None
    embed.set_image(url=f"attachment://{BANNER_FILE}")
    return banner


import functools
import inspect
import traceback

intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # needed for + prefix commands

PREFIX = "+"


class MiddlemanBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=PREFIX, intents=intents, case_insensitive=True, owner_ids={OWNER_ID})

    async def setup_hook(self):
        # Re-register buttons so they keep working after a restart
        self.add_view(PanelView())
        self.add_view(SupportPanelView())
        self.add_view(TicketView())
        self.add_view(StaffApplyView())
        self.add_view(VouchView())
        self.add_view(DMSubscriptionView())
        # Register slash commands globally so the bot's commands are available in every server.
        try:
            await self.tree.sync()
            print("Global slash-command sync completed")
        except discord.HTTPException as error:
            print(f"Global slash-command sync failed; bot will still start: {error}")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        banner_path = get_banner_path()
        if not os.path.isfile(banner_path):
            print(f"Warning: configured banner file not found: {banner_path}")


bot = MiddlemanBot()
# Remove discord.py's built-in prefix help before registering our hybrid help.
bot.remove_command("help")


# ---------- make every command work as both / and + ----------
class _PrefixResponse:
    """Lets the existing interaction.response.send_message(...) calls work for + commands."""

    def __init__(self, ctx):
        self.ctx = ctx

    async def send_message(self, content=None, *, ephemeral=False, **kwargs):
        if ephemeral:
            kwargs.setdefault("delete_after", 15)  # prefix commands can't be private, so auto-delete
        await self.ctx.send(content, **kwargs)

    async def send(self, content=None, *, ephemeral=False, **kwargs):
        return await self.send_message(content, ephemeral=ephemeral, **kwargs)

    async def defer(self, **kwargs):
        pass


class _PrefixInteraction:
    def __init__(self, ctx):
        self.user = ctx.author
        self.guild = ctx.guild
        self.channel = ctx.channel
        self.client = ctx.bot
        self.response = _PrefixResponse(ctx)
        self.followup = _PrefixResponse(ctx)


def _dual_command(*, description="No description", aliases=None, **_):
    def decorator(func):
        sig = inspect.signature(func)
        params = []
        for i, p in enumerate(sig.parameters.values()):
            name = "ctx" if i == 0 else p.name
            annotation = commands.Context if i == 0 else p.annotation
            kind = p.kind
            if name == "reason":  # lets +ban / +warnadd take a multi-word reason
                kind = inspect.Parameter.KEYWORD_ONLY
            params.append(
                commands.Parameter(name=name, kind=kind, default=p.default, annotation=annotation)
            )

        @functools.wraps(func)
        async def wrapper(ctx, *args, **kwargs):
            if ctx.guild is None:
                return await ctx.send("Use this command in the server.")
            inter = ctx.interaction or _PrefixInteraction(ctx)
            await func(inter, *args, **kwargs)

        wrapper.__signature__ = inspect.Signature(params)
        wrapper.__annotations__ = {
            p.name: p.annotation for p in params if p.annotation is not inspect.Parameter.empty
        }
        return bot.hybrid_command(
            name=func.__name__,
            description=description,
            aliases=aliases or [],
        )(wrapper)

    return decorator


bot.tree.command = _dual_command  # every @bot.tree.command(...) below is now / and +

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        return await ctx.send(
            f"Usage: `{PREFIX}{ctx.command.qualified_name} {ctx.command.signature}`",
            delete_after=15,
        )
    traceback.print_exception(type(error), error, error.__traceback__)

# ---------- helpers ----------
def is_bot_owner(member) -> bool:
    return member.id == OWNER_ID



def is_admin_or_owner(member) -> bool:
    return member.guild_permissions.administrator or is_bot_owner(member)



def is_mm(member: discord.Member) -> bool:
    return (
        is_bot_owner(member)
        or any(r.id == MIDDLEMAN_ROLE_ID for r in member.roles)
        or member.guild_permissions.administrator
    )


def has_vouch_blocked_role(member: discord.Member) -> bool:
    return any(role.id == VOUCH_BLOCKED_ROLE_ID for role in member.roles)


async def deny(interaction: discord.Interaction, text: str = "You don't have permission to use this command."):
    """Public (non-ephemeral) reply for people who aren't allowed to use a command."""
    await interaction.response.send_message(f"{interaction.user.mention} {text}")


def get_ticket(channel):
    """Identify ticket channels by their owner metadata in the channel topic."""
    if not isinstance(channel, discord.TextChannel):
        return None
    data = {}
    for part in (getattr(channel, "topic", None) or "").split():
        if "=" in part:
            k, v = part.split("=", 1)
            data[k] = v
    # Topic metadata is preferred. Legacy trade channels named mm-* may lack a topic.
    if data.get("owner", "").isdigit():
        return data
    if channel.name.lower().startswith("mm-"):
        # Legacy ticket fallback: recognize channel as a ticket for ticket-only features.
        return {"owner": "0", "legacy": "true"}
    return None

def load_dm_subscription_state() -> tuple:
    try:
        with open(DM_SUBSCRIBERS_FILE, encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return set(), set()
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("subscribers", []), list)
        or not isinstance(data.get("logged_subscribers", []), list)
    ):
        raise ValueError("Invalid DM subscriber data format.")
    subscribers = {int(user_id) for user_id in data.get("subscribers", [])}
    logged_subscribers = {int(user_id) for user_id in data.get("logged_subscribers", [])}
    return subscribers, logged_subscribers


def load_dm_subscribers() -> set:
    return load_dm_subscription_state()[0]


def save_dm_subscribers(user_ids: set, logged_user_ids: set):
    with open(DM_SUBSCRIBERS_FILE, "w", encoding="utf-8") as file:
        json.dump(
            {
                "subscribers": sorted(user_ids),
                "logged_subscribers": sorted(logged_user_ids),
            },
            file,
            indent=2,
        )


def load_vacation_records() -> dict:
    try:
        with open(VACATION_ROLES_FILE, encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Invalid vacation role data format.")

    for user_id, record in data.items():
        if (
            not str(user_id).isdigit()
            or not isinstance(record, dict)
            or not isinstance(record.get("roles"), list)
            or not isinstance(record.get("highest_restored", False), bool)
        ):
            raise ValueError("Invalid vacation role data format.")
        record["roles"] = [int(role_id) for role_id in record["roles"]]
        if record.get("highest_role") is not None:
            record["highest_role"] = int(record["highest_role"])
    return data


def save_vacation_records(records: dict):
    with open(VACATION_ROLES_FILE, "w", encoding="utf-8") as file:
        json.dump(records, file, indent=2)


async def dm_subscribers(guild: discord.Guild, content: str) -> tuple:
    async with _dm_subscriber_lock:
        subscribers = load_dm_subscribers()

    sent = 0
    failed = 0
    removed = set()
    for user_id in sorted(subscribers):
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                removed.add(user_id)
                continue
            except discord.HTTPException as error:
                print(f"Could not fetch DM subscriber {user_id}: {error}")
                failed += 1
                continue

        try:
            await member.send(content, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            failed += 1
        except discord.HTTPException as error:
            print(f"Could not DM subscriber {user_id}: {error}")
            failed += 1
        else:
            sent += 1
        await asyncio.sleep(1)

    if removed:
        async with _dm_subscriber_lock:
            current_subscribers, logged_subscribers = load_dm_subscription_state()
            save_dm_subscribers(
                current_subscribers - removed,
                logged_subscribers - removed,
            )

    return sent, failed, len(removed)


async def close_ticket(channel: discord.TextChannel, closer: discord.Member):
    lines = []
    async for m in channel.history(limit=None, oldest_first=True):
        lines.append(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {m.content}")
    text = "\n".join(lines) or "No messages."

    log = channel.guild.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
    if log:
        try:
            file = discord.File(io.BytesIO(text.encode("utf-8")), filename=f"{channel.name}.txt")
            await log.send(f"Ticket `{channel.name}` closed by {closer.mention}", file=file)
        except discord.HTTPException:
            pass

    await asyncio.sleep(5)
    await channel.delete()


async def claim_ticket(interaction: discord.Interaction):
    data = get_ticket(interaction.channel)
    if not data:
        return await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
    if not is_mm(interaction.user):
        return await deny(interaction, "Only middlemen can claim tickets.")
    if "claimed" in data:
        return await interaction.response.send_message(
            f"Already claimed by <@{data['claimed']}>.", ephemeral=True
        )
    await interaction.response.defer(thinking=True)
    try:
        await interaction.channel.edit(topic=f"{interaction.channel.topic} claimed={interaction.user.id}")
    except discord.Forbidden:
        return await interaction.followup.send(
            "I couldn't update this ticket. Check my Manage Channels permission."
        )
    except discord.HTTPException as error:
        print(f"Could not claim ticket {interaction.channel.id}: {error}")
        return await interaction.followup.send(
            "Discord couldn't update this ticket right now. Please try again."
        )
    embed = discord.Embed(
        description=f"{interaction.user.mention} will be your middleman for this trade.",
        color=discord.Color.green(),
    )
    await interaction.followup.send(embed=embed)


async def unclaim_ticket(interaction: discord.Interaction):
    data = get_ticket(interaction.channel)
    if not data:
        return await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
    if "claimed" not in data:
        return await interaction.response.send_message("This ticket isn't claimed.", ephemeral=True)
    is_claimer = str(interaction.user.id) == data["claimed"]
    if not (is_claimer or interaction.user.guild_permissions.administrator):
        return await deny(interaction, "Only the middleman who claimed this ticket (or an admin) can unclaim it.")
    await interaction.response.defer(thinking=True)
    try:
        await interaction.channel.edit(topic=f"owner={data['owner']}")
    except discord.Forbidden:
        return await interaction.followup.send(
            "I couldn't update this ticket. Check my Manage Channels permission."
        )
    except discord.HTTPException as error:
        print(f"Could not unclaim ticket {interaction.channel.id}: {error}")
        return await interaction.followup.send(
            "Discord couldn't update this ticket right now. Please try again."
        )
    embed = discord.Embed(
        description=f"{interaction.user.mention} unclaimed this ticket. A middleman can claim it now.",
        color=discord.Color.orange(),
    )
    await interaction.followup.send(embed=embed)


async def close_from_interaction(interaction: discord.Interaction):
    data = get_ticket(interaction.channel)
    if not data:
        return await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
    if not (is_mm(interaction.user) or str(interaction.user.id) == data["owner"]):
        return await deny(interaction, "Only middlemen or the ticket owner can close this.")
    await interaction.response.send_message("Closing this ticket in 5 seconds...")
    await close_ticket(interaction.channel, interaction.user)


# ---------- UI ----------
class TradeModal(discord.ui.Modal, title="Middleman Request"):
    partner = discord.ui.TextInput(label="Other trader (username or ID)", max_length=50)
    details = discord.ui.TextInput(
        label="What is being traded?", style=discord.TextStyle.paragraph, max_length=500
    )
    worth = discord.ui.TextInput(label="Trade value", max_length=50)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        role = guild.get_role(MIDDLEMAN_ROLE_ID)

        allow = dict(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(**allow),
            guild.me: discord.PermissionOverwrite(**allow, embed_links=True, manage_channels=True),
        }
        if role:
            overwrites[role] = discord.PermissionOverwrite(**allow)

        raw = self.partner.value.strip().lstrip("@")
        partner = guild.get_member(int(raw)) if raw.isdigit() else guild.get_member_named(raw)
        if partner:
            overwrites[partner] = discord.PermissionOverwrite(**allow)

        category = guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None
        channel = await guild.create_text_channel(
            name=f"mm-{interaction.user.name}"[:90],
            overwrites=overwrites,
            category=category,
            topic=f"owner={interaction.user.id}",
        )

        line = "━━━━━━━━━━━━━━━━━━━━"
        embed = discord.Embed(
            title="🎓 *Middleman Service Ticket*",
            description=(
                "*Middleman Service ticket created*\n\n"
                "📋 **Trade details**\n"
                f"{self.details.value}\n"
                f"Value: {self.worth.value}\n\n"
                "👤 **Trader 1 (ticket creator)**\n"
                f"Username: {interaction.user.mention}\n\n"
                "👤 **Trader 2**\n"
                f"Username: {partner.mention if partner else '@' + raw}\n\n"
                f"{line}\n\n"
                "📌 **Please follow the Middleman's instructions throughout the trade.**"
            ),
            color=EMBED_COLOR,
        )
        if not partner:
            embed.set_footer(text="Trader 2 wasn't found in the server. A middleman can add them with /add.")

        ping = f"{interaction.user.mention} {partner.mention if partner else ''} {role.mention if role else ''}"
        await channel.send(
            content=ping,
            embed=embed,
            view=TicketView(),
        )
        await interaction.followup.send(f"Your ticket is ready: {channel.mention}", ephemeral=True)


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Request Middleman", style=discord.ButtonStyle.blurple, custom_id="panel:request")
    async def request(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TradeModal())


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", emoji="✅", style=discord.ButtonStyle.green, custom_id="ticket:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await claim_ticket(interaction)

    @discord.ui.button(label="Unclaim", emoji="🔓", style=discord.ButtonStyle.secondary, custom_id="ticket:unclaim")
    async def unclaim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await unclaim_ticket(interaction)

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.red, custom_id="ticket:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await close_from_interaction(interaction)


class SupportPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Contact Support",
        style=discord.ButtonStyle.blurple,
        custom_id="support:open",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await open_support_ticket(interaction)


async def open_support_ticket(interaction: discord.Interaction):
    guild = interaction.guild

    if interaction.user.bot:
        return await interaction.response.send_message(
            "Bot accounts can't open support tickets.", ephemeral=True
        )

    # Use the configured support category when valid; otherwise recover by name,
    # and create it if it is missing so support tickets don't fail on a stale ID.
    category = guild.get_channel(SUPPORT_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        category = discord.utils.get(guild.categories, name="Support")
    if not isinstance(category, discord.CategoryChannel):
        try:
            category = await guild.create_category(
                "Support",
                reason="Create missing support-ticket category",
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                "I need Manage Channels permission to create the Support category. "
                "Please grant it or create a category named Support.",
                ephemeral=True,
            )
        except discord.HTTPException as error:
            print(f"Could not create support category: {error}")
            return await interaction.response.send_message(
                "Discord couldn't create the Support category. Please try again or contact an administrator.",
                ephemeral=True,
            )

    support_role = guild.get_role(MIDDLEMAN_ROLE_ID)
    bot_member = guild.me
    if support_role is None or bot_member is None:
        return await interaction.response.send_message(
            "The support team role or bot member could not be found. Please contact an administrator.",
            ephemeral=True,
        )

    await interaction.response.defer(ephemeral=True)
    user = interaction.user
    allow = dict(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
    )

    try:
        async with _support_ticket_lock:
            for channel in guild.text_channels:
                details = get_ticket(channel)
                if (
                    details
                    and details.get("owner") == str(user.id)
                    and channel.category_id == SUPPORT_CATEGORY_ID
                    and channel.name.startswith("support-")
                ):
                    return await interaction.followup.send(
                        f"You already have an open support ticket: {channel.mention}",
                        ephemeral=True,
                    )

            overwrites = dict(category.overwrites)
            overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
            overwrites[user] = discord.PermissionOverwrite(**allow)
            overwrites[bot_member] = discord.PermissionOverwrite(
                **allow,
                manage_channels=True,
            )
            overwrites[support_role] = discord.PermissionOverwrite(**allow)

            try:
                channel = await guild.create_text_channel(
                    name=f"support-{user.name}"[:90],
                    category=category,
                    overwrites=overwrites,
                    topic=f"owner={user.id} ticket=support",
                )
            except discord.Forbidden:
                return await interaction.followup.send(
                    "I couldn't create the support ticket. Check my Manage Channels permission.",
                    ephemeral=True,
                )
            except discord.HTTPException as error:
                print(f"Could not create support ticket for {user.id}: {error}")
                return await interaction.followup.send(
                    "Discord couldn't create the support ticket right now. Please try again.",
                    ephemeral=True,
                )

            ticket_embed = discord.Embed(
                title="Kyokah Support Ticket",
                description=(
                    f"{user.mention}, please explain the issue you need help with. "
                    "You can attach screenshots or other relevant information here."
                ),
                color=EMBED_COLOR,
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            ticket_embed.set_thumbnail(url=user.display_avatar.url)
            ticket_embed.set_footer(text=f"Ticket owner: {user} ({user.id})")
            try:
                await channel.send(
                    content=f"{user.mention} {support_role.mention}",
                    embed=ticket_embed,
                    view=TicketView(),
                )
            except discord.Forbidden:
                print(f"Support ticket {channel.id} was created, but the bot couldn't post in it.")
                return await interaction.followup.send(
                    f"Your ticket was created: {channel.mention}, but I couldn't post the opening "
                    "message. Please contact the support team.",
                    ephemeral=True,
                )
            except discord.HTTPException as error:
                print(f"Could not post opening message in support ticket {channel.id}: {error}")
                return await interaction.followup.send(
                    f"Your ticket was created: {channel.mention}, but Discord couldn't post the "
                    "opening message.",
                    ephemeral=True,
                )

            log_channel = guild.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
            if not isinstance(log_channel, discord.TextChannel):
                print(f"Support ticket {channel.id} was created, but the mod-log channel is missing.")
                return await interaction.followup.send(
                    f"Your support ticket is open: {channel.mention}, but the mod-log channel "
                    "isn't configured correctly. Please contact an administrator.",
                    ephemeral=True,
                )

            log_embed = discord.Embed(
                title="Support ticket opened",
                description=f"{channel.mention}",
                color=EMBED_COLOR,
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            log_embed.add_field(name="Opened by", value=f"{user.mention} (`{user.id}`)", inline=False)
            try:
                await log_channel.send(embed=log_embed)
            except discord.Forbidden:
                return await interaction.followup.send(
                    f"Your support ticket is open: {channel.mention}, but I couldn't send its log. "
                    "Check my permissions in the mod-log channel.",
                    ephemeral=True,
                )
            except discord.HTTPException as error:
                print(f"Could not log support ticket {channel.id}: {error}")
                return await interaction.followup.send(
                    f"Your support ticket is open: {channel.mention}, but Discord couldn't send its log.",
                    ephemeral=True,
                )
    except discord.HTTPException as error:
        print(f"Could not open support ticket for {user.id}: {error}")
        return await interaction.followup.send(
            "Discord couldn't open your support ticket right now. Please try again.",
            ephemeral=True,
        )

    await interaction.followup.send(
        f"Your support ticket is ready: {channel.mention}",
        ephemeral=True,
    )


async def update_dm_subscription(interaction: discord.Interaction, subscribe: bool):
    if interaction.user.bot:
        return await interaction.response.send_message(
            "Bot accounts can't subscribe to announcements.", ephemeral=True
        )

    if subscribe:
        log_channel = interaction.guild.get_channel(DM_SUBSCRIBE_LOG_CHANNEL_ID)
        if not isinstance(log_channel, discord.TextChannel):
            return await interaction.response.send_message(
                "The subscription log channel is missing. Please contact an administrator.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True)

    logged_now = False
    async with _dm_subscriber_lock:
        try:
            subscribers, logged_subscribers = load_dm_subscription_state()
            already_subscribed = interaction.user.id in subscribers
            if subscribe:
                needs_log = interaction.user.id not in logged_subscribers
                if needs_log:
                    embed = discord.Embed(
                        title="DM Announcement Subscription",
                        description="A member subscribed to server announcements by DM.",
                        color=discord.Color.green(),
                        timestamp=datetime.datetime.now(datetime.timezone.utc),
                    )
                    embed.add_field(
                        name="Discord username",
                        value=f"{interaction.user.name} (`{interaction.user.id}`)",
                        inline=False,
                    )
                    try:
                        await log_channel.send(embed=embed)
                    except discord.Forbidden:
                        return await interaction.followup.send(
                            "I couldn't post the subscription log. Ask an administrator to give "
                            "the bot Send Messages and Embed Links permission in the log channel.",
                            ephemeral=True,
                        )
                    except discord.HTTPException as error:
                        print(f"Could not post DM subscription log: {error}")
                        return await interaction.followup.send(
                            "I couldn't post the subscription log. Please try again later.",
                            ephemeral=True,
                        )
                    logged_subscribers.add(interaction.user.id)
                    logged_now = True
                if not already_subscribed or needs_log:
                    subscribers.add(interaction.user.id)
                    save_dm_subscribers(subscribers, logged_subscribers)
            else:
                subscribers.discard(interaction.user.id)
                logged_subscribers.discard(interaction.user.id)
                save_dm_subscribers(subscribers, logged_subscribers)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            print(f"Could not update DM subscriptions: {error}")
            reply = interaction.followup.send if subscribe else interaction.response.send_message
            return await reply(
                "I couldn't update your subscription. Please contact an administrator.",
                ephemeral=True,
            )

    if not subscribe:
        response = "You've been removed from the DM announcement list."
    elif already_subscribed and logged_now:
        response = "You're already subscribed; I sent the missing subscription log."
    elif already_subscribed:
        response = "You're already subscribed to server announcements by DM."
    else:
        response = "You're subscribed to server announcements by DM. Click **Decline** to opt out."
    if subscribe:
        await interaction.followup.send(response, ephemeral=True)
    else:
        await interaction.response.send_message(response, ephemeral=True)


class DMSubscriptionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Subscribe",
        emoji="📩",
        style=discord.ButtonStyle.green,
        custom_id="dm:subscribe",
    )
    async def subscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_dm_subscription(interaction, True)

    @discord.ui.button(
        label="Decline",
        emoji="🔕",
        style=discord.ButtonStyle.secondary,
        custom_id="dm:decline",
    )
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_dm_subscription(interaction, False)


class DMConfirmationView(discord.ui.View):
    def __init__(self, requester_id: int, content: str):
        super().__init__(timeout=90)
        self.requester_id = requester_id
        self.content = content

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the administrator who started this send can confirm it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Send to subscribers", emoji="📨", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):

        if not is_admin_or_owner(interaction.user):
            return await interaction.response.send_message(
                "Administrator permission is required.", ephemeral=True
            )

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="Sending to opted-in subscribers. This may take a while.",
            view=self,
        )
        try:
            sent, failed, removed = await dm_subscribers(interaction.guild, self.content)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            print(f"Could not send subscriber DMs: {error}")
            return await interaction.followup.send(
                "I couldn't read the subscriber list. Please contact an administrator.",
                ephemeral=True,
            )

        await interaction.followup.send(
            f"Announcement complete: **{sent}** sent, **{failed}** failed, "
            f"**{removed}** stale subscriptions removed.",
            ephemeral=True,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="Subscriber DM cancelled.",
            embed=None,
            view=self,
        )


# ---------- slash commands ----------
@bot.tree.command(description="Send the middleman request panel to this channel")
async def panel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await deny(interaction, "Only administrators can use this command.")
    line = "━━━━━━━━━━━━━━━━━━━━"
    embed = discord.Embed(
        title=f"{E_TITLE} {BRAND_NAME.upper()}",
        description=(
            f"**Welcome to the {BRAND_NAME} Centre.**\n\n"
            "Need a trusted middleman for your trade?\n\n"
            f"{line}\n"
            f"{E_BOLT} **HOW IT WORKS**\n"
            f"> {E_BULLET} Create a ticket\n"
            f"> {E_BULLET} Provide your trade information\n"
            f"> {E_BULLET} Enter Trader 2\n"
            f"> {E_BULLET} Complete your trade safely\n\n"
            f"{line}\n"
            f"{E_ARROW} **Click below to create a middleman ticket.**\n"
            f"{E_ARROW} **Please follow the middleman's instructions throughout the trade.**"
        ),
        color=EMBED_COLOR,
    )

    files = []
    logo_path = os.path.join(BASE_DIR, LOGO_FILE)
    banner_path = get_banner_path()
    if os.path.exists(logo_path):
        files.append(discord.File(logo_path, filename=LOGO_FILE))
        embed.set_thumbnail(url=f"attachment://{LOGO_FILE}")
    if os.path.exists(banner_path):
        files.append(discord.File(banner_path, filename=BANNER_FILE))
        embed.set_image(url=f"attachment://{BANNER_FILE}")

    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.channel.send(embed=embed, files=files, view=PanelView())
    except discord.Forbidden:
        return await interaction.followup.send(
            "I couldn't post the panel in this channel. Check my channel permissions.",
            ephemeral=True,
        )
    except discord.HTTPException as error:
        print(f"Could not post middleman panel: {error}")
        return await interaction.followup.send(
            "Discord couldn't post the panel. Please try again.",
            ephemeral=True,
        )
    await interaction.followup.send("Panel sent.", ephemeral=True)


@bot.tree.command(description="Send the Kyokah support ticket panel to this channel")
async def suppanel(interaction: discord.Interaction):

    if not interaction.user.guild_permissions.administrator:
        return await deny(interaction, "Only administrators can use this command.")

    embed = discord.Embed(
        title="<:Support:1569561796103245944> Kyokah SUPPORT",
        description=(
            "**Welcome to Kyokah Support**\n\n"
            "Need help with an issue, question, report, or anything else? "
            "Our Support Team is ready to assist.\n\n"
            "**➡️ GET IN TOUCH**\n\n"
            "> Open a private ticket and explain what you need help with.\n"
            "> Our team will review your request and assist you as soon as possible.\n\n"
            "**➡️ PLEASE PROVIDE**\n\n"
            "> • A clear explanation of your issue\n"
            "> • Relevant details or usernames\n"
            "> • Screenshots or proof when necessary\n\n"
            "**➡️ PRIVATE & SECURE**\n\n"
            "> Your support ticket is private and only accessible to you and our Support Team.\n\n"
            "**Press the button below to contact us.**"
        ),
        color=EMBED_COLOR,
    )
    banner = attach_banner(embed)
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.channel.send(
            embed=embed,
            files=[banner] if banner else [],
            view=SupportPanelView(),
        )
    except discord.Forbidden:
        return await interaction.followup.send(
            "I couldn't post the support panel in this channel. Check my channel permissions.",
            ephemeral=True,
        )
    except discord.HTTPException as error:
        print(f"Could not post support panel: {error}")
        return await interaction.followup.send(
            "Discord couldn't post the support panel. Please try again.",
            ephemeral=True,
        )
    await interaction.followup.send("Support panel sent.", ephemeral=True)


@bot.tree.command(description="Post the opt-in DM announcement panel")
async def dmpanel(interaction: discord.Interaction):

    if not interaction.user.guild_permissions.administrator:
        return await deny(interaction, "Only administrators can post this panel.")

    embed = discord.Embed(
        title="DM Announcements",
        description=(
            "Choose whether you'd like to receive server announcements by direct message.\n\n"
            "Click **Subscribe** to opt in. Click **Decline** to opt out or remove yourself "
            "from the subscriber list. You can change your choice at any time."
        ),
        color=EMBED_COLOR,
    )
    banner = attach_banner(embed)
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.send(
        embed=embed,
        files=[banner] if banner else [],
        view=DMSubscriptionView(),
    )
    await interaction.followup.send("DM subscription panel posted.", ephemeral=True)


@bot.tree.command(description="Send a message to members who opted in to DMs")
@app_commands.describe(message="Message to send to DM subscribers")
async def senddm(interaction: discord.Interaction, message: str):

    if not interaction.user.guild_permissions.administrator:
        return await deny(interaction, "Only administrators can send subscriber DMs.")
    if not message.strip():
        return await interaction.response.send_message(
            "The message can't be empty.", ephemeral=True
        )
    if len(message) > 2000:
        return await interaction.response.send_message(
            "The message can't exceed 2,000 characters.", ephemeral=True
        )

    async with _dm_subscriber_lock:
        try:
            subscriber_count = len(load_dm_subscribers())
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            print(f"Could not read DM subscriptions: {error}")
            return await interaction.response.send_message(
                "I couldn't read the subscriber list. Please contact an administrator.",
                ephemeral=True,
            )

    if subscriber_count == 0:
        return await interaction.response.send_message(
            "There are no DM subscribers yet.", ephemeral=True
        )

    embed = discord.Embed(
        title="Confirm DM announcement",
        description=f"Sending to **{subscriber_count}** opted-in subscribers:\n\n{message}",
        color=EMBED_COLOR,
    )
    await interaction.response.send_message(
        embed=embed,
        view=DMConfirmationView(interaction.user.id, message),
        ephemeral=True,
    )


@bot.tree.command(description="Claim this ticket (middlemen only)")
async def claim(interaction: discord.Interaction):
    await claim_ticket(interaction)


@bot.tree.command(description="Unclaim this ticket")
async def unclaim(interaction: discord.Interaction):
    await unclaim_ticket(interaction)


@bot.tree.command(description="Close this ticket")
async def close(interaction: discord.Interaction):
    await close_from_interaction(interaction)


@bot.tree.command(description="Add a user to this ticket (middlemen only)")
async def add(interaction: discord.Interaction, user: discord.Member):
    if not get_ticket(interaction.channel):
        return await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
    if not is_mm(interaction.user):
        return await deny(interaction, "This command is for middlemen only.")
    await interaction.channel.set_permissions(
        user, view_channel=True, send_messages=True, read_message_history=True, attach_files=True
    )
    await interaction.response.send_message(f"Added {user.mention} to the ticket.")


@bot.tree.command(description="Remove a user from this ticket (middlemen only)")
async def remove(interaction: discord.Interaction, user: discord.Member):
    if not get_ticket(interaction.channel):
        return await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
    if not is_mm(interaction.user):
        return await deny(interaction, "This command is for middlemen only.")
    await interaction.channel.set_permissions(user, overwrite=None)
    await interaction.response.send_message(f"Removed {user.mention} from the ticket.")


def build_guide_embed() -> discord.Embed:
    def step(title, *lines):
        return f"**▎ {title}**\n" + "\n".join(f"> • {l}" for l in lines) + "\n\n"

    description = (
        "**Welcome to Your Middleman Ticket!**\n\n"
        "Follow the steps below to ensure a safe and successful trade.\n\n"
        + step(
            "1. Trade Setup",
            "Both traders must join this ticket.",
            "Provide the full trade details, including all items and payments involved.",
        )
        + step(
            "2. Middleman Verification",
            "The middleman will confirm all trade details with both traders.",
            "Do not proceed until everything is verified and approved.",
        )
        + step(
            "3. Secure Handover",
            "Both traders must transfer the agreed items or payments to the middleman.",
            "Follow the middleman's instructions throughout the process.",
        )
        + step(
            "4. Final Exchange",
            "The middleman will verify all received items and payments.",
            "Once verified, everything will be delivered to the correct traders.",
        )
        + step(
            "5. Trade Completion",
            "Once the trade is complete, you may leave a voluntary tip to support the middleman.",
            "Leave a **vouch** to confirm your experience and help build trust in our service.",
        )
        + step(
            "⚠️ Important",
            "Never trade outside this ticket or transfer anything without the middleman's confirmation.",
            "Only the middleman who claimed this ticket may handle your trade.",
        )
        + "**Thank you for using our Middleman Service!**"
    )
    return discord.Embed(
        title=f"{SERVER_NAME} — Middleman Process Guide",
        description=description,
        color=EMBED_COLOR,
    )


@bot.tree.command(description="Send the middleman process guide in this channel (middlemen only)")
async def mminfo(interaction: discord.Interaction):
    if not is_mm(interaction.user):
        return await deny(interaction, "This command is for middlemen only.")

    embed = build_guide_embed()
    files = []
    logo_path = os.path.join(BASE_DIR, LOGO_FILE)
    if os.path.exists(logo_path):
        files.append(discord.File(logo_path, filename=LOGO_FILE))
        embed.set_thumbnail(url=f"attachment://{LOGO_FILE}")
    await interaction.response.send_message(embed=embed, files=files)


@bot.tree.command(description="Ban a user from the server")
async def ban(interaction: discord.Interaction, user: discord.User, reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.ban_members:
        return await deny(interaction, "You need the Ban Members permission to use this command.")

    guild = interaction.guild
    if user.id == interaction.user.id:
        return await interaction.response.send_message("You can't ban yourself.", ephemeral=True)
    if user.id == bot.user.id:
        return await interaction.response.send_message("I can't ban myself.", ephemeral=True)
    if user.id == guild.owner_id:
        return await interaction.response.send_message("You can't ban the server owner.", ephemeral=True)

    member = guild.get_member(user.id)
    if member:
        if member.top_role >= interaction.user.top_role and interaction.user.id != guild.owner_id:
            return await interaction.response.send_message(
                "You can't ban someone with an equal or higher role than yours.", ephemeral=True
            )
        if member.top_role >= guild.me.top_role:
            return await interaction.response.send_message(
                "My role is not high enough to ban them. Move my role above theirs in Server Settings → Roles.",
                ephemeral=True,
            )

    try:
        await guild.ban(user, reason=f"{interaction.user} ({interaction.user.id}): {reason}")
    except discord.Forbidden:
        return await interaction.response.send_message(
            "I don't have permission to ban. Give my role the Ban Members permission.", ephemeral=True
        )

    embed = discord.Embed(
        title="Member banned",
        description=f"{user.mention} (`{user.id}`) was banned.\n**Reason:** {reason}",
        color=discord.Color.red(),
    )
    embed.set_footer(text=f"Banned by {interaction.user}")
    await interaction.response.send_message(embed=embed)

    log = guild.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
    if log and log.id != interaction.channel.id:
        await log.send(embed=embed)


def resolve_server_emoji_shortcodes(emojis, text: str) -> str:
    emoji_by_name = {emoji.name.casefold(): str(emoji) for emoji in emojis}
    return re.sub(
        r"(?<![<\w]):([A-Za-z0-9_]{2,32}):(?!\d)",
        lambda match: emoji_by_name.get(match.group(1).casefold(), match.group(0)),
        text,
    )


@bot.tree.command(description="Make the bot send a message in an embed (middlemen only)")
@app_commands.describe(
    message="What the bot should say. Type :emoji_name: for server emojis and \\n for a new line",
    title="Optional title for the embed",
    channel="Where to send it (default: this channel)",
    color="Optional hex color, like FF0000 (default: your panel color)",
)
async def say(
    interaction: discord.Interaction,
    message: str,
    title: str = None,
    channel: discord.TextChannel = None,
    color: str = None,
):
    if not is_mm(interaction.user):
        return await deny(interaction, "This command is for middlemen only.")

    guild_emojis = interaction.guild.emojis if interaction.guild else ()
    text = resolve_server_emoji_shortcodes(guild_emojis, message.replace("\\n", "\n"))
    if title:
        title = resolve_server_emoji_shortcodes(guild_emojis, title)
    if len(text) > 4000 or (title and len(title) > 256):
        return await interaction.response.send_message(
            "Message is too long (max 4000 characters, title max 256).", ephemeral=True
        )

    embed_color = EMBED_COLOR
    if color:
        try:
            embed_color = int(color.lstrip("#"), 16)
            if not 0 <= embed_color <= 0xFFFFFF:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "Color must be a hex code like FF0000.", ephemeral=True
            )

    target = channel or interaction.channel
    embed = discord.Embed(title=title, description=text, color=embed_color)
    await interaction.response.defer(ephemeral=True)
    try:
        await target.send(embed=embed)
    except discord.Forbidden:
        return await interaction.followup.send(
            f"I can't send messages in {target.mention}.", ephemeral=True
        )
    await interaction.followup.send(f"Sent in {target.mention}.", ephemeral=True)


WARN_FILE = os.path.join(BASE_DIR, "warnings.json")


def load_warnings() -> dict:
    try:
        with open(WARN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_warnings(data: dict):
    with open(WARN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


@bot.tree.command(description="Warn a member (middlemen only)")
@app_commands.describe(user="Who to warn", reason="Why they are being warned")
async def warnadd(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not is_mm(interaction.user):
        return await deny(interaction, "This command is for middlemen only.")
    if user.bot:
        return await interaction.response.send_message("You can't warn a bot.", ephemeral=True)
    if user.id == interaction.user.id:
        return await interaction.response.send_message("You can't warn yourself.", ephemeral=True)
    if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message(
            "You can't warn someone with an equal or higher role than yours.", ephemeral=True
        )
    if len(reason) > 1000:
        return await interaction.response.send_message("Reason is too long (max 1000 characters).", ephemeral=True)

    data = load_warnings()
    entries = data.setdefault(str(user.id), [])
    entries.append(
        {
            "reason": reason,
            "by": interaction.user.id,
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    )
    save_warnings(data)
    total = len(entries)

    embed = discord.Embed(
        title="Warning added",
        description=f"{user.mention} has been warned.\n**Reason:** {reason}",
        color=discord.Color.orange(),
    )
    embed.add_field(name="Total warnings", value=str(total))
    embed.set_footer(text=f"Warned by {interaction.user}")
    await interaction.response.send_message(embed=embed)

    # Try to tell the user in DMs (works only if their DMs are open)
    dm = discord.Embed(
        title=f"You received a warning in {interaction.guild.name}",
        description=f"**Reason:** {reason}\n**Total warnings:** {total}",
        color=discord.Color.orange(),
    )
    try:
        await user.send(embed=dm)
    except (discord.Forbidden, discord.HTTPException):
        pass

    log = interaction.guild.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
    if log and log.id != interaction.channel.id:
        await log.send(embed=embed)


@bot.tree.command(description="Check your warnings (middlemen can check other members too)")
@app_commands.describe(user="Member to check (middlemen only). Leave empty to check yourself")
async def warncheck(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    checking_self = target.id == interaction.user.id
    if not checking_self and not is_mm(interaction.user):
        return await deny(interaction, "You can only check your own warnings.")

    entries = load_warnings().get(str(target.id), [])
    if not entries:
        msg = "You have no warnings." if checking_self else f"{target.mention} has no warnings."
        return await interaction.response.send_message(msg, ephemeral=True)

    shown = entries[-10:]
    start = len(entries) - len(shown) + 1
    lines = []
    for n, e in enumerate(shown, start=start):
        try:
            when = f"<t:{int(datetime.datetime.fromisoformat(e['time']).timestamp())}:d>"
        except (KeyError, ValueError):
            when = "unknown date"
        reason = str(e.get("reason", "No reason"))[:200]
        lines.append(f"**{n}.** {reason}\n└ by <@{e.get('by', 0)}> on {when}")

    embed = discord.Embed(
        title=f"Warnings for {target.display_name}",
        description="\n\n".join(lines),
        color=discord.Color.orange(),
    )
    footer = f"Total warnings: {len(entries)}"
    if len(entries) > len(shown):
        footer += f" (showing the last {len(shown)})"
    embed.set_footer(text=footer)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------- staff applications ----------
APPLY_COOLDOWN = datetime.timedelta(hours=1)
_last_apply = {}


class StaffApplicationModal(discord.ui.Modal, title="Staff Application"):
    role = discord.ui.TextInput(label="Role Applying For", placeholder="Example: Middleman", max_length=50)
    skills = discord.ui.TextInput(
        label="Skills / Experience",
        style=discord.TextStyle.paragraph,
        placeholder="Tell us about your skills and experience.",
        max_length=1000,
    )
    active = discord.ui.TextInput(label="Are You Active On Discord?", placeholder="Yes / No", max_length=20)
    availability = discord.ui.TextInput(
        label="Timezone / Daily Availability", placeholder="Example: IST, 4-5 hours a day", max_length=100
    )
    why = discord.ui.TextInput(
        label="Why Should We Pick You?", style=discord.TextStyle.paragraph, max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user
        role = guild.get_role(MIDDLEMAN_ROLE_ID)

        allow = dict(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(**allow),
            guild.me: discord.PermissionOverwrite(**allow, embed_links=True, manage_channels=True),
        }
        if role:
            overwrites[role] = discord.PermissionOverwrite(**allow)

        category = guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None
        channel = await guild.create_text_channel(
            name=f"app-{user.name}"[:90],
            overwrites=overwrites,
            category=category,
            topic=f"owner={user.id}",
        )

        embed = discord.Embed(
            title="New Staff Application",
            description="Staff will review your application in this ticket. Please wait for a reply.",
            color=EMBED_COLOR,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Applicant", value=f"{user.mention} (`{user.id}`)", inline=False)
        embed.add_field(name="Role applying for", value=self.role.value, inline=False)
        embed.add_field(name="Skills / Experience", value=self.skills.value, inline=False)
        embed.add_field(name="Active on Discord?", value=self.active.value)
        embed.add_field(name="Timezone / Availability", value=self.availability.value)
        embed.add_field(name="Why should we pick them?", value=self.why.value, inline=False)
        embed.set_footer(text=f"Account created {user.created_at:%Y-%m-%d}")

        ping = f"{user.mention} {role.mention if role else ''}"
        await channel.send(
            content=ping,
            embed=embed,
            view=TicketView(),
        )

        _last_apply[user.id] = datetime.datetime.now(datetime.timezone.utc)
        await interaction.followup.send(f"Your application ticket is ready: {channel.mention}", ephemeral=True)


class StaffApplyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply for Staff", emoji="📝", style=discord.ButtonStyle.secondary, custom_id="staff:apply")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        for ch in interaction.guild.text_channels:
            info = get_ticket(ch)
            if info and info["owner"] == str(interaction.user.id) and ch.name.startswith("app-"):
                return await interaction.response.send_message(
                    f"You already have an open application: {ch.mention}", ephemeral=True
                )
        now = datetime.datetime.now(datetime.timezone.utc)
        last = _last_apply.get(interaction.user.id)
        if last and now - last < APPLY_COOLDOWN:
            return await interaction.response.send_message(
                "You already applied recently. Please wait before applying again.", ephemeral=True
            )
        await interaction.response.send_modal(StaffApplicationModal())


@bot.tree.command(description="Send the staff application panel to this channel")
async def sapanel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await deny(interaction, "Only administrators can use this command.")

    embed = discord.Embed(
        title="🛡️ STAFF APPLICATIONS",
        description=(
            f"Want to join the {SERVER_NAME} Staff Team?\n"
            "We're looking for active, trustworthy, and helpful members to help make our server a better place!\n\n"
            f"{E_BOLT} **Requirements**\n\n"
            f"{E_BULLET} Be active and respectful\n"
            f"{E_BULLET} Have good knowledge of the server rules\n"
            f"{E_BULLET} Be mature and responsible\n"
            f"{E_BULLET} Be willing to help members\n"
            f"{E_BULLET} Have a good attitude and teamwork skills\n\n"
            f"{E_BOLT} **How to Apply**\n\n"
            "Click the Apply button below and answer all questions honestly.\n\n"
            f"{E_ARROW} Please do not joke or troll in your application. "
            "False or inappropriate applications may result in punishment.\n\n"
            "Good luck! 🍀\n"
            "We look forward to seeing your application!"
        ),
        color=EMBED_COLOR,
    )
    files = []
    logo_path = os.path.join(BASE_DIR, LOGO_FILE)
    banner_path = get_banner_path()
    if os.path.exists(logo_path):
        files.append(discord.File(logo_path, filename=LOGO_FILE))
        embed.set_thumbnail(url=f"attachment://{LOGO_FILE}")
    if os.path.exists(banner_path):
        files.append(discord.File(banner_path, filename=BANNER_FILE))
        embed.set_image(url=f"attachment://{BANNER_FILE}")

    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.channel.send(embed=embed, files=files, view=StaffApplyView())
    except discord.Forbidden:
        return await interaction.followup.send(
            "I couldn't post the staff application panel in this channel. "
            "Check my channel permissions.",
            ephemeral=True,
        )
    except discord.HTTPException as error:
        print(f"Could not post staff application panel: {error}")
        return await interaction.followup.send(
            "Discord couldn't post the staff application panel. Please try again.",
            ephemeral=True,
        )
    await interaction.followup.send("Staff application panel sent.", ephemeral=True)


# ---------- vouches ----------
VOUCH_FILE = os.path.join(BASE_DIR, "vouches.json")
_vouch_lock = asyncio.Lock()


def load_vouches() -> dict:
    try:
        with open(VOUCH_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("completed", {})  # ticket channel id -> middleman id
    data.setdefault("vouches", [])
    data.setdefault("base", {})  # middleman id -> staff-set base count
    return data


def save_vouches(data: dict):
    with open(VOUCH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def mm_total(data: dict, mm_id: int) -> int:
    real = sum(1 for v in data["vouches"] if v["mm"] == mm_id)
    return real + data["base"].get(str(mm_id), 0)


async def update_rank(member: discord.Member, total: int):
    """Give the member the highest rank role they qualify for and remove the other rank roles."""
    earned = None
    for need, rid in sorted(VOUCH_RANKS):
        if total >= need and rid:
            earned = rid
    all_ids = {rid for _, rid in VOUCH_RANKS if rid}
    remove = [r for r in member.roles if r.id in all_ids and r.id != earned]
    role = member.guild.get_role(earned) if earned else None
    try:
        if remove:
            await member.remove_roles(*remove, reason="Vouch rank update")
        if role and role not in member.roles:
            await member.add_roles(role, reason="Vouch rank update")
    except discord.Forbidden:
        return "no_permission"
    return role


class VouchModal(discord.ui.Modal, title="Leave a Vouch"):
    trade_type = discord.ui.TextInput(
        label="Trade type", placeholder="Example: Crypto (LTC) <-> In-Game Items", max_length=100
    )
    rating = discord.ui.TextInput(label="Rating (1-5)", placeholder="5", max_length=1)
    feedback = discord.ui.TextInput(
        label="Feedback", style=discord.TextStyle.paragraph, required=False, max_length=500
    )

    def __init__(self, mm_id: int):
        super().__init__()
        self.mm_id = mm_id

    async def on_submit(self, interaction: discord.Interaction):
        if has_vouch_blocked_role(interaction.user):
            return await interaction.response.send_message(
                "Your role cannot submit vouches.", ephemeral=True
            )
        if not self.rating.value.isdigit() or not 1 <= int(self.rating.value) <= 5:
            return await interaction.response.send_message("Rating must be a number from 1 to 5.", ephemeral=True)
        if not get_ticket(interaction.channel):
            return await interaction.response.send_message(
                "Vouches can only be submitted from a trade ticket.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel

        proof = None
        async for message in channel.history(limit=100):
            if message.author.id != interaction.user.id:
                continue
            image = next(
                (attachment for attachment in message.attachments
                 if (attachment.content_type or "").startswith("image/")),
                None,
            )
            if image:
                if image.size > 8_000_000:
                    return await interaction.followup.send(
                        "Your trade screenshot must be 8 MB or smaller. Upload a smaller image and try again.",
                        ephemeral=True,
                    )
                extension = os.path.splitext(image.filename)[1].lower()
                if extension not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                    extension = ".png"
                proof = discord.File(
                    io.BytesIO(await image.read()),
                    filename=f"trade-proof{extension}",
                )
                break

        if proof is None:
            return await interaction.followup.send(
                "Upload your actual trade screenshot in this ticket, then click the vouch button again.",
                ephemeral=True,
            )

        out = interaction.guild.get_channel(VOUCH_CHANNEL_ID) if VOUCH_CHANNEL_ID else None
        if out is None:
            return await interaction.followup.send(
                "The vouch output channel isn't configured or available. Please contact an administrator.",
                ephemeral=True,
            )

        stars = int(self.rating.value)
        async with _vouch_lock:
            data = load_vouches()
            if any(v["ticket"] == channel.id and v["voucher"] == interaction.user.id for v in data["vouches"]):
                return await interaction.followup.send("You already vouched for this trade.", ephemeral=True)
            data["vouches"].append(
                {
                    "ticket": channel.id,
                    "voucher": interaction.user.id,
                    "mm": self.mm_id,
                    "rating": stars,
                    "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
            )
            save_vouches(data)

        embed = discord.Embed(
            title="Verified Trade & Vouch",
            description=(
                f"**Trade Type**\n`{self.trade_type.value}`\n\n"
                "**Users**\n"
                "**Trader 1:** Anonymous\n"
                "**Trader 2:** Anonymous\n"
                f"**Middleman:** <@{self.mm_id}>\n\n"
                f"**Vouch submitted by:** {interaction.user.mention}\n\n"
                f"**Trade Feedback**\n{'⭐' * stars}\n"
                + (f"{self.feedback.value}\n" if self.feedback.value else "")
                + "\n***__Trade Status : completed__***"
            ),
            color=EMBED_COLOR,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_image(url=f"attachment://{proof.filename}")
        try:
            await out.send(embed=embed, file=proof)
        except discord.HTTPException:
            async with _vouch_lock:
                data = load_vouches()
                data["vouches"] = [
                    v for v in data["vouches"]
                    if not (v["ticket"] == channel.id and v["voucher"] == interaction.user.id)
                ]
                save_vouches(data)
            return await interaction.followup.send(
                "I couldn't post your vouch. Please check the bot's channel permissions and try again.",
                ephemeral=True,
            )

        mm_member = interaction.guild.get_member(self.mm_id)
        if mm_member:
            await update_rank(mm_member, mm_total(load_vouches(), self.mm_id))

        await interaction.followup.send("Thanks! Your vouch was posted.", ephemeral=True)


class VouchView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Generate My Vouch",
        emoji="📋",
        style=discord.ButtonStyle.green,
        custom_id="vouch:open",
    )
    async def vouch(self, interaction: discord.Interaction, button: discord.ui.Button):
        if has_vouch_blocked_role(interaction.user):
            return await interaction.response.send_message(
                "Your role cannot use the vouch generator.", ephemeral=True
            )
        if not get_ticket(interaction.channel):
            return await interaction.response.send_message(
                "The vouch generator can only be used in a trade ticket.", ephemeral=True
            )
        data = load_vouches()
        mm_id = data["completed"].get(str(interaction.channel.id))
        if not mm_id:
            return await interaction.response.send_message("This trade hasn't been completed.", ephemeral=True)
        if any(v["ticket"] == interaction.channel.id and v["voucher"] == interaction.user.id for v in data["vouches"]):
            return await interaction.response.send_message("You already vouched for this trade.", ephemeral=True)
        await interaction.response.send_modal(VouchModal(mm_id))


@bot.tree.command(description="Send the vouch generator panel in a completed trade ticket")
async def vouchgen(interaction: discord.Interaction):
    if has_vouch_blocked_role(interaction.user):
        return await interaction.response.send_message(
            "Your role cannot use the vouch generator.", ephemeral=True
        )
    info = get_ticket(interaction.channel)
    if not info:
        return await interaction.response.send_message(
            "Run this command inside a trade ticket.", ephemeral=True
        )
    if not is_admin_or_owner(interaction.user) and not is_mm(interaction.user):
        return await deny(interaction, "Only middlemen or administrators can send this panel.")

    data = load_vouches()
    mm_id = data["completed"].get(str(interaction.channel.id))
    if not mm_id:
        return await interaction.response.send_message(
            "This ticket must be marked complete with `/complete` before generating vouches.",
            ephemeral=True,
        )

    output = interaction.guild.get_channel(VOUCH_CHANNEL_ID) if VOUCH_CHANNEL_ID else None
    if output is None:
        return await interaction.response.send_message(
            "The vouch output channel isn't configured or available. Please contact an administrator.",
            ephemeral=True,
        )

    embed = discord.Embed(
        title="📋 Trade Log Generator",
        description=(
            "**Trade Vouch System**\n\n"
            "Anyone who can view this completed ticket, including traders and the middleman, "
            "can submit an honest vouch.\n\n"
            "**Generate My Vouch**\n"
            "Post an actual trade screenshot in this ticket, then use the button below "
            "to provide the trade type, rating, and feedback.\n\n"
            "**Features**\n"
            "🔹 Uses the middleman-completed ticket\n"
            "🔹 Includes a screenshot posted by the submitter\n"
            "🔹 Posts your rating and feedback\n"
            "🔹 One vouch per submitter for this trade\n"
            "🔹 Identifies who submitted the vouch\n\n"
            f"**Output Channel**\n{output.mention}"
        ),
        color=EMBED_COLOR,
    )
    banner = attach_banner(embed)
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.send(
        embed=embed,
        files=[banner] if banner else [],
        view=VouchView(),
    )
    await interaction.followup.send("Vouch generator panel sent.", ephemeral=True)


@bot.tree.command(description="Mark this trade as completed and let the traders vouch (middlemen only)")
async def complete(interaction: discord.Interaction):
    if has_vouch_blocked_role(interaction.user):
        return await interaction.response.send_message(
            "Your role cannot complete trades.", ephemeral=True
        )
    info = get_ticket(interaction.channel)
    if not info:
        return await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
    if not is_mm(interaction.user):
        return await deny(interaction, "This command is for middlemen only.")
    if "claimed" not in info:
        return await interaction.response.send_message("Claim this ticket first.", ephemeral=True)
    if str(interaction.user.id) != info["claimed"] and not is_admin_or_owner(interaction.user):
        return await deny(interaction, "Only the middleman who claimed this ticket can complete it.")

    async with _vouch_lock:
        data = load_vouches()
        if str(interaction.channel.id) in data["completed"]:
            return await interaction.response.send_message("This trade is already marked as completed.", ephemeral=True)
        data["completed"][str(interaction.channel.id)] = int(info["claimed"])
        save_vouches(data)

    embed = discord.Embed(
        title="Trade Completed ✅",
        description=(
            f"Thanks for using our service! <@{info['claimed']}> was your middleman.\n\n"
            "Anyone who can view this ticket, including the traders and middleman, can click below "
            "to generate an honest vouch. Post an actual trade screenshot in this ticket before submitting."
        ),
        color=discord.Color.green(),
    )
    await interaction.response.send_message(embed=embed, view=VouchView())


@bot.tree.command(description="Set a middleman's vouch count and update their rank (admins only)")
@app_commands.describe(user="The middleman", amount="The total vouch count to set")
async def setvouch(interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 0, 100000]):
    if not interaction.user.guild_permissions.administrator:
        return await deny(interaction, "Only administrators can use this command.")
    if user.bot:
        return await interaction.response.send_message("You can't set vouches for a bot.", ephemeral=True)
    if not is_mm(user):
        return await interaction.response.send_message("That user isn't a middleman.", ephemeral=True)

    async with _vouch_lock:
        data = load_vouches()
        real = sum(1 for v in data["vouches"] if v["mm"] == user.id)
        data["base"][str(user.id)] = amount - real
        save_vouches(data)

    result = await update_rank(user, amount)
    if result == "no_permission":
        rank_text = "⚠️ I couldn't change their rank role. Give me Manage Roles and move my role above the rank roles."
    elif result:
        rank_text = f"Rank: {result.mention}"
    else:
        rank_text = "Rank: none yet"

    embed = discord.Embed(
        title="Vouches updated",
        description=f"{user.mention} now has **{amount}** vouches.\n{rank_text}",
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Set by {interaction.user}")
    await interaction.response.send_message(embed=embed)

    log = interaction.guild.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
    if log and log.id != interaction.channel.id:
        await log.send(embed=embed)


@bot.tree.command(description="Show a middleman's vouch count and rank")
async def vouches(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    data = load_vouches()
    mine = [v for v in data["vouches"] if v["mm"] == target.id]
    base = data["base"].get(str(target.id), 0)
    total = len(mine) + base
    if total == 0:
        return await interaction.response.send_message(f"{target.mention} has no vouches yet.", ephemeral=True)

    rank_ids = {rid for _, rid in VOUCH_RANKS if rid}
    rank = next((r for r in target.roles if r.id in rank_ids), None)
    desc = f"**Total vouches:** {total}\n"
    desc += f"**Verified in bot:** {len(mine)}\n"
    if base:
        desc += f"**Set by staff:** {base}\n"
    if mine:
        avg = sum(v["rating"] for v in mine) / len(mine)
        desc += f"**Average rating:** {avg:.2f} / 5 {'⭐' * round(avg)}\n"
    if rank:
        desc += f"**Rank:** {rank.mention}\n"

    embed = discord.Embed(title=f"Vouches for {target.display_name}", description=desc, color=EMBED_COLOR)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(description="Send the vouch info panel to this channel")
async def autovpanel(interaction: discord.Interaction):
    if has_vouch_blocked_role(interaction.user):
        return await interaction.response.send_message(
            "Your role cannot send the vouch info panel.", ephemeral=True
        )
    if not interaction.user.guild_permissions.administrator:
        return await deny(interaction, "Only administrators can use this command.")
    embed = discord.Embed(
        title="📋 Trade Vouch System",
        description=(
            "Vouches are posted after a **completed middleman trade**.\n\n"
            f"{E_BOLT} **How it works**\n"
            f"{E_BULLET} Your middleman runs `/complete` when the trade is done\n"
            f"{E_BULLET} Click **Generate My Vouch** in your ticket\n"
            f"{E_BULLET} Upload your actual trade screenshot, then add your rating and feedback\n"
            f"{E_BULLET} Your vouch is posted with your proof, and your name stays anonymous\n\n"
            f"{E_ARROW} Use `/vouches` to see a middleman's rating."
        ),
        color=EMBED_COLOR,
    )
    banner = attach_banner(embed)
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.channel.send(embed=embed, files=[banner] if banner else [])
    except discord.Forbidden:
        return await interaction.followup.send(
            "I couldn't post the vouch info panel in this channel. Check my channel permissions.",
            ephemeral=True,
        )
    except discord.HTTPException as error:
        print(f"Could not post vouch info panel: {error}")
        return await interaction.followup.send(
            "Discord couldn't post the vouch info panel. Please try again.",
            ephemeral=True,
        )
    await interaction.followup.send("Panel sent.", ephemeral=True)

class RecruitView(discord.ui.View):
    def __init__(self, recruiter_id: int):
        super().__init__(timeout=60)
        self.recruiter_id = recruiter_id

    async def _finish(self, interaction: discord.Interaction, text: str, color: discord.Color):
        # Delete the old invite message.
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass

        embed = discord.Embed(
            title="Recruitment Update",
            description=text,
            color=color,
        )
        files = []
        logo_path = os.path.join(BASE_DIR, LOGO_FILE)
        banner_path = get_banner_path()
        if os.path.exists(logo_path):
            files.append(discord.File(logo_path, filename=LOGO_FILE))
            embed.set_thumbnail(url=f"attachment://{LOGO_FILE}")
        if os.path.exists(banner_path):
            files.append(discord.File(banner_path, filename=BANNER_FILE))
            embed.set_image(url=f"attachment://{BANNER_FILE}")
        await interaction.channel.send(content=f"<@{self.recruiter_id}>", embed=embed, files=files)
        self.stop()

    @discord.ui.button(label="Accept", emoji="✅", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        role = interaction.guild.get_role(HITTER_ROLE_ID)
        if role is None:
            return await interaction.followup.send(
                "The configured Hitter role wasn't found. Please contact an administrator.",
                ephemeral=True,
            )
        note = ""
        try:
            await interaction.user.add_roles(
                role, reason=f"Accepted recruit invite from {self.recruiter_id}"
            )
        except discord.Forbidden:
            note = (
                "\n⚠️ I couldn't assign the Hitter role. An administrator may need to move "
                "my role higher and grant Manage Roles."
            )
        await self._finish(
            interaction,
            f"{interaction.user.mention} has **accepted** the invite! 🎉{note}",
            discord.Color.green(),
        )

    @discord.ui.button(label="Decline", emoji="❌", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self._finish(
            interaction,
            f"{interaction.user.mention} has **declined** the invite.",
            discord.Color.red(),
        )


@bot.tree.command(description="Invite a member to join the hitting team")
async def recruit(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        return await deny(interaction, "Only administrators can recruit hitters.")
    if user.bot:
        return await interaction.response.send_message("You can't recruit a bot.", ephemeral=True)

    role = interaction.guild.get_role(HITTER_ROLE_ID)
    if role is None:
        return await interaction.response.send_message(
            "The configured Hitter role wasn't found.", ephemeral=True
        )
    if role in user.roles:
        return await interaction.response.send_message(
            f"{user.mention} already has the Hitter role.", ephemeral=True
        )

    embed = discord.Embed(
        title="Luck's hitting service offer",
        description=(
            "You have received an official recruit offer to join our team as a Hitter.\n\n"
            "By joining the team, you'll get access to our staff channels and can start hitting and earn profits alongside our other staff members.\n\n"
            "Read ⁠〃・main-guide to learn how hitting works.\n\n"
            "Need help getting started? Ask the staff in ⁠〃・staff-chat.\n\n"
            "Do you want to accept this recruiting offer?\n\n"
            "⏳ You have 1 minute to respond.\n\n"
            "The decision is yours. Make it count."
        ),
        color=EMBED_COLOR,
    )
    banner = attach_banner(embed)
    await interaction.response.send_message(
        content=user.mention,
        embed=embed,
        files=[banner] if banner else [],
        view=RecruitView(interaction.user.id),
    )


@bot.tree.command(
    description="Start vacation or restore your highest role",
    aliases=["vacattion"],
)
async def vacation(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "Use this command in the server.", ephemeral=True
        )

    if interaction.user.bot:
        return await interaction.response.send_message(
            "Bot accounts can't use this command.", ephemeral=True
        )

    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        return await interaction.response.send_message(
            "I need the Manage Roles permission to update your roles.", ephemeral=True
        )

    member = interaction.user
    await interaction.response.defer(ephemeral=True)
    user_key = str(member.id)
    action = None
    highest_role_id = None

    try:
        async with _vacation_lock:
            records = load_vacation_records()
            record = records.get(user_key)

            if record is None:
                highest_role_id = member.top_role.id
                saved_role_ids = [
                    role.id for role in member.roles if role.id != guild.id
                ]
                roles_to_remove = [
                    role for role in member.roles
                    if role.id != guild.id and role.id not in VACATION_KEEP_ROLE_IDS
                ]
                if any(
                    role.managed or role >= bot_member.top_role
                    for role in roles_to_remove
                ):
                    return await interaction.followup.send(
                        "I can't remove all your roles because one or more are managed or "
                        "above my role. Ask an administrator to adjust the role hierarchy.",
                        ephemeral=True,
                    )

                records[user_key] = {
                    "roles": saved_role_ids,
                    "highest_role": highest_role_id,
                    "highest_restored": False,
                }
                save_vacation_records(records)
                if roles_to_remove:
                    try:
                        await member.remove_roles(
                            *roles_to_remove,
                            reason="Member started vacation",
                            atomic=False,
                        )
                    except discord.Forbidden:
                        return await interaction.followup.send(
                            "I couldn't remove your roles. The saved role list was kept so "
                            "you can use `+fill` to restore them.",
                            ephemeral=True,
                        )
                    except discord.HTTPException as error:
                        print(f"Could not remove vacation roles for {member.id}: {error}")
                        return await interaction.followup.send(
                            "Discord couldn't update your roles. The saved role list was kept "
                            "so you can use `+fill` to restore them.",
                            ephemeral=True,
                        )
                action = "started"
            else:
                if record.get("highest_restored", False):
                    return await interaction.followup.send(
                        "Your highest role is already restored. Use `+fill` to restore all "
                        "your saved roles.",
                        ephemeral=True,
                    )

                highest_role_id = record.get("highest_role")
                if highest_role_id is None:
                    return await interaction.followup.send(
                        "There is no saved highest role to restore. Use `+fill` to restore "
                        "your saved roles.",
                        ephemeral=True,
                    )
                highest_role = guild.get_role(highest_role_id)
                if highest_role is None:
                    return await interaction.followup.send(
                        f"Saved highest role `{highest_role_id}` no longer exists. "
                        "Use `+fill` to restore any remaining roles.",
                        ephemeral=True,
                    )
                if (
                    highest_role.id != guild.id
                    and highest_role not in member.roles
                ):
                    if highest_role.managed or highest_role >= bot_member.top_role:
                        return await interaction.followup.send(
                            "I can't restore your highest role because it is managed or above "
                            "my role. Ask an administrator to adjust the role hierarchy.",
                            ephemeral=True,
                        )
                    try:
                        await member.add_roles(
                            highest_role,
                            reason="Member returned from vacation",
                        )
                    except discord.Forbidden:
                        return await interaction.followup.send(
                            "I couldn't restore your highest role. Check the bot's Manage Roles "
                            "permission and role hierarchy.",
                            ephemeral=True,
                        )
                    except discord.HTTPException as error:
                        print(f"Could not restore highest role for {member.id}: {error}")
                        return await interaction.followup.send(
                            "Discord couldn't restore your highest role right now. Please try again.",
                            ephemeral=True,
                        )

                record["highest_restored"] = True
                save_vacation_records(records)
                action = "highest_restored"
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        print(f"Could not update vacation role data for {member.id}: {error}")
        return await interaction.followup.send(
            "I couldn't read or save your vacation role list. Please contact an administrator.",
            ephemeral=True,
        )

    if action == "started":
        embed = discord.Embed(
            title="🏖️ Vacation Started",
            color=EMBED_COLOR,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="Highest Role", value=str(highest_role_id), inline=False)
    else:
        embed = discord.Embed(
            title="🏖️ Vacation Ended",
            description=(
                "Your highest role has been restored. Please use `+fill` to restore all your roles."
            ),
            color=EMBED_COLOR,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )

    try:
        await member.send(embed=embed)
    except discord.Forbidden:
        return await interaction.followup.send(
            "Your roles were updated, but I couldn't DM you. Enable server-member DMs "
            "to receive the confirmation.",
            ephemeral=True,
        )
    except discord.HTTPException as error:
        print(f"Could not DM vacation confirmation to {member.id}: {error}")
        return await interaction.followup.send(
            "Your roles were updated, but Discord couldn't deliver the DM.",
            ephemeral=True,
        )

    await interaction.followup.send(
        "Your role update is complete. I sent you a DM confirmation.",
        ephemeral=True,
    )


@bot.tree.command(description="Restore saved vacation roles and add approved fill roles")
async def fill(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "Use this command in the server.", ephemeral=True
        )

    if interaction.user.bot:
        return await interaction.response.send_message(
            "Bot accounts can't use this command.", ephemeral=True
        )

    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        return await interaction.response.send_message(
            "I need the Manage Roles permission to restore your roles.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    user_key = str(interaction.user.id)
    try:
        async with _vacation_lock:
            records = load_vacation_records()
            record = records.get(user_key)
            role_ids_to_restore = set(record["roles"]) if record else set()
            role_ids_to_restore.update(FILLABLE_ROLE_IDS)
            if not role_ids_to_restore:
                return await interaction.followup.send(
                    "There are no saved or approved roles to restore.", ephemeral=True
                )

            current_role_ids = {role.id for role in interaction.user.roles}
            roles_to_add = []
            missing_role_ids = []
            for role_id in role_ids_to_restore:
                if role_id == guild.id or role_id in current_role_ids:
                    continue
                role = guild.get_role(role_id)
                if role is None:
                    missing_role_ids.append(role_id)
                else:
                    roles_to_add.append(role)

            if missing_role_ids:
                missing_ids = ", ".join(str(role_id) for role_id in sorted(missing_role_ids))
                return await interaction.followup.send(
                    f"These saved roles no longer exist, so I can't fully restore your roles: "
                    f"`{missing_ids}`. Ask an administrator for help.",
                    ephemeral=True,
                )

            privileged_permissions = (
                "administrator",
                "manage_guild",
                "manage_roles",
                "manage_channels",
                "manage_messages",
                "manage_webhooks",
                "kick_members",
                "ban_members",
                "moderate_members",
                "mention_everyone",
                "view_audit_log",
            )
            unsafe_fill_roles = [
                role
                for role in roles_to_add
                if role.id in FILLABLE_ROLE_IDS
                and any(getattr(role.permissions, name) for name in privileged_permissions)
            ]
            if unsafe_fill_roles:
                unsafe_ids = ", ".join(str(role.id) for role in unsafe_fill_roles)
                return await interaction.followup.send(
                    f"I can't self-assign privileged roles: `{unsafe_ids}`. "
                    "Ask an administrator to remove them from the approved fill list.",
                    ephemeral=True,
                )
            if any(
                role.managed or role >= bot_member.top_role
                for role in roles_to_add
            ):
                return await interaction.followup.send(
                    "I can't restore all your roles because one or more are managed or above "
                    "my role. Ask an administrator to adjust the role hierarchy.",
                    ephemeral=True,
                )

            if roles_to_add:
                try:
                    await interaction.user.add_roles(
                        *roles_to_add,
                        reason="Member restored vacation roles",
                        atomic=False,
                    )
                except discord.Forbidden:
                    return await interaction.followup.send(
                        "I couldn't restore your roles. Check the bot's Manage Roles "
                        "permission and role hierarchy.",
                        ephemeral=True,
                    )
                except discord.HTTPException as error:
                    print(f"Could not restore vacation roles for {interaction.user.id}: {error}")
                    return await interaction.followup.send(
                        "Discord couldn't restore your roles right now. Please try again.",
                        ephemeral=True,
                    )

            if record is not None:
                del records[user_key]
                save_vacation_records(records)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        print(f"Could not restore vacation role data for {interaction.user.id}: {error}")
        return await interaction.followup.send(
            "I couldn't read or save your vacation role list. Please contact an administrator.",
            ephemeral=True,
        )

    await interaction.followup.send(
        "Your approved fill roles and any saved vacation roles have been restored.",
        ephemeral=True,
    )


# ---------- help command ----------
BOT_STARTED_AT = datetime.datetime.now(datetime.timezone.utc)

def _read_host_memory():
    try:
        values = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as memory_file:
            for line in memory_file:
                key, value = line.split(":", 1)
                values[key] = int(value.strip().split()[0])
        total = values.get("MemTotal", 0) / 1024
        available = values.get("MemAvailable", values.get("MemFree", 0)) / 1024
        return total, max(0, total - available), available
    except (OSError, ValueError, KeyError):
        return None, None, None

@bot.hybrid_command(name="ping", description="Show Kyokah gateway latency")
async def ping_command(ctx):
    embed = discord.Embed(title="Kyokah • Gateway Ping", description=f"Gateway latency: {bot.latency * 1000:.1f} ms", color=EMBED_COLOR)
    embed.set_footer(text="Discord gateway heartbeat latency")
    await ctx.send(embed=embed)

@bot.hybrid_command(name="stats", description="Show Kyokah bot and hosting statistics")
async def stats_command(ctx):
    uptime = datetime.datetime.now(datetime.timezone.utc) - BOT_STARTED_AT
    total_ram, used_ram, free_ram = _read_host_memory()
    try:
        loads = os.getloadavg()
        cpu_text = f"{loads[0]:.2f} / {loads[1]:.2f} / {loads[2]:.2f} (1 / 5 / 15 min)"
    except (AttributeError, OSError):
        cpu_text = "Unavailable"
    embed = discord.Embed(title="Kyokah • System Stats", color=EMBED_COLOR)
    embed.add_field(name="Gateway Ping", value=f"{bot.latency * 1000:.1f} ms", inline=True)
    embed.add_field(name="Uptime", value=str(uptime).split(".")[0], inline=True)
    embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Cached Users", value=str(len(bot.users)), inline=True)
    if total_ram is not None:
        embed.add_field(name="Host RAM (visible)", value=f"Used: {used_ram:.0f} MiB / {total_ram:.0f} MiB\nFree: {free_ram:.0f} MiB", inline=False)
    else:
        embed.add_field(name="Host RAM", value="Unavailable", inline=False)
    embed.add_field(name="Host CPU Load", value=cpu_text, inline=False)
    embed.set_footer(text="RAM/load may reflect shared host resources, not Render's exact service quota.")
    await ctx.send(embed=embed)

@bot.hybrid_command(name="botname", description="Change Kyokah's server nickname (owner only)")
@app_commands.describe(name="New nickname for Kyokah in this server")
async def botname_command(ctx, name: str):
    if ctx.author.id != OWNER_ID:
        return await ctx.send("Only the configured bot owner can use this command.")
    if ctx.guild is None:
        return await ctx.send("This command can only be used in a server.")
    name = name.strip()
    if not name or len(name) > 32:
        return await ctx.send("Nickname must be between 1 and 32 characters.")
    try:
        await ctx.guild.me.edit(nick=name, reason=f"Bot nickname changed by owner {ctx.author.id}")
    except discord.Forbidden:
        return await ctx.send("I need Manage Nicknames permission and my role must be high enough to change my nickname.")
    except discord.HTTPException as error:
        return await ctx.send(f"Discord rejected the nickname change: {error}")
    await ctx.send(f"My server nickname is now **{discord.utils.escape_markdown(name)}**.")


@bot.hybrid_command(name="help", description="Browse all Hitter commands")
async def help_command(ctx):
    commands_list = sorted(
        [command for command in bot.commands if not command.hidden and command.name != "help"],
        key=lambda command: command.name.lower(),
    )
    if not commands_list:
        return await ctx.send("No commands are currently registered.")

    per_page = 12
    pages = max(1, (len(commands_list) + per_page - 1) // per_page)

    def build_embed(page_number):
        start = page_number * per_page
        chunk = commands_list[start:start + per_page]
        embed = discord.Embed(
            title="Hitter | Command Directory",
            description=f"Prefix: `+`  •  Slash commands supported\nPage {page_number + 1}/{pages}",
            color=EMBED_COLOR,
        )
        for command in chunk:
            description = command.description or "No description provided."
            embed.add_field(name=f"+{command.name}", value=description[:180], inline=False)
        embed.set_footer(text=f"{len(commands_list)} commands • Use +help or /help")
        return embed

    if pages == 1:
        return await ctx.send(embed=build_embed(0))

    class HelpPager(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=120)
            self.page = 0

        async def update(self, interaction):
            await interaction.response.edit_message(embed=build_embed(self.page), view=self)

        @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
        async def previous(self, interaction, button):
            self.page = (self.page - 1) % pages
            await self.update(interaction)

        @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
        async def next(self, interaction, button):
            self.page = (self.page + 1) % pages
            await self.update(interaction)

    await ctx.send(embed=build_embed(0), view=HelpPager())


@bot.hybrid_command(name="commands", description="Show the command directory")
async def commands_command(ctx):
    await help_command(ctx)


bot.run(TOKEN)

