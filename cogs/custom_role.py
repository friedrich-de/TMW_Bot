from lib.bot import TMWBot
import discord
from discord.ext import commands
from discord.ext import tasks

import re

CREATE_CUSTOM_ROLE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS custom_roles (
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        role_id INTEGER NOT NULL,
        role_name TEXT,
        PRIMARY KEY (guild_id, user_id))"""

GET_CUSTOM_ROLES_SQL = "SELECT * FROM custom_roles WHERE guild_id = ?"

SET_CUSTOM_ROLE_SQL = """INSERT INTO custom_roles (guild_id, user_id, role_id, role_name)
                        VALUES (?, ?, ?, ?)"""

DELETE_CUSTOM_ROLE_SQL = "DELETE FROM custom_roles WHERE guild_id = ? AND user_id = ?"

CREATE_CUSTOM_ROLE_SETTINGS_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS custom_role_settings (
        guild_id INTEGER NOT NULL,
        allowed_roles TEXT,
        reference_role_id INTEGER,
        reference_role_name TEXT,
        PRIMARY KEY (guild_id))"""

GET_CUSTOM_ROLE_SETTINGS_SQL = "SELECT * FROM custom_role_settings WHERE guild_id = ?"

SET_CUSTOM_ROLE_SETTINGS_SQL = """INSERT INTO custom_role_settings (guild_id, allowed_roles, reference_role_id, reference_role_name)
                        VALUES (?, ?, ?, ?)"""

DELETE_CUSTOM_ROLE_SETTINGS_SQL = "DELETE FROM custom_role_settings WHERE guild_id = ?"


