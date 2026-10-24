"""메뉴 반복 게시: 봇 메뉴(사진, 문구, 버튼)를 정한 방에 일정 간격으로 다시 올린다.
새로 올릴 때 이전에 올린 메뉴 글은 지워서 방에 같은 글이 쌓이지 않게 한다. /admin 패널의 '🔁 메뉴 반복 게시'에서 켜고 끈다."""
import asyncio
import logging
import time

import alerts
import settings

log = logging.getLogger("menuloop")
INTERVALS = (1, 2, 3, 6)  # 시간
CHECK_EVERY = 60


KINDS = {
    "menu": {"prefix": "menuloop", "title": "메뉴", "hours": 1},
    "events": {"prefix": "evloop", "title": "이벤트 목록", "hours": 2},
}


def _k(kind, name):
    return f"{KINDS[kind]['prefix']}.{name}"


def state(kind="menu"):
    chat = settings.get(_k(kind, "chat"))
    return {
        "on": settings.get(_k(kind, "on")) == "1",
        "chat": int(chat) if chat else None,
        "hours": int(settings.get(_k(kind, "hours"), str(KINDS[kind]["hours"]))),
        "last": float(settings.get(_k(kind, "last"), "0")),
    }


def set_on(kind, on):
    settings.set_(_k(kind, "on"), "1" if on else "0")
    if on:
        settings.set_(_k(kind, "last"), "0")  # 켜면 바로 한 번 올림


def set_hours(kind, hours):
    settings.set_(_k(kind, "hours"), str(int(hours)))


def set_chat(kind, chat_id):
    settings.set_(_k(kind, "chat"), str(chat_id))
    settings.delete(_k(kind, "msg"))  # 방이 바뀌면 이전 방의 글은 건드리지 않음


def last_text(s):
    if not s["last"]:
        return "아직 올린 적 없음"
    return time.strftime("%m-%d %H:%M", time.localtime(s["last"]))


async def post_now(bot, send_fn, kind="menu"):
    """이전에 올린 글을 지우고 새로 올린다. 올린 메시지를 돌려준다."""
    s = state(kind)
    if not s["chat"]:
        raise ValueError("방이 정해지지 않았어요")
    prev = settings.get(_k(kind, "msg"))
    if prev:
        try:
            await bot.delete_message(s["chat"], int(prev))
        except Exception:
            pass  # 이미 지워졌거나 48시간이 지나 못 지우는 경우
    chat = await bot.get_chat(s["chat"])
    msg = await send_fn(chat)
    settings.set_(_k(kind, "last"), str(time.time()))
    if msg is not None:
        settings.set_(_k(kind, "msg"), str(msg.message_id))
    return msg


async def loop(bot, send_menu, send_events=None):
    await asyncio.sleep(45)
    senders = {"menu": send_menu, "events": send_events}
    while True:
        for kind, send_fn in senders.items():
            if send_fn is None:
                continue
            try:
                s = state(kind)
                if s["on"] and s["chat"] and time.time() >= s["last"] + s["hours"] * 3600:
                    await post_now(bot, send_fn, kind)
            except Exception as e:
                log.exception("%s 반복 게시 실패", kind)
                await alerts.notify(bot, f"🔴 {KINDS[kind]['title']} 반복 게시 실패\n{type(e).__name__}: {alerts._clean(e)}", key=f"menuloop_fail_{kind}")
                settings.set_(_k(kind, "last"), str(time.time()))  # 실패해도 다음 주기까지 기다림(계속 재시도하며 도배하지 않게)
        await asyncio.sleep(CHECK_EVERY)
