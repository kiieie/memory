"""JWT 발급/검증(access 15분 + refresh 30일). 구현: T3. 참조: docs/reference/api-spec.md#인증

토큰 종류 3개, `type` 클레임으로 구분:
- access:     { sub, type=access, iat, exp }              — API 인증
- refresh:    { sub, type=refresh, jti, iat, exp }         — /auth/refresh, HttpOnly 쿠키로만 전달
- kakao_link: { kakao_sub, nickname, type=kakao_link, exp } — 카카오 최초 로그인 시 OTP 화면으로 넘기는 임시 토큰
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.config import settings

ALGORITHM = "HS256"
KAKAO_LINK_TTL_SECONDS = 600  # 10분


class TokenError(Exception):
    """토큰이 없거나(만료/서명불일치/타입불일치) 신뢰할 수 없을 때."""


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(user_id: str) -> str:
    now = _now()
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_access_ttl),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    now = _now()
    payload = {
        "sub": user_id,
        "type": "refresh",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_refresh_ttl),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def create_kakao_link_token(kakao_sub: str, nickname: str | None) -> str:
    now = _now()
    payload = {
        "kakao_sub": kakao_sub,
        "nickname": nickname,
        "type": "kakao_link",
        "iat": now,
        "exp": now + timedelta(seconds=KAKAO_LINK_TTL_SECONDS),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != expected_type:
        raise TokenError(f"expected token type={expected_type!r}, got {payload.get('type')!r}")
    return payload
