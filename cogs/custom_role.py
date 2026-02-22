import logging
import re
from dataclasses import dataclass
from typing import Any, cast

import discord
import yaml
from discord.ext import commands, tasks
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from lib.bot import TMWBot
from lib.models import CustomRoleEntry

_log = logging.getLogger(__name__)

MAX_ROLE_NAME_LENGTH = 14
HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

CUSTOM_ROLE_SETTINGS_PATH = "config/custom_role_settings.yml"


@dataclass(frozen=True, slots=True)
class CustomRoleGuildSettings:
    allowed_role_ids: frozenset[int]
    reference_role_id: int
    role_icon_allowed: bool


@dataclass(frozen=True, slots=True)
class RoleCreationContext:
    guild: discord.Guild
    member: discord.Member
    reference_role: discord.Role
    role_name: str
    colour: discord.Colour
    icon_bytes: bytes | None
    settings: CustomRoleGuildSettings
    existing_role_id: int | None


def load_custom_role_settings() -> dict[int, CustomRoleGuildSettings]:
    with open(CUSTOM_ROLE_SETTINGS_PATH, encoding="utf-8") as f:
        raw_any: Any = yaml.safe_load(f)

    raw = cast(dict[int, dict[str, Any]] | None, raw_any) or {}
    settings: dict[int, CustomRoleGuildSettings] = {}

    for guild_id, cfg in raw.items():
        allowed_roles = cast(list[int], cfg["allowed_roles"])
        reference_role_id = int(cfg["reference_role_id"])
        role_icon_allowed = bool(cfg.get("role_icon_allowed", True))

        settings[int(guild_id)] = CustomRoleGuildSettings(
            allowed_role_ids=frozenset(allowed_roles),
            reference_role_id=reference_role_id,
            role_icon_allowed=role_icon_allowed,
        )

    return settings


CUSTOM_ROLE_SETTINGS = load_custom_role_settings()


def get_guild_settings(guild_id: int) -> CustomRoleGuildSettings | None:
    return CUSTOM_ROLE_SETTINGS.get(guild_id)


def member_has_allowed_role(member: discord.Member, allowed_role_ids: frozenset[int]) -> bool:
    if not allowed_role_ids:
        return False
    return any(role.id in allowed_role_ids for role in member.roles)


async def fetch_custom_roles(bot: TMWBot, guild_id: int) -> list[CustomRoleEntry]:
    async with bot.session_factory() as session:
        result = await session.execute(
            select(CustomRoleEntry).where(CustomRoleEntry.guild_id == guild_id)
        )
        return list(result.scalars().all())


async def clear_custom_role_data(
    bot: TMWBot,
    guild: discord.Guild,
    *,
    user_id: int,
    role_id: int,
    role_name: str | None,
    reason: str,
) -> None:
    role = guild.get_role(role_id)
    if role is not None:
        try:
            await role.delete(reason=reason)
        except discord.Forbidden:
            _log.warning(
                "Missing permissions to delete custom role role_id=%s in guild_id=%s",
                role_id,
                guild.id,
            )
        except discord.HTTPException:
            _log.exception(
                "HTTP error deleting custom role role_id=%s in guild_id=%s",
                role_id,
                guild.id,
            )

    async with bot.session_factory() as session:
        await session.execute(
            delete(CustomRoleEntry).where(
                CustomRoleEntry.guild_id == guild.id,
                CustomRoleEntry.user_id == user_id,
            )
        )
        await session.commit()

    _log.info(
        "Custom role cleanup in guild_id=%s user_id=%s role_id=%s role_name=%r (%s)",
        guild.id,
        user_id,
        role_id,
        role_name,
        reason,
    )


