# 라스트레터 MVP 구현 설계서 v0.1

> 대상 범위: **A(예약형) + B(여명형)** 필수, **C(유족 조회형)** 골격 포함
> 인프라: OCI Always Free (Ampere A1, ARM64) + 별도 GPU 서버(외부)
> 목적: Claude Code에 그대로 넘겨 구현 착수 가능한 수준의 설계

---

## 0. 확정된 기술 결정 (Decision Log)

| # | 항목 | 결정 | 근거 |
|---|---|---|---|
| D1 | 백엔드 | Python 3.12 + FastAPI | 아키텍처 친숙도, AI 서버 연동 용이 |
| D2 | DB | PostgreSQL 16 (컨테이너 자체 운영) | Autonomous DB(Oracle) 대신. 락인 회피, 로컬 개발 동일성 |
| D3 | 큐/스케줄 | Redis + RQ (또는 arq) | Celery는 이 규모에 과함 |
| D4 | 프론트 | Next.js 15 App Router + TS + Tailwind | SEO(랜딩) + 앱 화면 동시 처리 |
| D5 | 파일 저장 | OCI Object Storage (S3 호환 API, boto3) | 브라우저 → 스토리지 직접 업로드(Presigned) |
| D6 | 배포 | Docker Compose, **linux/arm64** | Ampere A1은 ARM. amd64 이미지 쓰면 안 뜸 |
| D7 | 리버스 프록시 | Caddy (자동 TLS) | nginx+certbot 대비 설정 1/10 |
| D8 | AI 추론 | 외부 GPU 서버, **OpenAI 호환 API(vLLM)** | 모델 교체 시 백엔드 무수정 |
| D9 | 인증 | 카카오 OAuth + SMS OTP (MVP) → 본인확인(PASS) 정식 연동은 Phase 2 | 초기 비용 절감 |
| D10 | 발송 채널 | 카카오 알림톡(대행사 API) 주, SMS 폴백, 이메일 보조 | 도달률 |

---

## 1. 인프라 설계 (OCI)

### 1-1. Always Free 자원 현황 (2026년 기준, **반드시 재확인**)

주의할 변경사항이 있습니다. <cite index="22-1">Oracle이 Always Free Ampere A1 할당량을 4 OCPU / 24GB RAM에서 2 OCPU / 12GB RAM으로 축소했고, 이는 2026년 6월 15일부로 적용됐습니다.</cite> <cite index="25-1">기존 한도를 초과한 인스턴스는 2026년 8월 18일 이후 종료 대상이라는 안내 메일이 발송됐습니다.</cite> <cite index="23-1">블록 스토리지 200GB, 아웃바운드 대역폭 월 10TB, AMD 마이크로 인스턴스 2대는 유지되며, 무료 티어에 GPU는 포함되지 않습니다.</cite>

→ **설계 전제를 2 OCPU / 12GB로 잡습니다.** 이 정도면 MVP는 충분합니다. 다만 Postgres + Redis + FastAPI + Next.js + Caddy를 한 박스에 올리므로 메모리 예산을 명시적으로 나눕니다.

### 1-2. 인스턴스 구성

```
[VM-1] VM.Standard.A1.Flex  2 OCPU / 12GB / Boot 100GB  ── 메인 애플리케이션
  ├─ caddy          (128MB)   :80/:443
  ├─ web (Next.js)  (512MB)   :3000
  ├─ api (FastAPI)  (1.5GB)   :8000
  ├─ worker (RQ)    (1GB)     발송/AI 잡
  ├─ scheduler      (256MB)   1분 주기 스캔
  ├─ postgres:16    (2GB)     shared_buffers=512MB
  └─ redis:7        (512MB)   maxmemory 384MB, allkeys-lru
                              여유 ~6GB (빌드/피크 대비)

[VM-2] VM.Standard.E2.1.Micro (AMD, 1GB)  ── 모니터링/백업 러너
  └─ Uptime 체크, pg_dump → Object Storage, 로그 수집

[GPU]  외부 (사용자 별도 준비)  ── vLLM + Whisper, OpenAI 호환 :8001
```

### 1-3. 스토리지 전략 (⚠️ 가장 중요한 제약)

Object Storage Always Free 한도는 **약 20GB 수준**(Standard 10GB + Infrequent/Archive 10GB)입니다. **영상 서비스에는 절대적으로 부족합니다.**

| 단계 | 정책 |
|---|---|
| MVP(파일럿 100명) | 계정당 **200MB 상한**, 영상은 720p / 3분 / 100MB 제한. 총 20GB 내 수용 |
| 베타 | Object Storage 유료 전환(GB당 월 단가 낮음). 이때부터 과금 모델 필수 |
| 정식 | 30일 미열람 콘텐츠는 Archive 티어 자동 이동(Lifecycle Policy) |

