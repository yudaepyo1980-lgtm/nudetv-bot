"""봇 이상 알림: 스코어·게시판 조회가 계속 실패하거나 예상 못 한 오류가 나면 관리자에게 텔레그램으로 알린다."""
import asyncio
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from telegram.error import BadRequest, Conflict, NetworkError, RetryAfter, TimedOut

import flashscore
import posts
import sports

log = logging.getLogger("alerts")
KST = timezone(timedelta(hours=9))
COOLDOWN = 1800  # 같은 문제는 30분에 한 번만 알림
CHECK_EVERY = 600  # 10분마다 점검
FAILS_BEFORE_ALERT = 2  # 연속 2번 실패하면 알림
_conflicts = []  # 최근 Conflict(봇이 두 곳에서 동시에 켜짐) 발생 시각
_last_sent = {}
_state = {}  # 점검 항목 -> {"fails": 연속 실패 횟수, "alerted": 알림 보냈는지}


def _admins():  # .env는 bot.py가 불러온 뒤에 읽어야 해서 쓸 때 계산
    return [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]


def _clean(text):
    return re.sub(r"\d{6,}:[A-Za-z0-9_-]{30,}", "<토큰>", str(text))[:300]  # 토큰이 알림에 섞이지 않게


async def notify(bot, text, key=None):
    now = time.time()
    if key and now - _last_sent.get(key, 0) < COOLDOWN:
        return
    if key:
        _last_sent[key] = now
    for admin in _admins():
        try:
            await bot.send_message(admin, text)
        except Exception:
            log.exception("관리자 알림 전송 실패")


async def on_error(update, context):
    err = context.error
    log.error("처리되지 않은 오류", exc_info=err)
    if isinstance(err, (NetworkError, TimedOut, RetryAfter)):
        return  # 일시적인 연결 문제는 알리지 않음
    if isinstance(err, Conflict):
        # 다른 곳에서 잠깐 getUpdates 를 호출하면 한 번 나고 저절로 풀림. 5분 안에 3번 이상 반복될 때만 알림.
        now = time.time()
        _conflicts[:] = [t for t in _conflicts if now - t < 300] + [now]
        if len(_conflicts) < 3:
            return
        await notify(context.bot, "⚠️ 봇이 두 곳에서 동시에 켜져 있는 것 같아요 (Conflict가 계속 반복돼요).\n"
                                  "다른 컴퓨터·서버·다른 작업창에서 같은 봇 토큰으로 봇을 켜 둔 곳이 없는지 확인해 주세요.", key="conflict")
        return
    if isinstance(err, BadRequest) and any(s in str(err) for s in ("Query is too old", "message is not modified", "message to edit not found")):
        return  # 오래된 버튼을 누르거나 같은 내용으로 수정한 경우는 문제가 아님
    msg = f"⚠️ 봇 오류\n{type(err).__name__}: {_clean(err)}"
    await notify(context.bot, msg, key=f"err:{type(err).__name__}:{_clean(err)[:60]}")


async def _check_board():
    items = await posts._fetch_site(1)
    if not items:
        raise ValueError("게시판 글이 0개")


async def _check_naver():
    day = datetime.now(KST).strftime("%Y-%m-%d")
    async with httpx.AsyncClient(timeout=15, headers=sports.HEADERS) as c:
        r = await c.get(sports.API, params={"fields": "basic", "upperCategoryId": "kfootball",
                                            "fromDate": day, "toDate": day, "size": 1})
        r.raise_for_status()
        r.json()["result"]


async def _check_flashscore():
    await flashscore._leagues("football")


CHECKS = {"게시판 글 조회": _check_board, "네이버 스포츠 일정": _check_naver, "플래시스코어 스코어": _check_flashscore}


async def run_checks(bot):
    for name, fn in CHECKS.items():
        st = _state.setdefault(name, {"fails": 0, "alerted": False})
        try:
            await fn()
        except Exception as e:
            st["fails"] += 1
            log.warning("점검 실패 %s (%d회): %s", name, st["fails"], _clean(e))
            if st["fails"] >= FAILS_BEFORE_ALERT and not st["alerted"]:
                st["alerted"] = True
                await notify(bot, f"🔴 {name} 실패 ({st['fails']}번 연속)\n{type(e).__name__}: {_clean(e)}")
        else:
            if st["alerted"]:
                await notify(bot, f"🟢 {name} 다시 정상입니다.")
            st.update(fails=0, alerted=False)


async def healthcheck(bot):
    await asyncio.sleep(60)  # 시작 직후에는 잠시 기다림
    while True:
        try:
            await run_checks(bot)
        except Exception:
            log.exception("점검 루프 오류")
        await asyncio.sleep(CHECK_EVERY)
