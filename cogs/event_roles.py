import discord
from discord.ext import commands, tasks

from lib.bot import TMWBot

CREATE_EVENT_ROLES_TABLE = """
CREATE TABLE IF NOT EXISTS event_roles (
    guild_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, event_id)
);"""

INSERT_EVENT_ROLE = """
INSERT OR REPLACE INTO event_roles (guild_id, event_id, role_id)
VALUES (?, ?, ?);"""

GET_ALL_EVENT_ROLES = """
SELECT guild_id, event_id, role_id FROM event_roles;"""

DELETE_EVENT_ROLE = """
DELETE FROM event_roles WHERE guild_id = ? AND event_id = ?;"""


class EventRoles(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot

    def cog_unload(self):
        self.sync_event_roles.cancel()

    async def cog_load(self):
        await self.bot.RUN(CREATE_EVENT_ROLES_TABLE)
        self.sync_event_roles.start()

    async def create_event_role(self, event: discord.ScheduledEvent) -> discord.Role:
        role_name = f"Event: {event.name}"
        try:
            role = await event.guild.create_role(
                name=role_name, mentionable=True, reason="Event role creation"
            )
            await self.bot.RUN(INSERT_EVENT_ROLE, (event.guild.id, event.id, role.id))

            async for user in event.users():
                member = event.guild.get_member(user.id)
                if member:
                    try:
                        await member.add_roles(role, reason="User interested in event")
                    except discord.Forbidden:
                        print(
                            f"Cannot add role to user {user.id} in guild {event.guild.id}"
                        )

            return role
        except discord.Forbidden:
            print(
                f"Missing permissions to create/manage roles in guild {event.guild.id}"
            )
            return None

    async def cleanup_role(self, guild_id: int, role_id: int, event_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            await self.bot.RUN(DELETE_EVENT_ROLE, (guild_id, event_id))
            return

        role = guild.get_role(role_id)
        if role:
            try:
                await role.delete(reason="Event ended or cancelled")
            except discord.Forbidden:
                print(f"Cannot delete role {role_id} in guild {guild_id}")

        await self.bot.RUN(DELETE_EVENT_ROLE, (guild_id, event_id))

    @tasks.loop(minutes=5)
    async def sync_event_roles(self):
        event_roles = await self.bot.GET(GET_ALL_EVENT_ROLES)

        for guild_id, event_id, role_id in event_roles:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            event = guild.get_scheduled_event(event_id)
            if not event:
                await self.cleanup_role(guild_id, role_id, event_id)
                continue

            if event.status in [
                discord.EventStatus.ended,
                discord.EventStatus.completed,
                discord.EventStatus.cancelled,
                discord.EventStatus.canceled,
            ]:
                await self.cleanup_role(guild_id, role_id, event_id)
                continue

            role = guild.get_role(role_id)
            if not role:
                await self.create_event_role(event)

        for guild in self.bot.guilds:
            for event in guild.scheduled_events:
                if event.status not in [
                    discord.EventStatus.scheduled,
                    discord.EventStatus.active,
                ]:
                    continue

                exists = any(
                    event_role[0] == guild.id and event_role[1] == event.id
                    for event_role in event_roles
                )
                if not exists:
                    await self.create_event_role(event)

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event: discord.ScheduledEvent):
        await self.create_event_role(event)

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent):
        role_data = await self.bot.GET_ONE(
            "SELECT role_id FROM event_roles WHERE guild_id = ? AND event_id = ?",
            (event.guild.id, event.id),
        )
        if role_data:
            await self.cleanup_role(event.guild.id, role_data[0], event.id)

    @commands.Cog.listener()
    async def on_scheduled_event_user_add(
        self, event: discord.ScheduledEvent, user: discord.User
    ):
        role_data = await self.bot.GET_ONE(
            "SELECT role_id FROM event_roles WHERE guild_id = ? AND event_id = ?",
            (event.guild.id, event.id),
        )
        if role_data:
            role = event.guild.get_role(role_data[0])
            if role:
                member = event.guild.get_member(user.id)
                if member:
                    try:
                        await member.add_roles(role, reason="User interested in event")
                    except discord.Forbidden:
                        print(
                            f"Cannot add role to user {user.id} in guild {event.guild.id}"
                        )

    @commands.Cog.listener()
    async def on_scheduled_event_user_remove(
        self, event: discord.ScheduledEvent, user: discord.User
    ):
        role_data = await self.bot.GET_ONE(
            "SELECT role_id FROM event_roles WHERE guild_id = ? AND event_id = ?",
            (event.guild.id, event.id),
        )
        if role_data:
            role = event.guild.get_role(role_data[0])
            if role:
                member = event.guild.get_member(user.id)
                if member:
                    try:
                        await member.remove_roles(
                            role, reason="User no longer interested in event"
                        )
                    except discord.Forbidden:
                        print(
                            f"Cannot remove role from user {user.id} in guild {event.guild.id}"
                        )

    @commands.Cog.listener()
    async def on_scheduled_event_update(
        self, before: discord.ScheduledEvent, after: discord.ScheduledEvent
    ):
        if before.status != after.status:
            if after.status in [
                discord.EventStatus.ended,
                discord.EventStatus.completed,
                discord.EventStatus.cancelled,
                discord.EventStatus.canceled,
            ]:
                role_data = await self.bot.GET_ONE(
                    "SELECT role_id FROM event_roles WHERE guild_id = ? AND event_id = ?",
                    (after.guild.id, after.id),
                )
                if role_data:
                    await self.cleanup_role(after.guild.id, role_data[0], after.id)


async def setup(bot):
    await bot.add_cog(EventRoles(bot))
