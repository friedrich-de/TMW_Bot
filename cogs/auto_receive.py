"""Cog that enables certain roles to automatically receive other roles."""

import asyncio
import logging
from typing import cast

import discord
import yaml
from discord.ext import commands, tasks

from lib.bot import TMWBot

_log = logging.getLogger(__name__)

AUTO_RECEIVE_LOCK = asyncio.Lock()
AUTO_RECIEVE_SETTINGS_PATH = "config/auto_receive.yml"


def load_auto_receive_config() -> dict[int, list[list[int]]]:
    with open(AUTO_RECIEVE_SETTINGS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return cast(dict[int, list[list[int]]], data)


auto_receive_config = load_auto_receive_config()


async def process_auto_roles(guild: discord.Guild, guild_settings: list[list[int]]):
    for role_to_have_id, role_to_get_id in guild_settings:
        role_to_have = discord.utils.get(guild.roles, id=role_to_have_id)
        role_to_get = discord.utils.get(guild.roles, id=role_to_get_id)

        if not role_to_have or not role_to_get:
            _log.warning(
                f"Role not found in guild {guild.id}: "
                f"role_to_have={role_to_have_id}, role_to_get={role_to_get_id}"
            )
            continue

        for member in role_to_have.members:
            await process_member(member, role_to_get)


async def process_member(member: discord.Member, role_to_get: discord.Role):
    if role_to_get in member.roles:
        return

    async with AUTO_RECEIVE_LOCK:
        await asyncio.sleep(1)

        try:
            await member.add_roles(role_to_get)
            _log.info(
                f"Added role {role_to_get.name} to member {member.name} in guild {member.guild.name}"
            )
        except Exception as e:
            _log.error(
                f"Failed to add role {role_to_get.name} to member {member.name} in guild {member.guild.name}: {e}"
            )


class AutoReceive(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.give_auto_roles.is_running():
            self.give_auto_roles.start()

    @tasks.loop(minutes=15)
    async def give_auto_roles(self):
        try:
            for guild in self.bot.guilds:
                guild_settings = auto_receive_config.get(guild.id, [])

                if not guild_settings:
                    continue

                await process_auto_roles(guild, guild_settings)

        except Exception as e:
            _log.error(f"Error in give_auto_roles task: {e}")


async def setup(bot: TMWBot):
    await bot.add_cog(AutoReceive(bot))
