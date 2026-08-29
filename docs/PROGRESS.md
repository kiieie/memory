# 진행 상태 (라스트레터 MVP)

작업 단위는 `docs/라스트레터_MVP_구현설계서.md` §8 기준. **T1부터 순서대로** 진행 (뒷 작업이 앞 작업에 의존).

작업 완료 시 이 파일의 상태/완료일/비고만 갱신한다. `CLAUDE.md`나 설계서는 건드리지 않는다.

| # | 작업 | 상태 | 완료일 | 비고 |
|---|---|---|---|---|
| T1 | 프로젝트 스캐폴딩 (레포 구조, Docker Compose, Caddy, 헬스체크) | 진행중 | | 실서버(`oc2`, x86_64)에서 실기동 중 arm64 강제가 원인인 `npm install` 빌드 실패 발견 → platform 고정 전부 제거(CLAUDE.md 절대규칙 9번 정정). docker compose up 재시도 중, healthz/readyz 확인 전 |
| T2 | DB 스키마 + Alembic | 미착수 | | |
| T3 | 인증 (OTP, 카카오 OAuth, JWT) | 미착수 | | |
| T4 | 수신자·캡슐 CRUD | 미착수 | | |
| T5 | 암호화 서비스 (봉투암호화, seal/unseal) | 미착수 | | |
| T6 | 미디어 업로드 (presign, 쿼터 강제) | 미착수 | | |
| T7 | 트리거·스케줄러 | 미착수 | | |
| T8 | 발송 파이프라인 | 미착수 | | |
| T9 | 수신자 열람 API | 미착수 | | |
| T10 | 프론트 – 위저드 | 미착수 | | |
| T11 | 프론트 – 수신자 화면 | 미착수 | | |
| T12 | AI 연동 | 미착수 | | |
| T13 | C형 유족 조회 + 관리자 콘솔 | 미착수 | | |
| T14 | 배포·백업·모니터링 | 진행중 | | CI/CD 자동배포 부분만 사용자 요청으로 조기 착수(순서 이탈): `.github/workflows/deploy.yml`, `scripts/bootstrap-server.sh`, `docs/reference/deploy-ci.md`. 백업/모니터링/OCI 프로비저닝은 미착수 |

상태값: `미착수` / `진행중` / `완료` / `보류(사유)`

## 다음 작업

**T1 마무리** — Docker 설치된 환경에서 `make up` 후 `curl localhost:8000/healthz`, `curl localhost:8000/readyz`,
`curl localhost:3000` 확인되면 상태를 `완료`로 바꾼다. 이후 **T2 DB 스키마 + Alembic**로 진행.

## 미결 항목 (사용자 확인 필요 — 해당 작업 착수 전 반드시 질문)

| 항목 | 관련 작업 | 확인 안 되면 생기는 문제 |
|---|---|---|
| 알림톡 대행사 및 템플릿 사전 심사 | T8 | 템플릿 반려 시 SMS+웹링크로 우회 필요, 발송 어댑터 설계가 달라짐 |
| GPU 서버 스펙(VRAM 등) | T12 | `AI_MODEL_CHAT` 후보 확정 불가 |
| 본인확인 방식(PASS/NICE 정식 계약 여부) | T13 | MVP는 SMS OTP로 대체 가능하나 C형 정식 오픈 시 필수 |
| 상호·사업자등록 | T14 | 알림톡 발신프로필 등록 선행 조건. 도메인은 확정(memory.2convert.org) |
| GitHub Secrets 등록 + 서버 부트스트랩 실행 (사용자가 직접) | T14 | 안 하면 push해도 자동배포 안 됨. 절차: `docs/reference/deploy-ci.md` |
| oc2 서버 실제 CPU/RAM (`nproc`, `free -h`) | T1/T14 | infra-ops.md의 mem_limit 예산표가 Ampere A1(ARM, 2 OCPU/12GB) 전제라 실서버(x86_64)와 안 맞을 수 있음 |

## 확인 완료

- DNS: `memory.2convert.org` A레코드 → `168.107.43.247`, Cloudflare Proxy 꺼짐(DNS only) — Caddy 자동 TLS 발급 조건 충족 (2026-08-30)
