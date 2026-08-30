# API 스펙 참조 (T3, T4, T6, T9, T13에서 사용)

원본: `docs/라스트레터_MVP_구현설계서.md` §4-2. 실제 구현 중 경로/필드가 바뀌면 **코드가 진실**이며,
이 파일은 새 엔드포인트를 추가하거나 계약을 다시 확인할 때만 참고한다.

인증 방식: `Authorization: Bearer <access_jwt>` (15분 만료) + Refresh 쿠키(HttpOnly, `Secure`,
`SameSite=Lax`, `Path=/api/v1/auth`, 30일). **refresh는 바디에 담지 않고 항상 쿠키로만 나간다** —
JS가 읽을 수 없어야 XSS로 탈취되지 않는다. `COOKIE_SECURE=false`(.env)로 로컬 http 테스트 가능.

## 인증 (T3 구현 완료, 아래는 실제 계약)

```
POST /api/v1/auth/phone/request   { phone(E.164) } → 200 { challenge_id }
                                   → 429 (같은 번호로 1시간에 5회 초과)

POST /api/v1/auth/phone/verify    { challenge_id, code, kakao_link_token? }
                                   → 200 { access } + Set-Cookie refresh_token
                                   → 400 (코드 오류/만료, kakao_link_token 만료)
                                   → 409 (kakao_link_token의 kakao_sub가 이미 다른 계정에 연동됨)
                                   ※ 이 번호로 첫 로그인이면 계정을 새로 만든다(display_name은
                                     카카오 닉네임 있으면 그걸로, 없으면 phone 그대로 - 나중에 수정 가능)

GET  /api/v1/auth/kakao/callback  ?code=
                                   → 200 { access } + Set-Cookie refresh_token   (기존 연동 계정)
                                   → 401 { need_phone_link: true, kakao_link_token }  (신규 카카오 유저)
                                   → 503 (KAKAO_CLIENT_ID 미설정)

POST /api/v1/auth/refresh         쿠키의 refresh_token 검증 후 access+refresh 둘 다 회전 발급.
                                   이전 refresh는 즉시 재사용 불가(Redis denylist). → 401 무효/재사용/만료

POST /api/v1/auth/logout          쿠키의 refresh_token을 denylist에 넣고 쿠키 삭제. 토큰 없어도 200.
```

**카카오 가입 정책 (설계 시 확정)**: `users.phone_e164`가 NOT NULL이라 카카오 프로필만으로는 계정을
만들 수 없다(카카오에서 전화번호를 받으려면 비즈니스 앱 심사 필요 — 사업자등록 미결 항목과 같은 문제).
그래서 계정 생성은 **항상 SMS OTP를 거친다**: 카카오 최초 로그인은 `kakao_link_token`(10분 만료 JWT)만
내려주고, 그 토큰을 들고 `phone/verify`를 통과해야 그 순간 계정에 `kakao_sub`가 연결된다. 카카오는
이미 연동된 계정의 로그인 편의 기능일 뿐, 단독 가입 경로가 아니다.

**OTP 저장**: `db-schema.md`에 별도 테이블 없음 - Redis TTL(5분)로만 관리한다(`ratelimit.py`). 코드
5회 오답이면 challenge 폐기, 같은 번호로 1시간 5회 초과 요청이면 429.

**SMS 발송 벤더 미정**: `SMS_API_KEY` 비어있으면(로컬/기본값) 인증번호를 서버 로그로만 남기고 실제
발송은 안 한다. 키가 채워졌는데 벤더 연동이 없으면 `NotImplementedError` — 알림톡 대행사 선정(T8
미결 항목)과 같이 정해질 예정이라 미리 임의 벤더 API를 만들어두지 않았다.

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
