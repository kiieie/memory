# DB 스키마 참조 (T2, T4, T5, T7, T13에서 사용)

원본: `docs/라스트레터_MVP_구현설계서.md` §3. PostgreSQL 16. 여기 있는 DDL은 최초 설계 스냅샷이다 —
실제 마이그레이션(Alembic)이 진행되며 벌어지는 컬럼 변경은 **이 파일이 아니라 마이그레이션 히스토리가 진실**이다.
이 파일은 스키마를 볼 때 참고용으로만 갱신한다.

## 테이블 목록과 책임

| 테이블 | 책임 |
|---|---|
| `users` | 발신자(가입자) 계정, 스토리지 쿼터, `last_seen_at`(자동 연기 판단용) |
| `consents` | 개별 동의 이력. 약관 동의와 별개 — 법적 증거로 남겨야 함 |
| `recipients` | 발신자가 등록한 수신자. `blocked_at`은 영구 거부 |
| `capsules` | 전달 단위 하나. 본문은 반드시 암호문(`body_ciphertext`)으로만 |
| `capsule_triggers` | 캡슐 1:1. 발사 조건(`next_fire_at`)과 여명형 안전장치 필드 |
| `capsule_recipients` | 캡슐 ↔ 수신자 매핑 |
| `media_assets` | 첨부. 업로드는 presign 방식이므로 `upload_status`로 완료 추적 |
| `deliveries` | 캡슐 × 수신자 × 발송회차. `idempotency_key`로 중복 발송 차단 |
| `delivery_events` | deliveries의 이벤트 로그 (감사/디버깅) |
| `death_claims` | C형: 유족이 제출한 사망 확인 요청 |
| `ai_jobs` | AI 호출 큐 (WRITING_COACH / SAFETY_REVIEW / TRANSCRIBE / 추후 PERSONA_REPLY) |
| `audit_logs` | 전역 감사 로그 |

## 전체 DDL

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

## 암호화 흐름 (T5) — `body_ciphertext` / `dek_wrapped` / `content_hash`에 대응

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

- 미디어는 파일이 크므로 AES-256-CTR 스트리밍 암호화를 클라이언트에서 수행하고 DEK만 서버 위탁하는 방식은 **Phase 2**. MVP는 Object Storage SSE + Private 버킷으로 대체.
- KEK 로테이션 시 `dek_wrapped`만 재래핑. 본문 재암호화 불필요.
- OCI Vault는 Always Free 키 개수 제한이 있으므로 **KEK 1개만** 사용.

## 상태 머신 (설계서 §2-2 참조, 스키마 구현 시 반드시 반영)

**Capsule**: `DRAFT → SEALED → ARMED → FIRING → COMPLETED`, 언제든 `CANCELLED`, `ARMED`에서 `EXPIRED` 가능.
`SEALED`에서 콘텐츠 해시 기록. `ARMED`는 트리거 등록 완료. `FIRING`은 Delivery 생성·발송 중.

**Delivery**: `PENDING → NOTIFIED → (OPENED | DECLINED | EXPIRED)`, 실패 시 `FAILED`(재시도 3회, 5m/30m/3h 백오프).
`DECLINED`는 영구 — 같은 발신자의 재발송 금지. `EXPIRED`는 알림 후 30일 미열람.
