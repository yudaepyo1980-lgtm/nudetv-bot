"""이벤트 공지: 누드TV 자체 이벤트(구글 검색 응원, 그룹방 입장 등)를 봇에 저장해 두고,
메뉴의 '이벤트' 버튼으로 보여주거나 /admin 패널에서 정한 방에 공지글로 올린다."""
import logging
import re
import sqlite3
import os

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

import settings
import textfmt

log = logging.getLogger("events")
_decorate = lambda text: None  # bot.py 가 register() 로 넘겨 줌 (🦊 → 구미호 커스텀 이모지)
STATUS_KO = {"on": "진행중", "draft": "기획", "off": "종료"}


def _db():
    c = sqlite3.connect(settings.DB)
    c.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, body TEXT, status TEXT)")
    if not c.execute("SELECT 1 FROM events LIMIT 1").fetchone() and settings.get("events.seeded") != "1":
        admin = "@" + os.getenv("SUPPORT_USERNAME", "관리자아이디").lstrip("@")
        link = os.getenv("EVENT_GROUP_LINK", "https://t.me/그룹방주소")
        c.executemany("INSERT INTO events (title, body, status) VALUES (?, ?, ?)",
                      [(t, b.replace("{admin}", admin).replace("{group_link}", link).replace("{site}", site_url() or "https://사이트주소"), s) for t, b, s in SEED])
        c.commit()
        settings.set_("events.seeded", "1")
    return c


def all_events(only_status=None):
    with _db() as c:
        q = "SELECT id, title, body, status FROM events" + (" WHERE status=?" if only_status else "") + " ORDER BY id"
        return list(c.execute(q, (only_status,) if only_status else ()))


def get(eid):
    with _db() as c:
        return c.execute("SELECT id, title, body, status FROM events WHERE id=?", (eid,)).fetchone()


def add(title, body, status="draft"):
    with _db() as c:
        return c.execute("INSERT INTO events (title, body, status) VALUES (?, ?, ?)", (title, body, status)).lastrowid


def update(eid, **kw):
    with _db() as c:
        for k, v in kw.items():
            c.execute(f"UPDATE events SET {k}=? WHERE id=?", (v, eid))


def delete(eid):
    with _db() as c:
        c.execute("DELETE FROM events WHERE id=?", (eid,))


def photo_for(eid):
    """이 이벤트 공지에 붙일 사진. 이벤트마다 따로 정한 사진만 쓴다 (목록 화면 사진과는 별개)."""
    own = settings.get(f"event.photo.{eid}")
    return None if own in (None, "none") else own


def list_photo():
    """이벤트 목록 화면 맨 위에 보여줄 사진"""
    return settings.get("event.list_photo")


async def send_list(chat):
    """이벤트 목록 화면을 사진(있으면)과 함께 새 메시지로 보낸다."""
    text, markup = list_screen(room=getattr(chat, "type", "private") != "private")
    photo = list_photo()
    if photo:
        return await chat.send_photo(photo, caption=text, caption_entities=_decorate(text), reply_markup=markup)
    return await chat.send_message(text, entities=_decorate(text), reply_markup=markup)


async def send_notice(bot, chat_id, ev):
    """공지글을 방에 올린다. 사진이 있으면 사진 아래 글(최대 1024자)로, 없으면 글만."""
    text = notice_text(ev)
    markup = notice_markup(bot.username, ev)
    photo = photo_for(ev[0])
    if photo:
        cap = text[:1020] + ("…" if len(text) > 1020 else "")
        return await bot.send_photo(chat_id, photo, caption=cap, caption_entities=textfmt.event_entities(cap), reply_markup=markup)
    return await bot.send_message(chat_id, text, entities=textfmt.event_entities(text), reply_markup=markup)


def admin_url():
    u = (settings.get("event.admin") or os.getenv("SUPPORT_USERNAME", "")).strip().lstrip("@")
    return f"https://t.me/{u}" if u else ""


