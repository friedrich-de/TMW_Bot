import logging
from dataclasses import dataclass
from typing import Any, TypedDict, cast

import discord
import yaml
from discord.ext import commands, tasks
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from lib.bot import TMWBot
from lib.models import UserRanks

_log = logging.getLogger(__name__)

RANKSAVER_SETTINGS_PATH = "config/rank_saver_settings.yml"


@dataclass(frozen=True, slots=True)
class RankSaverSettings:
    role_ids_to_ignore: frozenset[int]
    announce_channel: dict[int, int]


def load_ranksaver_settings() -> RankSaverSettings:
    with open(RANKSAVER_SETTINGS_PATH, encoding="utf-8") as f:
        raw = cast(dict[str, Any], yaml.safe_load(f))

    role_ids_to_ignore = frozenset(cast(list[int], raw["role_ids_to_ignore"]))
    announce_channel = cast(dict[int, int], raw["announce_channel"])

    return RankSaverSettings(
        role_ids_to_ignore=role_ids_to_ignore,
        announce_channel=announce_channel,
    )


RANKSAVER_SETTINGS = load_ranksaver_settings()


def _parse_role_ids_to_restore(role_ids_to_restore_str: str | None) -> list[int]:
    if not role_ids_to_restore_str:
        return []

    role_ids: set[int] = set()
    for token in role_ids_to_restore_str.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            role_ids.add(int(token))
        except ValueError:
            continue

    return list(role_ids)


def _resolve_roles_to_restore(
    guild: discord.Guild,
    *,
    role_ids: list[int],
) -> list[discord.Role]:
    roles: list[discord.Role] = []
    for role_id in role_ids:
        if role_id in RANKSAVER_SETTINGS.role_ids_to_ignore:
            continue
        role = guild.get_role(role_id)
        if role is not None:
            roles.append(role)
    return roles


def _get_announce_channel(guild: discord.Guild) -> discord.abc.Messageable | None:
    announce_channel_id = RANKSAVER_SETTINGS.announce_channel.get(guild.id)
    if announce_channel_id is not None:
        channel = guild.get_channel(announce_channel_id)
        if isinstance(channel, discord.abc.Messageable):
            return channel
    return guild.system_channel


class UserRankRow(TypedDict):
    guild_id: int
    discord_user_id: int
    role_ids: str


def build_user_rank_rows(bot: TMWBot,) -> list[UserRankRow]:
    rows: list[UserRankRow] = []
    for guild in bot.guilds:
        for member in guild.members:
            if member.bot:
                continue

            member_role_ids = [
                str(role.id)
                for role in member.roles
                if role.is_assignable() and role.id not in RANKSAVER_SETTINGS.role_ids_to_ignore
            ]

            rows.append(
                {
                    "guild_id": guild.id,
                    "discord_user_id": member.id,
                    "role_ids": ",".join(member_role_ids),
                }
            )

    return rows


async def upsert_user_rank_rows(bot: TMWBot, *, rows: list[UserRankRow]) -> None:
    if not rows:
        return

    async with bot.session_factory() as session:
        stmt = insert(UserRanks).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserRanks.guild_id, UserRanks.discord_user_id],
            set_={"role_ids": stmt.excluded.role_ids},
        )
        await session.execute(stmt)
        await session.commit()


class RankSaver(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.rank_saver.is_running():
            _log.info("Starting rank saver task.")
            self.rank_saver.start()

    @tasks.loop(minutes=10.0)
    async def rank_saver(self):
        try:
            rows = build_user_rank_rows(
                self.bot,
            )
            await upsert_user_rank_rows(self.bot, rows=rows)

            if rows:
                _log.info(
                    f"Saved roles for {len(rows)} members across {len(self.bot.guilds)} guilds."
                )

        except Exception as e:
            _log.error(f"Error in rank_saver task: {e}")

    @commands.Cog.listener(name="on_member_join")
    async def rank_restorer(self, member: discord.Member):
        if member.bot:
            return

        async with self.bot.session_factory() as session:
            result = await session.execute(
                select(UserRanks.role_ids).where(
                    UserRanks.guild_id == member.guild.id,
                    UserRanks.discord_user_id == member.id,
                )
            )
            role_ids_str = result.scalar_one_or_none()

        if role_ids_str is None:
            return

        role_ids_to_restore = _parse_role_ids_to_restore(role_ids_str)
        if not role_ids_to_restore:
            return

        roles_to_restore = _resolve_roles_to_restore(
            member.guild,
            role_ids=role_ids_to_restore,
        )

        assignable_roles = [
            role for role in roles_to_restore if role.is_assignable()]
        if not assignable_roles:
            return

        _log.info(
            "Restoring roles for user_id=%s in guild_id=%s (%s). Roles: %s",
            member.id,
            member.guild.id,
            member.guild.name,
            [role.name for role in assignable_roles],
        )

        try:
            await member.add_roles(*assignable_roles, reason="RankSaver: restore roles on rejoin")
        except discord.Forbidden:
            _log.warning(
                "Missing permissions to restore roles for user_id=%s in guild_id=%s",
                member.id,
                member.guild.id,
            )
            return
        except discord.HTTPException:
            _log.exception(
                "HTTP error restoring roles for user_id=%s in guild_id=%s",
                member.id,
                member.guild.id,
            )
            return

        channel = _get_announce_channel(member.guild)
        if channel is None:
            return
        try:
            await channel.send(
                f"**{member.mention} Rejoined:** Restored the following roles: **{', '.join([role.mention for role in assignable_roles])}**",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            _log.exception(
                "HTTP error announcing restored roles for user_id=%s in guild_id=%s",
                member.id,
                member.guild.id,
            )


async def setup(bot: TMWBot):
    await bot.add_cog(RankSaver(bot))
