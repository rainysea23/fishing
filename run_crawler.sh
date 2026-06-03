#!/bin/bash
cd ~/fishing
git pull --rebase

export TELEGRAM_BOT_TOKEN="8956265432:AAEZ8dthVr40CxsqxuZbYdV_GZDgEnGL-Xw"
export TELEGRAM_CHAT_ID="5472071056"

sleep $((RANDOM % 600))

python3 scripts/main.py

git add index.html data.json
if ! git diff --staged --quiet; then
  git commit -m "chore: 예약 현황 업데이트 $(TZ=Asia/Seoul date +'%Y-%m-%d %H:%M KST')"
  git push
fi
