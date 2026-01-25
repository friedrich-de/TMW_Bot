from lib.bot import TMWBot
from .username_fetcher import get_username_db

import discord
import os
import random
import re
import yaml
import logging

from typing import Optional
from datetime import timedelta, datetime, timezone
from discord.ext import commands
from discord import app_commands

_log = logging.getLogger(__name__)

WRITING_CLUB_SETTINGS = os.getenv("WRITING_CLUB_SETTINGS") or "config/writing_club_settings.yml"
with open(WRITING_CLUB_SETTINGS, "r", encoding="utf-8") as f:
    writing_club_settings = yaml.safe_load(f)

CREATE_WRITING_CLUB_LOGS_TABLE = """
    CREATE TABLE IF NOT EXISTS writing_club_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT,
    comment TEXT,
    characters_written INTEGER NOT NULL,
    points_received REAL NOT NULL,
    log_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
"""

CREATE_WRITING_CLUB_LOG_QUERY = """
    INSERT INTO writing_club_logs (user_id, name, comment, characters_written, points_received, log_date)
    VALUES (?, ?, ?, ?, ?, ?);
"""

GET_CONSECUTIVE_DAYS_QUERY = """
    SELECT DISTINCT(DATE(log_date)) AS log_date
    FROM writing_club_logs
    WHERE user_id = ?
    GROUP BY DATE(log_date)
    ORDER BY log_date DESC;
"""

GET_TOTAL_POINTS_QUERY = """
    SELECT SUM(points_received) AS total_points
    FROM writing_club_logs
    WHERE user_id = ?;
"""

GET_USER_LOGS_QUERY = """
    SELECT log_id, name, characters_written, log_date
    FROM writing_club_logs
    WHERE user_id = ?
    ORDER BY log_date DESC;
"""

GET_TO_BE_DELETED_LOG_QUERY = """
    SELECT log_id, name, characters_written, log_date, user_id
    FROM writing_club_logs
    WHERE log_id = ?;
"""

DELETE_LOG_QUERY = """
    DELETE FROM writing_club_logs
    WHERE log_id = ? AND user_id = ?;
"""

GET_USER_LOGS_FOR_DISPLAY_QUERY = """
    SELECT log_id, name, comment, characters_written, log_date
    FROM writing_club_logs
    WHERE user_id = ?
    ORDER BY log_date DESC;
"""


def extract_urls(text: str) -> list[str]:
    """Extract URLs from text."""
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    return re.findall(url_pattern, text)


def format_text_with_links(text: str, max_length: int = 1024) -> str:
    """Format text with clickable links. Discord auto-links URLs in embeds."""
    if not text:
        return text
    
    # If text is already a URL, make it clickable
    if text.startswith("http://") or text.startswith("https://"):
        return f"[View Link]({text})"
    
    # Check if text contains URLs
    urls = extract_urls(text)
    if urls:
        # Discord will auto-link URLs in embed fields, so we can just return the text
        # But we can also format them nicely
        formatted = text
        for url in urls:
            # Replace URL with markdown link if it's a standalone URL
            if text.strip() == url:
                formatted = f"[{url}]({url})"
                break
        return formatted[:max_length]
    
    return text[:max_length]


