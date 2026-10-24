"""예약 발송: 관리자가 정한 시각에 글(또는 사진+글)을 결과방·컨트롤 그룹 등에 올린다. 등록은 /admin 패널의 '🗓 예약 발송'에서 한다."""
import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from telegram import MessageEntity

import alerts
import settings

log = logging.getLogger("scheduled")
KST = timezone(timedelta(hours=9))
CHECK_EVERY = 30
LATE_LIMIT = timedelta(hours=6)  # 봇이 꺼져 있다 늦게 켜져도 이 시간 안이면 올리고, 넘으면 건너뜀


def _db():
    c = sqlite3.connect(settings.DB)
    c.execute("""CREATE TABLE IF NOT EXISTS scheduled (
        id INTEGER PRIMARY KEY AUTOINCREMENT, when_at TEXT, chat_id INTEGER, target TEXT,
        text TEXT, entities TEXT, photo TEXT, by_id INTEGER, by_name TEXT, status TEXT, note TEXT)""")
    return c


def now():
    return datetime.now(KST)


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def parse_time(s):
    """'21:30'(지났으면 내일), '10/20 09:00', '10-20 09:00', '2026-10-20 09:00' → 한국시간 datetime, 못 읽으면 None"""
    s = s.strip()
    n = now()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m:
        t = n.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        return t if t > n else t + timedelta(days=1)
    m = re.fullmatch(r"(?:(\d{4})[-/.])?(\d{1,2})[-/.](\d{1,2})\s+(\d{1,2}):(\d{2})", s)
    if m:
        y = int(m.group(1)) if m.group(1) else n.year
        try:
            return datetime(y, int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), tzinfo=KST)
        except ValueError:
            return None
    return None


def add(when, chat_id, target, text, entities, photo, by_id, by_name):
    with _db() as c:
        cur = c.execute("INSERT INTO scheduled (when_at, chat_id, target, text, entities, photo, by_id, by_name, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                        (fmt(when), chat_id, target, text, json.dumps(entities or [], ensure_ascii=False), photo, by_id, by_name))
        return cur.lastrowid


def pending():
    with _db() as c:
        return list(c.execute("SELECT id, when_at, target, text, photo FROM scheduled WHERE status='pending' ORDER BY when_at"))


def recent_done(n=3):
    with _db() as c:
        return list(c.execute("SELECT id, when_at, target, status, note FROM scheduled WHERE status!='pending' ORDER BY id DESC LIMIT ?", (n,)))


def cancel(sid):
    with _db() as c:
        c.execute("UPDATE scheduled SET status='canceled' WHERE id=? AND status='pending'", (sid,))


def _set(sid, status, note=""):
    with _db() as c:
        c.execute("UPDATE scheduled SET status=?, note=? WHERE id=?", (status, note, sid))


async def send_due(bot):
    t = fmt(now())
    with _db() as c:
        due = list(c.execute("SELECT id, when_at, chat_id, target, text, entities, photo FROM scheduled "
                             "WHERE status='pending' AND when_at<=? ORDER BY when_at", (t,)))
    for sid, when_at, chat_id, target, text, ents, photo in due:
        when = datetime.strptime(when_at, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        if now() - when > LATE_LIMIT:
            _set(sid, "missed", "봇이 꺼져 있어 시간이 많이 지나 건너뜀")
            await alerts.notify(bot, f"⚠️ 예약 #{sid}({when_at}, {target})은 시간이 많이 지나 올리지 못했어요.", key=f"sched_missed_{sid}")
            continue
        entities = [MessageEntity.de_json(e, None) for e in json.loads(ents or "[]")] or None
        try:
            if photo:
                await bot.send_photo(chat_id, photo, caption=(text or "")[:1024] or None, caption_entities=entities if text else None)
            else:
                await bot.send_message(chat_id, text, entities=entities)
            _set(sid, "sent")
        except Exception as e:
            log.exception("예약 발송 실패")
            _set(sid, "failed", f"{type(e).__name__}")
            await alerts.notify(bot, f"🔴 예약 #{sid}({when_at}, {target}) 발송 실패\n{type(e).__name__}: {alerts._clean(e)}", key=f"sched_fail_{sid}")


async def loop(bot):
    await asyncio.sleep(20)
    while True:
        try:
            await send_due(bot)
        except Exception:
            log.exception("예약 발송 점검 오류")
        await asyncio.sleep(CHECK_EVERY)
