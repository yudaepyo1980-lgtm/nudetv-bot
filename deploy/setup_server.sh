#!/bin/sh
# 우분투 서버에서 한 번 실행: 봇 설치와 자동 실행 등록.  사용법:  sudo sh setup_server.sh ~/nudetv-bot-pack.tar.gz
set -e
PACK="${1:-$HOME/nudetv-bot-pack.tar.gz}"
[ -f "$PACK" ] || { echo "묶음 파일이 없어요: $PACK"; exit 1; }
apt-get update -y
apt-get install -y python3 python3-venv python3-pip tzdata
timedatectl set-timezone Asia/Seoul || true
id nudetv >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin nudetv
mkdir -p /opt/nudetv-bot
tar xzf "$PACK" -C /opt/nudetv-bot
chown -R nudetv:nudetv /opt/nudetv-bot
chmod 600 /opt/nudetv-bot/.env
sudo -u nudetv python3 -m venv /opt/nudetv-bot/.venv
sudo -u nudetv /opt/nudetv-bot/.venv/bin/pip install -q -r /opt/nudetv-bot/requirements.txt
cp /opt/nudetv-bot/deploy/nudetv-bot.service /etc/systemd/system/nudetv-bot.service
systemctl daemon-reload
systemctl enable --now nudetv-bot
sleep 6
systemctl --no-pager status nudetv-bot | head -12
echo "로그 보기: journalctl -u nudetv-bot -f"