**설계 원칙: 용량 상한을 코드 레벨에서 강제**한다. `users.storage_quota_bytes` / `users.storage_used_bytes`를 두고 업로드 presign 발급 시점에 검사.

### 1-4. 네트워크/보안

- VCN Public Subnet 1개, Security List: 22(내 IP만), 80, 443만 개방
- **OS 방화벽 주의**: Oracle Linux/Ubuntu 이미지는 iptables 기본 정책이 막고 있음. `netfilter-persistent` 규칙 추가 필수 (놓치면 "포트 열었는데 안 됨" 함정)
- OCI Vault: 마스터 키(KEK) 보관. Always Free 키 개수 제한 있으므로 **KEK 1개만** 사용, DEK는 DB에 암호문으로 저장
- Object Storage는 **Private 버킷**. 모든 접근은 Presigned URL(PAR 아님, S3 호환 presign)로만

---

## 2. 도메인 모델

### 2-1. 핵심 개념

- **Capsule(캡슐)**: 전달 단위 하나. 본문 + 첨부 + 수신자 + 전달조건
- **Delivery(전달건)**: 캡슐 × 수신자 × 발송회차. 실제 발송의 최소 단위
- **Trigger(트리거)**: 캡슐이 열리는 조건. `SCHEDULED`(A형) / `PROGNOSIS`(B형) / `DEATH_CLAIM`(C형)

### 2-2. 상태 머신

**Capsule**
```
DRAFT ──seal()──> SEALED ──arm()──> ARMED ──fire()──> FIRING
                    │                  │                 │
                    └──unseal()────────┘                 ▼
                                                    COMPLETED
   any ──cancel()──> CANCELLED        ARMED ──expire()──> EXPIRED
```
- `SEALED`: 편집 잠금 + 암호화 완료. 이 시점에 콘텐츠 해시 기록(무결성 증명)
- `ARMED`: 트리거 등록 완료(스케줄 확정 또는 사망확인 대기)
- `FIRING`: Delivery 생성 및 발송 진행 중

**Delivery**
```
PENDING → NOTIFIED → (OPENED | DECLINED | EXPIRED)
   │          │
   └─ FAILED ─┘  (재시도 3회, 지수 백오프 5m/30m/3h)
```
- `DECLINED`는 **영구**. 해당 수신자에게 같은 캡슐 계열 재발송 금지
- `EXPIRED`: 알림 후 30일 미열람

### 2-3. 안전장치 (B형 필수)

여명형은 예후가 틀릴 수 있으므로 **3중 잠금**:
1. 발송 D-7 본인에게 확인 알림 → "예정대로 진행 / 연기 / 취소"
2. **무응답 시 발송 보류**(기본값 = 보류). 옵트인해야 자동 진행
3. 본인 로그인 감지 시 D-day 자동 30일 연기 (`last_seen_at` 기반)

---

## 3. 데이터베이스 스키마