def notice_text(ev):
    """방에 올리는 공지글: 제목 + 본문 (텔레그램 글자 수 제한 안으로)"""
    eid, title, body, _ = ev
    link = event_url(eid)
    if link and not body.strip():
        body = "자세한 내용은 아래 버튼에서 확인하세요."
    return f"{title}\n\n{body}"[:4000]


def notice_markup(bot_username, ev=None):
    rows = []
    link = event_url(ev[0]) if ev else None
    if link:
        rows.append([B("🌐 이벤트 자세히 보기", url=link)])
    if admin_url():
        rows.append([B("📩 참여 신청 (관리자)", url=admin_url())])
    if bot_username:
        rows.append([B("🎁 진행 중인 이벤트 모두 보기", url=f"https://t.me/{bot_username}?start=events")])
    if page_url():
        rows.append([B("🌐 누드TV 이벤트 페이지", url=page_url())])
    return M(rows) if rows else None


# ---- 사용자 화면 ----
async def _show(q, text, markup, entities=None):
    """메뉴 사진 메시지는 글자로 고칠 수 없어서, 사진이면 지우고 새로 보낸다."""
    if q.message.photo:
        chat = q.message.chat
        await q.message.delete()
        await chat.send_message(text, entities=entities, reply_markup=markup)
    else:
        await q.edit_message_text(text, entities=entities, reply_markup=markup)


_ICON_POOL = [13, 18, 0, 1, 15, 10, 8, 2, 3, 4]  # emoji_work/emoji_ids.json 안의 구미호 캐릭터 번호 (메뉴 버튼에 쓰는 것과 겹치지 않게)


def _fox_ids():
    try:
        import json
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "emoji_work", "emoji_ids.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


_bot_username = ""


def set_bot_username(name):
    global _bot_username
    _bot_username = name or ""


def fox_button(text, i, char=None, **kw):
    """구미호 커스텀 이모지를 아이콘으로 붙인 버튼. char 로 캐릭터를 고르고, 없으면 i 번째마다 다른 캐릭터."""
    ids = _fox_ids()
    idx = char if char is not None else _ICON_POOL[i % len(_ICON_POOL)]
    extra = {"icon_custom_emoji_id": ids[idx]} if ids else None
    return B(text, api_kwargs=extra, **kw)


def event_url(eid):
    """사이트의 이벤트 글로 바로 연결되는 이벤트 (본문을 봇에 저장하지 않고 사이트 글을 연다)"""
    return settings.get(f"event.url.{eid}")


def site_url():
    """사이트 주소는 .env 의 SITE_URL (코드에는 적지 않음)"""
    return os.getenv("SITE_URL", "").strip().rstrip("/")


def page_url():
    return settings.get("event.page") or (site_url() + "/announcements/events" if site_url() else "")


def short_title(title):
    """'📢 [누드티비] 구글 검색 응원 이벤트 (5,000P)' → '구글 검색 응원 이벤트 (5,000P)'"""
    s = re.sub(r"\[[^\]]*\]\s*", "", title)
    return re.sub(r"^[^0-9A-Za-z가-힣]+", "", s).strip() or title


def visible_events():
    """메뉴에 보이는 이벤트: 기획·진행중 구분 없이 등록 순서대로. 종료만 숨김."""
    return [e for e in all_events() if e[3] in ("on", "draft")]


DEFAULT_LIST_TEXT = (
    "🦊 누드TV 이벤트 🦊\n\n"
    "🦊 참여하고 포인트 받기\n"
    "🦊 지금 열린 이벤트 {n}개\n"
    "🦊 혜택·참여 방법은 각각 달라요\n\n"
    "궁금한 이벤트를 눌러 보세요 👇"
)


SHORT_LIST_TEXT = (
    "🦊 누드TV 이벤트 🦊\n\n"
    "🦊 참여하고 포인트 받기\n"
    "🦊 지금 열린 이벤트 {n}개\n\n"
    "전체 이벤트는 아래에서 확인하세요 👇"
)


