# 인프라 · 배포 · 운영 참조 (T1, T14에서 사용)

원본: `docs/라스트레터_MVP_구현설계서.md` §1, §7.

## OCI Always Free 자원 (⚠️ 재확인 필요할 때마다 갱신)

2026년 6월 15일부로 Ampere A1 Always Free 할당량이 **4 OCPU/24GB → 2 OCPU/12GB로 축소**됐고,
기존 한도 초과 인스턴스는 2026년 8월 18일 이후 종료 대상이라는 안내가 있었다. 블록 스토리지 200GB,
아웃바운드 대역폭 월 10TB, AMD 마이크로 인스턴스 2대는 유지. 무료 티어에 GPU 없음.

**설계 전제는 2 OCPU / 12GB (Ampere A1, ARM).** T1/T14 착수 전 OCI 콘솔에서 실제 할당량을 다시 확인할 것 —
이 문서의 숫자가 아니라 콘솔이 진실.

⚠️ **정정(2026-08-30)**: 실제 배포 서버 `kiie@168.107.43.247`(`oc2`)는 `uname -m` 확인 결과 **x86_64**다.
이 아래 인스턴스 구성/메모리 예산 표는 원래 Ampere A1(ARM) 전제로 짠 것이라 이 서버의 실제 CPU/RAM과
안 맞을 수 있다. `nproc` / `free -h` 결과로 재확인 전까지는 이 표의 숫자를 그대로 믿지 말 것.
컨테이너 이미지 platform 강제(arm64)는 이미 뺐다 — 절대규칙 9번(`CLAUDE.md`) 참고.

## 인스턴스 구성

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

`docker-compose.yml`의 `mem_limit`은 이 표와 정확히 맞춰야 한다. 서비스 하나 늘릴 때 다른 서비스 예산을
깎지 않았다면 12GB를 넘기는지부터 계산할 것.

## 스토리지 전략 (⚠️ 가장 중요한 제약)

Object Storage Always Free 한도는 약 20GB(Standard 10GB + Infrequent/Archive 10GB). 영상 서비스에는
절대적으로 부족하다.

| 단계 | 정책 |
|---|---|
| MVP(파일럿 100명) | 계정당 **200MB 상한**, 영상은 720p / 3분 / 100MB 제한. 총 20GB 내 수용 |
| 베타 | Object Storage 유료 전환. 이때부터 과금 모델 필수 |
| 정식 | 30일 미열람 콘텐츠는 Archive 티어 자동 이동(Lifecycle Policy) |

용량 상한은 코드 레벨에서 강제한다. `users.storage_quota_bytes` / `storage_used_bytes`를 업로드 presign
발급 시점(T6)에 반드시 검사 — 프론트 검증만으로는 우회 가능하므로 서버 검사가 원본.

## 네트워크 · 보안

- VCN Public Subnet 1개, Security List: 22(내 IP만), 80, 443만 개방.
- **OS 방화벽 주의**: Oracle Linux/Ubuntu 이미지는 iptables 기본 정책이 막고 있다. `netfilter-persistent`
  규칙 추가 필수 — 놓치면 "포트 열었는데 안 됨" 함정에 빠진다.
- OCI Vault: KEK 1개만 사용(Always Free 키 개수 제한). DEK는 DB에 암호문으로 저장.
- Object Storage는 **Private 버킷**. 모든 접근은 Presigned URL(S3 호환 presign)로만.

## Docker Compose 구성 요지

```yaml
# docker-compose.yml (platform 고정 없음 — 실제 배포 서버가 x86_64라 뺐다. 위 정정 참고)
services:
  caddy:      { image: caddy:2-alpine, ports: ["80:80","443:443"] }
  web:        { build: ./frontend, environment: [NEXT_PUBLIC_API_BASE] }
  api:        { build: ./backend, command: uvicorn app.main:app --workers 2 }
  worker:     { build: ./backend, command: rq worker default ai }
  scheduler:  { build: ./backend, command: python -m app.workers.scheduler }
  postgres:   { image: postgres:16-alpine, volumes: [pgdata:/var/lib/postgresql/data] }
  redis:      { image: redis:7-alpine, command: redis-server --maxmemory 384mb }
```

Caddyfile: 로컬은 http, 프로덕션은 자동 TLS. `/api/*` → `api:8000`, 나머지 → `web:3000`.
도메인 확정: **memory.2convert.org**. 프로덕션 `.env`에는 `SITE_ADDRESS=memory.2convert.org`로 설정 —
Caddy가 이 값으로 자동 TLS(Let's Encrypt) 발급.

## 환경변수 (`.env.example`에 전부 포함)

```
DATABASE_URL / REDIS_URL
JWT_SECRET / JWT_ACCESS_TTL=900 / JWT_REFRESH_TTL=2592000
OCI_NAMESPACE / OCI_BUCKET / OCI_REGION
OCI_S3_ENDPOINT / OCI_ACCESS_KEY / OCI_SECRET_KEY
OCI_VAULT_KEY_OCID
ALIMTALK_API_KEY / ALIMTALK_SENDER_KEY / SMS_API_KEY
KAKAO_CLIENT_ID / KAKAO_CLIENT_SECRET / KAKAO_REDIRECT_URI
COOKIE_SECURE=true (로컬 http 테스트 시 false)
AI_BASE_URL / AI_API_KEY / AI_MODEL_CHAT / AI_MODEL_ASR
ADMIN_ALERT_WEBHOOK
```

## 백업 (타협 불가)

- `pg_dump` 일 1회 → Object Storage, 30일 보존, **다른 리전에 복제**.
- Object Storage 버킷 **버저닝 활성화** (실수 삭제 방어).
- **월 1회 복원 리허설.** 안 해본 백업은 백업이 아니다.
- 재해 복구 목표: RPO 24시간 / RTO 4시간.

## CI/CD 자동배포

대상 서버 `kiie@168.107.43.247` / 도메인 `memory.2convert.org`. main push 시 자동 배포 구성은
`docs/reference/deploy-ci.md`, 워크플로는 `.github/workflows/deploy.yml`, 서버 최초 셋업은
`scripts/bootstrap-server.sh`. 저장소: `https://github.com/kiieie/memory`.

## 모니터링

- `/healthz`(liveness), `/readyz`(DB·Redis·Storage 체크) — T1에서 최소 구현, T14에서 알림 연결.
- 알림 조건: 스케줄러 5분 이상 미동작 / 발송 실패율 5% 초과 / 디스크 80% / 쿼터 90%.
- **"보내야 할 때 안 보낸 것"이 최악의 장애.** 스케줄러 하트비트를 최우선 감시.
