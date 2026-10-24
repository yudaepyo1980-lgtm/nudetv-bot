import asyncio
import json
import re
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import LinkPreviewOptions, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, MessageEntity, Update
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import admin
import ai
import alerts
import chatstats
import events
import favorites
import flashscore
import picks_report
import picks_today
import posts
import menuloop
import popular
import scheduled
import seo
import settings
import singleton
import sports
import support
import textfmt

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # 로그에 봇 토큰이 찍히지 않게
log = logging.getLogger("bot")
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}


try:  # 구미호 커스텀 이모지 번호 목록 (없으면 이모지 없이 동작)
    EMOJI_IDS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "emoji_work", "emoji_ids.json")))
except (FileNotFoundError, json.JSONDecodeError):
    EMOJI_IDS = []
DECO_CYCLE = [EMOJI_IDS[i] for i in (1, 3, 4, 6, 9, 11, 14, 5) if i < len(EMOJI_IDS)]


try:  # 프리미엄 이모지 지정: {"🎯": "커스텀이모지ID", ...}  (봇에 프리미엄 이모지를 보내면 ID를 알려줌)
    PREMIUM_MAP = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "emoji_work", "premium_map.json")))
except (FileNotFoundError, json.JSONDecodeError):
    PREMIUM_MAP = {}


def decorate(text, ids=DECO_CYCLE, limit=90):
    """글 속의 🦊 자리마다 구미호 커스텀 이모지를 돌아가며 입히고, premium_map.json 에 있는 이모지도 프리미엄으로 바꾼다. (텔레그램 오프셋은 UTF-16 기준)"""
    fmt = textfmt.format_entities(text)  # 굵은 제목·경기 이름·픽
    limit = min(limit, 98 - len(fmt))  # 텔레그램은 한 글에 꾸밈을 100개까지만 받음
    ents, pos, n = [], 0, 0
    for ch in text:
        size = 2 if ord(ch) > 0xFFFF else 1
        if ch == "🦊" and n < limit and ids:
            ents.append(MessageEntity(MessageEntity.CUSTOM_EMOJI, pos, 2, custom_emoji_id=ids[n % len(ids)]))
            n += 1
        elif ch in PREMIUM_MAP and len(ents) < 100:
            ents.append(MessageEntity(MessageEntity.CUSTOM_EMOJI, pos, size, custom_emoji_id=PREMIUM_MAP[ch]))
        pos += size
    ents = sorted(ents + fmt, key=lambda e: (e.offset, -e.length))
    return ents or None


BANNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "banner.jpg")
PICKS_BANNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "picks_banner.jpg")
MENU_TEXT = (
    "🦊 누드TV 스포츠 안내 🦊\n\n"
    "🦊 해외축구 · EPL · NBA · MLB 실시간 중계 일정\n"
    "🦊 라이브스코어 · 경기 분석\n"
    "🦊 하이라이트 정보\n\n"
    "아래 버튼을 눌러 확인하세요 👇"
)
MENU_ENTITIES = None  # decorate() 로 아래에서 채움


MENU_ENTITIES = decorate(MENU_TEXT)
NOTICE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notice.json")


