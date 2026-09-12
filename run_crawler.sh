#!/bin/bash
set -e  # 오류 발생 시 스크립트 중단 (push 실패도 감지)

cd ~/fishing

# 1. detached HEAD / 중단된 rebase 자동 복구
git rebase --abort 2>/dev/null || true
git checkout main 2>/dev/null || true

# 2. 원격 최신 커밋으로 동기화 (pull 대신 fetch+reset — 충돌 방지)
git fetch origin
git reset --hard origin/main

# 텔레그램 토큰은 커밋 금지 (과거 유출로 회전함) — VM 홈의 ~/telegram_env.sh에서 읽음
if [ -f ~/telegram_env.sh ]; then
  . ~/telegram_env.sh
fi

sleep $((RANDOM % 600))

python3 scripts/main.py

git add index.html data.json
if ! git diff --staged --quiet; then
  git commit -m "chore: 예약 현황 업데이트 $(TZ=Asia/Seoul date +'%Y-%m-%d %H:%M KST')"
  git push origin main
fi
