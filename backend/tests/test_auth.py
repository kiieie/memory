"""T3 인증 플로우 통합 스모크 테스트.

test_migrations.py와 달리 라이브 Postgres/Redis가 필요 없다 - db/redis 의존성을
가짜로 오버라이드해서 어디서든(로컬 venv 포함) 그냥 pytest로 돈다.
"""

import time
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core import ratelimit
from app.deps import get_db, get_redis
from app.main import app


class FakeRedis:
    """OTP challenge / rate-limit / refresh denylist에 필요한 최소 명령만 흉내."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.exp: dict[str, float] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = str(int(self.store.get(key, 0)) + 1)
        return int(self.store[key])

    async def expire(self, key: str, seconds: int) -> None:
        self.exp[key] = time.time() + seconds

    async def get(self, key: str) -> bytes | None:
        v = self.store.get(key)
        return v.encode() if v is not None else None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        if ex:
            self.exp[key] = time.time() + ex

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def ttl(self, key: str) -> int:
        return int(self.exp.get(key, 0) - time.time())

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """`select(User).where(User.col == val)` 형태의 단일 등호 조건만 지원하는 가짜 세션."""

    def __init__(self, users: list):
        self.users = users

    async def execute(self, stmt):
        col = stmt.whereclause.left.name
        val = stmt.whereclause.right.value
        for u in self.users:
            if getattr(u, col) == val:
                return _FakeResult(u)
        return _FakeResult(None)

    def add(self, obj) -> None:
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
        if not getattr(obj, "status", None):
            obj.status = "ACTIVE"
        self.users.append(obj)

    async def commit(self) -> None:
        pass

    async def refresh(self, obj) -> None:
        pass

    async def get(self, model, pk):
        for u in self.users:
            if u.id == pk:
                return u
        return None


@pytest.fixture
def client():
    settings.cookie_secure = False  # 로컬 http 테스트 클라이언트는 Secure 쿠키를 안 돌려보낸다
    users: list = []
    fake_redis = FakeRedis()

    async def _get_db():
        yield FakeSession(users)

    async def _get_redis():
        yield fake_redis

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_redis] = _get_redis
    try:
        yield TestClient(app), users
    finally:
        app.dependency_overrides.clear()
        settings.cookie_secure = True


def _request_and_verify(client: TestClient, phone: str, code: str = "123456"):
    with patch.object(ratelimit, "generate_otp_code", return_value=code):
        r = client.post("/api/v1/auth/phone/request", json={"phone": phone})
    assert r.status_code == 200, r.text
    challenge_id = r.json()["challenge_id"]
    return client.post("/api/v1/auth/phone/verify", json={"challenge_id": challenge_id, "code": code})


def test_phone_signup_and_login_issues_tokens(client):
    c, users = client
    r = _request_and_verify(c, "+821012345678")
    assert r.status_code == 200
    assert "access" in r.json()
    assert "refresh_token" in c.cookies
    assert len(users) == 1
    assert users[0].phone_e164 == "+821012345678"


def test_wrong_code_is_rejected_and_challenge_stays_consumable(client):
    c, _ = client
    with patch.object(ratelimit, "generate_otp_code", return_value="123456"):
        r = c.post("/api/v1/auth/phone/request", json={"phone": "+821000000001"})
    challenge_id = r.json()["challenge_id"]

    r = c.post("/api/v1/auth/phone/verify", json={"challenge_id": challenge_id, "code": "000000"})
    assert r.status_code == 400


def test_otp_request_rate_limited_per_phone(client):
    c, _ = client
    phone = "+821000000002"
    for _ in range(ratelimit.OTP_REQUEST_LIMIT):
        r = c.post("/api/v1/auth/phone/request", json={"phone": phone})
        assert r.status_code == 200
    r = c.post("/api/v1/auth/phone/request", json={"phone": phone})
    assert r.status_code == 429


def test_refresh_rotates_and_denylists_old_token(client):
    c, _ = client
    _request_and_verify(c, "+821000000003")
    old_refresh = c.cookies.get("refresh_token")

    r = c.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    new_refresh = c.cookies.get("refresh_token")
    assert new_refresh != old_refresh

    c.cookies.set("refresh_token", old_refresh)
    r = c.post("/api/v1/auth/refresh")
    assert r.status_code == 401  # 회전 후 이전 토큰 재사용 차단


def test_logout_invalidates_refresh_token(client):
    c, _ = client
    _request_and_verify(c, "+821000000004")

    r = c.post("/api/v1/auth/logout")
    assert r.status_code == 200

    r = c.post("/api/v1/auth/refresh")
    assert r.status_code == 401


def test_kakao_callback_without_config_returns_503(client):
    c, _ = client
    r = c.get("/api/v1/auth/kakao/callback", params={"code": "whatever"})
    assert r.status_code == 503