```sql
-- ============ 사용자 ============
CREATE TABLE users (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  phone_e164          TEXT UNIQUE NOT NULL,
  phone_verified_at   TIMESTAMPTZ,
  email               TEXT,
  display_name        TEXT NOT NULL,
  birth_date          DATE,
  kakao_sub           TEXT UNIQUE,
  tier                TEXT NOT NULL DEFAULT 'FREE',   -- FREE/PRO
  storage_quota_bytes BIGINT NOT NULL DEFAULT 209715200,  -- 200MB
  storage_used_bytes  BIGINT NOT NULL DEFAULT 0,
  last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  status              TEXT NOT NULL DEFAULT 'ACTIVE', -- ACTIVE/DECEASED/SUSPENDED
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 동의 이력 (약관 아닌 개별 동의, 법적 증거)
CREATE TABLE consents (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,   -- POSTHUMOUS_PROCESSING / AI_TRAINING / THIRD_PARTY_CONTACT
  version     TEXT NOT NULL,   -- 'v1.0'
  granted     BOOLEAN NOT NULL,
  ip          INET,
  user_agent  TEXT,
  granted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON consents (user_id, kind, granted_at DESC);

-- ============ 수신자 ============
CREATE TABLE recipients (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  display_name TEXT NOT NULL,
  relation     TEXT,                    -- 자녀/배우자/친구/...
  phone_e164   TEXT,
  email        TEXT,
  -- 수신자가 이 발신자로부터의 모든 수신을 영구 거부한 경우
  blocked_at   TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (phone_e164 IS NOT NULL OR email IS NOT NULL)
);
CREATE INDEX ON recipients (owner_id);

-- ============ 캡슐 ============
CREATE TABLE capsules (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title          TEXT NOT NULL,
  -- 본문은 암호문으로만 저장
  body_ciphertext BYTEA,
  body_nonce      BYTEA,
  dek_wrapped     BYTEA,          -- KEK로 감싼 데이터키
  content_hash    TEXT,           -- sealed 시점 SHA-256 (무결성 증명)
  trigger_type   TEXT NOT NULL,   -- SCHEDULED / PROGNOSIS / DEATH_CLAIM
  status         TEXT NOT NULL DEFAULT 'DRAFT',
  sealed_at      TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON capsules (owner_id, status);

-- 트리거 상세 (1:1)
CREATE TABLE capsule_triggers (
  capsule_id       UUID PRIMARY KEY REFERENCES capsules(id) ON DELETE CASCADE,
  fire_at          TIMESTAMPTZ,      -- SCHEDULED/PROGNOSIS
  recurrence_rule  TEXT,             -- RFC5545 RRULE, 기일/생일 반복
  recurrence_until DATE,
  -- 여명형 안전장치
  require_confirm  BOOLEAN NOT NULL DEFAULT true,
  confirm_sent_at  TIMESTAMPTZ,
  confirmed_at     TIMESTAMPTZ,
  auto_defer_days  INT NOT NULL DEFAULT 30,   -- 본인 로그인 시 연기
  next_fire_at     TIMESTAMPTZ                -- 스케줄러가 보는 필드
);
CREATE INDEX ON capsule_triggers (next_fire_at) WHERE next_fire_at IS NOT NULL;

-- 캡슐 ↔ 수신자
CREATE TABLE capsule_recipients (
  capsule_id   UUID NOT NULL REFERENCES capsules(id) ON DELETE CASCADE,
  recipient_id UUID NOT NULL REFERENCES recipients(id) ON DELETE CASCADE,
  personal_note TEXT,
  PRIMARY KEY (capsule_id, recipient_id)
);

-- ============ 첨부 ============
CREATE TABLE media_assets (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  capsule_id    UUID NOT NULL REFERENCES capsules(id) ON DELETE CASCADE,
  object_key    TEXT NOT NULL,       -- 버킷 내 경로
  mime_type     TEXT NOT NULL,
  size_bytes    BIGINT NOT NULL,
  duration_sec  INT,
  dek_wrapped   BYTEA,
  sha256        TEXT,
  upload_status TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING/COMPLETED/FAILED
  transcript    TEXT,                -- Whisper 결과 (GPU 서버)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON media_assets (capsule_id);

-- ============ 전달 ============
CREATE TABLE deliveries (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  capsule_id    UUID NOT NULL REFERENCES capsules(id),
  recipient_id  UUID NOT NULL REFERENCES recipients(id),
  occurrence_no INT NOT NULL DEFAULT 1,   -- 반복 발송 회차
  status        TEXT NOT NULL DEFAULT 'PENDING',
  channel       TEXT,                     -- ALIMTALK/SMS/EMAIL
  access_token_hash TEXT NOT NULL,        -- 열람 링크 토큰의 해시만 저장
  token_expires_at  TIMESTAMPTZ NOT NULL,
  notified_at   TIMESTAMPTZ,
  opened_at     TIMESTAMPTZ,
  declined_at   TIMESTAMPTZ,
  retry_count   INT NOT NULL DEFAULT 0,
  idempotency_key TEXT UNIQUE NOT NULL,   -- capsule:recipient:occurrence
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON deliveries (status, created_at);

CREATE TABLE delivery_events (
  id          BIGSERIAL PRIMARY KEY,
  delivery_id UUID NOT NULL REFERENCES deliveries(id) ON DELETE CASCADE,
  event       TEXT NOT NULL,
  payload     JSONB,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============ C형: 사망 확인 ============
CREATE TABLE death_claims (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_user_id UUID REFERENCES users(id),
  -- 유족이 조회한 경우 대상자를 특정하기 위한 해시(원문 저장 안 함)
  subject_lookup_hash TEXT NOT NULL,   -- sha256(이름|생년월일|휴대폰뒷4)
  claimant_name  TEXT NOT NULL,
  claimant_phone TEXT NOT NULL,
  relation       TEXT NOT NULL,
  evidence_key   TEXT,                 -- 증명서 오브젝트 키
  evidence_type  TEXT,                 -- E_CERT(전자증명서) / SCAN
  status         TEXT NOT NULL DEFAULT 'SUBMITTED', -- SUBMITTED/VERIFYING/APPROVED/REJECTED
  reviewer_note  TEXT,
  reviewed_by    UUID,
  reviewed_at    TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON death_claims (subject_lookup_hash);

-- ============ AI 작업 ============
CREATE TABLE ai_jobs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind        TEXT NOT NULL,   -- WRITING_COACH / SAFETY_REVIEW / TRANSCRIBE
  ref_type    TEXT NOT NULL,
  ref_id      UUID NOT NULL,
  status      TEXT NOT NULL DEFAULT 'QUEUED',
  request     JSONB,
  result      JSONB,
  error       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);

-- ============ 감사 로그 (필수) ============
CREATE TABLE audit_logs (
  id         BIGSERIAL PRIMARY KEY,
  actor_type TEXT NOT NULL,   -- USER/RECIPIENT/ADMIN/SYSTEM
  actor_id   TEXT,
  action     TEXT NOT NULL,
  target     TEXT,
  ip         INET,
  detail     JSONB,
  at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**설계 노트**
- 본문은 평문으로 어디에도 없습니다. 운영자 콘솔에서도 못 봅니다. 이게 이 서비스가 파는 신뢰의 실체입니다.
- `access_token_hash`: 열람 링크가 DB 유출 시에도 재사용 불가하도록 원문 미저장
- `idempotency_key`: 스케줄러 중복 실행 시 이중 발송 방지. **부고 문자가 두 번 가면 신뢰가 끝납니다.**

---

## 4. 백엔드 설계

### 4-1. 디렉토리 구조

```
backend/
├─ app/
│  ├─ main.py                 # FastAPI 앱, 라우터 등록, 미들웨어
│  ├─ config.py               # pydantic-settings, 환경변수
│  ├─ deps.py                 # DI: db session, current_user
│  ├─ db/
│  │  ├─ base.py  session.py
│  │  └─ models/              # SQLAlchemy 2.0 ORM
│  ├─ schemas/                # Pydantic v2 요청/응답
│  ├─ api/v1/
│  │  ├─ auth.py  users.py  capsules.py  recipients.py
│  │  ├─ media.py  deliveries.py  view.py  claims.py
│  │  ├─ ai.py    admin.py
│  ├─ services/
│  │  ├─ crypto.py            # 봉투암호화
│  │  ├─ storage.py           # OCI Object Storage (S3 호환)
│  │  ├─ messaging.py         # 알림톡/SMS/이메일 어댑터
│  │  ├─ ai_client.py         # GPU 서버 클라이언트
│  │  ├─ trigger.py           # RRULE 계산, next_fire_at
│  │  └─ delivery.py          # 발송 파이프라인
│  ├─ workers/
│  │  ├─ scheduler.py         # 1분 주기 스캔
│  │  └─ tasks.py             # RQ 태스크
│  └─ core/  security.py  ratelimit.py  audit.py
├─ alembic/
├─ tests/
├─ Dockerfile                 # FROM python:3.12-slim (arm64)
└─ pyproject.toml
```

### 4-2. API 명세

인증: `Authorization: Bearer <access_jwt>` (15분) + Refresh 쿠키(HttpOnly, 30일)

#### 인증
```
POST /api/v1/auth/phone/request      { phone } → { challenge_id }
POST /api/v1/auth/phone/verify       { challenge_id, code } → { access, refresh }
GET  /api/v1/auth/kakao/callback     ?code= → 세션 발급
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
```

#### 수신자
```
GET    /api/v1/recipients
POST   /api/v1/recipients            { display_name, relation, phone_e164?, email? }
PATCH  /api/v1/recipients/{id}
DELETE /api/v1/recipients/{id}       # 사용 중 캡슐 있으면 409
```

#### 캡슐 (핵심)
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

#### 미디어 (직접 업로드)
```
POST /api/v1/capsules/{id}/media/presign
     { filename, mime_type, size_bytes }
     → { upload_url, object_key, asset_id, expires_in }
     ※ 서버는 여기서 쿼터 검사 + MIME 화이트리스트 검사
