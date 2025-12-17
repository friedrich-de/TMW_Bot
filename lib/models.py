from sqlalchemy.orm import Mapped, mapped_column

from lib.db import Base


class User(Base):
    __tablename__ = "users"
    discord_user_id: Mapped[int] = mapped_column(primary_key=True)
    user_name: Mapped[str]


def register_models() -> None:
    pass
