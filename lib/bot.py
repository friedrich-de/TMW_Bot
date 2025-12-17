import logging
import os
from typing import Any

import discord
from discord.ext import commands
from sqlalchemy.ext.asyncio import async_sessionmaker

import lib.models
from lib.db import Base, get_db_engine

_log = logging.getLogger(__name__)


class TMWBot(commands.Bot):
    def __init__(
        self,
        command_prefix: str,
        path_to_db: str | None,
    ):
        super().__init__(command_prefix=command_prefix, intents=discord.Intents.all())
        self.path_to_db = path_to_db or "data/db.sqlite3"
        self.engine = get_db_engine(self.path_to_db)
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False)

    async def setup_hook(self):
        lib.models.register_models()
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def reply(
        self,
        interaction: discord.Interaction,
        content: str | None = None,
        embed: discord.Embed | None = None,
        ephemeral: bool = False,
    ) -> None:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                content=content if content is not None else discord.utils.MISSING,
                embed=embed if embed is not None else discord.utils.MISSING,
                ephemeral=ephemeral,
            )
        else:
            await interaction.followup.send(
                content=content if content is not None else discord.utils.MISSING,
                embed=embed if embed is not None else discord.utils.MISSING,
                ephemeral=ephemeral,
            )

    async def on_ready(self):
        if self.user:
            _log.info(
                f"Bot is ready. Logged in as {self.user.name} ({self.user.id})")
        else:
            _log.info("Bot is ready but user information is unavailable.")

    async def load_cogs(self) -> None:
        cogs_to_load = [cog for cog in os.listdir(
            "cogs") if cog.endswith(".py")]

        for cog in cogs_to_load:
            cog = f"cogs.{cog[:-3]}"
            await self.load_extension(cog)
            _log.info(f"Loaded {cog}")

    async def on_command_error(
        self, ctx: commands.Context[Any], error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CommandNotFound):
            return

        if isinstance(error, commands.MissingPermissions):
            return

    async def on_application_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ):
        if isinstance(error, discord.app_commands.MissingAnyRole):
            await self.reply(
                interaction,
                "You do not have the permission to use this command.",
                ephemeral=True,
            )
            return

        elif isinstance(error, discord.app_commands.CommandOnCooldown):
            await self.reply(
                interaction,
                f"This command is currently on cooldown. You can use this command again after {int(error.retry_after)} seconds.",
                ephemeral=True,
            )
            return

        if interaction.command:
            _log.error(
                "Exception in command %r", interaction.command.name, exc_info=error
            )
        else:
            _log.error("Exception in unknown command", exc_info=error)

        error_embed = discord.Embed(
            title="Error",
            description=f"```{str(error)[:4000]}```",
            color=discord.Color.red(),
        )

        await self.reply(
            interaction,
            "An error occurred while processing your command:",
            embed=error_embed,
        )

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            await self.engine.dispose()
