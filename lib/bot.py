import os
import asyncio
import discord
import aiosqlite
import logging
from discord.ext import commands

_log = logging.getLogger(__name__)


class TMWBot(commands.Bot):
    def __init__(self, command_prefix, cog_folder="cogs", path_to_db="data/db.sqlite3"):
        super().__init__(command_prefix=command_prefix, intents=discord.Intents.all())
        self.cog_folder = cog_folder
        self.path_to_db = path_to_db
        self._db_lock = asyncio.Lock()

        db_directory = os.path.dirname(self.path_to_db)
        if not os.path.exists(db_directory):
            os.makedirs(db_directory)

    async def on_ready(self):
        _log.info(f"Bot is ready. Logged in as {self.user.name} ({self.user.id})")

    async def setup_hook(self):
        self.tree.on_error = self.on_application_command_error

    async def load_cogs(self, cogs_to_load):
        cogs = [cog for cog in os.listdir(self.cog_folder) if cog.endswith(".py") and (cogs_to_load == "*" or cog[:-3] in cogs_to_load)]

        for cog in cogs:
            cog = f"{self.cog_folder}.{cog[:-3]}"
            await self.load_extension(cog)
            _log.info(f"Loaded {cog}")

    async def RUN(self, query: str, params: tuple = ()):
        async with self._db_lock:
            async with aiosqlite.connect(self.path_to_db) as db:
                await db.execute(query, params)
                await db.commit()

    async def RUN_MANY(self, query: str, rows: list[tuple]):
        async with self._db_lock:
            async with aiosqlite.connect(self.path_to_db) as db:
                await db.executemany(query, rows)
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
            return

        if isinstance(error, commands.MissingPermissions):
            _log.warning(f"User {ctx.author} tried to use a command without permission: {ctx.command}")
            return

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

            _log.error("Exception in command %r", command.name, exc_info=error)
        else:
            _log.error("Exception in command tree", exc_info=error)

        error_embed = discord.Embed(title="Error", description=f"```{str(error)[:4000]}```", color=discord.Color.red())

        if not interaction.response.is_done():
            await interaction.response.send_message("An error occurred while processing your command:", embed=error_embed)
        else:
            await interaction.followup.send("An error occurred while processing your command:", embed=error_embed)
