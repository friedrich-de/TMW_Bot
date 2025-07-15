import discord
import os
import yaml
import logging
from discord.ext import commands, tasks
from lib.bot import TMWBot

_log = logging.getLogger(__name__)

RANKSAVER_SETTINGS_PATH = os.getenv("ALT_RANKSAVER_SETTINGS_PATH") or "config/rank_saver_settings.yml"
with open(RANKSAVER_SETTINGS_PATH, "r", encoding="utf-8") as f:
    ranksaver_settings = yaml.safe_load(f)

CREATE_USER_RANKS_TABLE = """
CREATE TABLE IF NOT EXISTS user_ranks (
    guild_id INTEGER NOT NULL,
    discord_user_id INTEGER NOT NULL,
    role_ids TEXT NOT NULL,
    PRIMARY KEY (guild_id, discord_user_id)
);"""

GET_USER_ROLES_QUERY = """
SELECT role_ids FROM user_ranks
WHERE guild_id = ? AND discord_user_id = ?;"""

SAVE_USER_ROLE_QUERY = """
INSERT OR REPLACE INTO user_ranks (guild_id, discord_user_id, role_ids)
VALUES (?, ?, ?);"""


class RankSaver(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot

    async def cog_load(self):
        await self.bot.RUN(CREATE_USER_RANKS_TABLE)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.rank_saver.is_running():
            _log.info("Starting rank saver task.")
            self.rank_saver.start()

    @tasks.loop(minutes=10.0)
    async def rank_saver(self):
        all_role_ids_to_ignore = ranksaver_settings["role_ids_to_ignore"]
        rows = []
        for guild in self.bot.guilds:
            all_members = [member for member in guild.members if not member.bot]
            for member in all_members:
                member_role_ids = [str(role.id) for role in member.roles if role.is_assignable() and role.id not in all_role_ids_to_ignore]
                role_ids_str = ",".join(member_role_ids)

                rows.append((guild.id, member.id, role_ids_str))

        if rows:
            await self.bot.RUN_MANY(SAVE_USER_ROLE_QUERY, rows)
            _log.info(f"Saved roles for {len(rows)} members across {len(self.bot.guilds)} guilds.")

    @commands.Cog.listener(name="on_member_join")
    async def rank_restorer(self, member: discord.Member):
        result = await self.bot.GET(GET_USER_ROLES_QUERY, (member.guild.id, member.id))
        if result:
            role_ids_str = result[0][0]
            role_ids = role_ids_str.split(",") if role_ids_str else []
            roles_to_restore = [discord.utils.get(member.guild.roles, id=int(role_id)) for role_id in role_ids if discord.utils.get(member.guild.roles, id=int(role_id))]
            all_role_ids_to_ignore = ranksaver_settings["role_ids_to_ignore"]
            roles_to_restore = [role for role in roles_to_restore if role.id not in all_role_ids_to_ignore]
            if roles_to_restore:
                _log.info(f"Restoring roles for {member.name} in guild {member.guild.name}. Roles: {[role.name for role in roles_to_restore]}")
                assignable_roles = [role for role in roles_to_restore if role.is_assignable()]
                await member.add_roles(*assignable_roles)

                to_restore_channel = member.guild.get_channel(ranksaver_settings["announce_channel"][member.guild.id])
                if not to_restore_channel:
                    to_restore_channel = member.guild.system_channel
                if not to_restore_channel:
                    return
                await to_restore_channel.send(
                    f"**{member.mention} Rejoined:** Restored the following roles: **{', '.join([role.mention for role in assignable_roles])}**",
                    allowed_mentions=discord.AllowedMentions.none(),
                )


async def setup(bot):
    await bot.add_cog(RankSaver(bot))
