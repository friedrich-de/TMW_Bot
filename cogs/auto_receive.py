"""Cog that enables certain roles to automatically receive other roles."""
import discord
import asyncio
import logging
import os
import yaml

from discord.ext import commands
from discord.ext import tasks

from lib.bot import TMWBot

_log = logging.getLogger(__name__)

AUTO_RECEIVE_LOCK = asyncio.Lock()
AUTO_RECIEVE_SETTINGS_PATH = os.getenv("ALT_AUTO_RECEIVE_SETTINGS_PATH") or "config/auto_receive.yml"


class AutoReceive(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot
        self.auto_receive_config = {}

    async def load_settings(self):
        try:
            with open(AUTO_RECIEVE_SETTINGS_PATH, "r", encoding="utf-8") as f:
                self.auto_receive_config = yaml.safe_load(f) or {}
        except FileNotFoundError:
            _log.warning(f"Auto receive settings file not found: {AUTO_RECIEVE_SETTINGS_PATH}")
            self.auto_receive_config = {}
        except Exception as e:
            _log.error(f"Failed to load auto receive settings: {e}")
            self.auto_receive_config = {}

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.give_auto_roles.is_running():
            await self.load_settings()
            self.give_auto_roles.start()

    @tasks.loop(minutes=15)
    async def give_auto_roles(self):
        async with AUTO_RECEIVE_LOCK:
            await self.load_settings()
            
            for guild in self.bot.guilds:
                guild_settings = self.auto_receive_config.get(guild.id, {})
                
                for role_to_have_id, role_to_get_id in guild_settings.items():
                    role_to_have = discord.utils.get(guild.roles, id=role_to_have_id)
                    role_to_get = discord.utils.get(guild.roles, id=role_to_get_id)
                    
                    if not role_to_have or not role_to_get:
                        _log.warning(f"Role not found in guild {guild.id}: role_to_have={role_to_have_id}, role_to_get={role_to_get_id}")
                        continue

                    for member in role_to_have.members:
                        if role_to_get not in member.roles:
                            try:
                                await asyncio.sleep(1)
                                await member.add_roles(role_to_get)
                                _log.info(f"Added role {role_to_get.name} to member {member.name} in guild {guild.name}")
                            except Exception as e:
                                _log.error(f"Failed to add role {role_to_get.name} to member {member.name} in guild {guild.name}: {e}")



async def setup(bot: TMWBot):
    await bot.add_cog(AutoReceive(bot))
