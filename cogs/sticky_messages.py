"""Cog that pins a 'sticky' message to the bottom of a channel.

Every time a new (non-bot) message is sent in a channel with an active
sticky, the old copy is deleted and a fresh one is posted so it always
appears at the bottom of the conversation.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from lib.bot import TMWBot
from lib.models import StickyMessage

_log = logging.getLogger(__name__)

STICKY_PREFIX = "\N{PUSHPIN} **Sticky Message:**\n\n"


async def get_sticky(bot: TMWBot, guild_id: int, channel_id: int) -> StickyMessage | None:
    """Return the sticky row for *channel_id* in *guild_id*, or ``None``."""
    async with bot.session_factory() as session:
        result = await session.execute(
            select(StickyMessage).where(
                StickyMessage.guild_id == guild_id,
                StickyMessage.channel_id == channel_id,
            )
        )
        return result.scalar_one_or_none()


async def upsert_sticky(
    bot: TMWBot,
    *,
    guild_id: int,
    channel_id: int,
    original_message_id: int,
    stickied_message_id: int,
) -> None:
    """Insert or update the sticky row for a channel."""
    async with bot.session_factory() as session:
        stmt = insert(StickyMessage).values(
            guild_id=guild_id,
            channel_id=channel_id,
            original_message_id=original_message_id,
            stickied_message_id=stickied_message_id,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[StickyMessage.guild_id, StickyMessage.channel_id],
            set_={
                "original_message_id": stmt.excluded.original_message_id,
                "stickied_message_id": stmt.excluded.stickied_message_id,
            },
        )
        await session.execute(stmt)
        await session.commit()


async def delete_sticky(bot: TMWBot, guild_id: int, channel_id: int) -> None:
    """Remove the sticky row for a channel."""
    async with bot.session_factory() as session:
        await session.execute(
            delete(StickyMessage).where(
                StickyMessage.guild_id == guild_id,
                StickyMessage.channel_id == channel_id,
            )
        )
        await session.commit()


async def fetch_message_safe(
    bot: TMWBot, channel_id: int, message_id: int
) -> discord.Message | None:
    """Fetch a message by ID, returning ``None`` on failure."""
    try:
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)

        if not isinstance(channel, discord.abc.Messageable):
            return None

        cached = discord.utils.get(bot.cached_messages, id=message_id)
        if cached is not None:
            return cached

        return await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
        _log.debug(
            "Could not fetch message_id=%s in channel_id=%s: %s",
            message_id,
            channel_id,
            exc,
        )
        return None


async def delete_message_safe(message: discord.Message) -> None:
    """Delete a message, ignoring common failures."""
    try:
        await message.delete()
    except discord.NotFound:
        pass
    except (discord.Forbidden, discord.HTTPException) as exc:
        _log.warning(
            "Failed to delete message_id=%s in channel_id=%s: %s",
            message.id,
            message.channel.id,
            exc,
        )


async def send_sticky_copy(
    channel: discord.abc.Messageable,
    original: discord.Message,
) -> discord.Message:
    """Re-post the sticky content derived from *original*."""
    files = [await a.to_file() for a in original.attachments]

    kwargs: dict[str, object] = {
        "content": f"{STICKY_PREFIX}{original.content}",
        "files": files,
    }
    if original.embeds:
        kwargs["embed"] = original.embeds[0]

    return await channel.send(**kwargs)  # type: ignore[arg-type]


class StickyMessages(commands.Cog):
    """Keep a designated message pinned to the bottom of a channel."""

    def __init__(self, bot: TMWBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="sticky_last_message",
        description="Make the last message in this channel sticky.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def sticky_last_message(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None  # guild_only
        assert isinstance(interaction.channel, discord.abc.Messageable)

        await interaction.response.defer(ephemeral=True)

        # Find the most recent non-interaction message.
        last_message: discord.Message | None = None
        async for message in interaction.channel.history(limit=10):
            if message.interaction_metadata is not None:
                continue
            last_message = message
            break

        if last_message is None:
            await interaction.followup.send(
                "Could not find a suitable message to sticky.",
                ephemeral=True,
            )
            return

        sticky_copy = await send_sticky_copy(interaction.channel, last_message)

        await upsert_sticky(
            self.bot,
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            original_message_id=last_message.id,
            stickied_message_id=sticky_copy.id,
        )

        _log.info(
            "Sticky set in guild_id=%s channel_id=%s original_message_id=%s by user_id=%s",
            interaction.guild.id,
            interaction.channel.id,
            last_message.id,
            interaction.user.id,
        )

        await interaction.followup.send("Message has been made sticky!", ephemeral=True)

    @app_commands.command(
        name="unsticky",
        description="Remove the sticky message from this channel.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def unsticky(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None  # guild_only
        assert isinstance(interaction.channel, discord.abc.Messageable)

        await interaction.response.defer(ephemeral=True)

        sticky = await get_sticky(self.bot, interaction.guild.id, interaction.channel.id)
        if sticky is None:
            await interaction.followup.send(
                "No sticky message found in this channel.",
                ephemeral=True,
            )
            return

        if sticky.stickied_message_id is not None:
            stickied_msg = await fetch_message_safe(
                self.bot, interaction.channel.id, sticky.stickied_message_id
            )
            if stickied_msg is not None:
                await delete_message_safe(stickied_msg)

        await delete_sticky(self.bot, interaction.guild.id, interaction.channel.id)

        _log.info(
            "Sticky removed in guild_id=%s channel_id=%s by user_id=%s",
            interaction.guild.id,
            interaction.channel.id,
            interaction.user.id,
        )

        await interaction.followup.send(
            "Sticky message has been removed!",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        sticky = await get_sticky(self.bot, message.guild.id, message.channel.id)
        if sticky is None:
            return

        # Delete the previous sticky copy.
        if sticky.stickied_message_id is not None:
            old_sticky = await fetch_message_safe(
                self.bot, message.channel.id, sticky.stickied_message_id
            )
            if old_sticky is not None:
                await delete_message_safe(old_sticky)

        # Fetch the original message and re-post.
        original = await fetch_message_safe(
            self.bot, message.channel.id, sticky.original_message_id
        )

        if original is None:
            _log.warning(
                "Original sticky message_id=%s not found in channel_id=%s; removing sticky.",
                sticky.original_message_id,
                message.channel.id,
            )
            await delete_sticky(self.bot, message.guild.id, message.channel.id)
            return

        assert isinstance(message.channel, discord.abc.Messageable)
        try:
            new_sticky = await send_sticky_copy(message.channel, original)
        except discord.HTTPException:
            _log.exception(
                "Failed to re-post sticky in channel_id=%s guild_id=%s",
                message.channel.id,
                message.guild.id,
            )
            return

        await upsert_sticky(
            self.bot,
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            original_message_id=sticky.original_message_id,
            stickied_message_id=new_sticky.id,
        )


async def setup(bot: TMWBot) -> None:
    await bot.add_cog(StickyMessages(bot))
