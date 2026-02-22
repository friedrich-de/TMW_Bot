"""Cog that lets users bookmark messages via reactions.

Reacting with 🔖 on a guild message sends the user a DM copy of that
message.  The bot tracks per-message bookmark counts and exposes a
leaderboard of the most bookmarked messages in each guild.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from lib.bot import TMWBot
from lib.helpers import delete_message_safe, fetch_message_safe, get_user_safe
from lib.models import BookmarkedMessage, UserBookmark
from lib.username import get_username_db

_log = logging.getLogger(__name__)

BOOKMARK_EMOJI = "\N{BOOKMARK}"
REMOVE_EMOJI = "\N{CROSS MARK}"


async def check_bookmark_exists(
    bot: TMWBot, user_id: int, message_id: int
) -> bool:
    """Return ``True`` if *user_id* already bookmarked *message_id*."""
    async with bot.session_factory() as session:
        result = await session.execute(
            select(UserBookmark).where(
                UserBookmark.user_id == user_id,
                UserBookmark.message_id == message_id,
            )
        )
        return result.scalar_one_or_none() is not None


async def insert_user_bookmark(
    bot: TMWBot,
    *,
    guild_id: int,
    channel_id: int,
    user_id: int,
    message_id: int,
    message_link: str,
    dm_message_id: int,
) -> None:
    """Record a user's bookmark in the database."""
    async with bot.session_factory() as session:
        session.add(
            UserBookmark(
                guild_id=guild_id,
                channel_id=channel_id,
                user_id=user_id,
                message_id=message_id,
                message_link=message_link,
                dm_message_id=dm_message_id,
            )
        )
        await session.commit()


async def delete_user_bookmark(
    bot: TMWBot, user_id: int, dm_message_id: int
) -> None:
    """Remove a bookmark identified by the DM message that was sent."""
    async with bot.session_factory() as session:
        await session.execute(
            delete(UserBookmark).where(
                UserBookmark.user_id == user_id,
                UserBookmark.dm_message_id == dm_message_id,
            )
        )
        await session.commit()


async def upsert_bookmark_count(
    bot: TMWBot,
    *,
    guild_id: int,
    channel_id: int,
    message_id: int,
    message_author_id: int,
    message_link: str,
    bookmark_count: int,
) -> None:
    """Insert or update the aggregate bookmark count for a message."""
    async with bot.session_factory() as session:
        stmt = sqlite_insert(BookmarkedMessage).values(
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
            message_author_id=message_author_id,
            message_link=message_link,
            bookmark_count=bookmark_count,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[BookmarkedMessage.guild_id,
                            BookmarkedMessage.message_id],
            set_={"bookmark_count": stmt.excluded.bookmark_count},
        )
        await session.execute(stmt)
        await session.commit()


