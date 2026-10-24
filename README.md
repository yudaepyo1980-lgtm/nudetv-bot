# 누드TV 텔레그램 안내봇

스포츠 스코어·추천픽·이벤트 안내를 보여주는 텔레그램 봇입니다. (python-telegram-bot 21)

## 실행
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # 값을 채운다 (BOT_TOKEN, ADMIN_IDS, SUPPORT_USERNAME …)
.venv/bin/python bot.py
```
- 이미지(`banner.jpg`, `picks_banner.jpg`)와 구미호 이모지 번호 목록(`emoji_work/emoji_ids.json`, `premium_map.json`)은 저장소에 없습니다. 직접 만들어 같은 이름으로 넣으면 쓰이고, 없어도 봇은 이미지·이모지 없이 동작합니다.
- `.env`, 데이터베이스(`*.db`), 추천픽 기록 등은 저장소에 올라가지 않습니다(`.gitignore`).
- 서버에 24시간 올리는 방법은 `deploy/README.md`를 보세요.
- 여러 사람이 함께 고칠 때의 규칙은 `CLAUDE.md`에 있습니다.

## 구성
- `bot.py` 메뉴·화면, `admin.py` 관리자 패널(/admin), `events.py` 이벤트, `support.py` 1:1 문의 중계
- `sports.py`·`flashscore.py` 스코어, `posts.py`·`picks_*.py` 분석글과 추천픽
- `menuloop.py`·`scheduled.py`·`popular.py` 반복·예약·인기글 게시

⚠️ 봇 토큰과 `.env` 는 절대 올리지 마세요.