POST /api/v1/media/{asset_id}/complete   { sha256 }  # 검증 후 COMPLETED
DELETE /api/v1/media/{asset_id}
```
업로드 흐름: 브라우저 → presign URL → Object Storage **직접 PUT**. VM 대역폭·메모리를 안 씁니다. 12GB RAM에서 영상 프록시하면 바로 죽습니다.

#### 여명형 확인
```
GET  /api/v1/capsules/{id}/confirmation      # D-7 알림에서 진입
POST /api/v1/capsules/{id}/confirmation      { action: PROCEED|DEFER|CANCEL, defer_days? }
```

#### 수신자 열람 (비로그인, 토큰 기반)
```
GET  /api/v1/view/{token}            → { sender_name, sent_reason, sealed_at, has_media }
                                       ※ 본문은 아직 안 줌
POST /api/v1/view/{token}/accept     → { body, media[] (presigned GET) }
POST /api/v1/view/{token}/decline    { scope: THIS|ALL_FROM_SENDER }
POST /api/v1/view/{token}/later      → 30일 뒤 재알림
```
**이 4개 엔드포인트가 §3-3의 2단계 동의 설계를 구현합니다.** `GET`은 "있다"만 알려주고, 본문은 `accept` 후에만 나갑니다.

#### C형: 유족 조회
```
POST /api/v1/claims/lookup      { name, birth_date, phone_last4 }
     → { has_result: null }    ※ 항상 null 반환. 결과는 심사 통과 후 통보
