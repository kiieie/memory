# 라스트레터 (LastLetter) — CLAUDE.md

사후/예정 시점에 메시지를 대신 전달하는 서비스. MVP 범위: **A(예약형) + B(여명형) 필수, C(유족 조회형) 골격**.
전체 설계 원문은 `docs/라스트레터_MVP_구현설계서.md`. 이 파일은 그 설계를 구현하는 동안 매 세션 지켜야 할
규칙과, 방대한 세부 스펙을 어디서 찾는지에 대한 지도다.

**현재 상태**: T1(스캐폴딩) 코드 작성 완료, `docker compose up` 실기동 검증 전. T14 중 CI/CD 자동배포만
사용자 요청으로 조기 착수(`docs/reference/deploy-ci.md`). 진행 상황과 다음 작업은
**`docs/PROGRESS.md`를 먼저 확인**한다.

**저장소**: `https://github.com/jklee78cn/memory`. **배포 서버**: `kiie@168.107.43.247` (도메인 `memory.2convert.org`).

---

## 절대 규칙 (Non-negotiables)

이 서비스는 "고인이 남긴 말"을 다룬다. 아래 항목은 어떤 작업(T1~T14)을 하든 예외 없이 지킨다.
편의를 위해 완화하고 싶어지는 순간이 오면, 그게 바로 이 목록이 있는 이유다.

1. **본문 평문 저장 금지.** 캡슐 본문은 항상 봉투암호화(envelope encryption)로만 저장한다. 운영자·관리자
   콘솔 어디에서도 평문을 볼 수 없어야 한다. 상세: `docs/reference/db-schema.md`의 "암호화 흐름".
2. **중복 발송 절대 금지.** 발송 관련 코드는 전부 `idempotency_key`(`capsule:recipient:occurrence`)와
   advisory lock을 거친다. "부고 문자가 두 번 가면 신뢰가 끝난다" — 이 문장을 기준으로 판단한다.
3. **거부(decline)는 영구.** `recipients.blocked_at`이 찍히면 그 발신자의 어떤 캡슐도 다시 보내지 않는다.
   발송 파이프라인 어디서든 `blocked_at` 체크를 건너뛰지 않는다.
4. **여명형(B) 3중 안전장치.** ① D-7 본인 확인 알림 ② 무응답 시 기본값은 **보류**(옵트인해야 자동 진행)
   ③ 본인 로그인 감지(`last_seen_at`) 시 자동 30일 연기. 셋 중 하나라도 생략한 구현은 미완성이다.
5. **자해 위험 신호는 사람에게.** AI SAFETY_REVIEW에서 `SELF_HARM_RISK: high`가 나오면 봉인을 자동 진행하지
   않고 사람 검토로 넘긴다. 이 판단을 모델이나 규칙 엔진이 최종 결정하게 만들지 않는다.
6. **유족 조회는 항상 `null`을 먼저 반환한다.** `/claims/lookup`이 동기 응답에 "있음/없음"을 담으면 그 자체가
   개인정보 유출이다. 결과는 반드시 심사 후 비동기 통보.
7. **AI는 있어도 그만, 없어도 서비스는 돈다.** 모든 AI 호출은 워커에서 비동기(`ai_jobs` 경유)로 하고,
   실패해도 사용자 플로우가 막히지 않게 graceful degradation을 넣는다.
8. **스토리지 쿼터는 서버가 강제한다.** presign 발급 시점에 `storage_used_bytes` 검사. 프론트 검증만으로
   끝내지 않는다(우회 가능).
9. **모든 컨테이너 이미지는 `linux/arm64`.** 대상 VM(Ampere A1)이 ARM이다. amd64 이미지는 그냥 안 뜬다.
10. **관리자도 본문은 못 본다.** 관리자 화면·API가 다루는 건 증명서와 메타데이터뿐이다.

---

## 확정 기술 스택 (재논의 대상 아님)

| 영역 | 결정 |
|---|---|
| 백엔드 | Python 3.12 + FastAPI |
| DB | PostgreSQL 16 (컨테이너 자체 운영, Autonomous DB 아님) |
| 큐/스케줄 | Redis + RQ |
| 프론트 | Next.js 15 App Router + TS + Tailwind |
| 파일 저장 | OCI Object Storage (S3 호환, boto3, Presigned URL 방식) |
| 배포 | Docker Compose, **linux/arm64** |
| 리버스 프록시 | Caddy |
| AI 추론 | 외부 GPU 서버, OpenAI 호환 API(vLLM) |
| 인증(MVP) | 카카오 OAuth + SMS OTP |
| 발송 채널 | 카카오 알림톡 주, SMS 폴백, 이메일 보조 |

바꾸고 싶은 이유가 생기면 코드를 고치기 전에 사용자에게 먼저 확인한다 — 이 표는 이미 내려진 결정이다.

---

## 도메인 모델 (모든 작업의 공통 어휘)

