
import discord
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from lib.bot import TMWBot
from lib.models import User


async def _upsert_username(bot: TMWBot, user_id: int, user_name: str) -> None:
    async with bot.session_factory() as session:
        async with session.begin():
            stmt = sqlite_insert(User).values(
                discord_user_id=user_id,
                user_name=user_name,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["discord_user_id"],
                set_={"user_name": stmt.excluded.user_name},
            )
            await session.execute(stmt)


async def _get_username_from_db(bot: TMWBot, user_id: int) -> str | None:
    async with bot.session_factory() as session:
        result = await session.execute(
            select(User.user_name).where(User.discord_user_id == user_id)
        )
        return result.scalar_one_or_none()


async def get_username_db(bot: TMWBot, user_id: int) -> str:
    user = bot.get_user(user_id)
    if user:
        await _upsert_username(bot, user.id, user.display_name)
        return user.display_name

    user_name = await _get_username_from_db(bot, user_id)

    if user_name:
        return user_name

    try:
        user = await bot.fetch_user(user_id)
    except (discord.NotFound, discord.HTTPException):
        user = None

    if user:
        await _upsert_username(bot, user.id, user.display_name)
        return user.display_name
    else:
        return "Unknown User"