POST /api/v1/claims             { claimant 정보, evidence presign 업로드 후 key }
GET  /api/v1/claims/{id}/status
```
> `lookup`이 즉시 "있음/없음"을 반환하면 **그 자체가 정보 유출**입니다. 무조건 심사 후 비동기 통보.

#### 관리자
```
GET   /api/v1/admin/claims?status=SUBMITTED
POST  /api/v1/admin/claims/{id}/approve   { note }
POST  /api/v1/admin/claims/{id}/reject    { reason }
GET   /api/v1/admin/deliveries?status=FAILED
POST  /api/v1/admin/deliveries/{id}/retry
```
관리자도 **본문은 못 봅니다.** 심사 대상은 증명서와 메타데이터뿐입니다.

### 4-3. 암호화 (services/crypto.py)

```
[Seal 시점]
1. DEK = os.urandom(32)                      # AES-256-GCM 데이터키
2. ciphertext, nonce = AESGCM(DEK).encrypt(body)
3. dek_wrapped = OCI_Vault.encrypt(KEK_id, DEK)   # 봉투암호화
4. content_hash = sha256(body)
5. DB에 ciphertext/nonce/dek_wrapped/content_hash 저장, DEK 메모리 소거

[열람 시점]
1. DEK = OCI_Vault.decrypt(KEK_id, dek_wrapped)
2. body = AESGCM(DEK).decrypt(nonce, ciphertext)
3. sha256(body) == content_hash 검증 (변조 탐지)
```
- 미디어는 파일이 크므로 **AES-256-CTR 스트리밍 암호화**를 클라이언트에서 수행하고 DEK만 서버 위탁 (Phase 2). MVP는 Object Storage SSE + Private 버킷으로 대체.
- KEK 로테이션 시 `dek_wrapped`만 재래핑. 본문 재암호화 불필요.

### 4-4. 스케줄러 & 발송 파이프라인

```python
# workers/scheduler.py — 매 60초
def tick():
    now = utcnow()
    # 1) 여명형 D-7 확인 알림
    for t in triggers_needing_confirmation(now):
        enqueue(send_confirmation_request, t.capsule_id)

    # 2) 발사 대상
    for t in due_triggers(now):          # next_fire_at <= now AND capsule.status=ARMED
        if t.require_confirm and not t.confirmed_at:
            log_and_skip(t)              # 기본값 = 보류
            continue
        if owner_recently_active(t):     # last_seen_at 기준 자동 연기
            defer(t); continue
        enqueue(fire_capsule, t.capsule_id)

    # 3) 만료 처리
    expire_stale_deliveries(now)
```

```python
# workers/tasks.py
def fire_capsule(capsule_id):
    with advisory_lock(f"capsule:{capsule_id}"):        # 중복 실행 차단
        capsule = load(capsule_id)
        if capsule.status != ARMED: return
        capsule.status = FIRING
        for r in capsule.recipients:
            if r.blocked_at: continue                   # 영구 거부 존중
            key = f"{capsule_id}:{r.id}:{occurrence}"
            delivery = get_or_create_delivery(idempotency_key=key)  # 멱등
            enqueue(send_notification, delivery.id)
        advance_recurrence(capsule)   # RRULE이 있으면 next_fire_at 갱신, 없으면 COMPLETED
