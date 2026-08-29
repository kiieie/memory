# API 스펙 참조 (T3, T4, T6, T9, T13에서 사용)

원본: `docs/라스트레터_MVP_구현설계서.md` §4-2. 실제 구현 중 경로/필드가 바뀌면 **코드가 진실**이며,
이 파일은 새 엔드포인트를 추가하거나 계약을 다시 확인할 때만 참고한다.

인증 방식: `Authorization: Bearer <access_jwt>` (15분 만료) + Refresh 쿠키(HttpOnly, 30일).

## 인증

```
POST /api/v1/auth/phone/request      { phone } → { challenge_id }
POST /api/v1/auth/phone/verify       { challenge_id, code } → { access, refresh }
GET  /api/v1/auth/kakao/callback     ?code= → 세션 발급
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
```

## 수신자

```
GET    /api/v1/recipients
POST   /api/v1/recipients            { display_name, relation, phone_e164?, email? }
PATCH  /api/v1/recipients/{id}
DELETE /api/v1/recipients/{id}       # 사용 중 캡슐 있으면 409
```

## 캡슐 (핵심)

```
GET    /api/v1/capsules?status=
POST   /api/v1/capsules              { title, trigger_type }  → DRAFT 생성
GET    /api/v1/capsules/{id}         # 본인만, 본문 복호화하여 반환
PATCH  /api/v1/capsules/{id}         { title?, body? }        # SEALED면 409
POST   /api/v1/capsules/{id}/recipients   { recipient_ids[], personal_notes{} }
PUT    /api/v1/capsules/{id}/trigger      { fire_at?, rrule?, require_confirm?, auto_defer_days? }
POST   /api/v1/capsules/{id}/seal         # 암호화 확정 + content_hash + ARMED 전이
POST   /api/v1/capsules/{id}/unseal       # 재인증(OTP) 필요
POST   /api/v1/capsules/{id}/cancel
GET    /api/v1/capsules/{id}/preview      # 수신자가 보게 될 화면 그대로 미리보기
```

## 미디어 (직접 업로드)

```
POST /api/v1/capsules/{id}/media/presign
     { filename, mime_type, size_bytes }
     → { upload_url, object_key, asset_id, expires_in }
     ※ 서버는 여기서 쿼터 검사 + MIME 화이트리스트 검사
POST /api/v1/media/{asset_id}/complete   { sha256 }  # 검증 후 COMPLETED
DELETE /api/v1/media/{asset_id}
```

업로드 흐름: 브라우저 → presign URL → Object Storage **직접 PUT**. VM은 파일 바이트를 절대 거치지 않는다
(12GB RAM 박스에서 영상을 프록시하면 죽는다).

## 여명형 확인

```
GET  /api/v1/capsules/{id}/confirmation      # D-7 알림에서 진입
POST /api/v1/capsules/{id}/confirmation      { action: PROCEED|DEFER|CANCEL, defer_days? }
```

## 수신자 열람 (비로그인, 토큰 기반)

```
GET  /api/v1/view/{token}            → { sender_name, sent_reason, sealed_at, has_media }
                                       ※ 본문은 아직 안 줌
POST /api/v1/view/{token}/accept     → { body, media[] (presigned GET) }
POST /api/v1/view/{token}/decline    { scope: THIS|ALL_FROM_SENDER }
POST /api/v1/view/{token}/later      → 30일 뒤 재알림
```

이 4개 엔드포인트가 2단계 동의(존재만 알림 → accept 후 본문 공개)를 구현한다. `GET`이 본문을 절대 포함하지 않게 하는 것이 이 설계의 핵심이다.

## C형: 유족 조회

```
POST /api/v1/claims/lookup      { name, birth_date, phone_last4 }
     → { has_result: null }    ※ 항상 null 반환. 결과는 심사 통과 후 통보
POST /api/v1/claims             { claimant 정보, evidence presign 업로드 후 key }
GET  /api/v1/claims/{id}/status
```

`lookup`이 즉시 "있음/없음"을 반환하면 그 자체가 정보 유출이다. **무조건 심사 후 비동기 통보**로 구현할 것 — 동기 응답에 결과를 담는 구현은 리뷰에서 반려 대상이다.

## 관리자

```
GET   /api/v1/admin/claims?status=SUBMITTED
POST  /api/v1/admin/claims/{id}/approve   { note }
POST  /api/v1/admin/claims/{id}/reject    { reason }
GET   /api/v1/admin/deliveries?status=FAILED
POST  /api/v1/admin/deliveries/{id}/retry
```

관리자 엔드포인트 어디에도 캡슐 본문을 반환하는 응답 필드를 넣지 않는다. 심사 대상은 증명서와 메타데이터뿐이다.