async def writing_club_log_undo_autocomplete(interaction: discord.Interaction, current_input: str):
    current_input = current_input.strip()

    tmw_bot = interaction.client
    tmw_bot: TMWBot

    # Check if user is Writing Club Leader
    leader_role_id = None
    if interaction.guild:
        guild_settings = writing_club_settings.get(interaction.guild.id, {})
        leader_role_id = guild_settings.get('leader_role_id')
    is_leader = False
    if leader_role_id and interaction.guild:
        member = interaction.guild.get_member(interaction.user.id)
        if member and any(role.id == leader_role_id for role in member.roles):
            is_leader = True

    if is_leader:
        # Leaders can see all logs - we'd need a different query for this
        # For now, let's just show their own logs
        user_logs = await tmw_bot.GET(GET_USER_LOGS_QUERY, (interaction.user.id,))
    else:
        user_logs = await tmw_bot.GET(GET_USER_LOGS_QUERY, (interaction.user.id,))
    
    choices = []

    for log_id, name, characters_written, log_date in user_logs:
        log_date_str = datetime.strptime(log_date, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')
        name_str = name or 'Untitled'
        # Include log ID in the autocomplete display
        log_name = f"[ID: {log_id}] {name_str}: {characters_written:,} chars on {log_date_str}"[:100]
        # Allow searching by ID, name, or date
        search_text = f"{log_id} {log_name}".lower()
        if current_input.lower() in search_text:
            choices.append(discord.app_commands.Choice(name=log_name, value=str(log_id)))

    return choices[:10]


async def is_valid_channel(interaction: discord.Interaction) -> bool:
    """Check if the command can be used in this channel."""
    # DMs are not allowed - all logs must be in configured channels for moderation
    if interaction.guild is None:
        return False
    
    # Admins can use anywhere
    if interaction.user.guild_permissions.administrator:
        return True
    
    # Get guild-specific settings
    # YAML loads numeric keys as ints, but we should check both int and str versions
    guild_id = interaction.guild.id
    guild_settings = writing_club_settings.get(guild_id) or writing_club_settings.get(str(guild_id), {})
    
    # Check allowed channels (text channels and forum channels)
    # Forum posts create threads, so we check both channel.id and channel.parent_id
    allowed_channels = guild_settings.get('allowed_log_channels', [])
    if not allowed_channels:
        # If no allowed channels configured, deny access (except for admins)
        return False
    
    # Convert channel IDs to ints for comparison (YAML might have them as strings)
    allowed_channel_ids = [int(ch) if isinstance(ch, (str, int)) else ch for ch in allowed_channels]
    current_channel_id = interaction.channel.id
    
    if current_channel_id in allowed_channel_ids:
        return True
    
    # Check if this is a thread (forum post) and if the parent forum channel is allowed
    if hasattr(interaction.channel, 'parent_id') and interaction.channel.parent_id:
        if interaction.channel.parent_id in allowed_channel_ids:
            return True
    
    return False


class WritingClub(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot
        self.points_per_character = writing_club_settings.get('points_multiplier', 1.0)
        self.max_characters_per_log = writing_club_settings.get('max_characters_per_log', 50000)

    async def cog_load(self):
        await self.bot.RUN(CREATE_WRITING_CLUB_LOGS_TABLE)

    @discord.app_commands.command(name="writing_club_log", description="Log your writing activity!")
    @app_commands.describe(
        amount="Number of characters written.",
        name="Name/title of the writing piece (optional).",
        comment="Comment about the writing session (optional).",
        backfill_date="The date for the log, in YYYY-MM-DD or YYYY-MM-DD HH:MM format. You can log no more than 7 days in the past."
    )
    async def log(
        self,
        interaction: discord.Interaction,
        amount: str,
        name: Optional[str] = None,
        comment: Optional[str] = None,
        backfill_date: Optional[str] = None
    ):
        if not await is_valid_channel(interaction):
            return await interaction.response.send_message(
                "You can only use this command in the writing club channels.",
                ephemeral=True
            )

        if not amount.isdigit():
            return await interaction.response.send_message("Amount must be a valid number.", ephemeral=True)
        amount = int(amount)
        if amount < 0:
            return await interaction.response.send_message("Amount must be a positive number.", ephemeral=True)
        if amount > self.max_characters_per_log:
            return await interaction.response.send_message(
                f"Amount must be less than {self.max_characters_per_log:,} characters per log.",
                ephemeral=True
            )

        if name and len(name) > 150:
            return await interaction.response.send_message("Name must be less than 150 characters.", ephemeral=True)
        elif name:
            name = name.strip()

        if comment and len(comment) > 200:
            return await interaction.response.send_message("Comment must be less than 200 characters.", ephemeral=True)
        elif comment:
            comment = comment.strip()

        if backfill_date is None:
            log_date = discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        else:
            try:
                try:
                    log_date = datetime.strptime(backfill_date, '%Y-%m-%d %H:%M')
                except ValueError:
                    log_date = datetime.strptime(backfill_date, '%Y-%m-%d')
                today = discord.utils.utcnow().date()
                if log_date.date() > today:
                    return await interaction.response.send_message("You cannot backfill a date in the future.", ephemeral=True)
                if (today - log_date.date()).days > 7:
                    return await interaction.response.send_message("You cannot log a date more than 7 days in the past.", ephemeral=True)
                log_date = log_date.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                return await interaction.response.send_message("Invalid date format. Please use YYYY-MM-DD or YYYY-MM-DD HH:MM.", ephemeral=True)

        await interaction.response.defer()

        points_received = round(amount * self.points_per_character, 2)
        total_points_before = await self.get_total_points(interaction.user.id)

        log_id = await self.bot.RUN_AND_GET_ID(
            CREATE_WRITING_CLUB_LOG_QUERY,
            (interaction.user.id, name, comment, amount, points_received, log_date)
        )

        total_points_after = await self.get_total_points(interaction.user.id)
        consecutive_days = await self.get_consecutive_days_logged(interaction.user.id)

        if interaction.guild and interaction.guild.emojis:
            random_guild_emoji = random.choice(interaction.guild.emojis)
        else:
            random_guild_emoji = "✍️"

        embed_title = f"Logged {amount:,} character{'s' if amount != 1 else ''} {random_guild_emoji}"

        log_embed = discord.Embed(title=embed_title, color=discord.Color.random())
        
        if name:
            name_display = format_text_with_links(name)
            log_embed.description = name_display
        
        comment_display = format_text_with_links(comment or "No comment")
        log_embed.add_field(name="Comment", value=comment_display, inline=False)
        log_embed.add_field(name="Points Received", value=f"`+{points_received}` ({amount} chars × {self.points_per_character})")
        log_embed.add_field(name="Total Points (All-Time)", value=f"`{total_points_before}` → `{total_points_after}`")
        log_embed.add_field(name="Streak", value=f"{consecutive_days} day{'s' if consecutive_days > 1 else ''}")
        log_embed.add_field(name="Log ID", value=f"`{log_id}`", inline=True)

        log_embed.set_footer(
            text=f"Logged by {interaction.user.display_name} for {log_date.split(' ')[0]}",
            icon_url=interaction.user.display_avatar.url
        )

        logged_message = await interaction.followup.send(embed=log_embed)

        # Reply with URLs if they're in name or comment
        if name and (name.startswith("http://") or name.startswith("https://")):
            await logged_message.reply(f"> {name}")
        elif comment and (comment.startswith("http://") or comment.startswith("https://")):
            await logged_message.reply(f"> {comment}")

    async def get_consecutive_days_logged(self, user_id: int) -> int:
        """Calculate consecutive days logged (UTC calendar days)."""
        result = await self.bot.GET(GET_CONSECUTIVE_DAYS_QUERY, (user_id,))
        if not result:
            return 0

        consecutive_days = 0
        today = discord.utils.utcnow().date()

        for row in result:
            log_date = datetime.strptime(row[0], '%Y-%m-%d').date()
            if log_date == today - timedelta(days=consecutive_days):
                consecutive_days += 1
            else:
                break

        return consecutive_days

    async def get_total_points(self, user_id: int) -> float:
        """Get total all-time points for a user."""
        result = await self.bot.GET(GET_TOTAL_POINTS_QUERY, (user_id,))
        if result and result[0] and result[0][0]:
            return round(result[0][0], 2)
        return 0.0

    @discord.app_commands.command(name="writing_club_log_undo", description="Undo a previous writing log!")
    @app_commands.describe(log_entry="Select the log entry you want to undo.")
    @app_commands.autocomplete(log_entry=writing_club_log_undo_autocomplete)
    async def log_undo(self, interaction: discord.Interaction, log_entry: str):
        if not await is_valid_channel(interaction):
            return await interaction.response.send_message(
                "You can only use this command in the writing club channels.",
                ephemeral=True
            )

        if not log_entry.isdigit():
            return await interaction.response.send_message("Invalid log entry selected.", ephemeral=True)

        log_id = int(log_entry)
        
        # Get the log entry
        deleted_log_info = await self.bot.GET(GET_TO_BE_DELETED_LOG_QUERY, (log_id,))
        if not deleted_log_info:
            return await interaction.response.send_message("The selected log entry does not exist.", ephemeral=True)

        log_id_db, name, characters_written, log_date, log_user_id = deleted_log_info[0]
        
        # Check permissions
        leader_role_id = None
        if interaction.guild:
            guild_settings = writing_club_settings.get(interaction.guild.id, {})
            leader_role_id = guild_settings.get('leader_role_id')
        is_leader = False
        if leader_role_id and interaction.guild:
            member = interaction.guild.get_member(interaction.user.id)
            if member and any(role.id == leader_role_id for role in member.roles):
                is_leader = True

        # Users can only undo their own logs, unless they're a Writing Club Leader
        if log_user_id != interaction.user.id and not is_leader:
            return await interaction.response.send_message(
                "You can only undo your own logs. Writing Club Leaders can undo any user's log.",
                ephemeral=True
            )

        log_date_str = datetime.strptime(log_date, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')
        await self.bot.RUN(DELETE_LOG_QUERY, (log_id, log_user_id))
        
        name_str = name or 'Untitled'
        await interaction.response.send_message(
            f"> {interaction.user.mention} Your log for `{characters_written:,} characters` "
            f"of `{name_str}` on `{log_date_str}` has been deleted."
        )

    @discord.app_commands.command(name="writing_club_logs", description="View writing logs for a user!")
    @app_commands.describe(user="The user to view logs for (optional)")
    async def logs(self, interaction: discord.Interaction, user: Optional[discord.User] = None):
        if not await is_valid_channel(interaction):
            return await interaction.response.send_message(
                "You can only use this command in the writing club channels.",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)
        user_id = user.id if user else interaction.user.id
        user_logs = await self.bot.GET(GET_USER_LOGS_FOR_DISPLAY_QUERY, (user_id,))

        if not user_logs:
            user_name = user.display_name if user else interaction.user.display_name
            return await interaction.followup.send(f"{user_name} has no writing logs yet.", ephemeral=True)

        user_name = await get_username_db(self.bot, user_id)
        embed = discord.Embed(
            title=f"{user_name}'s Writing Logs",
            color=discord.Color.blue()
        )

        # Discord embeds have a limit of 25 fields and 6000 characters total
        # We'll show up to 25 entries, or split into multiple embeds if needed
        description_parts = []
        total_chars = 0
        
        for i, (log_id, name, comment, characters_written, log_date) in enumerate(user_logs[:25]):
            log_date_str = datetime.strptime(log_date, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')
            name_str = name or 'Untitled'
            
            # Format name with link if it's a URL
            name_display = format_text_with_links(name_str, max_length=100)
            
            # Format comment with links
            comment_display = format_text_with_links(comment or 'No comment', max_length=200)
            
            entry = f"**ID: {log_id}** | {log_date_str} | {characters_written:,} chars\n"
            entry += f"📝 {name_display}\n"
            if comment and comment != 'No comment':
                entry += f"💬 {comment_display}\n"
            entry += "\n"
            
            # Check if adding this entry would exceed embed limits
            if len(embed.description or "") + len(entry) > 4096:
                break
            
            description_parts.append(entry)
            total_chars += characters_written

        if description_parts:
            embed.description = "".join(description_parts)
            footer_text = ""
            if len(user_logs) > 25:
                footer_text = f"Showing first 25 of {len(user_logs)} logs. Total: {total_chars:,} characters"
            else:
                footer_text = f"Total: {total_chars:,} characters across {len(user_logs)} log(s)"
            footer_text += " | Use /writing_club_log_undo to remove a log"
            embed.set_footer(text=footer_text)
        else:
            embed.description = "No logs to display."

        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.app_commands.command(name="writing_club_claim_badge", description="Claim a writing club badge based on your points!")
    @app_commands.describe(role="The badge role you want to claim.")
    @app_commands.guild_only()
    async def claim_badge(self, interaction: discord.Interaction, role: discord.Role):
        # Check if role is a valid badge
        badges = writing_club_settings.get('badges', {})
        # Normalize badge keys to ints (YAML might load them as strings)
        badges_normalized = {int(k) if isinstance(k, (str, int)) else k: v for k, v in badges.items()}
        
        role_id = role.id
        if role_id not in badges_normalized:
            return await interaction.response.send_message(
                f"{role.mention} is not a valid writing club badge role.",
                ephemeral=True
            )

        # Get required points for this badge
        required_points = badges_normalized[role_id]

        # Get user's total points
        total_points = await self.get_total_points(interaction.user.id)

        # Check if user has enough points
        if total_points < required_points:
            return await interaction.response.send_message(
                f"You need {required_points:,} points to claim this badge. You currently have {total_points:,.2f} points.",
                ephemeral=True
            )

        # Get member object
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            return await interaction.response.send_message(
                "Could not find your member information in this server.",
                ephemeral=True
            )

        # Check if user already has the role
        if role in member.roles:
            return await interaction.response.send_message(
                f"You already have the {role.mention} badge!",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        # Remove all other writing club badges (users can claim any badge they qualify for)
        badges_to_remove = []
        for badge_role_id in badges_normalized.keys():
            if badge_role_id != role.id:
                badge_role = interaction.guild.get_role(badge_role_id)
                if badge_role and badge_role in member.roles:
                    badges_to_remove.append(badge_role)

        # Remove other badges if any
        if badges_to_remove:
            try:
                await member.remove_roles(*badges_to_remove, reason="Claiming writing club badge")
            except discord.Forbidden:
                await interaction.followup.send(
                    "I don't have permission to remove your existing badges. Please contact an administrator.",
                    ephemeral=True
                )
                return
            except discord.HTTPException as e:
                _log.error(f"Error removing badges: {e}")
                await interaction.followup.send(
                    "An error occurred while removing your existing badges. Please contact an administrator.",
                    ephemeral=True
                )
                return

        # Assign the new badge
        try:
            await member.add_roles(role, reason=f"Claimed writing club badge ({total_points:,.2f} points)")
            
            # Get badge name for display
            badge_name = role.name
            
            # Create success message
            success_message = f"Congratulations! You've claimed the **{badge_name}** badge! {role.mention}\n"
            success_message += f"You have {total_points:,.2f} points (required: {required_points:,} points)."
            
            if badges_to_remove:
                removed_names = [r.name for r in badges_to_remove]
                success_message += f"\n\nYour previous badge(s) ({', '.join(removed_names)}) have been replaced with this badge."

            await interaction.followup.send(success_message, ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to assign roles. Please contact an administrator.",
                ephemeral=True
            )
        except discord.HTTPException as e:
            _log.error(f"Error assigning badge: {e}")
            await interaction.followup.send(
                "An error occurred while assigning the badge. Please contact an administrator.",
                ephemeral=True
            )

    @discord.app_commands.command(name="writing_club_remove_badge", description="Remove your writing club badge!")
    @app_commands.guild_only()
    async def remove_badge(self, interaction: discord.Interaction):
        # Get member object
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            return await interaction.response.send_message(
                "Could not find your member information in this server.",
                ephemeral=True
            )

        # Get all valid badge roles
        badges = writing_club_settings.get('badges', {})
        # Normalize badge keys to ints (YAML might load them as strings)
        badges_normalized = {int(k) if isinstance(k, (str, int)) else k: v for k, v in badges.items()}

        # Find all writing club badges the user has
        badges_to_remove = []
        for badge_role_id in badges_normalized.keys():
            badge_role = interaction.guild.get_role(badge_role_id)
            if badge_role and badge_role in member.roles:
                badges_to_remove.append(badge_role)

        # Check if user has any badges
        if not badges_to_remove:
            return await interaction.response.send_message(
                "You don't have any writing club badges to remove.",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        # Remove all badges
        try:
            await member.remove_roles(*badges_to_remove, reason="Removed writing club badge")
            
            # Get badge names for display
            removed_names = [r.name for r in badges_to_remove]
            badge_mentions = [r.mention for r in badges_to_remove]
            
            success_message = f"Successfully removed your writing club badge(s): {', '.join(badge_mentions)}\n"
            success_message += f"**{', '.join(removed_names)}**"
            
            await interaction.followup.send(success_message, ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to remove your badges. Please contact an administrator.",
                ephemeral=True
            )
        except discord.HTTPException as e:
            _log.error(f"Error removing badges: {e}")
            await interaction.followup.send(
                "An error occurred while removing your badges. Please contact an administrator.",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(WritingClub(bot))