```

**재시도 정책**: 5분 / 30분 / 3시간. 3회 실패 시 채널 폴백(알림톡 → SMS → 이메일). 전부 실패하면 관리자 알림 + `FAILED` 고정.

---

## 5. 프론트엔드 설계

### 5-1. 라우트 구조 (Next.js App Router)

```
app/
├─ (marketing)/
│  ├─ page.tsx                    # 랜딩 (타깃별 3종 → /a, /b, /c 변형)
│  ├─ pricing/  faq/  trust/      # trust: 보안·사업지속성 설명 (전환 핵심)
├─ (auth)/login/  verify/
├─ (app)/
│  ├─ dashboard/page.tsx          # 캡슐 목록
│  ├─ capsules/
│  │  ├─ new/page.tsx             # 4단계 위저드
│  │  └─ [id]/
│  │     ├─ page.tsx  edit/  recipients/  trigger/  preview/
│  ├─ recipients/page.tsx
│  ├─ confirm/[capsuleId]/page.tsx  # 여명형 D-7 확인
│  └─ settings/  (보관용량, 동의 내역, 계정)
├─ view/[token]/page.tsx          # ★ 수신자 화면 (비로그인)
├─ claims/                        # 유족 조회
│  ├─ page.tsx  new/  [id]/status/
└─ admin/                         # 심사 콘솔
```

### 5-2. 캡슐 작성 위저드 (4단계)

```
Step 1  누구에게       수신자 선택/추가
Step 2  무엇을         본문 작성 (+ AI 글쓰기 도우미) / 사진·영상 첨부
Step 3  언제           A: 날짜 선택  B: D-day + 안전장치  C: 사망 확인 시
Step 4  확인·봉인      미리보기 → 동의 체크 → 봉인
```

**UX 원칙 3가지**
1. **자동 저장.** 유서를 쓰다가 날리면 그 사람은 다시 안 옵니다. 5초 디바운스 + 로컬 백업.
2. **빈 화면을 주지 않는다.** "무엇을 쓸지 모르겠다"가 최대 이탈 지점. AI 질문 카드로 시작(§6-1).
3. **봉인 전 반드시 미리보기.** 수신자가 볼 화면 그대로. 여기서 "아 이렇게 보이는구나"가 전환을 만듭니다.

### 5-3. 수신자 화면 `/view/[token]` (가장 중요한 단일 화면)

```
┌──────────────────────────────┐
│  故 김○○ 님이                 │   ← 이름 외 정보 없음
│  당신에게 남긴 메시지가         │
│  있습니다                     │
│                              │
│  2024년 3월 12일에 작성됨      │   ← sealed_at, 신뢰 신호
│  사진 3장 · 영상 1개 포함       │   ← 내용은 안 보여줌
│                              │
│  [ 지금 확인하기 ]             │
│  [ 나중에 (30일 보관) ]        │
│  [ 받지 않겠습니다 ]           │   ← 눈에 잘 띄게. 숨기면 안 됨
└──────────────────────────────┘
```
- 광고 없음, 회원가입 요구 없음, 앱 설치 유도 없음. **여기서 뭔가를 팔려고 하면 서비스 전체가 죽습니다.**
- "받지 않겠습니다" 선택 시 확인 다이얼로그 1회 + `THIS/ALL_FROM_SENDER` 선택
- 열람 후: 본문 → 미디어 순차 표시, 하단에 조용한 한 줄로만 서비스 안내

### 5-4. 기술 세부

| 항목 | 선택 |
|---|---|
| 상태관리 | TanStack Query (서버 상태) + Zustand (위저드 로컬) |
| 폼 | react-hook-form + zod (백엔드 Pydantic과 스키마 대응) |
| 업로드 | presign → `fetch PUT` + 진행률, 이탈 시 재개 |
| 영상 재생 | 기본 `<video>` + presigned GET (TTL 10분) |
| 접근성 | **60대 이상 타깃 필수**: 기본 폰트 18px, 터치 타겟 48px, 명도대비 4.5:1 이상 |
| 다크모드 | MVP 제외 |

---

## 6. AI / GPU 연동

### 6-1. 인터페이스 계약

백엔드는 GPU 서버를 **OpenAI 호환 엔드포인트**로만 봅니다. 모델이 바뀌어도 백엔드 코드는 그대로입니다.

```
AI_BASE_URL=http://<gpu-host>:8001/v1
AI_API_KEY=<shared-secret>
AI_MODEL_CHAT=qwen2.5-32b-instruct     # 한국어 품질 우선
AI_MODEL_ASR=whisper-large-v3
AI_TIMEOUT_SEC=60
```

**보안**: GPU 서버는 공개 노출 금지. OCI VM ↔ GPU 서버 간 **WireGuard 터널** 또는 IP 화이트리스트 + mTLS. 전송되는 건 사용자의 유서 초안입니다.

**호출 규칙**
- 모든 AI 호출은 워커에서 비동기 (`ai_jobs` 테이블 경유). API 스레드 블로킹 금지
- 실패 시 AI 없이도 서비스가 동작해야 함 (**Graceful degradation**). AI는 부가기능이지 의존성이 아님

### 6-2. 프롬프트 1 — 글쓰기 도우미 (WRITING_COACH)

```
[system]
당신은 사용자가 소중한 사람에게 남길 마지막 메시지를 쓰도록 돕는 조력자입니다.

역할:
- 사용자가 무엇을 쓸지 막막해할 때, 구체적인 질문을 던져 기억을 끌어냅니다.
- 사용자가 답한 내용을 바탕으로 초안을 다듬습니다.

반드시 지킬 것:
1. 대신 써주지 않습니다. 사용자의 단어와 기억을 사용합니다. 사용자가 말하지
   않은 사실, 감정, 일화를 만들어내지 마십시오.
2. 미화하지 않습니다. 사용자가 담담하게 썼으면 담담하게 둡니다.
   "사랑한다"를 사용자가 쓰지 않았다면 넣지 마십시오.
3. 한 번에 질문은 하나만 합니다.
4. 재산 분배, 상속, 계좌, 부동산 처분에 관한 내용이 나오면 즉시 알립니다:
   "이 내용은 법적 유언으로서의 효력이 없습니다. 유언장은 별도로 준비하셔야
   합니다." 그리고 그 부분의 작성을 돕지 않습니다.
5. 죽음을 재촉하거나 미화하는 표현을 쓰지 않습니다.
6. 사용자가 현재 자살이나 자해를 암시하면, 작성을 돕는 것을 중단하고
   전문 상담 연결을 안내합니다. (플래그: NEEDS_HUMAN_REVIEW)