async def get_top_bookmarks(
    bot: TMWBot, guild_id: int, *, limit: int = 10
) -> list[BookmarkedMessage]:
    """Return the most bookmarked messages in *guild_id*."""
    async with bot.session_factory() as session:
        result = await session.execute(
            select(BookmarkedMessage)
            .where(BookmarkedMessage.guild_id == guild_id)
            .order_by(BookmarkedMessage.bookmark_count.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


async def delete_bookmarked_message(
    bot: TMWBot, guild_id: int, message_id: int
) -> None:
    """Remove a message from the bookmarked-messages leaderboard table."""
    async with bot.session_factory() as session:
        await session.execute(
            delete(BookmarkedMessage).where(
                BookmarkedMessage.guild_id == guild_id,
                BookmarkedMessage.message_id == message_id,
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Discord helpers
# ---------------------------------------------------------------------------


async def send_bookmark_dm(
    user: discord.User, message: discord.Message
) -> discord.Message:
    """Format *message* as an embed and send it to *user*'s DMs.

    Returns the DM message that was sent.
    """
    guild_name = message.guild.name if message.guild else "Unknown Server"
    embed = discord.Embed(
        title=f"**Bookmark from {guild_name}**",
        description=message.content,
        timestamp=message.created_at,
        color=discord.Color.blue(),
    )
    embed.set_author(
        name=message.author.display_name,
        icon_url=message.author.display_avatar.url,
    )

    files_to_send: list[discord.File] = []
    if message.attachments:
        # Set the first image as the embed image.
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                embed.set_image(url=attachment.url)
                break

        # List every attachment and collect videos to send as files.
        for idx, attachment in enumerate(message.attachments, 1):
            if attachment.content_type and attachment.content_type.startswith("video/"):
                files_to_send.append(await attachment.to_file())
            embed.add_field(
                name=f"Attachment {idx}",
                value=f"[{attachment.filename}]({attachment.url})",
                inline=False,
            )

    embed.add_field(
        name="Source",
        value=f"[[Jump to message]]({message.jump_url})",
        inline=False,
    )

    user_dm_channel = user.dm_channel or await user.create_dm()

    dm_message = await user_dm_channel.send(embed=embed, files=files_to_send)

    try:
        await dm_message.pin()
    except discord.HTTPException:
        _log.debug(
            "Could not pin bookmark DM for user_id=%s (likely 50-pin limit)",
            user.id,
        )
        await user_dm_channel.send(
            "Reached 50 pinned messages limit. Unpin messages to pin more."
        )

    await dm_message.add_reaction(REMOVE_EMOJI)
    return dm_message


class Bookmarks(commands.Cog):
    """Bookmark messages via 🔖 reactions and browse the leaderboard."""

    def __init__(self, bot: TMWBot) -> None:
        self.bot = bot

    async def _update_bookmark_count(
        self, channel_id: int, message_id: int
    ) -> None:
        """Re-count 🔖 reactions on a message and persist the total."""
        message = await fetch_message_safe(self.bot, channel_id, message_id)
        if message is None or message.guild is None:
            _log.warning(
                "Cannot update bookmark count — message_id=%s in channel_id=%s not found",
                message_id,
                channel_id,
            )
            return

        bookmark_count = 0
        for reaction in message.reactions:
            if str(reaction.emoji) == BOOKMARK_EMOJI:
                bookmark_count = reaction.count
                break

        await upsert_bookmark_count(
            self.bot,
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            message_id=message.id,
            message_author_id=message.author.id,
            message_link=message.jump_url,
            bookmark_count=bookmark_count,
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        if self.bot.user and payload.user_id == self.bot.user.id:
            return

        # --- DM reaction: handle bookmark removal --------------------------
        if payload.guild_id is None:
            if str(payload.emoji) != REMOVE_EMOJI:
                return

            user = await get_user_safe(self.bot, payload.user_id)
            if user is None:
                return

            user_dm_channel = user.dm_channel or await user.create_dm()

            await delete_user_bookmark(self.bot, payload.user_id, payload.message_id)

            dm_msg = await fetch_message_safe(
                self.bot, user_dm_channel.id, payload.message_id
            )
            if dm_msg is not None:
                await delete_message_safe(dm_msg)
            return

        # --- Guild reaction: bookmark a message ---------------------------
        if str(payload.emoji) != BOOKMARK_EMOJI:
            return

        if await check_bookmark_exists(self.bot, payload.user_id, payload.message_id):
            await self._update_bookmark_count(payload.channel_id, payload.message_id)
            return

        message = await fetch_message_safe(
            self.bot, payload.channel_id, payload.message_id
        )
        if message is None:
            return

        user = await get_user_safe(self.bot, payload.user_id)
        if user is None:
            return

        try:
            dm_message = await send_bookmark_dm(user, message)
        except discord.Forbidden:
            _log.debug(
                "Cannot DM user_id=%s — DMs are disabled", payload.user_id
            )
            return

        await insert_user_bookmark(
            self.bot,
            guild_id=payload.guild_id,
            channel_id=payload.channel_id,
            user_id=payload.user_id,
            message_id=payload.message_id,
            message_link=message.jump_url,
            dm_message_id=dm_message.id,
        )

        await self._update_bookmark_count(payload.channel_id, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        if payload.guild_id is not None and str(payload.emoji) == BOOKMARK_EMOJI:
            await self._update_bookmark_count(payload.channel_id, payload.message_id)

    @app_commands.command(
        name="bookmarkboard",
        description="Shows most bookmarked messages",
    )
    @app_commands.guild_only()
    async def bookmark_leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)

        assert interaction.guild is not None  # guild_only

        entries = await get_top_bookmarks(self.bot, interaction.guild.id)
        if not entries:
            await interaction.followup.send("No bookmarked messages found.")
            return

        embed = discord.Embed(
            title="Most Bookmarked Messages",
            color=discord.Color.blue(),
        )

        for index, entry in enumerate(entries, 1):
            author_name = await get_username_db(self.bot, entry.message_author_id)
            embed.add_field(
                name=f"{index}. By {author_name} ({entry.bookmark_count} bookmarks)",
                value=f"[Jump to message]({entry.message_link})",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="checkbookmarks",
        description="Check and remove deleted messages from bookmark leaderboard",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def check_bookmarked_messages(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)

        assert interaction.guild is not None  # guild_only

        entries = await get_top_bookmarks(self.bot, interaction.guild.id)
        if not entries:
            await interaction.followup.send("No bookmarked messages found.")
            return

        removed_count = 0
        for entry in entries:
            msg = await fetch_message_safe(self.bot, entry.channel_id, entry.message_id)
            if msg is None:
                await delete_bookmarked_message(
                    self.bot, interaction.guild.id, entry.message_id
                )
                removed_count += 1

        _log.info(
            "Bookmark cleanup in guild_id=%s: removed %s stale entries",
            interaction.guild.id,
            removed_count,
        )
        await interaction.followup.send(
            f"Cleanup complete. Removed {removed_count} deleted messages from bookmarks."
        )


async def setup(bot: TMWBot) -> None:
    await bot.add_cog(Bookmarks(bot))