async def ensure_guild_and_member(
    bot: TMWBot, interaction: discord.Interaction
) -> tuple[discord.Guild, discord.Member] | None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await bot.reply(
            interaction,
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return None
    return interaction.guild, interaction.user


async def ensure_settings(
    bot: TMWBot, interaction: discord.Interaction, guild_id: int
) -> CustomRoleGuildSettings | None:
    settings = get_guild_settings(guild_id)
    if settings is None:
        await bot.reply(
            interaction,
            "Custom role settings are missing. Please ask an admin to set them up.",
        )
        return None
    return settings


async def ensure_reference_role(
    bot: TMWBot, interaction: discord.Interaction, guild: discord.Guild, reference_role_id: int
) -> discord.Role | None:
    reference_role = guild.get_role(reference_role_id)
    if reference_role is None:
        await bot.reply(interaction, "The reference role for custom roles is missing.")
        return None
    return reference_role


async def ensure_member_is_allowed(
    bot: TMWBot, interaction: discord.Interaction, member: discord.Member, allowed_role_ids: frozenset[int]
) -> bool:
    if member_has_allowed_role(member, allowed_role_ids):
        return True

    await bot.reply(interaction, "You are not allowed to create a custom role.")
    return False


async def validate_role_name(
    bot: TMWBot,
    interaction: discord.Interaction,
    guild: discord.Guild,
    role_name: str,
    *,
    existing_role_id: int | None = None,
) -> str | None:
    cleaned_name = role_name.strip()
    if len(cleaned_name) > MAX_ROLE_NAME_LENGTH:
        await bot.reply(
            interaction,
            f"Please use a shorter role name (max {MAX_ROLE_NAME_LENGTH} characters).",
        )
        return None

    if any(
        role.name == cleaned_name and role.id != existing_role_id
        for role in guild.roles
    ):
        await bot.reply(interaction, "You can't use this role name. Try another one.")
        return None

    return cleaned_name


async def parse_colour(
    bot: TMWBot, interaction: discord.Interaction, color_code: str
) -> discord.Colour | None:
    cleaned_code = color_code.strip()
    if not HEX_COLOR_RE.fullmatch(cleaned_code):
        await bot.reply(
            interaction,
            "Please enter a valid hex color code. Example: `#A47267`",
        )
        return None

    try:
        return discord.Colour(int(cleaned_code.lstrip("#"), 16))
    except ValueError:
        await bot.reply(
            interaction,
            "Please enter a valid hex color code. Example: `#A47267`",
        )
        return None


async def read_role_icon(
    bot: TMWBot, interaction: discord.Interaction, guild: discord.Guild, role_icon: discord.Attachment | None
) -> tuple[bytes | None, bool]:
    if role_icon is None:
        return None, True

    if "ROLE_ICONS" not in guild.features:
        await bot.reply(
            interaction,
            "This server doesn't have enough boosts to use custom role icons.",
        )
        return None, False

    try:
        icon_bytes = await role_icon.read()
    except discord.HTTPException:
        _log.exception("Failed to read uploaded role icon")
        await bot.reply(
            interaction,
            "Couldn't read that icon file. Please try again.",
        )
        return None, False

    return icon_bytes, True


async def build_role_creation_context(
    bot: TMWBot,
    interaction: discord.Interaction,
    role_name: str,
    color_code: str,
    role_icon: discord.Attachment | None,
) -> RoleCreationContext | None:
    guild_member = await ensure_guild_and_member(bot, interaction)
    if guild_member is None:
        return None
    guild, member = guild_member

    settings = await ensure_settings(bot, interaction, guild.id)
    if settings is None:
        return None

    if role_icon is not None and not settings.role_icon_allowed:
        await bot.reply(
            interaction,
            "Custom role icons are not allowed on this server.",
        )
        return None

    reference_role = await ensure_reference_role(bot, interaction, guild, settings.reference_role_id)
    if reference_role is None:
        return None

    if not await ensure_member_is_allowed(bot, interaction, member, settings.allowed_role_ids):
        return None

    entries = await fetch_custom_roles(bot, guild.id)
    existing_entry = next(
        (entry for entry in entries if entry.user_id == member.id), None)
    existing_role_id = existing_entry.role_id if existing_entry is not None else None

    validated_role_name = await validate_role_name(
        bot,
        interaction,
        guild,
        role_name,
        existing_role_id=existing_role_id,
    )
    if validated_role_name is None:
        return None

    colour = await parse_colour(bot, interaction, color_code)
    if colour is None:
        return None

    icon_bytes, ok = await read_role_icon(bot, interaction, guild, role_icon)
    if not ok:
        return None

    return RoleCreationContext(
        guild=guild,
        member=member,
        reference_role=reference_role,
        role_name=validated_role_name,
        colour=colour,
        icon_bytes=icon_bytes,
        settings=settings,
        existing_role_id=existing_role_id,
    )


async def remove_existing_custom_role(
    bot: TMWBot,
    guild: discord.Guild,
    entries: list[CustomRoleEntry],
    user_id: int,
    *,
    reason: str,
) -> None:
    for entry in entries:
        if entry.user_id == user_id:
            await clear_custom_role_data(
                bot,
                guild,
                user_id=user_id,
                role_id=entry.role_id,
                role_name=entry.role_name,
                reason=reason,
            )
            break


async def create_custom_role(
    bot: TMWBot, interaction: discord.Interaction, context: RoleCreationContext
) -> discord.Role | None:
    try:
        if context.icon_bytes is not None:
            return await context.guild.create_role(
                name=context.role_name,
                colour=context.colour,
                display_icon=context.icon_bytes,
                reason="Custom role creation",
            )

        return await context.guild.create_role(
            name=context.role_name,
            colour=context.colour,
            reason="Custom role creation",
        )
    except discord.Forbidden:
        await bot.reply(interaction, "I don't have permission to create roles.")
        return None
    except discord.HTTPException:
        _log.exception("HTTP error creating custom role")
        await bot.reply(
            interaction,
            "Failed to create the role due to a Discord error. Try again.",
        )
        return None


async def position_custom_role(custom_role: discord.Role, reference_role: discord.Role) -> None:
    try:
        await custom_role.guild.edit_role_positions({custom_role: reference_role.position - 1})
    except discord.Forbidden:
        _log.warning(
            "Missing permissions to move custom role in guild_id=%s",
            custom_role.guild.id,
        )
    except discord.HTTPException:
        _log.exception("HTTP error moving role positions")


async def assign_custom_role_to_member(
    bot: TMWBot, interaction: discord.Interaction, member: discord.Member, custom_role: discord.Role
) -> bool:
    try:
        await member.add_roles(custom_role, reason="Custom role assignment")
    except discord.Forbidden:
        await bot.reply(interaction, "I don't have permission to assign that role.")
        return False
    except discord.HTTPException:
        _log.exception("HTTP error assigning custom role")
        await bot.reply(
            interaction,
            "Failed to assign the role due to a Discord error. Try again.",
        )
        return False

    return True


async def cleanup_failed_custom_role_creation(custom_role: discord.Role) -> None:
    try:
        await custom_role.delete(reason="Cleanup after failed custom role assignment")
    except discord.Forbidden:
        _log.warning(
            "Missing permissions to delete failed custom role role_id=%s in guild_id=%s",
            custom_role.id,
            custom_role.guild.id,
        )
    except discord.HTTPException:
        _log.exception(
            "HTTP error deleting failed custom role role_id=%s in guild_id=%s",
            custom_role.id,
            custom_role.guild.id,
        )


async def save_custom_role_entry(bot: TMWBot, context: RoleCreationContext, role_id: int) -> None:
    async with bot.session_factory() as session:
        stmt = insert(CustomRoleEntry).values(
            {
                "guild_id": context.guild.id,
                "user_id": context.member.id,
                "role_id": role_id,
                "role_name": context.role_name,
            }
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[CustomRoleEntry.guild_id, CustomRoleEntry.user_id],
            set_={
                "role_id": stmt.excluded.role_id,
                "role_name": stmt.excluded.role_name,
            },
        )
        await session.execute(stmt)
        await session.commit()


async def cleanup_custom_role_entry(
    bot: TMWBot, guild: discord.Guild, settings: CustomRoleGuildSettings, entry: CustomRoleEntry
) -> None:
    member = guild.get_member(entry.user_id)
    if member is None:
        await clear_custom_role_data(
            bot,
            guild,
            user_id=entry.user_id,
            role_id=entry.role_id,
            role_name=entry.role_name,
            reason="User left guild",
        )
        return

    if not member_has_allowed_role(member, settings.allowed_role_ids):
        await clear_custom_role_data(
            bot,
            guild,
            user_id=entry.user_id,
            role_id=entry.role_id,
            role_name=entry.role_name,
            reason="User lost required role",
        )
        return

    role = guild.get_role(entry.role_id)
    if role is None:
        await clear_custom_role_data(
            bot,
            guild,
            user_id=entry.user_id,
            role_id=entry.role_id,
            role_name=entry.role_name,
            reason="Role missing from guild",
        )
        return

    if not role.members:
        await clear_custom_role_data(
            bot,
            guild,
            user_id=entry.user_id,
            role_id=entry.role_id,
            role_name=entry.role_name,
            reason="Role has no members",
        )


class CustomRole(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.strip_roles.is_running():
            self.strip_roles.start()

    @discord.app_commands.command(
        name="make_custom_role", description="Create a custom role for yourself."
    )
    @discord.app_commands.guild_only()
    @discord.app_commands.describe(
        role_name=f"Role name. Maximum of {MAX_ROLE_NAME_LENGTH} characters.",
        color_code="Hex color code. Example: #A47267",
        role_icon="Image that should be used.",
    )
    async def make_custom_role(
        self,
        interaction: discord.Interaction,
        role_name: str,
        color_code: str,
        role_icon: discord.Attachment | None = None,
    ) -> None:
        await interaction.response.defer()
        context = await build_role_creation_context(
            self.bot, interaction, role_name, color_code, role_icon
        )
        if context is None:
            return

        entries = await fetch_custom_roles(self.bot, context.guild.id)
        await remove_existing_custom_role(
            self.bot,
            context.guild,
            entries,
            context.member.id,
            reason="User recreated custom role",
        )

        custom_role = await create_custom_role(self.bot, interaction, context)
        if custom_role is None:
            return

        await position_custom_role(custom_role, context.reference_role)

        if not await assign_custom_role_to_member(
            self.bot, interaction, context.member, custom_role
        ):
            await cleanup_failed_custom_role_creation(custom_role)
            return

        await save_custom_role_entry(self.bot, context, custom_role.id)

        await self.bot.reply(interaction, f"Created your custom role: {custom_role.mention}")

    @discord.app_commands.command(name="delete_custom_role", description="Remove a custom role from yourself.")
    @discord.app_commands.guild_only()
    async def delete_custom_role(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        guild_member = await ensure_guild_and_member(self.bot, interaction)
        if guild_member is None:
            return
        guild, member = guild_member

        entries = await fetch_custom_roles(self.bot, guild.id)
        for entry in entries:
            if entry.user_id == member.id:
                await clear_custom_role_data(
                    self.bot,
                    guild,
                    user_id=member.id,
                    role_id=entry.role_id,
                    role_name=entry.role_name,
                    reason="User deleted custom role",
                )
                await self.bot.reply(interaction, "Deleted your custom role.")
                return

        await self.bot.reply(interaction, "You don't seem to have a custom role.")

    @tasks.loop(minutes=60.0)
    async def strip_roles(self) -> None:
        for guild in self.bot.guilds:
            settings = get_guild_settings(guild.id)
            if settings is None:
                continue

            allowed_role_ids = settings.allowed_role_ids
            if not allowed_role_ids:
                continue

            entries = await fetch_custom_roles(self.bot, guild.id)
            for entry in entries:
                try:
                    await cleanup_custom_role_entry(self.bot, guild, settings, entry)
                except Exception:
                    _log.exception(
                        "Error during custom role cleanup guild_id=%s user_id=%s",
                        guild.id,
                        entry.user_id,
                    )


async def setup(bot: TMWBot) -> None:
    await bot.add_cog(CustomRole(bot))