수신자 관계: {relation}
수신자 호칭: {recipient_name}
현재 초안: {draft}

출력 형식(JSON만, 다른 텍스트 금지):
{"mode":"question|revision", "message":"...", "suggested_draft":null|"...",
 "flags":["LEGAL_WILL_CONTENT"|"NEEDS_HUMAN_REVIEW"]}
```

**질문 카드 시드**(빈 화면 방지용, AI 호출 없이 프론트에 하드코딩):
- 이 사람과 함께한 순간 중 가장 자주 떠오르는 장면은 무엇인가요?
- 이 사람에게 미처 하지 못한 말이 있나요?
- 이 사람이 앞으로 어떻게 살았으면 하나요?
- 이 사람이 나를 어떻게 기억했으면 하나요?

### 6-3. 프롬프트 2 — 콘텐츠 안전 검수 (SAFETY_REVIEW)

봉인(seal) 직전 자동 실행. **차단이 아니라 경고**가 목적입니다.

```
[system]
당신은 사후 전달 메시지의 위험 요소를 점검하는 검수기입니다.
내용을 평가하거나 수정하지 마십시오. 아래 항목의 해당 여부만 판정합니다.

판정 항목:
- LEGAL_WILL: 재산·상속·계좌·부동산의 분배를 지시하는 내용
- CREDENTIAL: 비밀번호, 계좌번호, 인증정보가 평문으로 포함
- THIRD_PARTY_HARM: 특정 인물에 대한 비방, 폭로, 협박으로 읽힐 수 있는 내용
- SELF_HARM_RISK: 작성자의 임박한 자해를 시사하는 표현
- RECIPIENT_BURDEN: 수신자에게 과도한 의무·죄책감을 지우는 표현
   (예: "네가 하지 않으면 나는 눈을 감지 못한다")

각 항목에 대해 severity를 none/low/high로 판정하고,
high인 경우 해당 문장을 그대로 인용해 근거를 제시합니다.

출력(JSON만):
{"findings":[{"code":"...","severity":"...","evidence":"...","advice":"..."}]}
```

처리:
- `LEGAL_WILL: high` → 봉인 화면에 "법적 효력 없음" 고지 + 유언공증 안내 (차단 안 함)
- `CREDENTIAL: high` → 강한 경고 + 별도 '보안 금고' 기능 안내
- `SELF_HARM_RISK: high` → **봉인 보류 + 사람이 검토.** 자동 판단 금지
- `RECIPIENT_BURDEN: high` → 작성자에게만 조용히 제안, 강제 없음

### 6-4. 프롬프트 3 — 영상 자막·요약 (TRANSCRIBE)

```
1) Whisper large-v3, language=ko → transcript
2) 요약(수신자 화면 미리보기용 1줄, 선택 노출):
[system]
아래는 사망 후 유족에게 전달될 영상의 자막입니다.
내용을 한 문장으로 요약하되, 감정을 덧붙이거나 해석하지 마십시오.
사실만 기술합니다. 25자 이내.
```
> 요약은 기본 **비노출**입니다. 유족이 영상을 보기 전에 요약부터 읽는 건 좋은 경험이 아닙니다. 검색·관리 용도로만 저장.

### 6-5. AI 답장 대행 — MVP 제외 (골격만)

`ai_jobs.kind = 'PERSONA_REPLY'` 자리만 비워둡니다. 구현 시 최소 요건:
- 생전 `consents.kind='AI_TRAINING'` 명시 동의 필수
- 모든 응답에 **"AI가 생성한 답변입니다" 상시 표기** (해제 불가)
- 유족 중 1인이라도 중단 요청 시 즉시 정지
- 세션당 응답 횟수 상한 (애도 지연 방지)

---

## 7. 배포 / 운영

### 7-1. compose 구성 요지

```yaml
# docker-compose.yml (platform: linux/arm64 필수)
services:
  caddy:      { image: caddy:2-alpine, ports: ["80:80","443:443"] }
  web:        { build: ./frontend, environment: [NEXT_PUBLIC_API_BASE] }
  api:        { build: ./backend, command: uvicorn app.main:app --workers 2 }
  worker:     { build: ./backend, command: rq worker default ai }
  scheduler:  { build: ./backend, command: python -m app.workers.scheduler }
  postgres:   { image: postgres:16-alpine, volumes: [pgdata:/var/lib/postgresql/data] }
  redis:      { image: redis:7-alpine, command: redis-server --maxmemory 384mb }
