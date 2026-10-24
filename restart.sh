#!/bin/sh
# 봇을 안전하게 다시 켠다: 문법 검사 → 자동 실행(맥은 launchd, 서버는 systemd) 재시작. nohup/pkill로 직접 켜지 마세요.
cd "$(dirname "$0")" || exit 1
.venv/bin/python -m py_compile bot.py || { echo "문법 오류로 재시작하지 않았습니다."; exit 1; }
if command -v launchctl >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/com.nudetv.bot" && sleep 5 && launchctl print "gui/$(id -u)/com.nudetv.bot" | grep -E "state =|pid ="
else
  sudo systemctl restart nudetv-bot && sleep 5 && systemctl --no-pager status nudetv-bot | head -5
fi
