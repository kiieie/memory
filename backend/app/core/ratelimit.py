"""OTP 요청/로그인 시도 rate limit + OTP challenge 저장. 구현: T3.

DB에 별도 테이블(db-schema.md엔 없음)을 추가하지 않고 Redis만 쓴다 — OTP는 원래 짧게 살다 죽는
데이터라 TTL 네이티브인 Redis가 자연스럽고, 이미 스택에 있는 인프라라 마이그레이션이 안 늘어난다.
"""

import hashlib
import json
import secrets

from redis.asyncio import Redis

OTP_TTL_SECONDS = 300  # 인증번호 유효 5분
OTP_MAX_ATTEMPTS = 5  # 틀린 시도 5회면 challenge 폐기 (브루트포스 방지)
OTP_REQUEST_LIMIT = 5  # 같은 번호로 1시간에 5번까지만 요청 허용
OTP_REQUEST_WINDOW_SECONDS = 3600

REFRESH_DENYLIST_PREFIX = "auth:refresh:denylist:"


def generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_code(challenge_id: str, code: str) -> str:
    # challenge_id를 섞어서 코드 하나가 다른 challenge에 재사용되지 않게 한다.
    return hashlib.sha256(f"{challenge_id}:{code}".encode()).hexdigest()


async def check_otp_request_rate_limit(redis: Redis, phone: str) -> bool:
    """True면 허용. 한도 초과면 False (호출측에서 429)."""
    key = f"otp:ratelimit:{phone}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, OTP_REQUEST_WINDOW_SECONDS)
    return count <= OTP_REQUEST_LIMIT


async def create_otp_challenge(redis: Redis, challenge_id: str, phone: str, code: str) -> None:
    key = f"otp:challenge:{challenge_id}"
    value = json.dumps({"phone": phone, "code_hash": _hash_code(challenge_id, code), "attempts": 0})
    await redis.set(key, value, ex=OTP_TTL_SECONDS)


async def verify_otp_challenge(redis: Redis, challenge_id: str, code: str) -> str | None:
    """성공하면 phone 반환. 코드 불일치/만료/시도초과면 None (호출측에서 400)."""
    key = f"otp:challenge:{challenge_id}"
    raw = await redis.get(key)
    if raw is None:
        return None
    data = json.loads(raw)

    if _hash_code(challenge_id, code) == data["code_hash"]:
        await redis.delete(key)  # 1회용 - 성공하든 실패하든 재사용 금지
        return data["phone"]

    data["attempts"] += 1
    if data["attempts"] >= OTP_MAX_ATTEMPTS:
        await redis.delete(key)
    else:
        ttl = await redis.ttl(key)
        await redis.set(key, json.dumps(data), ex=ttl if ttl and ttl > 0 else OTP_TTL_SECONDS)
    return None


async def denylist_refresh_jti(redis: Redis, jti: str, ttl_seconds: int) -> None:
    """logout/rotation 시 이 refresh 토큰을 재사용 못 하게 막는다. TTL 지나면 Redis가 알아서 지운다."""
    if ttl_seconds > 0:
        await redis.set(f"{REFRESH_DENYLIST_PREFIX}{jti}", "1", ex=ttl_seconds)


async def is_refresh_jti_denylisted(redis: Redis, jti: str) -> bool:
    return bool(await redis.exists(f"{REFRESH_DENYLIST_PREFIX}{jti}"))
