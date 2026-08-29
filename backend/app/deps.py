"""DI: db 세션 등. current_user 의존성은 T3(인증)에서 추가."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session
