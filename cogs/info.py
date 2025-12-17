from typing import cast

import discord
import yaml
from discord.ext import commands

from lib.bot import TMWBot

INFO_COMMANDS_PATH = "config/info_commands.yml"


def load_info_commands() -> dict[str, str]:
    with open(INFO_COMMANDS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise TypeError(
            "Info commands file must contain a dictionary at the top level."
        )

    return cast(dict[str, str], data)


info_commands = load_info_commands()


async def info_autocomplete(interaction: discord.Interaction, current: str):
    if not current:
        return [
            discord.app_commands.Choice(name=key, value=key)
            for key in info_commands.keys()
        ][:25]
    else:
        return [
            discord.app_commands.Choice(name=key, value=key)
            for key in info_commands.keys()
            if current.lower() in key.lower()
        ][:25]


class InfoCommand(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot

    @discord.app_commands.command(
        name="info", description="Get various pieces of valuable knowledge!"
    )
    @discord.app_commands.describe(info_key="The topic.")
    @discord.app_commands.autocomplete(info_key=info_autocomplete)
    async def info(self, interaction: discord.Interaction, info_key: str):
        text_info = info_commands.get(info_key)

        if not text_info:
            await interaction.response.send_message(
                content=f"No information found for `{info_key}`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Info for `{info_key}`",
            description=text_info,
            color=discord.Color.random(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: TMWBot):
    await bot.add_cog(InfoCommand(bot))
