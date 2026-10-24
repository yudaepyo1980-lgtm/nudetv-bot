# 봇을 서버로 옮기기 (맥미니 없이 24시간)

봇은 맥에 묶여 있지 않아서 인터넷 서버(VPS)에서 그대로 돌릴 수 있습니다. 한 번에 한 곳에서만 켜야 하므로(같은 봇 토큰으로 두 곳이 동시에 켜지면 충돌) 순서를 지켜 주세요.

## 준비물
- 우분투 22.04 또는 24.04 서버 1대 (메모리 1GB 이상, 지역은 서울 또는 도쿄 권장)
- 서버에 접속하는 방법(주소, 아이디, SSH 키 또는 비밀번호)

## 옮기는 순서
1. **맥에서 봇을 끈다** (이 순간부터 봇이 멈춥니다. 데이터가 어긋나지 않게 하려는 것)
   `launchctl bootout gui/$(id -u)/com.nudetv.bot`
2. **맥에서 묶는다**: `sh ~/nudetv-bot/deploy/pack.sh` → `~/nudetv-bot-pack.tar.gz` 생성
   ⚠️ 이 파일 안에 봇 토큰이 들어 있습니다. 서버로 옮긴 뒤 맥에서 지우세요.
3. **서버로 보낸다**: `scp ~/nudetv-bot-pack.tar.gz 아이디@서버주소:~`
4. **서버에서 설치한다**:
   ```
   ssh 아이디@서버주소
   tar xzf nudetv-bot-pack.tar.gz deploy/setup_server.sh
   sudo sh deploy/setup_server.sh ~/nudetv-bot-pack.tar.gz
   ```
5. **확인**: 텔레그램에서 봇에 `/start`, 결과방에서 `메뉴`. 로그는 `journalctl -u nudetv-bot -f`
6. **맥 정리**: 맥의 자동 실행 설정을 지운다 `rm ~/Library/LaunchAgents/com.nudetv.bot.plist` (맥을 끄기 전에)

## 되돌리기 (문제가 생기면)
- 서버: `sudo systemctl stop nudetv-bot`
- 맥: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nudetv.bot.plist`

## 서버에서 자주 쓰는 명령
- 다시 켜기: `sudo systemctl restart nudetv-bot`
- 상태: `systemctl status nudetv-bot`
- 로그: `journalctl -u nudetv-bot -n 100`
- 백업(설정·데이터): `cd /opt/nudetv-bot && tar czf ~/backup.tar.gz userdata.db chat.db today_picks.json notice.json .env`

## 서버로 옮기기 전에 확인할 것
- 서버가 해외에 있으면 네이버 스포츠나 플래시스코어가 막힐 수 있습니다. 옮긴 뒤 스코어 화면이 나오는지 꼭 확인하세요. 서울 지역 서버를 권장합니다.
- 서버 업체의 이용약관에서 서비스 내용이 허용되는지 먼저 읽어 보세요.
