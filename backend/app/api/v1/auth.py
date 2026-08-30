"""휴대폰 OTP, 카카오 OAuth, JWT 발급. 구현: T3. 참조: docs/reference/api-spec.md#인증

카카오 최초 로그인 처리(사용자 확정 방침): users.phone_e164가 NOT NULL이라 카카오 프로필만으로는
계정을 만들 수 없다. kakao_sub가 아직 없는 유저가 콜백에 들어오면 401 + kakao_link_token을 내려주고,
프론트가 OTP 화면으로 유도해 phone/verify에 그 토큰을 같이 실어 보내면 그때 계정에 연결한다.
즉 계정 생성은 언제나 SMS OTP를 거친다 - 카카오는 이미 phone 인증된 계정에 연결하는 로그인 편의 기능.
"""

import logging
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import ratelimit
from app.core.security import (
    TokenError,
    create_access_token,
    create_kakao_link_token,
    create_refresh_token,
    decode_token,
)
from app.db.models import User
from app.deps import get_db, get_redis
from app.schemas.auth import PhoneRequestIn, PhoneRequestOut, PhoneVerifyIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def _send_otp_sms(phone: str, code: str) -> None:
    if not settings.sms_api_key:
        logger.warning("[DEV-ONLY] OTP for %s = %s (SMS_API_KEY 미설정 - 실제 발송 안 함)", phone, code)
        return
    # ponytail: SMS 벤더 미정(알림톡 대행사 선정과 같은 미결 항목, docs/PROGRESS.md 참고).
    # 벤더 정해지면 여기서 실제 HTTP 호출로 교체.
    raise NotImplementedError("SMS_API_KEY is set but no SMS vendor is wired yet")


def _session_response(user: User, status_code: int = 200) -> JSONResponse:
    access = create_access_token(str(user.id))
    refresh = create_refresh_token(str(user.id))
    resp = JSONResponse(status_code=status_code, content={"access": access})
    resp.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh,
        max_age=settings.jwt_refresh_ttl,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )
    return resp


@router.post("/phone/request", response_model=PhoneRequestOut)
async def phone_request(body: PhoneRequestIn, redis: Redis = Depends(get_redis)) -> PhoneRequestOut:
    allowed = await ratelimit.check_otp_request_rate_limit(redis, body.phone)
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "요청이 너무 잦습니다. 잠시 후 다시 시도하세요.")

    challenge_id = uuid.uuid4().hex
    code = ratelimit.generate_otp_code()
    await ratelimit.create_otp_challenge(redis, challenge_id, body.phone, code)
    _send_otp_sms(body.phone, code)
    return PhoneRequestOut(challenge_id=challenge_id)


@router.post("/phone/verify")
async def phone_verify(
    body: PhoneVerifyIn,
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    phone = await ratelimit.verify_otp_challenge(redis, body.challenge_id, body.code)
    if phone is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "인증번호가 올바르지 않거나 만료되었습니다.")

    kakao_sub: str | None = None
    kakao_nickname: str | None = None
    if body.kakao_link_token:
        try:
            link_payload = decode_token(body.kakao_link_token, expected_type="kakao_link")
        except TokenError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "카카오 연동 정보가 만료되었습니다. 다시 시도하세요."
            ) from exc
        kakao_sub = link_payload["kakao_sub"]
        kakao_nickname = link_payload.get("nickname")

    now = datetime.now(UTC)
    result = await db.execute(select(User).where(User.phone_e164 == phone))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            phone_e164=phone,
            phone_verified_at=now,
            display_name=kakao_nickname or phone,
            last_seen_at=now,
            kakao_sub=kakao_sub,
        )
        db.add(user)
    else:
        user.phone_verified_at = now
        user.last_seen_at = now
        if kakao_sub and user.kakao_sub != kakao_sub:
            conflict = await db.execute(select(User).where(User.kakao_sub == kakao_sub))
            other = conflict.scalar_one_or_none()
            if other is not None and other.id != user.id:
                raise HTTPException(status.HTTP_409_CONFLICT, "이미 다른 계정에 연동된 카카오 계정입니다.")
            user.kakao_sub = kakao_sub

    await db.commit()
    await db.refresh(user)
    return _session_response(user)


@router.get("/kakao/callback")
async def kakao_callback(code: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    if not settings.kakao_client_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "카카오 로그인이 설정되지 않았습니다.")

    token_data = {
        "grant_type": "authorization_code",
        "client_id": settings.kakao_client_id,
        "redirect_uri": settings.kakao_redirect_uri,
        "code": code,
    }
    if settings.kakao_client_secret:
        token_data["client_secret"] = settings.kakao_client_secret

    async with httpx.AsyncClient(timeout=10.0) as client:
        token_res = await client.post("https://kauth.kakao.com/oauth/token", data=token_data)
        if token_res.status_code != 200:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "카카오 인증 코드가 유효하지 않습니다.")
        kakao_access_token = token_res.json()["access_token"]

        profile_res = await client.get(
            "https://kapi.kakao.com/v2/user/me",
            headers={"Authorization": f"Bearer {kakao_access_token}"},
        )
        if profile_res.status_code != 200:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "카카오 프로필 조회에 실패했습니다.")
        profile = profile_res.json()

    kakao_sub = str(profile["id"])
    nickname = (profile.get("kakao_account") or {}).get("profile", {}).get("nickname") or (
        profile.get("properties") or {}
    ).get("nickname")

    result = await db.execute(select(User).where(User.kakao_sub == kakao_sub))
    user = result.scalar_one_or_none()

    if user is None:
        link_token = create_kakao_link_token(kakao_sub, nickname)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"need_phone_link": True, "kakao_link_token": link_token},
        )

    user.last_seen_at = datetime.now(UTC)
    await db.commit()
    return _session_response(user)


@router.post("/refresh")
async def refresh_session(
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    if refresh_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "리프레시 토큰이 없습니다.")
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "리프레시 토큰이 유효하지 않습니다.") from exc

    jti = payload["jti"]
    if await ratelimit.is_refresh_jti_denylisted(redis, jti):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "이미 로그아웃되었거나 갱신된 토큰입니다.")

    try:
        user_id = uuid.UUID(payload["sub"])
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "리프레시 토큰이 유효하지 않습니다.") from exc

    user = await db.get(User, user_id)
    if user is None or user.status != "ACTIVE":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "사용자를 찾을 수 없습니다.")

    # 재사용 방지: 회전 즉시 이전 refresh를 폐기
    remaining = payload["exp"] - int(datetime.now(UTC).timestamp())
    await ratelimit.denylist_refresh_jti(redis, jti, remaining)

    user.last_seen_at = datetime.now(UTC)
    await db.commit()
    return _session_response(user)


@router.post("/logout")
async def logout(
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    redis: Redis = Depends(get_redis),
) -> JSONResponse:
    resp = JSONResponse(content={"status": "ok"})
    if refresh_token:
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
            remaining = payload["exp"] - int(datetime.now(UTC).timestamp())
            await ratelimit.denylist_refresh_jti(redis, payload["jti"], remaining)
        except TokenError:
            pass  # 이미 만료/무효한 토큰이면 재사용도 불가능하니 조용히 넘어간다
    resp.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
    return resp
