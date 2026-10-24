"""사이트 인기글 소개: 누드TV 자유게시판·유머·스포츠토론에서 최근 가장 반응이 좋은 글을 하나씩 골라
정해진 시각에 방에 올린다. 제목을 누르면 사이트 글로 열린다. /admin 의 '📰 인기글 소개'에서 켜고 설정한다."""
import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import httpx
from telegram import LinkPreviewOptions, MessageEntity, InlineKeyboardMarkup as M

import alerts
import events
import settings

log = logging.getLogger("popular")
KST = timezone(timedelta(hours=9))
# (이름, 게시판 종류, 사이트 주소의 게시판 이름)
GROUPS = [("자유", "free", "free-board"), ("유머", "humor", "humor"), ("스포츠", "sports_talk", "sports-talk")]
DEFAULT_BLOCK = ["야한", "야설", "성인", "19금", "섹스", "섹시", "야동", "노출", "에로", "몸매", "가슴", "엉덩이", "팬티", "유두", "불륜",
                 "도박", "카지노", "배팅", "베팅", "먹튀", "토토"]
TIMES = ["12:00", "18:00", "20:00", "22:00"]
RECENT_DAYS = (7, 14)  # 먼저 7일 안에서 고르고, 없으면 14일 안에서 고른다
_decorate = lambda text: None


def set_decorate(fn):
    global _decorate
    _decorate = fn


def _now():
    return datetime.now(KST)


def block_words():
    extra = settings.get("popular.block") or ""
    return DEFAULT_BLOCK + [w.strip() for w in extra.split(",") if w.strip()]


def state():
    chat = settings.get("popular.chat") or settings.get("result_chat")
    return {"on": settings.get("popular.on") == "1", "chat": int(chat) if chat else None,
            "time": settings.get("popular.time", "20:00"), "last_day": settings.get("popular.last_day", "")}


def _api():
    return os.getenv("BOARD_API", "")


def _score(p):
    return (p.get("view_count") or 0) + 5 * (p.get("comment_count") or 0) + 3 * (p.get("like_count") or 0)


def _ok(p, words):
    title = p.get("title", "")
    return not p.get("is_notice") and not any(w.lower() in title.lower() for w in words)


async def pick_posts():
    """게시판별로 최근 글 중 점수(조회+댓글·좋아요 가중)가 가장 높은 글 하나씩. [(이름, 글, 사이트 게시판)]"""
    words = block_words()
    out = []
    async with httpx.AsyncClient(timeout=40, headers={"User-Agent": "Mozilla/5.0"}) as c:
        for label, board, route in GROUPS:
            try:
                r = await c.get(_api(), params={"board": board, "page": 1, "pageSize": 40})
                posts = [p for p in r.json().get("posts", []) if p.get("board_type") == board and _ok(p, words)]
            except Exception:
                log.exception("인기글 조회 실패 (%s)", board)
                continue
            for days in RECENT_DAYS:
                limit = _now() - timedelta(days=days)
                recent = [p for p in posts if datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")) > limit]
                if recent:
                    out.append((label, max(recent, key=_score), route))
                    break
    return out


def _u16(s):
    return len(s.encode("utf-16-le")) // 2


def build(picks):
    """(글, 글자 꾸밈 목록): 제목은 누르면 사이트 글로 열리는 링크, 🦊 는 구미호 이모지"""
    text = "🦊 오늘의 누드TV 인기글 🦊\n"
    links, bolds = [], []
    for label, p, route in picks:
        title = p["title"].strip()
        title = title if len(title) <= 38 else title[:37] + "…"
        text += f"\n🦊 "
        bolds.append((_u16(text), _u16(f"[{label}]")))  # 게시판 이름은 굵게
        text += f"[{label}] "
        links.append((_u16(text), _u16(title), f"{events.site_url()}/community/{route}/{p['id']}"))
        text += title
    text += "\n\n제목을 누르면 바로 볼 수 있어요 👇"
    ents = list(_decorate(text) or [])
    ents += [MessageEntity(MessageEntity.TEXT_LINK, off, ln, url=url) for off, ln, url in links]
    ents += [MessageEntity(MessageEntity.BOLD, off, ln) for off, ln in bolds]
    ents.sort(key=lambda e: (e.offset, -e.length))
    return text, ents


def markup():
    url = settings.button_state("home")[1] or events.site_url()
    return M([[events.fox_button("누드TV 바로가기", 0, char=5, url=url)]]) if url else None


async def post(bot, chat_id):
    picks = await pick_posts()
    if not picks:
        return None
    text, ents = build(picks)
    return await bot.send_message(chat_id, text, entities=ents, reply_markup=markup(),
                                  link_preview_options=LinkPreviewOptions(is_disabled=True))


async def maybe_post(bot):
    s = state()
    if not s["on"] or not s["chat"]:
        return
    now = _now()
    today = now.strftime("%Y-%m-%d")
    if s["last_day"] == today:
        return
    hh, mm = (int(x) for x in s["time"].split(":"))
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < due:
        return
    settings.set_("popular.last_day", today)  # 실패해도 하루에 한 번만 시도
    if now - due > timedelta(hours=3):
        return  # 봇이 꺼져 있다 한참 뒤에 켜졌다면 오늘은 건너뜀
    try:
        await post(bot, s["chat"])
    except Exception as e:
        log.exception("인기글 소개 올리기 실패")
        await alerts.notify(bot, f"🔴 인기글 소개 올리기 실패\n{type(e).__name__}: {alerts._clean(e)}", key="popular_fail")


async def loop(bot):
    await asyncio.sleep(55)
    while True:
        try:
            await maybe_post(bot)
        except Exception:
            log.exception("인기글 소개 점검 오류")
        await asyncio.sleep(60)
