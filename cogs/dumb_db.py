import os
import gzip
import shutil
import tempfile
import discord
import asyncio
from discord.ext import commands
from lib.bot import TMWBot

PATH_TO_DB = os.getenv("PATH_TO_DB", "data/db.sqlite3")


def create_temporary_gzip_file():
    temp_file_path = os.path.join(tempfile.gettempdir(), "db.sqlite3.gz")
    with open(PATH_TO_DB, 'rb') as f_in:
        with gzip.open(temp_file_path, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    return temp_file_path


class DatabasePoster(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot

    @discord.app_commands.command(name="post_db", description="Gzip the database file and post it to the channel.")
    async def post_db(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            temp_file_path = await asyncio.to_thread(create_temporary_gzip_file)
            await interaction.followup.send(file=discord.File(temp_file_path, filename="db.sqlite3.gz"))
        except Exception as e:
            raise
        finally:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)


async def setup(bot: TMWBot):
    await bot.add_cog(DatabasePoster(bot))
