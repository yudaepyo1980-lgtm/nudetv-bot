"""관리자 패널(/admin): 코딩 없이 봇 안에서 버튼 이름·링크·숨김, 메뉴 문구·사진, 관리자 목록을 바꾼다."""
import json
import logging
import os
import re

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, Update
from telegram.ext import (ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import events
import menuloop
import popular
import scheduled
import settings

log = logging.getLogger("admin")
NOTICE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notice.json")
_send_menu = None  # bot.py 가 register() 로 넘겨 줌
_send_events = None


def _notice():
    try:
        with open(NOTICE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_notice(data):
    if not any(data.values()):
        if os.path.exists(NOTICE_FILE):
            os.remove(NOTICE_FILE)
        return
    with open(NOTICE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


async def _tell_owners(bot, who_id, text):
    """소유자가 아닌 관리자가 바꾸면 소유자에게 알려 준다."""
    if settings.is_owner(who_id):
        return
    for o in settings.owners():
        try:
            await bot.send_message(o, f"🛠 {text}")
        except Exception:
            log.exception("소유자 알림 실패")


async def _done(ctx, user, what):
    settings.log_change(user.id, user.full_name, what)
    cc = control_chat()
    here = ctx.user_data.get("adm_chat")
    if cc and here != cc:  # 컨트롤 그룹 밖에서 바꿨다면 그룹에도 알려 모두가 보게 함
        try:
            await ctx.bot.send_message(cc, f"🛠 {user.full_name} 님이 {what}")
        except Exception:
            log.exception("컨트롤 그룹 알림 실패")
    await _tell_owners(ctx.bot, user.id, f"{user.full_name}({user.id}) 님이 {what}")


def control_chat():
    v = settings.get("control_chat")
    return int(v) if v else None


async def can_admin(bot, user, chat):
    """관리자(소유자 포함)이거나 컨트롤 그룹의 멤버면 패널을 쓸 수 있다."""
    if user.is_bot:
        return False
    if settings.is_admin(user.id):
        return True
    cc = control_chat()
    if not cc:
        return False
    if chat.id == cc:
        return True  # 컨트롤 그룹 안에서 누른 사람은 멤버
    if chat.type == "private":
        try:  # 1:1 대화에서는 컨트롤 그룹 멤버인지 확인 (봇이 그 그룹의 관리자여야 정확함)
            m = await bot.get_chat_member(cc, user.id)
            return m.status in ("creator", "administrator", "member") or (m.status == "restricted" and getattr(m, "is_member", False))
        except Exception:
            return False
    return False


def home_auto_label():
    return "📣 추천픽 결과 자동 게시: " + ("꺼짐" if settings.get("picks_report.auto") == "0" else "켜짐")


def home():
    rows = [
        [B("🔘 버튼 이름·링크·숨김", callback_data="adm_btns")],
        [B("📝 메뉴 문구 변경", callback_data="adm_text"), B("🖼 메뉴 사진 변경", callback_data="adm_photo")],
        [B("👀 메뉴 미리보기", callback_data="adm_preview"), B("🗓 예약 발송", callback_data="adm_sched")],
        [B("🔁 메뉴 반복 게시", callback_data="adm_ml"), B("🎁 이벤트 공지", callback_data="adm_evs")],
        [B("🔁 이벤트 목록 반복 게시", callback_data="adm_el"), B("📰 인기글 소개", callback_data="adm_pp")],
        [B(home_auto_label(), callback_data="adm_pa")],
        [B("👥 관리자", callback_data="adm_admins"), B("🧾 변경 기록", callback_data="adm_log")],
    ]
    return "🛠 관리자 패널\n\n바꾸고 싶은 항목을 고르세요. 바꾼 내용은 바로 적용돼요.", M(rows)


def btn_list():
    rows = []
    for b in settings.BUTTONS:
        label, url, hidden = settings.button_state(b["key"])
        rows.append([B(f"{'🙈 ' if hidden else ''}{label}", callback_data=f"adm_b_{b['key']}")])
    rows.append([B("⬅️ 처음으로", callback_data="adm_home")])
    return "🔘 버튼 목록\n\n고치고 싶은 버튼을 누르세요. 🙈는 지금 메뉴에서 숨겨진 버튼이에요.", M(rows)


def btn_detail(key):
    b = settings.BY_KEY[key]
    label, url, hidden = settings.button_state(key)
    lines = [f"🔘 {label}", "", f"이름: {label}"]
    if b["kind"] == "url":
        lines.append(f"링크: {url or '(아직 없음)'}")
    lines.append(f"상태: {'숨김' if hidden else '보임'}")
    if b["kind"] == "url" and not url:
        lines.append("\n링크를 정하면 메뉴에 나타나요.")
    rows = [[B("✏️ 이름 변경", callback_data=f"adm_bn_{key}")]]
    if b["kind"] == "url":
        rows.append([B("🔗 링크 변경", callback_data=f"adm_bl_{key}")])
    rows.append([B("👁 보이기" if hidden else "🙈 숨기기", callback_data=f"adm_bh_{key}")])
    rows.append([B("↩️ 기본값으로", callback_data=f"adm_br_{key}")])
    rows.append([B("⬅️ 버튼 목록", callback_data="adm_btns")])
    return "\n".join(lines), M(rows)


def ml_screen(kind="menu"):
    st = menuloop.state(kind)
    cb = "ml" if kind == "menu" else "el"
    title = menuloop.KINDS[kind]["title"]
    room = {settings.get("result_chat"): "결과방", settings.get("control_chat"): "컨트롤 그룹"}.get(str(st["chat"]), "정해지지 않음" if not st["chat"] else str(st["chat"]))
    lines = [f"🔁 {title} 반복 게시", "",
             f"상태: {'켜짐' if st['on'] else '꺼짐'}", f"올리는 방: {room}", f"간격: {st['hours']}시간마다",
             f"마지막으로 올린 때: {menuloop.last_text(st)}", "",
             f"새 {title}을(를) 올릴 때 이전에 올린 글은 지워서 방에 같은 글이 쌓이지 않아요.",
             "켜면 바로 한 번 올라가고, 그 뒤로 정한 간격마다 다시 올라와요."]
    if kind == "menu":
        lines += ["", "다른 방에 올리려면 그 방에서 /setmenuroom 을 보내 주세요. (채널은 /setmenuroom @채널이름)"]
    rows = [[B("⏹ 끄기" if st["on"] else "▶️ 켜기", callback_data=f"adm_{cb}_toggle")],
            [B(("✔ " if st["hours"] == h else "") + f"{h}시간", callback_data=f"adm_{cb}_h_{h}") for h in menuloop.INTERVALS]]
    rooms = []
    if settings.get("result_chat"):
        rooms.append(B(("✔ " if str(st["chat"]) == settings.get("result_chat") else "") + "결과방", callback_data=f"adm_{cb}_c_result"))
    if settings.get("control_chat"):
        rooms.append(B(("✔ " if str(st["chat"]) == settings.get("control_chat") else "") + "컨트롤 그룹", callback_data=f"adm_{cb}_c_control"))
    if rooms:
        rows.append(rooms)
    rows.append([B("📤 지금 올리기", callback_data=f"adm_{cb}_now")])
    rows.append([B("⬅️ 처음으로", callback_data="adm_home")])
    return "\n".join(lines), M(rows)


STATUS_KO = {"sent": "발송됨", "failed": "실패", "missed": "건너뜀", "canceled": "취소됨"}


def sched_list():
    pend = scheduled.pending()
    lines = ["🗓 예약 발송", ""]
    rows = []
    if pend:
        for sid, w, target, text, photo in pend:
            lines.append(f"#{sid} {w[5:]} · {target} · {'🖼 ' if photo else ''}{(text or '(사진)').replace(chr(10), ' ')[:20]}")
            rows.append([B(f"✖ #{sid} 취소", callback_data=f"adm_sc_{sid}")])
    else:
        lines.append("예약된 발송이 없어요.")
    done = scheduled.recent_done()
    if done:
        lines += ["", "최근:"] + [f"#{i} {w[5:]} · {tg} · {STATUS_KO.get(st, st)}" for i, w, tg, st, _ in done]
    rows.append([B("➕ 새 예약", callback_data="adm_snew")])
    rows.append([B("⬅️ 처음으로", callback_data="adm_home")])
    return "\n".join(lines), M(rows)


def pp_screen():
    s = popular.state()
    room = {settings.get("result_chat"): "결과방", settings.get("control_chat"): "컨트롤 그룹"}.get(str(s["chat"]), "정해지지 않음" if not s["chat"] else str(s["chat"]))
    extra = settings.get("popular.block") or "없음"
    lines = ["📰 인기글 소개", "",
             f"상태: {'켜짐' if s['on'] else '꺼짐'}", f"올리는 방: {room}", f"올리는 시각: 매일 {s['time']}",
             f"마지막으로 올린 날: {s['last_day'] or '아직 없음'}", "",
             "자유게시판·유머·스포츠토론에서 최근 반응이 좋은 글을 하나씩 골라 올려요.",
             "성인물·베팅 관련 단어가 들어간 제목은 자동으로 빼요.",
             f"추가로 뺄 단어: {extra}"]
    rows = [[B("⏹ 끄기" if s["on"] else "▶️ 켜기", callback_data="adm_pp_toggle")],
            [B(("✔ " if s["time"] == t else "") + t, callback_data=f"adm_pp_t_{t.replace(':', '')}") for t in popular.TIMES]]
    rooms = []
    if settings.get("result_chat"):
        rooms.append(B(("✔ " if str(s["chat"]) == settings.get("result_chat") else "") + "결과방", callback_data="adm_pp_c_result"))
    if settings.get("control_chat"):
        rooms.append(B(("✔ " if str(s["chat"]) == settings.get("control_chat") else "") + "컨트롤 그룹", callback_data="adm_pp_c_control"))
    if rooms:
        rows.append(rooms)
    rows.append([B("👀 미리보기 (내 대화)", callback_data="adm_pp_prev"), B("📤 지금 방에 올리기", callback_data="adm_pp_now")])
    rows.append([B("🚫 뺄 단어 추가", callback_data="adm_pp_block")])
    rows.append([B("⬅️ 처음으로", callback_data="adm_home")])
    return "\n".join(lines), M(rows)


def ev_list():
    evs = events.all_events()
    lines = ["🎁 이벤트 공지", "", "이벤트를 고르면 방에 공지글로 올리거나 상태를 바꿀 수 있어요.", "메뉴의 이벤트 버튼에는 '진행중'(그리고 '기획'은 🕐 준비 중으로) 이벤트가 보이고, '종료'는 숨겨져요.", ""]
    rows = []
    for eid, title, _, st in evs:
        rows.append([B(f"[{events.STATUS_KO.get(st, st)}] {title[:48]}", callback_data=f"adm_ev_{eid}")])
    if not evs:
        lines.append("등록된 이벤트가 없어요.")
    rows.append([B("➕ 새 이벤트", callback_data="adm_evn"), B("🖼 목록 사진 변경", callback_data="adm_evb")])
    rows.append([B("📝 목록 문구 변경", callback_data="adm_evt")])
    rows.append([B("🔘 이벤트별 버튼 " + ("숨기기" if events.show_buttons() else "보이기"), callback_data="adm_evq")])
    rows.append([B("⬅️ 처음으로", callback_data="adm_home")])
    return "\n".join(lines), M(rows)


def ev_detail(eid):
    ev = events.get(eid)
    if not ev:
        return ev_list()
    _, title, body, st = ev
    preview = body if len(body) <= 500 else body[:500] + "…"
    text = f"🎁 {title}\n상태: {events.STATUS_KO.get(st, st)}\n\n{preview}"
    rows = []
    post = []
    if settings.get("result_chat"):
        post.append(B("📣 결과방에 올리기", callback_data=f"adm_evp_{eid}_result"))
    if settings.get("control_chat"):
        post.append(B("🛠 컨트롤 그룹에 올리기", callback_data=f"adm_evp_{eid}_control"))
    if post:
        rows.append(post)
    rows.append([B(("✔ " if st == k else "") + v, callback_data=f"adm_evs_{eid}_{k}") for k, v in events.STATUS_KO.items()])
    has = settings.get(f"event.photo.{eid}") not in (None, "none")
    rows.append([B("🖼 이 이벤트 사진 변경", callback_data=f"adm_evi_{eid}")] + ([B("사진 지우기", callback_data=f"adm_evo_{eid}")] if has else []))
    rows.append([B("✏️ 제목·내용 수정", callback_data=f"adm_eve_{eid}"), B("🗑 삭제", callback_data=f"adm_evd_{eid}")])
    rows.append([B("⬅️ 이벤트 목록", callback_data="adm_evs")])
    return text, M(rows)


def admins_screen(uid):
    lines = ["👥 관리자", "", "소유자(삭제 불가):"] + [f"🔒 {o}" for o in settings.owners()]
    extra = settings.extra_admins()
    cc = control_chat()
    lines.append(f"\n컨트롤 그룹: {'연결됨 (그 그룹 멤버는 모두 패널을 쓸 수 있어요)' if cc else '없음 (그룹에서 소유자가 /setcontrol 을 보내면 연결돼요)'}")
    lines.append("\n추가한 관리자:" if extra else "\n추가한 관리자가 없어요.")
    lines += [f"• {n} ({a})" for a, n in extra]
    rows = []
    if settings.is_owner(uid):
        rows += [[B(f"✖ {n}", callback_data=f"adm_adel_{a}")] for a, n in extra]
        rows.append([B("➕ 관리자 추가", callback_data="adm_aadd")])
        lines.append("\n추가하려면 새 관리자가 봇에게 /myid 를 보내 받은 숫자 ID가 필요해요.")
    else:
        lines.append("\n관리자 추가·삭제는 소유자만 할 수 있어요.")
    rows.append([B("⬅️ 처음으로", callback_data="adm_home")])
    return "\n".join(lines), M(rows)


def log_screen():
    items = settings.recent_changes(10)
    body = "\n".join(f"{ts}  {name}: {what}" for ts, name, what in items) or "아직 변경 기록이 없어요."
    return "🧾 최근 변경 기록\n\n" + body, M([[B("⬅️ 처음으로", callback_data="adm_home")]])


async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private" and chat.id != control_chat():
        return  # 컨트롤 그룹이 아닌 그룹에서는 반응하지 않음
    if not await can_admin(ctx.bot, update.effective_user, chat):
        await update.message.reply_text("관리자만 사용할 수 있어요.")
        return
    ctx.user_data.pop("adm_await", None)
    ctx.user_data["adm_chat"] = chat.id
    text, markup = home()
    await update.message.reply_text(text, reply_markup=markup)


async def admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user
    if not await can_admin(ctx.bot, user, q.message.chat):
        await q.answer("관리자만 사용할 수 있어요.", show_alert=True)
        return
    ctx.user_data["adm_chat"] = q.message.chat.id
    d = q.data
    await q.answer()

    async def edit(text, markup):
        try:
            await q.edit_message_text(text, reply_markup=markup)
        except Exception:
            pass  # 내용이 같아 수정이 거부된 경우

    if d == "adm_home":
        ctx.user_data.pop("adm_await", None)
        await edit(*home())
    elif d == "adm_btns":
        ctx.user_data.pop("adm_await", None)
        await edit(*btn_list())
    elif d.startswith("adm_b_"):
        ctx.user_data.pop("adm_await", None)
        await edit(*btn_detail(d[6:]))
    elif d.startswith("adm_bn_") or d.startswith("adm_bl_"):
        kind, key = ("label", d[7:]) if d.startswith("adm_bn_") else ("url", d[7:])
        ctx.user_data["adm_await"] = {"chat": q.message.chat.id, "type": kind, "key": key}
        ask = "새 버튼 이름을 보내 주세요. (최대 40자)" if kind == "label" else "새 링크를 보내 주세요.\n예: https://t.me/채널이름 또는 https://사이트주소"
        await edit(ask, M([[B("취소", callback_data=f"adm_b_{key}")]]))
    elif d.startswith("adm_bh_"):
        key = d[7:]
        _, _, hidden = settings.button_state(key)
        if hidden and settings.get(f"btn.{key}.hidden") != "1":
            t, m = btn_detail(key)
            await edit("⚠️ 링크가 없어서 숨겨져 있어요. 링크를 먼저 정해 주세요.\n\n" + t, m)
            return
        else:
            settings.set_(f"btn.{key}.hidden", "0" if hidden else "1")
            await _done(ctx, user, f"'{settings.button_state(key)[0]}' 버튼을 {'보이게' if hidden else '숨기게'} 바꿨어요")
        await edit(*btn_detail(key))
    elif d.startswith("adm_br_"):
        key = d[7:]
        settings.reset_button(key)
        await _done(ctx, user, f"'{settings.button_state(key)[0]}' 버튼을 기본값으로 되돌렸어요")
        await edit(*btn_detail(key))
    elif d == "adm_text":
        ctx.user_data["adm_await"] = {"chat": q.message.chat.id, "type": "notice_text"}
        rows = [[B("↩️ 기본 문구로", callback_data="adm_textreset")], [B("취소", callback_data="adm_home")]]
        await edit("새 메뉴 문구를 채팅으로 보내 주세요.\n프리미엄 이모지를 넣어 보내면 그대로 보여요.", M(rows))
    elif d == "adm_textreset":
        n = _notice()
        n.pop("text", None); n.pop("entities", None)
        _save_notice(n)
        ctx.user_data.pop("adm_await", None)
        await _done(ctx, user, "메뉴 문구를 기본값으로 되돌렸어요")
        await edit(*home())
    elif d == "adm_photo":
        ctx.user_data["adm_await"] = {"chat": q.message.chat.id, "type": "notice_photo"}
        rows = [[B("↩️ 기본 사진으로", callback_data="adm_photoreset")], [B("취소", callback_data="adm_home")]]
        await edit("메뉴에 쓸 새 사진을 보내 주세요. (사진으로 보내세요. 파일 첨부는 안 돼요.)\n사진에 글을 같이 적으면 메뉴 문구도 함께 바뀌어요.", M(rows))
    elif d == "adm_photoreset":
        n = _notice()
        n.pop("photo_file_id", None)
        _save_notice(n)
        ctx.user_data.pop("adm_await", None)
        await _done(ctx, user, "메뉴 사진을 기본값으로 되돌렸어요")
        await edit(*home())
    elif d == "adm_preview":
        if _send_menu:
            await _send_menu(q.message.chat)
    elif d == "adm_admins":
        ctx.user_data.pop("adm_await", None)
        await edit(*admins_screen(user.id))
    elif d == "adm_aadd":
        if not settings.is_owner(user.id):
            await q.answer("소유자만 할 수 있어요.", show_alert=True)
            return
        ctx.user_data["adm_await"] = {"chat": q.message.chat.id, "type": "admin_add"}
        await edit("추가할 관리자의 숫자 ID를 보내 주세요.\n(새 관리자가 봇에게 /myid 를 보내면 알 수 있어요.)", M([[B("취소", callback_data="adm_admins")]]))
    elif d.startswith("adm_adel_"):
        if not settings.is_owner(user.id):
            await q.answer("소유자만 할 수 있어요.", show_alert=True)
            return
        aid = int(d[9:])
        name = dict(settings.extra_admins()).get(aid, str(aid))
        settings.remove_admin(aid)
        await _done(ctx, user, f"관리자 {name}({aid}) 를 삭제했어요")
        await edit(*admins_screen(user.id))
    elif d == "adm_sched":
        ctx.user_data.pop("adm_await", None)
        ctx.user_data.pop("sched_draft", None)
        await edit(*sched_list())
    elif d == "adm_snew":
        ctx.user_data["adm_await"] = {"chat": q.message.chat.id, "type": "sched_content"}
        await edit("예약할 내용을 보내 주세요.\n글, 또는 사진에 글을 붙여서 보낼 수 있어요. 프리미엄 이모지도 그대로 올라가요.",
                   M([[B("취소", callback_data="adm_sched")]]))
    elif d.startswith("adm_sc_"):
        scheduled.cancel(int(d[7:]))
        await _done(ctx, user, f"예약 #{d[7:]} 을 취소했어요")
        await edit(*sched_list())
    elif d in ("adm_st_result", "adm_st_control"):
        draft = ctx.user_data.pop("sched_draft", None)
        key, label = ("result_chat", "결과방") if d == "adm_st_result" else ("control_chat", "컨트롤 그룹")
        chat_id = settings.get(key)
        if not draft or not chat_id:
            await edit("예약 내용이 사라졌거나 방이 정해져 있지 않아요. 처음부터 다시 해 주세요.", M([[B("⬅️ 예약 발송", callback_data="adm_sched")]]))
            return
        when = scheduled.parse_time(draft["when"])
        sid = scheduled.add(when, int(chat_id), label, draft["text"], draft["entities"], draft["photo"], user.id, user.full_name)
        await _done(ctx, user, f"{scheduled.fmt(when)} {label}에 올릴 글을 예약했어요 (#{sid})")
        await edit(*sched_list())
    elif d.startswith("adm_ml") or d.startswith("adm_el"):
        kind = "menu" if d.startswith("adm_ml") else "events"
        cb = d[:6]
        title = menuloop.KINDS[kind]["title"]
        sender = _send_menu if kind == "menu" else _send_events
        rest = d[7:]
        if rest.startswith("h_"):
            menuloop.set_hours(kind, rest[2:])
            await _done(ctx, user, f"{title} 반복 간격을 {rest[2:]}시간으로 바꿨어요")
        elif rest in ("c_result", "c_control"):
            key = "result_chat" if rest.endswith("result") else "control_chat"
            menuloop.set_chat(kind, settings.get(key))
            await _done(ctx, user, f"{title} 반복 게시 방을 {'결과방' if rest.endswith('result') else '컨트롤 그룹'}으로 정했어요")
        elif rest == "toggle":
            st = menuloop.state(kind)
            if not st["on"] and not st["chat"]:
                await edit("⚠️ 올릴 방을 먼저 골라 주세요.\n\n" + ml_screen(kind)[0], ml_screen(kind)[1])
                return
            menuloop.set_on(kind, not st["on"])
            await _done(ctx, user, f"{title} 반복 게시를 {'껐어요' if st['on'] else '켰어요 (' + str(st['hours']) + '시간마다)'}")
        elif rest == "now":
            try:
                await menuloop.post_now(ctx.bot, sender, kind)
            except Exception as e:
                await q.message.chat.send_message(f"올리지 못했어요: {type(e).__name__} {e}")
            else:
                await _done(ctx, user, f"{title}을(를) 지금 올렸어요")
        await edit(*ml_screen(kind))
    elif d == "adm_evs":
        ctx.user_data.pop("adm_await", None)
        await edit(*ev_list())
    elif d == "adm_evn":
        ctx.user_data["adm_await"] = {"chat": q.message.chat.id, "type": "ev_new"}
        await edit("새 이벤트를 보내 주세요.\n첫 줄은 제목, 둘째 줄부터는 본문으로 올라가요.\n(저장하면 '기획' 상태로 만들어져요. 준비가 되면 '진행중'으로 바꾸세요.)",
                   M([[B("취소", callback_data="adm_evs")]]))
    elif d == "adm_evb":
        ctx.user_data["adm_await"] = {"chat": q.message.chat.id, "type": "ev_photo", "id": 0}
        await edit("이벤트 목록 화면 맨 위에 보여줄 사진을 보내 주세요. (사진으로 보내세요)", M([[B("취소", callback_data="adm_evs")]]))
    elif d == "adm_evq":
        on = events.show_buttons()
        settings.set_("event.show_buttons", "0" if on else "1")
        await _done(ctx, user, f"이벤트 목록의 이벤트별 버튼을 {'숨겼어요' if on else '보이게 했어요'}")
        await edit(*ev_list())
    elif d == "adm_evt":
        ctx.user_data["adm_await"] = {"chat": q.message.chat.id, "type": "ev_listtext"}
        await edit("이벤트 목록 화면의 문구를 보내 주세요.\n폰에서 줄이 길어 보이지 않게 한 줄을 짧게 쓰고, 🦊 를 넣으면 구미호 이모지로 바뀌어요.\n{n} 을 쓰면 열린 이벤트 개수로 바뀌어요.\n'기본' 이라고 보내면 처음 문구로 돌아가요.",
                   M([[B("취소", callback_data="adm_evs")]]))
    elif d.startswith("adm_evi_"):
        eid = int(d[8:])
        ctx.user_data["adm_await"] = {"chat": q.message.chat.id, "type": "ev_photo", "id": eid}
        await edit("이 이벤트에만 쓸 사진을 보내 주세요. (사진으로 보내세요)", M([[B("취소", callback_data=f"adm_ev_{eid}")]]))
    elif d.startswith("adm_evo_"):
        eid = int(d[8:])
        settings.delete(f"event.photo.{eid}")
        await _done(ctx, user, f"이벤트 #{eid} 사진을 지웠어요")
        await edit(*ev_detail(eid))
    elif d.startswith("adm_ev_"):
        ctx.user_data.pop("adm_await", None)
        await edit(*ev_detail(int(d[7:])))
    elif d.startswith("adm_eve_"):
        eid = int(d[8:])
        ctx.user_data["adm_await"] = {"chat": q.message.chat.id, "type": "ev_edit", "id": eid}
        await edit("수정할 내용을 보내 주세요.\n첫 줄은 제목, 둘째 줄부터는 본문이에요. 보낸 내용으로 통째로 바뀌어요.",
                   M([[B("취소", callback_data=f"adm_ev_{eid}")]]))
    elif d.startswith("adm_evs_"):
        _, _, eid, st = d.split("_")
        events.update(int(eid), status=st)
        await _done(ctx, user, f"이벤트 #{eid} 상태를 '{events.STATUS_KO[st]}'(으)로 바꿨어요")
        await edit(*ev_detail(int(eid)))
    elif d.startswith("adm_evp_"):
        _, _, eid, target = d.split("_")
        ev = events.get(int(eid))
        key, label = ("result_chat", "결과방") if target == "result" else ("control_chat", "컨트롤 그룹")
        chat_id = settings.get(key)
        if not ev or not chat_id:
            await edit("이벤트가 없거나 방이 정해져 있지 않아요.", M([[B("⬅️ 이벤트 목록", callback_data="adm_evs")]]))
            return
        try:
            await events.send_notice(ctx.bot, int(chat_id), ev)
        except Exception as e:
            await q.message.chat.send_message(f"올리지 못했어요: {type(e).__name__}")
        else:
            await _done(ctx, user, f"이벤트 '{ev[1][:30]}' 공지를 {label}에 올렸어요")
        await edit(*ev_detail(int(eid)))
    elif d.startswith("adm_evd_"):
        eid = int(d[8:])
        rows = [[B("✔ 네, 삭제해요", callback_data=f"adm_evx_{eid}")], [B("취소", callback_data=f"adm_ev_{eid}")]]
        await edit("이 이벤트를 삭제할까요? 되돌릴 수 없어요.", M(rows))
    elif d.startswith("adm_evx_"):
        ev = events.get(int(d[8:]))
        if ev:
            events.delete(ev[0])
            await _done(ctx, user, f"이벤트 '{ev[1][:30]}'를 삭제했어요")
        await edit(*ev_list())
    elif d.startswith("adm_pp"):
        rest = d[7:]
        if rest == "" or d == "adm_pp":
            pass
        elif rest == "toggle":
            s = popular.state()
            if not s["on"] and not s["chat"]:
                await edit("⚠️ 올릴 방을 먼저 골라 주세요.\n\n" + pp_screen()[0], pp_screen()[1])
                return
            settings.set_("popular.on", "0" if s["on"] else "1")
            if not s["on"]:
                settings.delete("popular.last_day")  # 켜면 오늘 시각이 지났어도 오늘 한 번 올림(3시간 안이면)
            await _done(ctx, user, f"인기글 소개를 {'껐어요' if s['on'] else '켰어요 (매일 ' + s['time'] + ')'}")
        elif rest.startswith("t_"):
            t = rest[2:4] + ":" + rest[4:6]
            settings.set_("popular.time", t)
            await _done(ctx, user, f"인기글 소개 시각을 매일 {t}로 정했어요")
        elif rest in ("c_result", "c_control"):
            key = "result_chat" if rest.endswith("result") else "control_chat"
            settings.set_("popular.chat", settings.get(key))
            await _done(ctx, user, f"인기글 소개 방을 {'결과방' if rest.endswith('result') else '컨트롤 그룹'}으로 정했어요")
        elif rest in ("prev", "now"):
            target = q.message.chat.id if rest == "prev" else popular.state()["chat"]
            try:
                m = await popular.post(ctx.bot, target) if target else None
                if m is None:
                    await q.message.chat.send_message("올릴 글을 찾지 못했어요. (최근 글이 없거나 제외 단어에 걸렸어요)" if target else "올릴 방이 정해져 있지 않아요.")
                elif rest == "now":
                    await _done(ctx, user, "인기글 소개를 지금 방에 올렸어요")
            except Exception as e:
                await q.message.chat.send_message(f"올리지 못했어요: {type(e).__name__} {e}")
        elif rest == "block":
            ctx.user_data["adm_await"] = {"chat": q.message.chat.id, "type": "pp_block"}
            await edit("인기글에서 뺄 단어를 쉼표로 구분해 보내 주세요.\n예: 광고, 홍보\n(기본으로 뺀 성인·베팅 단어는 그대로 유지돼요. '없음'을 보내면 추가한 단어를 모두 지워요.)",
                       M([[B("취소", callback_data="adm_pp")]]))
            return
        await edit(*pp_screen())
    elif d == "adm_pa":
        off = settings.get("picks_report.auto") == "0"
        settings.set_("picks_report.auto", "1" if off else "0")
        await _done(ctx, user, "추천픽 결과 자동 게시를 " + ("켰어요" if off else "껐어요 (/picksreport 로 확인한 뒤 버튼으로만 올라가요)"))
        await edit(*home())
    elif d == "adm_log":
        await edit(*log_screen())


def _norm_url(s):
    s = s.strip()
    if re.fullmatch(r"@[A-Za-z0-9_]{4,}", s):
        s = "https://t.me/" + s[1:]
    if re.match(r"^(t\.me|telegram\.me)/", s):
        s = "https://" + s
    return s if re.fullmatch(r"https?://\S+", s) else None


async def admin_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """관리자가 입력을 기다리는 중일 때만 처리하고, 아니면 다른 처리기(관심 팀·AI 대화)로 넘긴다."""
    st = ctx.user_data.get("adm_await")
    if not st or st.get("chat") != update.effective_chat.id:
        return  # 패널을 연 그 대화에서 보낸 입력만 처리
    user, msg = update.effective_user, update.message
    if not await can_admin(ctx.bot, user, update.effective_chat):
        ctx.user_data.pop("adm_await", None)
        return
    ctx.user_data["adm_chat"] = update.effective_chat.id
    t = st["type"]
    back = M([[B("⬅️ 처음으로", callback_data="adm_home")]])
    if t == "label":
        name = (msg.text or "").strip()
        if not 1 <= len(name) <= 40:
            await msg.reply_text("이름은 1~40자로 보내 주세요.")
            raise ApplicationHandlerStop
        settings.set_(f"btn.{st['key']}.label", name)
        await _done(ctx, user, f"버튼 이름을 '{name}'(으)로 바꿨어요")
        ctx.user_data.pop("adm_await")
        await msg.reply_text("바꿨어요 ✅", reply_markup=btn_detail(st["key"])[1])
    elif t == "url":
        url = _norm_url(msg.text or "")
        if not url:
            await msg.reply_text("링크 모양이 아니에요. https://… 또는 @채널이름 으로 보내 주세요.")
            raise ApplicationHandlerStop
        settings.set_(f"btn.{st['key']}.url", url)
        settings.delete(f"btn.{st['key']}.hidden")
        await _done(ctx, user, f"'{settings.button_state(st['key'])[0]}' 버튼 링크를 {url} 로 바꿨어요")
        ctx.user_data.pop("adm_await")
        await msg.reply_text("바꿨어요 ✅", reply_markup=btn_detail(st["key"])[1])
    elif t == "notice_text":
        if not msg.text:
            await msg.reply_text("글자로 보내 주세요.")
            raise ApplicationHandlerStop
        n = _notice()
        n["text"] = msg.text
        n["entities"] = [e.to_dict() for e in (msg.entities or [])]
        _save_notice(n)
        await _done(ctx, user, "메뉴 문구를 바꿨어요")
        ctx.user_data.pop("adm_await")
        await msg.reply_text("바꿨어요 ✅ '메뉴 미리보기'로 확인해 보세요.", reply_markup=home()[1])
    elif t == "notice_photo":
        if not msg.photo:
            await msg.reply_text("사진으로 보내 주세요.")
            raise ApplicationHandlerStop
        n = _notice()
        n["photo_file_id"] = msg.photo[-1].file_id
        if msg.caption:
            n["text"] = msg.caption
            n["entities"] = [e.to_dict() for e in (msg.caption_entities or [])]
        _save_notice(n)
        await _done(ctx, user, "메뉴 사진을 바꿨어요" + (" (문구 포함)" if msg.caption else ""))
        ctx.user_data.pop("adm_await")
        await msg.reply_text("바꿨어요 ✅ '메뉴 미리보기'로 확인해 보세요.", reply_markup=home()[1])
    elif t == "sched_content":
        if msg.photo:
            draft = {"text": msg.caption or "", "entities": [e.to_dict() for e in (msg.caption_entities or [])], "photo": msg.photo[-1].file_id}
        elif msg.text:
            draft = {"text": msg.text, "entities": [e.to_dict() for e in (msg.entities or [])], "photo": None}
        else:
            await msg.reply_text("글이나 사진으로 보내 주세요.")
            raise ApplicationHandlerStop
        ctx.user_data["sched_draft"] = draft
        ctx.user_data["adm_await"] = {"chat": update.effective_chat.id, "type": "sched_time"}
        await msg.reply_text("언제 올릴까요? 한국시간으로 보내 주세요.\n예: 21:30 (이미 지났으면 내일), 10/20 09:00",
                             reply_markup=M([[B("취소", callback_data="adm_sched")]]))
    elif t == "sched_time":
        when = scheduled.parse_time(msg.text or "")
        if not when or when <= scheduled.now():
            await msg.reply_text("시각을 읽지 못했거나 이미 지난 시각이에요. 예: 21:30 또는 10/20 09:00")
            raise ApplicationHandlerStop
        draft = ctx.user_data.get("sched_draft")
        if not draft:
            ctx.user_data.pop("adm_await", None)
            await msg.reply_text("예약 내용이 사라졌어요. 처음부터 다시 해 주세요.", reply_markup=home()[1])
            raise ApplicationHandlerStop
        draft["when"] = scheduled.fmt(when)
        ctx.user_data.pop("adm_await", None)
        rows = []
        if settings.get("result_chat"):
            rows.append([B("📣 결과방에 올리기", callback_data="adm_st_result")])
        if settings.get("control_chat"):
            rows.append([B("🛠 컨트롤 그룹에 올리기", callback_data="adm_st_control")])
        if not rows:
            await msg.reply_text("올릴 방이 정해져 있지 않아요. 그룹에서 /setresultchat 을 먼저 보내 주세요.", reply_markup=home()[1])
            raise ApplicationHandlerStop
        rows.append([B("취소", callback_data="adm_sched")])
        await msg.reply_text(f"{scheduled.fmt(when)} 에 올릴게요. 어느 방에 올릴까요?", reply_markup=M(rows))
    elif t == "pp_block":
        txt = (msg.text or "").strip()
        if txt == "없음":
            settings.delete("popular.block")
            await _done(ctx, user, "인기글 소개의 추가 제외 단어를 모두 지웠어요")
        else:
            words = ",".join(w.strip() for w in txt.replace("，", ",").split(",") if w.strip())
            if not words or len(words) > 300:
                await msg.reply_text("단어를 쉼표로 구분해 300자 이내로 보내 주세요.")
                raise ApplicationHandlerStop
            settings.set_("popular.block", words)
            await _done(ctx, user, f"인기글 소개 제외 단어를 '{words[:40]}'(으)로 정했어요")
        ctx.user_data.pop("adm_await")
        await msg.reply_text("저장했어요 ✅", reply_markup=pp_screen()[1])
    elif t == "ev_listtext":
        txt = (msg.text or "").strip()
        if not txt or len(txt) > 900:
            await msg.reply_text("문구를 900자 이하로 보내 주세요.")
            raise ApplicationHandlerStop
        if txt == "기본":
            settings.delete("event.list_text")
            await _done(ctx, user, "이벤트 목록 문구를 기본값으로 되돌렸어요")
        else:
            settings.set_("event.list_text", txt)
            await _done(ctx, user, "이벤트 목록 문구를 바꿨어요")
        ctx.user_data.pop("adm_await")
        await msg.reply_text("바꿨어요 ✅", reply_markup=ev_list()[1])
    elif t == "ev_photo":
        if not msg.photo:
            await msg.reply_text("사진으로 보내 주세요.")
            raise ApplicationHandlerStop
        fid = msg.photo[-1].file_id
        if st["id"]:
            settings.set_(f"event.photo.{st['id']}", fid)
            await _done(ctx, user, f"이벤트 #{st['id']} 사진을 바꿨어요")
            back = ev_detail(st["id"])[1]
        else:
            settings.set_("event.list_photo", fid)
            await _done(ctx, user, "이벤트 목록 사진을 바꿨어요")
            back = ev_list()[1]
        ctx.user_data.pop("adm_await")
        await msg.reply_text("바꿨어요 ✅", reply_markup=back)
    elif t in ("ev_new", "ev_edit"):
        raw = (msg.text or "").strip()
        title, _, body = raw.partition("\n")
        if not title.strip() or not body.strip():
            await msg.reply_text("첫 줄에 제목, 둘째 줄부터 본문을 적어 보내 주세요.")
            raise ApplicationHandlerStop
        if t == "ev_new":
            eid = events.add(title.strip(), body.strip(), "draft")
            await _done(ctx, user, f"새 이벤트 '{title.strip()[:30]}'를 만들었어요 (기획)")
        else:
            eid = st["id"]
            events.update(eid, title=title.strip(), body=body.strip())
            await _done(ctx, user, f"이벤트 '{title.strip()[:30]}' 내용을 고쳤어요")
        ctx.user_data.pop("adm_await")
        await msg.reply_text("저장했어요 ✅", reply_markup=ev_detail(eid)[1])
    elif t == "admin_add":
        txt = (msg.text or "").strip()
        if not settings.is_owner(user.id) or not txt.isdigit() or not 5 <= len(txt) <= 15:
            await msg.reply_text("숫자 ID만 보내 주세요. (예: 123456789)")
            raise ApplicationHandlerStop
        aid = int(txt)
        settings.add_admin(aid, f"관리자 {aid}", user.id)
        await _done(ctx, user, f"관리자 {aid} 를 추가했어요")
        ctx.user_data.pop("adm_await")
        await msg.reply_text("추가했어요 ✅ 새 관리자가 봇에게 /admin 을 보내면 패널이 열려요.", reply_markup=admins_screen(user.id)[1])
    raise ApplicationHandlerStop


async def setcontrol(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """소유자가 그룹에서 /setcontrol 을 보내면 그 그룹이 컨트롤 그룹이 된다."""
    chat, user = update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("컨트롤 그룹으로 쓸 그룹방에서 보내 주세요.")
        return
    if not settings.is_owner(user.id):
        return
    note = ""
    try:
        me = await ctx.bot.get_chat_member(chat.id, ctx.bot.id)
        if me.status != "administrator":
            note = "\n\n⚠️ 봇을 이 그룹의 관리자로 올려 주세요. 관리자여야 모든 메시지를 읽고 멤버를 확인할 수 있어요."
    except Exception:
        note = "\n\n⚠️ 봇의 권한을 확인하지 못했어요. 봇을 관리자로 올려 주세요."
    settings.set_("control_chat", str(chat.id))
    settings.log_change(user.id, user.full_name, f"컨트롤 그룹을 '{chat.title}'(으)로 정했어요")
    await update.message.reply_text("✅ 이 그룹을 컨트롤 그룹으로 정했어요.\n이 그룹 멤버는 /admin 으로 버튼 이름·링크, 메뉴 사진·문구를 바꿀 수 있어요. 바뀐 내용은 이 그룹에 남아요." + note)


async def unsetcontrol(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if settings.is_owner(update.effective_user.id) and update.effective_chat.id == control_chat():
        settings.delete("control_chat")
        settings.log_change(update.effective_user.id, update.effective_user.full_name, "컨트롤 그룹 연결을 풀었어요")
        await update.message.reply_text("컨트롤 그룹 연결을 풀었어요.")


async def setmenuroom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """메뉴 반복 게시를 올릴 방을 정한다: 그 그룹에서 /setmenuroom, 채널이면 /setmenuroom @채널이름"""
    chat, user = update.effective_chat, update.effective_user
    if not await can_admin(ctx.bot, user, chat):
        return
    target = chat
    if ctx.args:
        arg = ctx.args[0]
        try:
            target = await ctx.bot.get_chat(int(arg) if re.fullmatch(r"-?\d+", arg) else arg)
        except Exception:
            await update.message.reply_text("그 방을 찾지 못했어요. 봇을 먼저 초대했는지, 이름(@채널이름)이 맞는지 확인해 주세요.")
            return
    elif chat.type == "private":
        await update.message.reply_text("메뉴를 올릴 그룹에서 이 명령을 보내거나, /setmenuroom @채널이름 처럼 보내 주세요.")
        return
    note = ""
    try:
        me = await ctx.bot.get_chat_member(target.id, ctx.bot.id)
        if target.type == "channel" and me.status != "administrator":
            note = "\n\n⚠️ 채널에는 봇을 관리자로 올려야 글을 올릴 수 있어요."
    except Exception:
        note = "\n\n⚠️ 봇이 그 방에 있는지 확인하지 못했어요."
    settings.set_("menuloop.chat", str(target.id))
    settings.delete("menuloop.msg")
    settings.log_change(user.id, user.full_name, f"메뉴 반복 게시 방을 '{target.title or target.id}'(으)로 정했어요")
    await update.message.reply_text(f"✅ '{target.title or target.id}' 방을 메뉴 반복 게시 방으로 정했어요.\n켜려면 봇과의 1:1 대화에서 /admin → 🔁 메뉴 반복 게시 → ▶️ 켜기를 눌러 주세요." + note)


def register(app, send_menu, send_events=None):
    global _send_menu, _send_events
    _send_menu = send_menu
    _send_events = send_events
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO) & ~filters.COMMAND, admin_input), group=-2)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & (filters.TEXT | filters.PHOTO) & ~filters.COMMAND, admin_input), group=-2)
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("setcontrol", setcontrol))
    app.add_handler(CommandHandler("setmenuroom", setmenuroom))
    app.add_handler(CommandHandler("unsetcontrol", unsetcontrol))
    app.add_handler(CallbackQueryHandler(admin_cb, pattern="^adm_"))
