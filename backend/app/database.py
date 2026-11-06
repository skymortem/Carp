from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Создать схему напрямую из моделей.

    Приложение этим больше не пользуется: схема живёт в alembic/versions
    и применяется app.migrations.run_migrations() при старте. Функция
    оставлена для тестов и разовых скриптов, где миграции избыточны.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)