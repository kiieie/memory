# 백엔드 구조 · 스케줄러 · 발송 파이프라인 참조 (T1, T7, T8에서 사용)

원본: `docs/라스트레터_MVP_구현설계서.md` §4-1, §4-4.

## 디렉토리 구조 (T1에서 생성)

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
├─ Dockerfile                 # FROM python:3.12-slim, platform linux/arm64
└─ pyproject.toml
```

새 파일을 어디에 둘지 애매하면 이 트리에서 가장 가까운 책임을 찾는다. 여기 없는 새 카테고리가 필요하면
먼저 이 파일에 추가하고 나서 코드를 쓴다 (구조가 코드보다 먼저 정의되어야 함).

## 스케줄러 (`workers/scheduler.py`) — 매 60초 tick

```python
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

## 발송 태스크 (`workers/tasks.py`)

```python
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

## 재시도·폴백 정책

5분 → 30분 → 3시간 백오프, 3회 실패 시 채널 폴백(**알림톡 → SMS → 이메일** 순). 전부 실패하면
`deliveries.status = FAILED` 고정 + 관리자 알림(`ADMIN_ALERT_WEBHOOK`). 이 정책 값을 바꿀 땐
스케줄러/태스크 코드와 `db-schema.md`의 `deliveries.retry_count` 설명을 함께 갱신한다.
