import asyncio
from lib.bot import TMWBot
import discord
from discord.ext import commands
from .username_fetcher import get_username_db

CREATE_USER_BOOKMARKS_TABLE = """
CREATE TABLE IF NOT EXISTS user_bookmarks (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    message_link TEXT NOT NULL,
    dm_message_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, message_id));"""

CREATE_BOOKMARKED_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS bookmarked_messages (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    message_author_id INTEGER NOT NULL,
    message_link TEXT NOT NULL,
    bookmark_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, message_id));"""

UPDATE_BOOKMARK_COUNT_QUERY = """
INSERT INTO bookmarked_messages (guild_id, channel_id, message_id, message_author_id, message_link, bookmark_count)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (guild_id, message_id) DO UPDATE SET
bookmark_count = ?;"""

INSERT_USER_BOOKMARK_QUERY = """
INSERT INTO user_bookmarks (guild_id, channel_id, user_id, message_id, message_link, dm_message_id)
VALUES (?, ?, ?, ?, ?, ?);"""

DELETE_USER_BOOKMARK_QUERY = """
DELETE FROM user_bookmarks WHERE user_id = ? AND dm_message_id = ?;"""

CHECK_BOOKMARK_EXISTS_QUERY = """
SELECT 1 FROM user_bookmarks 
WHERE user_id = ? AND message_id = ?;"""

GET_TOP_BOOKMARKS_QUERY = """
SELECT channel_id, message_id, message_author_id, message_link, bookmark_count
FROM bookmarked_messages
WHERE guild_id = ?
ORDER BY bookmark_count DESC
LIMIT 10;"""

DELETE_BOOKMARKED_MESSAGE_QUERY = """
DELETE FROM bookmarked_messages WHERE guild_id = ? AND message_id = ?;"""


FETCH_LOCK = asyncio.Lock()


class Bookmarks(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot
        self.bookmark_emoji = "🔖"
        self.remove_emoji = "❌"

    async def cog_load(self):
        await self.bot.RUN(CREATE_USER_BOOKMARKS_TABLE)
        await self.bot.RUN(CREATE_BOOKMARKED_MESSAGES_TABLE)

    async def _get_message(self, channel_id: int, message_id: int) -> discord.Message:
        channel = self.bot.get_channel(channel_id)
        if not channel:
            channel = await self.bot.fetch_channel(channel_id)
        message = discord.utils.get(self.bot.cached_messages, id=message_id)
        if not message:
            message = await channel.fetch_message(message_id)
        return message

    async def send_bookmark_dm(self, user: discord.User, message: discord.Message) -> discord.Message:
        embed = discord.Embed(title=f"**Bookmark from {message.guild.name}**", description=message.content,
                              timestamp=message.created_at, color=discord.Color.blue())
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)

        files_to_send = []
        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type:
                    if attachment.content_type.startswith('image/'):
                        embed.set_image(url=attachment.url)
                        break

            # Add all attachments as fields and collect videos to send as files
            for idx, attachment in enumerate(message.attachments, 1):
                if attachment.content_type and attachment.content_type.startswith('video/'):
                    files_to_send.append(await attachment.to_file())
                embed.add_field(
                    name=f"Attachment {idx}",
                    value=f"[{attachment.filename}]({attachment.url})",
                    inline=False
                )

        embed.add_field(name="Source", value=f"[[Jump to message]]({message.jump_url})", inline=False)

        if not user.dm_channel:
            await user.create_dm()
        dm_message = await user.dm_channel.send(embed=embed, files=files_to_send)
        try:
            await dm_message.pin()
        except discord.HTTPException:
            await user.dm_channel.send("Reached 50 pinned messages limit. Unpin messages to pin more.")
        await dm_message.add_reaction(self.remove_emoji)
        return dm_message

    async def update_bookmark_count(self, payload: discord.RawReactionActionEvent):
        async with FETCH_LOCK:
            await asyncio.sleep(1)
            message = await self._get_message(payload.channel_id, payload.message_id)

        bookmark_count = 0
        for reaction in message.reactions:
            if str(reaction.emoji) == self.bookmark_emoji:
                bookmark_count = reaction.count
                break

        await self.bot.RUN(UPDATE_BOOKMARK_COUNT_QUERY,
                           (message.guild.id, message.channel.id, message.id, message.author.id,
                            message.jump_url, bookmark_count, bookmark_count))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return

        if payload.guild_id is None:
            if str(payload.emoji) == self.remove_emoji:
                user = self.bot.get_user(payload.user_id)
                if not user:
                    user = await self.bot.fetch_user(payload.user_id)
                if not user.dm_channel:
                    await user.create_dm()

                try:
                    await self.bot.RUN(DELETE_USER_BOOKMARK_QUERY, (payload.user_id, payload.message_id))
                    message = await self._get_message(user.dm_channel.id, payload.message_id)
                    await message.delete()
                except discord.NotFound:
                    pass
                return

        if str(payload.emoji) != self.bookmark_emoji or payload.guild_id is None:
            return

        exists = await self.bot.GET_ONE(CHECK_BOOKMARK_EXISTS_QUERY,
                                        (payload.user_id, payload.message_id))

        if exists:
            await self.update_bookmark_count(payload)
            return

        async with FETCH_LOCK:
            message = await self._get_message(payload.channel_id, payload.message_id)
            user = self.bot.get_user(payload.user_id)
            if not user:
                user = await self.bot.fetch_user(payload.user_id)

        try:
            dm_message = await self.send_bookmark_dm(user, message)
        except discord.Forbidden:
            return

        await self.bot.RUN(INSERT_USER_BOOKMARK_QUERY,
                           (payload.guild_id, payload.channel_id, payload.user_id, payload.message_id,
                            message.jump_url, dm_message.id))

        await self.update_bookmark_count(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is not None and str(payload.emoji) == self.bookmark_emoji:
            await self.update_bookmark_count(payload)

    @discord.app_commands.command(name="bookmarkboard", description="Shows most bookmarked messages")
    @discord.app_commands.guild_only()
    async def bookmark_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        leaderboard_data = await self.bot.GET(GET_TOP_BOOKMARKS_QUERY, (interaction.guild.id,))
        if not leaderboard_data:
            return await interaction.edit_original_response(content="No bookmarked messages found.")

        leaderboard_embed = discord.Embed(
            title="Most Bookmarked Messages",
            color=discord.Color.blue()
        )

        for index, (channel_id, message_id, author_id, message_link, bookmark_count) in enumerate(leaderboard_data, 1):
            author_name = await get_username_db(self.bot, author_id)
            leaderboard_embed.add_field(
                name=f"{index}. By {author_name} ({bookmark_count} bookmarks)",
                value=f"[Jump to message]({message_link})",
                inline=False
            )

        await interaction.edit_original_response(embed=leaderboard_embed)

    @discord.app_commands.command(name="checkbookmarks", description="Check and remove deleted messages from bookmark leaderboard")
    @discord.app_commands.guild_only()
    @discord.app_commands.default_permissions(administrator=True)
    async def check_bookmarked_messages(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        leaderboard_data = await self.bot.GET(GET_TOP_BOOKMARKS_QUERY, (interaction.guild.id,))
        if not leaderboard_data:
            return await interaction.edit_original_response(content="No bookmarked messages found.")

        removed_count = 0
        for channel_id, message_id, author_id, message_link, _ in leaderboard_data:
            try:
                await self._get_message(channel_id, message_id)
            except (discord.NotFound, discord.Forbidden):
                await self.bot.RUN(DELETE_BOOKMARKED_MESSAGE_QUERY, (interaction.guild.id, message_id))
                removed_count += 1
            except Exception as e:
                continue

        await interaction.edit_original_response(
            content=f"Cleanup complete. Removed {removed_count} deleted messages from bookmarks.")


async def setup(bot):
    await bot.add_cog(Bookmarks(bot))