def show_buttons():
    """목록 화면에 이벤트별 버튼을 보여줄지 (기본: 숨김, 이벤트 페이지 버튼 하나만)"""
    return settings.get("event.show_buttons") == "1"


def list_text(n):
    """목록 화면 문구. /admin 에서 바꾼 문구가 있으면 그것을 쓰고, {n} 은 열린 이벤트 개수로 바뀐다."""
    default = DEFAULT_LIST_TEXT if show_buttons() else SHORT_LIST_TEXT
    return (settings.get("event.list_text") or default).replace("{n}", str(n))


def button_label(eid, title):
    """버튼 글자: 따로 정한 이름이 있으면 그것, 없으면 제목에서 이모지·[..]를 떼고 폰에서 한 줄에 들어가게 자른다."""
    custom = settings.get(f"event.label.{eid}")
    if custom:
        return custom
    s = short_title(title)
    return s if len(s) <= 18 else s[:17] + "…"


def list_screen(room=False):
    """이벤트 목록 화면. room=True 면 방에 올리는 글이라 눌러도 글이 바뀌는 '메뉴' 버튼을 뺀다."""
    evs = visible_events()
    rows = [[fox_button("누드TV 이벤트 페이지", 0, char=13, url=page_url())]] if page_url() else []
    for i, (eid, title, _, st) in enumerate(evs if show_buttons() else []):
        link = event_url(eid)
        name = button_label(eid, title)
        rows.append([fox_button(name, i, url=link) if link else fox_button(name, i, callback_data=f"ev_{eid}")])
    if admin_url():
        rows.append([fox_button("이벤트 문의", 0, char=4, url=admin_url())])
    if _bot_username:  # 눌러도 방의 글이 바뀌지 않게, 봇과의 1:1 대화로 이동해서 보여줌
        rows.append([fox_button("오늘의 픽 확인", 0, char=2, url=f"https://t.me/{_bot_username}?start=picktoday")])
    if not room:
        rows.append([B("⬅️ 메뉴", callback_data="menu")])
    if evs:
        text = list_text(len(evs))
    else:
        text = "🦊 누드TV 이벤트 🦊\n\n지금 열려 있는 이벤트가 없어요.\n새 이벤트가 열리면 이곳에 올라와요."
    return text, M(rows)


async def events_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat = q.message.chat
    try:
        await q.message.delete()
    except Exception:
        pass
    await send_list(chat)


async def event_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ev = get(int(q.data[3:]))
    if not ev or ev[3] not in ("on", "draft"):
        chat = q.message.chat
        await q.message.delete()
        await send_list(chat)
        return
    rows = []
    if admin_url():
        rows.append([B("📩 참여 신청 (관리자)", url=admin_url())])
    if page_url():
        rows.append([B("🌐 누드TV 이벤트 페이지", url=page_url())])
    rows.append([B("⬅️ 이벤트 목록", callback_data="events")])
    rows.append([B("⬅️ 메뉴", callback_data="menu")])
    photo, text = photo_for(ev[0]), notice_text(ev)
    if photo:  # 사진이 있으면 사진 아래 글로 보여주고, 이전 화면은 지운다
        chat = q.message.chat
        await q.message.delete()
        cap = text[:1020] + ("…" if len(text) > 1020 else "")
        await chat.send_photo(photo, caption=cap, caption_entities=textfmt.event_entities(cap), reply_markup=M(rows))
    else:
        await _show(q, text, M(rows), textfmt.event_entities(text))


def register(app, decorate=None):
    global _decorate
    if decorate:
        _decorate = decorate
    app.add_handler(CallbackQueryHandler(events_menu, pattern="^events$"))
    app.add_handler(CallbackQueryHandler(event_detail, pattern="^ev_\\d+$"))


