from sqlalchemy import event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import ConnectionPoolEntry


class Base(DeclarativeBase):
    pass


def get_db_engine(path_to_db: str) -> AsyncEngine:
    db_url = f"sqlite+aiosqlite:///{path_to_db}"
    engine = create_async_engine(db_url)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(  # pyright: ignore[reportUnusedFunction]
            dbapi_connection: DBAPIConnection,
            connection_record: ConnectionPoolEntry) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.close()

    return engine
