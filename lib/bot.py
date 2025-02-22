import os
import discord
import aiosqlite
import logging
import sys
import traceback
from discord.ext import commands

_log = logging.getLogger(__name__)


class TMWBot(commands.Bot):
    def __init__(self, command_prefix, cog_folder="cogs", path_to_db="data/db.sqlite3"):

        super().__init__(command_prefix=command_prefix, intents=discord.Intents.all())
        self.cog_folder = cog_folder
        self.path_to_db = path_to_db

        db_directory = os.path.dirname(self.path_to_db)
        if not os.path.exists(db_directory):
            os.makedirs(db_directory)

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        await self.create_debug_dm()

    async def setup_hook(self):
        self.tree.on_error = self.on_application_command_error

    async def load_cogs(self, cogs_to_load):

        cogs = [cog for cog in os.listdir(self.cog_folder) if cog.endswith(".py") and
                (cogs_to_load == "*" or cog[:-3] in cogs_to_load)]

        for cog in cogs:
            cog = f"{self.cog_folder}.{cog[:-3]}"
            await self.load_extension(cog)
            print(f"Loaded {cog}")

    async def create_debug_dm(self):
        await self.wait_until_ready()
        debug_user_id = int(os.getenv("DEBUG_USER"))
        debug_user = self.get_user(debug_user_id)
        if not debug_user:
            debug_user = await self.fetch_user(debug_user_id)

        self.debug_dm = debug_user.dm_channel
        if not debug_user.dm_channel:
            self.debug_dm = await debug_user.create_dm()

        await self.debug_dm.send("Bot is ready.")

    async def RUN(self, query: str, params: tuple = ()):
        async with aiosqlite.connect(self.path_to_db) as db:
            await db.execute(query, params)
            await db.commit()

    async def GET(self, query: str, params: tuple = ()):
        async with aiosqlite.connect(self.path_to_db) as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return rows

    async def GET_ONE(self, query: str, params: tuple = ()):
        async with aiosqlite.connect(self.path_to_db) as db:
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
                return row

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            _log.info(f"Command by user {ctx.author.name} not found: {ctx.message.content}")
            return

        raise error

    async def on_application_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.MissingAnyRole):
            await interaction.response.send_message("You do not have the permission to use this command.", ephemeral=True)
            return
        elif isinstance(error, discord.app_commands.CommandOnCooldown):
            await interaction.response.send_message(f"This command is currently on cooldown. You can use this command again after {int(error.retry_after)} seconds.", ephemeral=True)
            return

        command = interaction.command
        if command is not None:
            if command._has_any_error_handlers():
                return

            _log.error('Ignoring exception in command %r', command.name, exc_info=error)
        else:
            _log.error('Ignoring exception in command tree', exc_info=error)

        error_embed = discord.Embed(title="Error", description=f"```{str(error)[:4000]}```", color=discord.Color.red())

        if interaction.channel.type == discord.ChannelType.private:
            await self.debug_dm.send(f"Triggered by: `{interaction.command.name}` | Channel: private | User: {interaction.user.id} ({interaction.user.name})  \n"
                                     f"Data: ```json\n{interaction.data}```",
                                     embed=error_embed)
        else:
            await self.debug_dm.send(f"Triggered by: `{interaction.command.name}` | Channel: {interaction.channel.name} | Guild: {interaction.guild.name} | User: {interaction.user.id} ({interaction.user.name})\n"
                                     f"Data: ```json\n{interaction.data}```",
                                     embed=error_embed)

        if not interaction.response.is_done():
            await interaction.response.send_message("An error occurred while processing your command:", embed=error_embed)
        else:
            await interaction.followup.send("An error occurred while processing your command:", embed=error_embed)

    async def on_error(self, event_method, *args, **kwargs):
        _log.exception('Ignoring exception in %s', event_method)

        error_type, error, tb = sys.exc_info()

        traceback_string = '\n'.join(traceback.format_list(traceback.extract_tb(tb)))

        error_message = f"`{error_type}` occurred in `{event_method}`\n" + \
            f"```{error}```"
        embed_description = f"\n```python\n{traceback_string}```"

        error_embed = discord.Embed(title="Error", description=embed_description[:4000], color=discord.Color.red())
        await self.debug_dm.send(error_message, embed=error_embed)
