"""T3 인증 요청/응답 스키마. 참조: docs/reference/api-spec.md#인증"""

from pydantic import BaseModel, Field

# E.164: '+' + 국가코드 포함 최대 15자리
_E164_PATTERN = r"^\+[1-9]\d{7,14}$"


class PhoneRequestIn(BaseModel):
    phone: str = Field(..., pattern=_E164_PATTERN)


class PhoneRequestOut(BaseModel):
    challenge_id: str


class PhoneVerifyIn(BaseModel):
    challenge_id: str
    code: str = Field(..., pattern=r"^\d{6}$")
    # 카카오 최초 로그인 직후 넘어온 경우에만 채움 - 인증 성공 시 이 계정에 kakao_sub 연결
    kakao_link_token: str | None = None


class KakaoLinkPendingOut(BaseModel):
    need_phone_link: bool = True
    kakao_link_token: str