- **Capsule(캡슐)**: 전달 단위 하나. 본문 + 첨부 + 수신자 + 전달조건.
- **Delivery(전달건)**: 캡슐 × 수신자 × 발송회차. 실제 발송의 최소 단위.
- **Trigger(트리거)**: 캡슐이 열리는 조건. `SCHEDULED`(A) / `PROGNOSIS`(B) / `DEATH_CLAIM`(C).

**Capsule 상태**: `DRAFT → SEALED → ARMED → FIRING → COMPLETED`, 언제든 `CANCELLED`, `ARMED`에서 `EXPIRED`.
**Delivery 상태**: `PENDING → NOTIFIED → (OPENED | DECLINED | EXPIRED)`, 실패 시 `FAILED`(재시도 3회).

전체 스키마와 암호화 흐름은 `docs/reference/db-schema.md`.

---

## 디렉토리 구조

```
backend/    FastAPI. 세부 트리는 docs/reference/backend-pipeline.md. api/v1·services·workers·core는
            대부분 "구현: Tn" 주석만 있는 자리표시자 — 실제 로직은 해당 작업에서 채운다.
frontend/   Next.js. 라우트 구조는 docs/reference/frontend-spec.md. 지금은 루트 page.tsx만 존재,
            (marketing)/(auth)/(app)/view/claims/admin 트리는 T10/T11 등 해당 작업에서 생성.
docker-compose.yml / Caddyfile / .env.example / Makefile   T14 전까지는 로컬 기동용.
docs/       설계서 + 이 CLAUDE.md가 가리키는 참조 문서 + PROGRESS.md
```

## 빌드/테스트 명령

```
make up       # docker compose up -d --build
make down
make logs
make migrate  # alembic upgrade head — alembic 자체는 T2에서 초기화 예정, 그 전엔 실패함
make test     # docker compose exec api pytest
```

로컬 실행 전 `.env.example`을 `.env`로 복사할 것. Docker Desktop 없이 문법만 검증하려면
`python -c "import yaml; yaml.safe_load(open('docker-compose.yml'))"`.

---

## 작업 진행 방식

1. `docs/PROGRESS.md`를 읽고 다음 작업(T번호)과 미결 항목 중 이 작업에 걸리는 게 있는지 확인한다.
   미결 항목이 걸리면 **작업을 시작하기 전에 사용자에게 묻는다** — 추측으로 알림톡 대행사나 GPU 모델을
   정하지 않는다.
2. 아래 매핑표에서 해당 작업의 참조 문서를 열어 세부 스펙을 확인한다. 설계서 원문(`docs/라스트레터_MVP_구현설계서.md`)의
   해당 절도 필요하면 함께 본다.
3. 구현한다. 위 "절대 규칙"은 참조 문서에 다시 안 적혀 있어도 항상 적용된다.
4. 작업이 끝나면 `docs/PROGRESS.md`의 상태/완료일/비고만 갱신한다. 이 파일(CLAUDE.md)과 설계서는 작업
   완료로 인해 내용이 바뀌지 않는 한 건드리지 않는다.

### 작업 → 참조 문서 매핑

| # | 작업 | 참조 문서 |
|---|---|---|
| T1 | 프로젝트 스캐폴딩 | `infra-ops.md`(인스턴스/compose/env), `backend-pipeline.md`(디렉토리) |
| T2 | DB 스키마 + Alembic | `db-schema.md` |
| T3 | 인증 | `api-spec.md`(인증 섹션) |
| T4 | 수신자·캡슐 CRUD | `api-spec.md`, `db-schema.md` |
| T5 | 암호화 서비스 | `db-schema.md`(암호화 흐름) |
| T6 | 미디어 업로드 | `api-spec.md`(미디어), `infra-ops.md`(스토리지 전략) |
| T7 | 트리거·스케줄러 | `backend-pipeline.md`, `db-schema.md`(capsule_triggers) |
| T8 | 발송 파이프라인 | `backend-pipeline.md` |
| T9 | 수신자 열람 API | `api-spec.md`(수신자 열람) |
| T10 | 프론트 – 위저드 | `frontend-spec.md` |
| T11 | 프론트 – 수신자 화면 | `frontend-spec.md` |
| T12 | AI 연동 | `ai-prompts.md` |
| T13 | C형 유족조회 + 관리자 | `api-spec.md`(claims/admin), `db-schema.md`(death_claims) |
| T14 | 배포·백업·모니터링 | `infra-ops.md`, `deploy-ci.md`(CI/CD는 조기 착수됨) |

경로는 전부 `docs/reference/` 아래.

---

## 미결 항목

착수 전 사용자 확인이 필요한 항목(알림톡 대행사, GPU 스펙, 본인확인 방식, 상호·사업자등록)은
`docs/PROGRESS.md` 하단 표에 있다. 도메인은 확정(`memory.2convert.org`, `docs/reference/infra-ops.md`).
해당 작업(T8/T12/T13/T14)에 들어가기 전에 그 표부터 본다.