class CustomRole(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot

    async def cog_load(self):
        await self.bot.RUN(CREATE_CUSTOM_ROLE_TABLE_SQL)
        await self.bot.RUN(CREATE_CUSTOM_ROLE_SETTINGS_TABLE_SQL)
        self.strip_roles.start()

    async def get_custom_roles(self, guild_id):
        data = await self.bot.GET(GET_CUSTOM_ROLES_SQL, (guild_id,))
        if data:
            return [{"user_id": row[1], "role_id": row[2], "role_name": row[3]} for row in data]
        return []

    async def get_custom_role_settings(self, guild_id):
        data = await self.bot.GET(GET_CUSTOM_ROLE_SETTINGS_SQL, (guild_id,))
        if data:
            return {"allowed_roles": data[0][1], "reference_role_id": data[0][2], "reference_role_name": data[0][3]}
        return []

    async def check_if_allowed(self, member: discord.Member, allowed_roles: str):
        allowed_role_ids = [int(role_id)
                            for role_id in allowed_roles.split(",")]
        if any(role.id in allowed_role_ids for role in member.roles):
            return True
        return False

    async def clear_custom_role_data(self, guild: discord.Guild, member_id: int, role_id: int):
        custom_role = guild.get_role(role_id)
        if custom_role:
            await custom_role.delete()
        await self.bot.RUN(DELETE_CUSTOM_ROLE_SQL, (guild.id, member_id))

    @discord.app_commands.command(name="make_custom_role", description="Create a custom role for yourself.")
    @discord.app_commands.guild_only()
    @discord.app_commands.describe(
        role_name="Role name. Maximum of 14 symbols.",
        color_code="Hex color code. Example: #A47267",
        role_icon="Image that should be used.",)
    async def make_custom_role(self, interaction: discord.Interaction, role_name: str, color_code: str, role_icon: discord.Attachment = None):
        await interaction.response.defer()

        # Temporarily disable role icons
        if role_icon:
            await interaction.followup.send("Custom role icons are temporarily disabled.")
            return

        custom_role_settings = await self.get_custom_role_settings(interaction.guild.id)
        if not custom_role_settings:
            await interaction.followup.send("Custom role settings are missing. Please ask an admin to set them up.")
            return

        reference_role = interaction.guild.get_role(
            custom_role_settings["reference_role_id"])
        if not reference_role:
            await interaction.followup.send("The reference role for custom roles is missing.")
            return

        allowed = await self.check_if_allowed(interaction.user, custom_role_settings["allowed_roles"])
        if not allowed:
            await interaction.followup.send("You are not allowed to create a custom role.")
            return

        if len(role_name) > 14:
            await interaction.followup.send("Please use a shorter role name. Restrict yourself to 14 symbols.")
            return

        color_match = re.search(r"^#(?:[0-9a-fA-F]{3}){1,2}$", color_code)
        if not color_match:
            await interaction.followup.send("Please enter a valid hex color code. Example: `#A47267` ")
            return

        custom_role_data = await self.get_custom_roles(interaction.guild.id)
        for user_data in custom_role_data:
            if user_data["user_id"] == interaction.user.id:
                await self.clear_custom_role_data(interaction.guild, interaction.user.id, user_data["role_id"])

        if role_name in [role.name for role in interaction.guild.roles]:
            await interaction.followup.send("You can't use this role name. Try another one.")
            return

        actual_color_code = int(re.findall(
            r"^#((?:[0-9a-fA-F]{3}){1,2})$", color_code)[0], base=16)
        discord_colour = discord.Colour(actual_color_code)

        if role_icon:
            if not "ROLE_ICONS" in interaction.guild.features:
                await interaction.followup.send("This server doesn't have enough boosts to use custom role icons.")
                return
            display_icon = await role_icon.read()
            custom_role = await interaction.guild.create_role(name=role_name, colour=discord_colour, display_icon=display_icon)
        else:
            custom_role = await interaction.guild.create_role(name=role_name, colour=discord_colour)

        positions = {custom_role: reference_role.position - 1}
        await interaction.guild.edit_role_positions(positions)
        await interaction.user.add_roles(custom_role)
        await self.bot.RUN(SET_CUSTOM_ROLE_SQL, (interaction.guild.id, interaction.user.id, custom_role.id, role_name))
        await interaction.followup.send(f"Created your custom role: {custom_role.mention}")

    @discord.app_commands.command(name="delete_custom_role", description="Remove a custom role from yourself.")
    @discord.app_commands.guild_only()
    async def delete_custom_role(self, interaction: discord.Interaction):
        await interaction.response.defer()
        custom_role_data = await self.get_custom_roles(interaction.guild.id)
        for user_data in custom_role_data:
            if user_data["user_id"] == interaction.user.id:
                await self.clear_custom_role_data(interaction.guild, interaction.user.id, user_data["role_id"])
                await interaction.followup.send("Deleted your custom role.")
                return

        await interaction.followup.send("You don't seem to have a custom role.")

    @discord.app_commands.command(name="_create_custom_role_settings", description="Set up custom role settings.")
    @discord.app_commands.guild_only()
    @discord.app_commands.default_permissions(administrator=True)
    async def create_custom_role_settings(self, interaction: discord.Interaction, reference_role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        view_object = discord.ui.View()
        role_select = discord.ui.RoleSelect(min_values=1, max_values=10)

        async def role_select_callback(select_interaction: discord.Interaction):
            await select_interaction.response.defer()
            allowed_roles = [select_interaction.guild.get_role(
                int(role_id)) for role_id in select_interaction.data["values"]]
            allowed_role_string = ",".join(select_interaction.data["values"])

            custom_role_settings = await self.get_custom_role_settings(select_interaction.guild.id)
            if custom_role_settings:
                await self.bot.RUN(DELETE_CUSTOM_ROLE_SETTINGS_SQL, (select_interaction.guild.id,))
            await self.bot.RUN(SET_CUSTOM_ROLE_SETTINGS_SQL, (select_interaction.guild.id, allowed_role_string, reference_role.id, reference_role.name))
            await select_interaction.followup.send(f"Set up custom roles.\nRoles allowed: {', '.join([role.mention for role in allowed_roles])}\nReference role: {reference_role.mention}")

        role_select.callback = role_select_callback
        view_object.add_item(role_select)
        await interaction.followup.send("Select the roles that are allowed to create custom roles.", view=view_object)

    @tasks.loop(minutes=200)
    async def strip_roles(self):
        for guild in self.bot.guilds:
            custom_role_settings = await self.get_custom_role_settings(guild.id)

            if not custom_role_settings:
                continue

            allowed_role_ids = [
                int(role_id) for role_id in custom_role_settings["allowed_roles"].split(",")]
            custom_role_data = await self.get_custom_roles(guild.id)

            for user_data in custom_role_data:
                member = guild.get_member(user_data["user_id"])
                if not member:
                    await self.clear_custom_role_data(guild, user_data["user_id"], user_data["role_id"])
                    print(
                        f"CUSTOM ROLE: Removed custom role from {str(member)}.")
                    continue

                if not any(role.id in allowed_role_ids for role in member.roles):
                    await self.clear_custom_role_data(guild, user_data["user_id"], user_data["role_id"])
                    print(
                        f"CUSTOM ROLE: Removed custom role from {str(member)}.")
                    continue

                role = guild.get_role(user_data["role_id"])

                if not role:
                    await self.clear_custom_role_data(guild, user_data["user_id"], user_data["role_id"])
                    print(
                        f"CUSTOM ROLE: Removed custom role from {str(member)}.")
                    continue

                if not role.members:
                    role_name = user_data["role_name"]
                    await self.clear_custom_role_data(guild, user_data["user_id"], user_data["role_id"])
                    print(f"CUSTOM ROLE: Deleted role {role_name}.")
                    continue


async def setup(bot):
    await bot.add_cog(CustomRole(bot))
