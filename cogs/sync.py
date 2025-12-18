import os

import discord
from discord.ext import commands

from lib.bot import TMWBot


def is_authorized():
    async def predicate(ctx: commands.Context[TMWBot]):
        authorized_user_ids = os.getenv("AUTHORIZED_USERS")
        if not authorized_user_ids:
            return False
        authorized_ids = [int(id) for id in authorized_user_ids.split(",")]
        return ctx.author.id in authorized_ids
    return commands.check(predicate)


class Sync(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot

    async def cog_load(self):
        pass

    @commands.command()
    @is_authorized()
    async def sync_guild(self, ctx: commands.Context[TMWBot]):
        """Sync commands to current guild."""
        if not ctx.guild:
            await ctx.send("This command can only be used in a guild.")
            return
        self.bot.tree.copy_global_to(guild=discord.Object(id=ctx.guild.id))
        self.bot.tree.clear_commands(guild=None)
        await self.bot.tree.sync(guild=discord.Object(id=ctx.guild.id))
        await ctx.send(f"Synced commands to guild with id {ctx.guild.id}.")

    @commands.command()
    @is_authorized()
    async def sync_global(self, ctx: commands.Context[TMWBot]):
        """Sync commands to global."""
        await self.bot.tree.sync()
        await ctx.send("Synced commands to global.")

    @commands.command()
    @is_authorized()
    async def clear_global_commands(self, ctx: commands.Context[TMWBot]):
        """Clear all global commands."""
        self.bot.tree.clear_commands(guild=None)
        await self.bot.tree.sync()
        await ctx.send("Cleared global commands.")

    @commands.command()
    @is_authorized()
    async def clear_guild_commands(self, ctx: commands.Context[TMWBot]):
        """Clear all guild commands."""
        if not ctx.guild:
            await ctx.send("This command can only be used in a guild.")
            return
        self.bot.tree.clear_commands(guild=discord.Object(id=ctx.guild.id))
        await self.bot.tree.sync(guild=discord.Object(id=ctx.guild.id))
        await ctx.send(f"Cleared guild commands for guild with id {ctx.guild.id}.")


async def setup(bot: TMWBot):
    await bot.add_cog(Sync(bot))