SEED = [
    ("📢 [누드티비] 구글 검색 응원 이벤트 (5,000P)", """회원님들의 검색 한 번이 누드티비에 큰 힘이 됩니다!
회원님이 직접 고른 키워드로 구글 검색 후 들어오시면 5,000 포인트를 드립니다.

🔹 참여 방법
1. 구글(Google) 접속
2. 원하는 키워드로 검색한 뒤 누드티비 접속
3. 검색한 키워드와 누드티비 접속 화면이 함께 보이도록 캡처
4. 텔레그램 관리자({admin})에게 캡처본 전송

📌 유의사항
- 1일 최대 2회 참여 가능 (서로 다른 키워드 사용 필수)
- 동일 키워드 중복 참여 불가""", "on"),
    ("📢 [누드티비] 텔레그램 그룹방 입장 이벤트 (5,000P)", """누드티비 공식 텔레그램 그룹방에 입장하시면 5,000 포인트를 드립니다!
공지, 이벤트, 점검 소식을 가장 빠르게 받아보세요.

🔹 참여 방법
1. 누드티비 공식 텔레그램 그룹방 입장 ({group_link})
2. 입장한 화면 캡처 (그룹방 이름과 내 텔레그램 계정명이 보이도록)
3. 텔레그램 관리자({admin})에게 캡처본과 누드티비 아이디 전송
4. 확인 후 5,000P 지급

📌 유의사항
- 회원 1인당 1회만 참여 가능
- 입장 후 바로 퇴장하면 지급된 포인트가 회수될 수 있습니다
- 허위·조작 캡처는 포인트 회수 및 이용 제한 사유가 됩니다.

🎁 지급 포인트 : 5,000P
📢 지급 조건 : 운영진 확인 후 지급""", "on"),
    ("🎯 [누드티비] 데일리 미션 이벤트 (매일 2,000P)", """누드티비 커뮤니티 활동에 더 큰 재미를 드리기 위한 이벤트입니다.
매일 한 번 참여할 수 있으며, 미션 2개를 완료하면 2,000포인트가 적립됩니다.

📝 자유게시판 미션 (1,000P)
- 게시글 1개 50자 이상 작성
- 댓글 5개 이상 작성

⭐ 이용후기 미션 (1,000P)
- 게시글 1개 50자 이상 작성
- 댓글 5개 10자 이상 작성

🔹 포인트 수령 방법
1. 두 가지 미션을 모두 완료
2. 작성한 게시글 링크 또는 캡처를 텔레그램 관리자({admin})에게 전송
3. 운영진 확인 후 2,000P 지급

📌 유의사항
- 매일 1회 참여 가능
- 무성의하거나 게시판 특성과 맞지 않는 글은 포인트가 회수 처리됩니다.

누드티비 운영팀""", "draft"),
    ("📣 [누드티비] 홍보 이벤트 (홍보비 최대 2,500P)", """커뮤니티, 카페, SNS 등 어디서든 누드티비 홍보글을 작성하고 포인트를 받아가세요!
홍보글 1건당 500P, 하루 최대 5건 참여할 수 있습니다. (최대 2,500P)

🔹 참여 방법
1. 홍보가 가능한 커뮤니티에 누드티비 홍보글 5건까지 작성
2. 홍보글 하단에 아래 필수 내용 기재
3. 작성한 홍보글 링크와 캡처, 누드티비 닉네임을 텔레그램 관리자({admin})에게 전달
4. 확인 후 1건당 500P 지급

✍️ 홍보글 하단 필수 작성 내용
- 누드티비 바로가기 : {site}
- 누드티비 평생주소 : {site}/
- 구글검색 : 누드티비

📌 유의사항
- 홍보 1건당 500P, 하루 최대 5건까지 참여 가능
- 같은 글을 중복 제출하거나 삭제된 글은 지급되지 않습니다.
- 필수 작성 내용이 빠진 글은 인정되지 않습니다.
- 각 커뮤니티의 운영 규정을 지켜 작성해 주세요. 규정 위반에 대한 책임은 작성자에게 있습니다.

누드티비 운영팀""", "draft"),
]