```

### 7-2. 필수 환경변수

```
DATABASE_URL / REDIS_URL
JWT_SECRET / JWT_ACCESS_TTL=900 / JWT_REFRESH_TTL=2592000
OCI_NAMESPACE / OCI_BUCKET / OCI_REGION
OCI_S3_ENDPOINT / OCI_ACCESS_KEY / OCI_SECRET_KEY
OCI_VAULT_KEY_OCID
ALIMTALK_API_KEY / ALIMTALK_SENDER_KEY / SMS_API_KEY
KAKAO_CLIENT_ID / KAKAO_CLIENT_SECRET
AI_BASE_URL / AI_API_KEY / AI_MODEL_CHAT / AI_MODEL_ASR
ADMIN_ALERT_WEBHOOK
```

### 7-3. 백업 (타협 불가)

- `pg_dump` 일 1회 → Object Storage, 30일 보존, **다른 리전에 복제**
- Object Storage 버킷 **버저닝 활성화** (실수 삭제 방어)
- **월 1회 복원 리허설.** 안 해본 백업은 백업이 아닙니다.
- 재해 복구 목표: RPO 24시간 / RTO 4시간

### 7-4. 모니터링

- `/healthz`(liveness), `/readyz`(DB·Redis·Storage 체크)
- 알림 조건: 스케줄러 5분 이상 미동작 / 발송 실패율 5% 초과 / 디스크 80% / 쿼터 90%
- **"보내야 할 때 안 보낸 것"이 최악의 장애**입니다. 스케줄러 하트비트를 최우선 감시.

---

## 8. Claude Code 작업 분할

각 항목을 별도 세션으로 넘기시면 됩니다. 순서대로 진행해야 의존성이 맞습니다.

| # | 작업 | 산출물 | 예상 |
|---|---|---|---|
| T1 | 프로젝트 스캐폴딩 | 레포 구조, Docker Compose(arm64), Caddy, 헬스체크 | 0.5일 |
| T2 | DB 스키마 + Alembic | §3 전체 마이그레이션, ORM 모델, 시드 스크립트 | 1일 |
| T3 | 인증 | 휴대폰 OTP, 카카오 OAuth, JWT, 미들웨어 | 1일 |
| T4 | 수신자·캡슐 CRUD | §4-2 엔드포인트, Pydantic 스키마, 권한 검사 | 1.5일 |
| T5 | 암호화 서비스 | 봉투암호화, seal/unseal, content_hash 검증, 단위테스트 | 1일 |
| T6 | 미디어 업로드 | presign, 쿼터 강제, complete 검증, 삭제 | 1일 |
| T7 | 트리거·스케줄러 | RRULE 계산, tick 루프, advisory lock, 멱등성 테스트 | 1.5일 |
| T8 | 발송 파이프라인 | 알림톡/SMS 어댑터, 재시도, 폴백, delivery_events | 1.5일 |
| T9 | 수신자 열람 API | 토큰 발급·검증, 2단계 동의, decline 처리 | 1일 |
| T10 | 프론트 – 위저드 | 4단계, 자동저장, 업로드 UI, 미리보기 | 2.5일 |
| T11 | 프론트 – 수신자 화면 | §5-3, 접근성 기준 준수 | 1일 |
| T12 | AI 연동 | ai_client, 3개 프롬프트, ai_jobs 워커, 폴백 | 1.5일 |
| T13 | C형 유족 조회 + 관리자 콘솔 | lookup/claim/심사 | 1.5일 |
| T14 | 배포·백업·모니터링 | OCI 프로비저닝, CI, pg_dump, 알림 | 1일 |

합계 약 **17~18 인일**. 실제로는 3~4주로 보시면 됩니다.

### T1 착수 프롬프트 예시

```
이 설계서(첨부)의 T1을 구현해줘.
- 모노레포: backend(FastAPI/Python 3.12), frontend(Next.js 15 App Router/TS)
- docker-compose.yml: caddy, web, api, worker, scheduler, postgres:16, redis:7
- 모든 이미지 platform: linux/arm64 (Ampere A1 대상)
- 메모리 제한을 §1-2 표대로 각 서비스에 mem_limit으로 명시
- api에 /healthz, /readyz 구현 (readyz는 DB·Redis·Object Storage 연결 확인)
- Caddyfile: 로컬은 http, 프로덕션은 자동 TLS. /api/* → api:8000, 나머지 → web:3000
- .env.example에 §7-2 변수 전부 포함
- Makefile: up, down, logs, migrate, test
아직 비즈니스 로직은 만들지 마. 컨테이너가 다 뜨고 헬스체크가 통과하는 것까지만.
```

---

## 9. 미결 항목

1. **알림톡 대행사 선정** — 템플릿 사전 심사가 필요합니다. "고인이 남긴 메시지" 문구가 승인될지 사전 확인이 필요합니다. 반려되면 SMS+웹링크로 우회.
2. **GPU 서버 스펙** — 사용하실 모델과 VRAM을 알려주시면 `AI_MODEL_CHAT` 후보를 좁혀드리겠습니다.
3. **본인확인 방식** — MVP는 SMS OTP지만, C형 유족 조회를 열려면 본인확인 서비스(PASS/NICE) 정식 계약이 필요합니다.
4. **도메인·상호** — 알림톡 발신프로필 등록에 사업자등록이 선행됩니다.
