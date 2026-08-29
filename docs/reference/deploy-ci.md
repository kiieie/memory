# CI/CD 자동배포 참조 (T14, 사용자 요청으로 조기 착수)

대상 서버: `kiie@168.107.43.247` (도메인 `memory.2convert.org`). 저장소: `https://github.com/jklee78cn/memory`.

이 자동배포는 순서상 T14 항목이지만 사용자 요청으로 T1 직후 먼저 구축했다. **git pull + docker compose up -d --build**
이상은 하지 않는다 — DB 마이그레이션 자동 적용, 백업, 모니터링 알림은 아직 없음(T2/T14 나머지).

## 흐름

```
로컬에서 main에 push
  → GitHub Actions(.github/workflows/deploy.yml)가 서버에 SSH
  → cd $DEPLOY_PATH && git pull --ff-only && docker compose up -d --build && docker image prune -f
```

Actions는 `appleboy/ssh-action` 같은 서드파티 액션 없이 OpenSSH를 직접 써서 공급망 노출을 줄였다.

## 사람이 직접 해야 하는 일 (에이전트가 대신할 수 없음)

이 세션은 docker/gh CLI도 없고 서버 SSH 키도 없다 — 아래는 전부 사용자가 직접 실행해야 한다.

1. **서버 부트스트랩(최초 1회)**: `scripts/bootstrap-server.sh`를 서버에 올려 실행. docker 설치, 80/443 방화벽,
   저장소 clone, `.env` 생성, **GitHub Actions 전용 배포 SSH 키 발급**까지 한 번에 처리한다
   (스크립트가 각 단계마다 필요하면 안내를 찍고 멈춘다 — 한 번에 안 끝날 수 있다).
2. **GitHub Secrets 등록** (저장소 Settings → Secrets and variables → Actions):

   | 이름 | 값 |
   |---|---|
   | `DEPLOY_HOST` | `168.107.43.247` |
   | `DEPLOY_USER` | `kiie` |
   | `DEPLOY_PATH` | `/home/kiie/lastletter` (부트스트랩 스크립트 기본값) |
   | `DEPLOY_SSH_KEY` | 부트스트랩 스크립트가 출력한 **배포 전용** 개인키 전체 (사용자 개인 SSH 키 아님) |

3. **DNS 확인**: `memory.2convert.org` A레코드가 `168.107.43.247`을 가리켜야 Caddy가 Let's Encrypt 인증서를
   발급받을 수 있다. 안 되어 있으면 프로덕션 `.env`의 `SITE_ADDRESS`를 `memory.2convert.org`로 바꿔도 TLS가 안 나온다.
4. **최초 push**: 로컬에서 `git push -u origin main` (또는 이미 이 세션이 시도해뒀다면 결과를 확인).

## 롤백

`git revert <커밋>` 후 다시 push하면 같은 파이프라인으로 이전 상태가 재배포된다. 급하면 서버에서 직접
`git checkout <이전 커밋> && docker compose up -d --build`.

## .env는 절대 git에 안 들어간다

`.gitignore`에 이미 있음. 서버의 `.env`는 부트스트랩 때 한 번 만들고, 값이 바뀌면(카카오 키, 알림톡 키 등)
서버에서 직접 고친 뒤 `docker compose up -d`로 재기동한다. `git pull`은 `.env`를 건드리지 않는다.