def load_notice():
    try:
        with open(NOTICE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


async def send_menu(chat, bot_message=None):
    """메뉴: (등록한 공지 또는 기본 문구) + 사진 + 버튼."""
    n = load_notice()
    text, entities, photo = MENU_TEXT, MENU_ENTITIES, None
    if n:
        text = n.get("text") or MENU_TEXT
        entities = ([MessageEntity.de_json(e, None) for e in n.get("entities", [])] or None) if n.get("text") else MENU_ENTITIES
        photo = n.get("photo_file_id")
    try:
        if photo:
            return await chat.send_photo(photo, caption=text, caption_entities=entities, reply_markup=main_menu())
        elif os.path.exists(BANNER):
            with open(BANNER, "rb") as f:
                return await chat.send_photo(f, caption=text, caption_entities=entities, reply_markup=main_menu())
        else:
            return await chat.send_message(text, entities=entities, reply_markup=main_menu())
    except Exception:
        # 등록한 공지가 문제를 일으키면 기본 메뉴로 대체
        return await chat.send_message(MENU_TEXT, entities=MENU_ENTITIES, reply_markup=main_menu())


async def setnotice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """관리자가 공지 메시지에 답장하며 /setnotice 하면 /start 문구로 저장"""
    if not settings.is_admin(update.effective_user.id):
        return
    src = update.message.reply_to_message
    if not src or not (src.text or src.caption):
        await update.message.reply_text("공지 메시지에 '답장'으로 /setnotice 를 보내주세요.")
        return
    ents = src.entities if src.text else src.caption_entities
    data = {
        "text": src.text or src.caption,
        "entities": [e.to_dict() for e in (ents or [])],
        "photo_file_id": src.photo[-1].file_id if src.photo else None,
    }
    with open(NOTICE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    await update.message.reply_text("공지 등록 완료 ✅ /start 로 확인하세요.")


async def clearnotice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if settings.is_admin(update.effective_user.id) and os.path.exists(NOTICE_FILE):
        os.remove(NOTICE_FILE)
        await update.message.reply_text("공지를 지우고 기본 문구로 되돌렸습니다.")


def u16(s):
    return len(s.encode("utf-16-le")) // 2


async def show(q, text, markup, extra=None):
    """사진 메시지는 글자로 수정할 수 없어서, 사진이면 지우고 새 글을 보낸다."""
    if q.message.photo:
        chat = q.message.chat  # 새 글 보내기와 옛 사진 지우기를 동시에 (왕복 시간이 길어서)
        await asyncio.gather(
            chat.send_message(text, entities=(decorate(text) or []) + (extra or []) or None, reply_markup=markup),
            q.message.delete(),
        )
    else:
        await q.edit_message_text(text, entities=(decorate(text) or []) + (extra or []) or None, reply_markup=markup)


def btn(text, style=None, emoji_id=None, **kw):
    """style: primary(파랑)/success(초록)/danger(빨강), emoji_id: 프리미엄 커스텀 이모지 ID"""
    extra = {}
    if style:
        extra["style"] = style
    if emoji_id:
        extra["icon_custom_emoji_id"] = emoji_id
    return InlineKeyboardButton(text, api_kwargs=extra or None, **kw)


def main_menu():
    """메뉴 버튼은 settings.BUTTONS 에서 만든다. 이름·링크·숨김은 /admin 패널에서 바꾼다."""
    rows = []
    for spec in settings.BUTTONS:
        label, url, hidden = settings.button_state(spec["key"])
        if hidden:
            continue
        icon = os.getenv(spec["icon"]) if spec["icon"] else None
        target = {"url": url} if spec["kind"] == "url" else {"callback_data": spec["cb"]}
        if spec["key"] == "support" and support.enabled():
            target = {"callback_data": "support"}  # 문의방이 정해지면 봇이 문의를 중계
        rows.append([btn(label, style=spec["style"], emoji_id=icon or None, **target)])
    return InlineKeyboardMarkup(rows)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.args and ctx.args[0] == "support" and update.effective_chat.type == "private" and support.enabled():
        await support.open_private(update.effective_chat, ctx.user_data)
        return
    if ctx.args and ctx.args[0] == "picks" and update.effective_chat.type == "private":
        text, markup = await build_picks()
        if os.path.exists(PICKS_BANNER):
            _remember_photo(await update.effective_chat.send_photo(_picks_photo(), caption=text, caption_entities=decorate(text), reply_markup=markup))
        else:
            await update.message.reply_text(text, entities=decorate(text), reply_markup=markup)
        return
    if ctx.args and ctx.args[0] == "score" and update.effective_chat.type == "private":
        text = "🦊 오늘의 경기결과 스코어 🦊\n\n종목을 선택하세요 👇"
        rows = sports_menu() + [[InlineKeyboardButton("⬅️ 메뉴", callback_data="menu")]]
        await update.message.reply_text(text, entities=decorate(text), reply_markup=InlineKeyboardMarkup(rows))
        return
    if ctx.args and ctx.args[0] == "picktoday" and update.effective_chat.type == "private":
        text, markup = await build_picks("today")
        await update.message.reply_text(text, entities=decorate(text), reply_markup=markup)
        return
    if ctx.args and ctx.args[0] == "events" and update.effective_chat.type == "private":
        await events.send_list(update.effective_chat)
        return
    if ctx.args and ctx.args[0] == "fav" and update.effective_chat.type == "private":
        text, rows = await fav_screen(update.effective_user.id)
        await update.message.reply_text(text, entities=decorate(text), reply_markup=InlineKeyboardMarkup(rows))
        return
    sticker = os.getenv("STICKER_ID", "").strip()
    if sticker:
        try:
            await update.effective_chat.send_sticker(sticker)
        except Exception:
            pass  # 스티커 번호가 틀려도 메뉴는 보여줌
    await send_menu(update.effective_chat)


async def stickerid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """스티커를 보내면 번호(file_id)를 알려줌"""
    await update.message.reply_text(f"스티커 ID\n{update.message.sticker.file_id}")


def category_rows(current=None):
    icons = {"ovfb": "⚽", "dofb": "🇰🇷", "bask": "🏀", "base": "⚾", "hock": "🏒", "voll": "🏐", "esp": "🎮"}
    btns = [
        InlineKeyboardButton(("✔ " if k == current else "") + f"{icons[k]} {n}", callback_data=f"pk_{k}_0")
        for k, n, _ in posts.CATEGORIES
    ]
    # 해외축구·국내축구 순서로 보이게 앞의 두 개를 바꿔 2열로 배치
    order = ["ovfb", "dofb", "bask", "base", "hock", "voll", "esp"]
    by = {k: b for (k, _, _), b in zip(posts.CATEGORIES, btns)}
    ordered = [by[k] for k in order]
    today = InlineKeyboardButton(("✔ " if current == "today" else "") + "🎯 오늘의 추천픽", callback_data="pk_today_0")
    return [[today], ordered[0:2], ordered[2:4], ordered[4:6], ordered[6:7]]


def _split_title(title):
    """'[리그 프리뷰] A vs B · C vs D' -> ('리그', 'A vs B 외 1경기')"""
    m = re.match(r"\s*\[([^\]]*)\]\s*(.*)", title)
    league, rest = (m.group(1), m.group(2)) if m else ("", title)
    league = re.sub(r"\s*\d+경기|\s*(프리뷰|핵심 정리|선발 매치업|분석)$", "", league).strip()
    rest = re.sub(r"\s*(핵심 정리|선발 매치업|분석|프리뷰)$", "", rest).strip()
    games = [g.strip() for g in rest.split("·") if g.strip()]
    match = games[0] if games else rest
    if len(games) > 1:
        match += f" 외 {len(games) - 1}경기"
    return league, match


def _emo(s):
    return sum(s.count(c) for c in "🦊🎯⏰🔸")


MAX_EMO = 70  # 텔레그램은 한 메시지에 커스텀 이모지 100개까지만 인식 -> 넘으면 전부 일반 이모지로 보임


def pick_pages(items, limit_chars=3300):
    pages, cur = [], ""
    for it in items:
        league, match = _split_title(it["title"])
        head = " · ".join(x for x in (it["kickoff"] and f"⏰ {it['kickoff']}", league) if x)
        block = (head + "\n" if head else "") + f"🦊 {match}"
        for ln in it["lines"]:
            block += f"\n🎯 픽: {ln[5:]}" if ln.startswith("PICK:") else f"\n🔸 {ln if len(ln) <= 46 else ln[:45] + '…'}"
        if cur and (len(cur) + len(block) > limit_chars or _emo(cur) + _emo(block) > MAX_EMO):
            pages.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + block
    if cur:
        pages.append(cur)
    return pages


# 오늘의 추천픽 선정: 축구 5대리그·챔스, NBA, MLB·KBO 중 스타팀(유명 선수)이 걸린 경기를 점수로 골라 5경기만 보여준다.
LEAGUE_SCORE = [("챔피언스리그", 6), ("프리미어리그", 5), ("라리가", 5), ("분데스리가", 4), ("세리에 A", 4), ("리그 1", 3),
                ("NBA", 5), ("MLB", 5), ("KBO", 2)]
STAR_TEAMS = ["맨체스터 시티", "맨체스터 유나이티드", "리버풀", "아스널", "첼시", "토트넘", "뉴캐슬", "레알 마드리드", "바르셀로나",
              "AT 마드리드", "바이에른", "도르트문트", "레버쿠젠", "인터", "AC 밀란", "유벤투스", "나폴리", "파리 생제르맹", "PSG",
              "레이커스", "워리어스", "셀틱스", "닉스", "썬더", "웸반야", "스퍼스", "밀워키", "너기츠", "다저스", "양키스", "메츠",
              "파드리스", "필리스"]
TOP_TEAMS = ["맨체스터 시티", "맨체스터 유나이티드", "리버풀", "아스널", "레알 마드리드", "바르셀로나", "바이에른", "파리 생제르맹",
             "PSG", "인터", "유벤투스", "레이커스", "워리어스", "다저스", "양키스"]
TODAY_COUNT = 5


def _star_score(title):
    s = 0
    for w, sc in LEAGUE_SCORE:
        if w in title:
            s = sc
            break
    else:
        return 0
    if any(w in title for w in ("챔피언쉽", "챔피언십 시리즈", "플레이오프", "월드시리즈", "파이널")):
        s += 3
    return s + sum(2 for t in STAR_TEAMS if t in title) + sum(2 for t in TOP_TEAMS if t in title)


async def picks_result_loop():
    """5분마다 끝난 추천픽 경기를 판정해 두어, 자정이 지나도 결과가 남게 한다."""
    while True:
        try:
            await picks_today.update_results()
        except Exception:
            log.exception("추천픽 결과 판정 실패")
        await asyncio.sleep(300)


async def today_pages():
    """오늘의 추천픽: 가장 화제인 경기 5개의 픽만 한눈에."""
    icons = {"ovfb": "⚽", "dofb": "🇰🇷", "bask": "🏀", "base": "⚾", "hock": "🏒", "voll": "🏐", "esp": "🎮"}
    cands = []
    for key, _, _ in posts.CATEGORIES:
        try:
            items = await posts.get_category(key)
        except Exception:
            log.exception("오늘의 추천픽 불러오기 실패 (%s)", key)
            continue
        for it in items:
            pick = next((ln[5:] for ln in it["lines"] if ln.startswith("PICK:")), None)
            sc = _star_score(it["title"])
            if pick:  # 점수 0(5대리그·NBA 등이 아닌 경기)도 후보로 두고, 화제 경기가 부족할 때 채운다
                cands.append((sc, key, it, pick))
    cands.sort(key=lambda x: (-x[0], x[2]["kickoff"] == "", x[2]["kickoff"]))
    try:  # 하루 5경기를 한 번 정해 저장하고, 끝난 경기는 적중/미적중을 표시한다
        await picks_today.update_results()
        rec = await picks_today.ensure_snapshot(cands)
        await picks_today.update_results()
    except Exception:
        log.exception("오늘의 추천픽 기록 실패")
        rec = []
    return [picks_today.render(rec)] if rec else []


async def build_picks(cat=None, page=0, back_button=True):
    """분석 및 추천픽: 처음 화면은 제목 + 종목 버튼만, 종목을 누르면 그 종목 경기 멘트를 쭉 보여준다. (text, markup)"""
    name = "오늘의 추천픽" if cat == "today" else posts.CAT_NAME.get(cat)
    title = (f"🦊 {name} 🦊" if cat == "today" else f"🦊 {name} 경기분석 🦊") if name else "🦊 분석 및 추천픽 🦊"
    text, nav = title, []
    if cat:
        try:
            pages = await today_pages() if cat == "today" else pick_pages(await posts.get_category(cat))
        except Exception:
            log.exception("추천픽 불러오기 실패")
            pages = []
        if not pages:
            text += "\n\n아직 등록된 글이 없어요."
        else:
            page = max(0, min(page, len(pages) - 1))
            text += (f"  [{page + 1}/{len(pages)}]" if len(pages) > 1 else "") + "\n\n" + pages[page]
            if page > 0:
                nav.append(InlineKeyboardButton("◀ 이전", callback_data=f"pk_{cat}_{page - 1}"))
            if page < len(pages) - 1:
                nav.append(InlineKeyboardButton("다음 ▶", callback_data=f"pk_{cat}_{page + 1}"))
    rows = category_rows(cat) + ([nav] if nav else [])
    if back_button:
        rows.append([InlineKeyboardButton("⬅️ 분석 및 추천픽" if cat else "⬅️ 메뉴", callback_data="picks" if cat else "menu")])
    return text, InlineKeyboardMarkup(rows)


_picks_photo_id = [None]  # 한 번 올린 사진은 file_id 로 다시 쓰면 업로드 시간이 없다


def _picks_photo():
    return _picks_photo_id[0] or open(PICKS_BANNER, "rb")


def _remember_photo(msg):
    if msg and getattr(msg, "photo", None):
        _picks_photo_id[0] = msg.photo[-1].file_id


async def show_picks_home(q, text, markup):
    """분석 및 추천픽 첫 화면: 사진 + 제목 + 종목 버튼. 글자 메시지에서 오면 새로 보내고 옛 글은 동시에 지운다."""
    if q.message.photo:
        res = await q.edit_message_media(InputMediaPhoto(_picks_photo(), caption=text, caption_entities=decorate(text)), reply_markup=markup)
    else:
        res, _ = await asyncio.gather(
            q.message.chat.send_photo(_picks_photo(), caption=text, caption_entities=decorate(text), reply_markup=markup),
            q.message.delete(),
        )
    _remember_photo(res)


async def show_posts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ack = asyncio.create_task(q.answer())  # 응답 확인과 화면 만들기를 동시에
    cat, page = None, 0
    if q.data.startswith("pk_"):
        _, cat, page = q.data.split("_")
        page = int(page)
    text, markup = await build_picks(cat, page)
    try:
        if cat is None and os.path.exists(PICKS_BANNER):
            await show_picks_home(q, text, markup)
        else:
            await show(q, text, markup)
    except Exception:
        log.exception("추천픽 화면 표시 실패 (%s)", q.data)
    try:
        await ack
    except Exception:
        pass


def _private_link_arg(data):
    """그룹에서 누른 버튼이 어떤 화면이었는지 → 봇과의 1:1 대화로 열 때 쓰는 start 값"""
    if data == "picks" or data.startswith("pk_"):
        return "picktoday" if data.startswith("pk_today") else "picks"
    if data == "analysis" or data.startswith("sc_"):
        return "score"
    if data == "events" or data.startswith("ev_"):
        return "events"
    if data.startswith("fav"):
        return "fav"
    if data == "support":
        return "support"
    return None


async def group_guard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """그룹방에서 메뉴 버튼을 누르면 방의 글이 바뀌어 모두에게 보인다. 그래서 누른 사람의 봇 1:1 대화로 보내서 그곳에서만 보이게 한다."""
    q = update.callback_query
    if not q.message or q.message.chat.type == "private":
        return  # 1:1 대화에서는 원래대로 처리
    arg = _private_link_arg(q.data or "")
    if arg:
        await q.answer(url=f"https://t.me/{ctx.bot.username}?start={arg}")
        raise ApplicationHandlerStop


async def pick_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """.픽 / /pick : 채팅에서 바로 오늘의 추천픽 소개를 보여준다."""
    text, markup = await build_picks(back_button=False)
    if os.path.exists(PICKS_BANNER):
        with open(PICKS_BANNER, "rb") as f:
            await update.message.reply_photo(f, caption=text, caption_entities=decorate(text), reply_markup=markup)
    else:
        await update.message.reply_text(text, entities=decorate(text), reply_markup=markup)


def sports_menu(current=None, mode="m"):
    row = [
        InlineKeyboardButton(("✔ " if k == current else "") + v[0], callback_data=f"sc_{k}_{mode}_0")
        for k, v in sports.SPORTS.items()
    ]
    return [row[:2], row[2:]]


def mode_row(sport, mode):
    names = {"m": "⭐ 주요", "l": "🔴 LIVE", "a": "📋 전체"}
    return [
        InlineKeyboardButton(("✔ " if k == mode else "") + v, callback_data=f"sc_{sport}_{k}_0")
        for k, v in names.items()
    ]


async def score_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    rows = sports_menu() + [[InlineKeyboardButton("⬅️ 메뉴", callback_data="menu")]]
    await show(q, "🦊 오늘의 경기결과 스코어 🦊\n\n종목을 선택하세요 👇", InlineKeyboardMarkup(rows))


async def score_sport(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, sport, mode, page = q.data.split("_")
    page = int(page)
    label = sports.SPORTS[sport][0]
    try:
        pgs = await flashscore.pages(sport, label, mode)
    except Exception:
        log.exception("flashscore 실패")
        pgs = None
    if pgs:
        page = min(page, len(pgs) - 1)
        text = pgs[page]
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ 이전", callback_data=f"sc_{sport}_{mode}_{page - 1}"))
        if page < len(pgs) - 1:
            nav.append(InlineKeyboardButton("다음 ▶", callback_data=f"sc_{sport}_{mode}_{page + 1}"))
        nav.append(InlineKeyboardButton("🔄", callback_data=f"sc_{sport}_{mode}_{page}"))
        rows = sports_menu(sport, mode) + [mode_row(sport, mode), nav]
    else:
        try:  # 플래시스코어가 안 될 때는 네이버로 대체
            text = await sports.scores_text(sport)
        except Exception:
            text = "스코어를 불러오지 못했습니다. 잠시 후 다시 눌러주세요."
        rows = sports_menu(sport)
    rows.append([InlineKeyboardButton("⬅️ 메뉴", callback_data="menu")])
    try:
        await show(q, text, InlineKeyboardMarkup(rows))
    except Exception:
        log.exception("스코어 화면 표시 실패 (%s)", q.data)


async def back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.delete()
    await send_menu(q.message.chat)


async def emojiid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """프리미엄 커스텀 이모지를 보내면 ID를 알려줌"""
    msg = update.message
    ents = [e for e in (msg.entities or []) if e.type == "custom_emoji"]
    if not ents:
        return
    raw = msg.text.encode("utf-16-le")  # 텔레그램 offset 은 UTF-16 단위
    lines = [f"{raw[e.offset * 2:(e.offset + e.length) * 2].decode('utf-16-le')} → {e.custom_emoji_id}" for e in ents]
    log.info("커스텀 이모지 ID: %s", lines)
    await msg.reply_text("커스텀 이모지 ID\n" + "\n".join(lines))


async def myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"내 ID: {update.effective_user.id}")


async def addpick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not settings.is_admin(update.effective_user.id):
        return
    text = update.message.text.partition(" ")[2]
    title, _, url = text.rpartition("|")
    if not title.strip() or not url.strip().startswith("http"):
        await update.message.reply_text("형식: /addpick 제목 | https://링크")
        return
    posts.add_local(title.strip(), url.strip())
    await update.message.reply_text("등록 완료 ✅")


async def clearpicks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if settings.is_admin(update.effective_user.id):
        posts.clear_local()
        await update.message.reply_text("등록된 글을 모두 지웠습니다.")


def display_name(u):
    return f"@{u.username}" if u.username else u.full_name


async def count_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if u and not u.is_bot:
        chatstats.add(update.effective_chat.id, u.id, display_name(u))


FOX_TITLE, FOX_RANK, FOX_INFO = EMOJI_IDS[1], EMOJI_IDS[6], EMOJI_IDS[3]


def fox_line(emoji_id, text, lines, ents):
    """줄 맨 앞에 구미호 커스텀 이모지(🦊 자리)를 붙인다. 오프셋은 UTF-16 기준."""
    pos = len("".join(lines).encode("utf-16-le")) // 2 + sum(1 for _ in lines)  # 줄바꿈 포함
    ents.append(MessageEntity(MessageEntity.CUSTOM_EMOJI, pos, 2, custom_emoji_id=emoji_id))
    lines.append("🦊 " + text)


def emoji_divider(ids, lines, ents):
    """구분선 대신 커스텀 이모지를 한 줄로 나열한다."""
    pos = len("".join(lines).encode("utf-16-le")) // 2 + len(lines)
    for i, eid in enumerate(ids):
        ents.append(MessageEntity(MessageEntity.CUSTOM_EMOJI, pos + i * 2, 2, custom_emoji_id=eid))
    lines.append("🦊" * len(ids))


def rank_buttons():
    rows = [[btn("🔗 누드티비 바로가기", style="success", url=os.getenv("SCORE_URL", "https://sports.daum.net/"))]]
    ai = os.getenv("AI_URL", "").strip()
    if ai:
        rows.append([btn("🤖 누드티비 AI 연결", style="primary", url=ai)])
    return InlineKeyboardMarkup(rows)


async def rank(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id, u = update.effective_chat.id, update.effective_user
    n, pos = chatstats.my_stat(chat_id, u.id)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines, ents = [], []
    fox_line(FOX_TITLE, "TODAY 채팅현황", lines, ents)
    lines.append("")
    fox_line(FOX_INFO, display_name(u), lines, ents)
    fox_line(FOX_INFO, f"오늘 채팅수 : {n}회", lines, ents)
    lines.append(f"현재 순위 : {pos}위" if pos else "현재 순위 : -")
    lines.append("")
    emoji_divider([EMOJI_IDS[i] for i in (1, 3, 4, 6, 9, 11, 14)], lines, ents)
    lines.append("")
    fox_line(FOX_RANK, f"오늘 순위 ({datetime.now(chatstats.KST).strftime('%m/%d')})", lines, ents)
    lines.append("")
    top = chatstats.ranking(chat_id)
    if not top:
        lines.append("아직 채팅 기록이 없습니다.")
    for i, (_, name, cnt) in enumerate(top, 1):
        crown = " 👑" if i == 1 else ""
        lines.append(f"{medals.get(i, str(i) + '.')} {name} · {cnt}회{crown}")
    await update.message.reply_text("\n".join(lines), entities=ents, reply_markup=rank_buttons())


def intro_text():
    """봇 프로필의 소개 문구(처음 열었을 때 보이는 글)를 그대로 쓰되, [시작] 안내만 버튼 안내로 바꾼다."""
    return seo.bot_about().replace("아래 [시작]을 눌러 메뉴를 확인하세요 👇", "아래 버튼을 눌러 확인하세요 👇")


async def send_intro(chat):
    text = intro_text()
    return await chat.send_message(text, entities=decorate(text), reply_markup=main_menu(),
                                   link_preview_options=LinkPreviewOptions(is_disabled=True))


async def private_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """1:1 대화: 글을 치면 AI가 답하고(켜져 있을 때), 꺼져 있으면 처음 열었을 때와 같은 소개 화면과 메뉴 버튼을 보여준다."""
    if not ai.enabled():
        await send_intro(update.effective_chat)
        return
    await update.effective_chat.send_action("typing")
    try:
        text = await ai.reply(update.effective_user.id, update.message.text)
    except Exception:
        text = "지금은 답변이 어려워요. 아래 메뉴를 이용해 주세요."
    await update.message.reply_text(text, reply_markup=main_menu())


async def send_welcome(chat, names):
    text = f"🦊 {names}님 환영합니다! 🦊\n\n🦊 아래 버튼으로 오늘의 경기와 스코어를 확인하세요.\n🦊 '채팅현황'을 입력하면 오늘의 채팅 순위가 나와요!"
    ents = decorate(text)
    try:
        with open(BANNER, "rb") as f:
            await chat.send_photo(f, caption=text, caption_entities=ents, reply_markup=main_menu())
    except Exception:
        await chat.send_message(text, entities=ents, reply_markup=main_menu())


async def welcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    names = ", ".join(m.full_name for m in update.message.new_chat_members if not m.is_bot)
    if names:
        await send_welcome(update.effective_chat, names)


async def welcome_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/welcome : 환영 메시지를 내 이름으로 미리 보기"""
    await send_welcome(update.effective_chat, update.effective_user.full_name)


async def group_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_menu(update.effective_chat)


# ---- 관심 팀 ----
async def fav_screen(user_id):
    """관심 팀 화면 (text, rows)"""
    teams = favorites.list_teams(user_id)
    rows = [[InlineKeyboardButton(f"✖ {t}", callback_data=f"favdel_{i}")] for i, t in enumerate(teams)]
    if len(teams) < favorites.MAX_TEAMS:
        rows.append([InlineKeyboardButton("➕ 팀 추가", callback_data="favadd")])
    rows.append([InlineKeyboardButton("🔄 새로고침", callback_data="fav"), InlineKeyboardButton("⬅️ 메뉴", callback_data="menu")])
    if not teams:
        return ("🦊 내 관심 팀 🦊\n\n좋아하는 팀을 추가하면 그 팀의 오늘 경기와 다음 경기를 모아 보여드려요.\n"
                f"최대 {favorites.MAX_TEAMS}개까지 고를 수 있어요."), rows
    try:
        body = await favorites.summary(teams)
    except Exception:
        log.exception("관심 팀 경기 조회 실패")
        body = "\n경기 정보를 불러오지 못했어요. 잠시 후 🔄를 눌러 주세요."
    return "🦊 내 관심 팀 🦊\n" + body + "\n\n✖ 를 누르면 팀이 삭제돼요.", rows


async def fav_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.message.chat.type != "private":
        # 그룹에서 누르면 봇과의 1:1 대화로 바로 이동 (대화가 열리면 /start fav 로 관심 팀 화면을 보여줌)
        await q.answer(url=f"https://t.me/{ctx.bot.username}?start=fav")
        return
    await q.answer()
    ctx.user_data.pop("await_team", None)
    text, rows = await fav_screen(q.from_user.id)
    await show(q, text, InlineKeyboardMarkup(rows))


async def fav_add_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data["await_team"] = True
    rows = [[InlineKeyboardButton("취소", callback_data="fav")]]
    await show(q, "추가할 팀 이름을 채팅으로 보내 주세요.\n예: 맨유, 토트넘, 두산, 현대캐피탈\n\n앞으로 7일 안에 경기가 있는 팀만 추가할 수 있어요.",
               InlineKeyboardMarkup(rows))


async def fav_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    teams = favorites.list_teams(q.from_user.id)
    idx = int(q.data.split("_")[1])
    if idx < len(teams):
        favorites.remove_team(q.from_user.id, teams[idx])
    text, rows = await fav_screen(q.from_user.id)
    await show(q, text, InlineKeyboardMarkup(rows))


async def fav_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """팀 이름 입력 대기 중일 때만 가로채서 처리하고, 아니면 다른 처리기(AI 대화)로 넘긴다."""
    if not ctx.user_data.get("await_team"):
        return
    name = update.message.text.strip()
    if not 1 < len(name) <= 20:
        await update.message.reply_text("팀 이름은 2~20자로 보내 주세요.")
        raise ApplicationHandlerStop
    try:
        found = await favorites.exists(name)
    except Exception:
        log.exception("팀 확인 실패")
        await update.message.reply_text("지금은 팀을 확인하지 못했어요. 잠시 후 다시 보내 주세요.")
        raise ApplicationHandlerStop
    if not found:
        await update.message.reply_text(f"'{name}'이(가) 들어간 팀의 경기를 7일 안에서 찾지 못했어요.\n"
                                        "다른 이름(예: 맨유, 두산)으로 다시 보내거나 취소를 눌러 주세요.")
        raise ApplicationHandlerStop
    res = favorites.add_team(update.effective_user.id, name)
    if res == "full":
        await update.message.reply_text(f"관심 팀은 최대 {favorites.MAX_TEAMS}개예요. 하나를 지우고 추가해 주세요.")
    ctx.user_data.pop("await_team", None)
    text, rows = await fav_screen(update.effective_user.id)
    await update.message.reply_text(text, entities=decorate(text), reply_markup=InlineKeyboardMarkup(rows))
    raise ApplicationHandlerStop


async def on_start(app):
    import asyncio
    events.set_bot_username(app.bot.username)
    popular.set_decorate(decorate)
    app.bot_data["popular"] = asyncio.create_task(popular.loop(app.bot))
    app.bot_data["warm"] = asyncio.create_task(posts.warm_cache())
    app.bot_data["picks_result"] = asyncio.create_task(picks_result_loop())
    app.bot_data["health"] = asyncio.create_task(alerts.healthcheck(app.bot))
    app.bot_data["picks_report"] = asyncio.create_task(picks_report.report_loop(app.bot))
    app.bot_data["scheduled"] = asyncio.create_task(scheduled.loop(app.bot))
    app.bot_data["menuloop"] = asyncio.create_task(menuloop.loop(app.bot, send_menu, events.send_list))
    app.bot_data["seo"] = asyncio.create_task(seo.apply_bot(app.bot))


def main():
    singleton.acquire()
    app = Application.builder().token(os.environ["BOT_TOKEN"]).post_init(on_start).concurrent_updates(True).build()
    app.add_error_handler(alerts.on_error)
    admin.register(app, send_menu, events.send_list)
    picks_report.register(app, decorate)
    support.register(app)
    events.register(app, decorate)
    seo.register(app)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, fav_text), group=-1)
    app.add_handler(MessageHandler(filters.Entity("custom_emoji"), emojiid))
    app.add_handler(CommandHandler("rank", rank))
    app.add_handler(CommandHandler("pick", pick_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^[.。]\s*(픽|추천픽)\s*$"), pick_cmd))
    app.add_handler(CommandHandler("welcome", welcome_test))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.Regex(r"^\s*[.。/]?\s*(채팅현황|채팅순위)\s*$"), rank))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.Regex(r"^(메뉴|누드티비)$"), group_menu))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_chat))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, count_chat), group=1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("setnotice", setnotice))
    app.add_handler(CommandHandler("clearnotice", clearnotice))
    app.add_handler(CommandHandler("addpick", addpick))
    app.add_handler(CommandHandler("clearpicks", clearpicks))
    app.add_handler(MessageHandler(filters.Sticker.ALL, stickerid))
    app.add_handler(CallbackQueryHandler(group_guard, pattern=r"^(picks|analysis|events|support|fav\w*|pk_\w+|sc_\w+|ev_\d+)$"), group=-5)
    app.add_handler(CallbackQueryHandler(show_posts, pattern="^(picks|pk_[a-z]+_\\d+)$"))
    app.add_handler(CallbackQueryHandler(score_menu, pattern="^analysis$"))
    app.add_handler(CallbackQueryHandler(score_sport, pattern="^sc_"))
    app.add_handler(CallbackQueryHandler(fav_menu, pattern="^fav$"))
    app.add_handler(CallbackQueryHandler(fav_add_prompt, pattern="^favadd$"))
    app.add_handler(CallbackQueryHandler(fav_delete, pattern="^favdel_\\d+$"))
    app.add_handler(CallbackQueryHandler(back, pattern="^menu$"))
    app.run_polling()


if __name__ == "__main__":
    main()
