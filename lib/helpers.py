"""Shared Discord helper utilities used across multiple cogs."""

import logging

import discord

from lib.bot import TMWBot

_log = logging.getLogger(__name__)


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


async def get_user_safe(bot: TMWBot, user_id: int) -> discord.User | None:
    """Get a user from cache, or fetch them from the API if not cached.

    Returns ``None`` if the user cannot be found or fetched.
    """
    user = bot.get_user(user_id)
    if user is not None:
        return user

    try:
        return await bot.fetch_user(user_id)
    except (discord.NotFound, discord.HTTPException) as exc:
        _log.debug("Could not fetch user_id=%s: %s", user_id, exc)
        return None
