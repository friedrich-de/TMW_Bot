from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db import Base


class User(Base):
    __tablename__ = "usernames"
    discord_user_id: Mapped[int] = mapped_column(primary_key=True)
    user_name: Mapped[str]


class UserRanks(Base):
    __tablename__ = "user_ranks"

    guild_id: Mapped[int] = mapped_column(primary_key=True)
    discord_user_id: Mapped[int] = mapped_column(primary_key=True)
    role_ids: Mapped[str] = mapped_column(Text, nullable=False, default="")


class CustomRoleEntry(Base):
    __tablename__ = "custom_roles"

    guild_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(primary_key=True)
    role_id: Mapped[int]
    role_name: Mapped[str | None]


class StickyMessage(Base):
    __tablename__ = "sticky_messages"

    guild_id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(primary_key=True)
    original_message_id: Mapped[int]
    stickied_message_id: Mapped[int | None]


def register_models() -> None:
    # Import side-effects register models on Base.metadata.
    # This function exists to make registration explicit.
    return None
