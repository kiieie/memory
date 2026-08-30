"""DI: db 세션, redis, current_user(T3)."""

import uuid
from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import TokenError, decode_token
from app.db.models import User
from app.db.session import async_session

_bearer = HTTPBearer(auto_error=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


async def get_redis() -> AsyncIterator[aioredis.Redis]:
    client = aioredis.from_url(settings.redis_url)
    try:
        yield client
    finally:
        await client.aclose()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, ValueError, KeyError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token") from exc

    user = await db.get(User, user_id)
    if user is None or user.status != "ACTIVE":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found or inactive")
    return user
