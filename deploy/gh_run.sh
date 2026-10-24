#!/bin/sh
# GitHub Actions 실행용: 데이터 복원 → 봇 약 5시간 55분 실행 → 데이터 저장.
# 비밀 값(.env 내용, 데이터 저장소 주소와 열쇠)은 저장소의 Secrets 에서만 가져옵니다.
set -e
export TZ=Asia/Seoul
mkdir -p ~/.ssh
printf '%s\n' "$DATA_DEPLOY_KEY" > ~/.ssh/data_key && chmod 600 ~/.ssh/data_key
export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/data_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
git clone --depth 1 "git@github.com:${DATA_REPO}.git" /tmp/data
printf '%s\n' "$DOTENV" > .env
cp -a /tmp/data/files/. . 2>/dev/null || true   # 이미지·이모지 번호·DB·기록 파일을 원래 자리로

save() {
  python deploy/gh_save.py /tmp/data/files
  tail -n 300 /tmp/bot.log > /tmp/data/last_run.log 2>/dev/null || true   # 점검용 로그는 비공개 데이터 저장소에만 둠
  cd /tmp/data
  git add -A
  git -c user.name=bot -c user.email=bot@users.noreply.github.com commit -qm "data $(date +%m-%d_%H:%M)" || true
  git push -q origin HEAD 2>/dev/null || { git pull -q --rebase && git push -q origin HEAD; } || true
  cd - >/dev/null
}
trap save EXIT
( while true; do sleep 600; save; done ) &   # 10분마다 중간 저장 (갑자기 끊겨도 최대 10분 치만 잃음)
# 봇 출력은 공개되는 실행 기록에 남지 않게 파일로만 보냄
timeout 21300 python -u bot.py > /tmp/bot.log 2>&1; echo "봇 종료 코드: $?"
