#!/bin/sh
# 맥에서 봇 코드와 데이터를 한 파일로 묶는다. 결과: ~/nudetv-bot-pack.tar.gz  (⚠️ .env 에 봇 토큰이 들어 있으니 남에게 주지 마세요)
# 데이터가 어긋나지 않게, 묶기 직전에 맥의 봇을 꺼 두는 것을 권장합니다:  launchctl bootout gui/$(id -u)/com.nudetv.bot
cd "$(dirname "$0")/.." || exit 1
OUT="$HOME/nudetv-bot-pack.tar.gz"
FILES="*.py requirements.txt restart.sh CLAUDE.md banner.jpg picks_banner.jpg assets deploy .env"
for f in userdata.db chat.db today_picks.json notice.json picks.json; do [ -e "$f" ] && FILES="$FILES $f"; done
for f in emoji_work/emoji_ids.json emoji_work/premium_map.json emoji_work/sets.json; do [ -e "$f" ] && FILES="$FILES $f"; done
tar czf "$OUT" --exclude='deploy/*.tar.gz' $FILES && echo "묶음 완료: $OUT" && ls -lh "$OUT" | awk '{print "크기:", $5}'
