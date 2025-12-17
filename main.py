import asyncio
import os

import discord
from dotenv import load_dotenv

from lib.bot import TMWBot

load_dotenv()

discord.utils.setup_logging()

COMMAND_PREFIX = os.getenv("COMMAND_PREFIX") or "!!!"
TOKEN = os.getenv("TOKEN")
PATH_TO_DB = os.getenv("PATH_TO_DB")


my_bot = TMWBot(command_prefix=COMMAND_PREFIX, path_to_db=PATH_TO_DB)


async def main():
    if not TOKEN:
        raise ValueError("TOKEN environment variable is not set.")

    await my_bot.load_cogs()
    await my_bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
