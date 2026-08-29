#!/usr/bin/env bash
# 서버(kiie@168.107.43.247)에서 최초 1회만 직접 실행하는 부트스트랩.
# 이 저장소의 CI는 이 스크립트를 대신 실행해주지 않는다 — docker/git 설치, 최초 clone,
# .env 생성, 배포용 SSH 키 발급은 사람이 한 번은 해야 하는 일이라 스크립트로만 남겨둔다.
#
# 사용법:
#   scp scripts/bootstrap-server.sh kiie@168.107.43.247:~/bootstrap-server.sh
#   ssh kiie@168.107.43.247
#   chmod +x bootstrap-server.sh && ./bootstrap-server.sh
#
# 상세: docs/reference/deploy-ci.md

set -euo pipefail

REPO_URL="https://github.com/jklee78cn/memory.git"
DEPLOY_PATH="$HOME/lastletter"

echo "== 1) git / docker 설치 확인 =="
if ! command -v git >/dev/null; then
  sudo apt-get update && sudo apt-get install -y git
fi
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh   # arm64 자동 감지, compose plugin 포함
  sudo usermod -aG docker "$USER"
  echo "!! docker 그룹 적용을 위해 재로그인(ssh 재접속) 후 이 스크립트를 다시 실행하세요."
  exit 0
fi

echo "== 2) 방화벽: 80/443 개방 (netfilter-persistent 없으면 놓치기 쉬움, docs/reference/infra-ops.md 참고) =="
if command -v ufw >/dev/null; then
  sudo ufw allow 80/tcp; sudo ufw allow 443/tcp
else
  sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
  sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
  if command -v netfilter-persistent >/dev/null; then
    sudo netfilter-persistent save
  else
    echo "!! netfilter-persistent 없음 - 재부팅하면 규칙 날아감. 수동으로 영속화 필요."
  fi
fi

echo "== 3) 저장소 clone/pull =="
if [ -d "$DEPLOY_PATH/.git" ]; then
  git -C "$DEPLOY_PATH" pull --ff-only
else
  git clone "$REPO_URL" "$DEPLOY_PATH"
fi

echo "== 4) .env =="
if [ ! -f "$DEPLOY_PATH/.env" ]; then
  cp "$DEPLOY_PATH/.env.example" "$DEPLOY_PATH/.env"
  echo "!! $DEPLOY_PATH/.env 생성함. 최소 JWT_SECRET / POSTGRES_PASSWORD / SITE_ADDRESS=memory.2convert.org 값을 채우고 다시 실행하세요."
  exit 0
fi

echo "== 5) GitHub Actions 배포용 SSH 키 (사용자 개인 키와 별개) =="
DEPLOY_KEY="$HOME/.ssh/lastletter_deploy_key"
if [ ! -f "$DEPLOY_KEY" ]; then
  ssh-keygen -t ed25519 -N "" -C "lastletter-deploy" -f "$DEPLOY_KEY"
  cat "$DEPLOY_KEY.pub" >> "$HOME/.ssh/authorized_keys"
  chmod 600 "$HOME/.ssh/authorized_keys"
  echo
  echo "!! 아래 개인키 전체를 GitHub 저장소 Settings > Secrets > Actions > DEPLOY_SSH_KEY 에 붙여넣고,"
  echo "!! 이 서버의 $DEPLOY_KEY, $DEPLOY_KEY.pub 파일은 지우세요:"
  echo
  cat "$DEPLOY_KEY"
  echo
fi

echo "== 6) 최초 기동 =="
cd "$DEPLOY_PATH" && docker compose up -d --build
echo "완료. curl -s localhost:8000/healthz 로 확인하세요."
