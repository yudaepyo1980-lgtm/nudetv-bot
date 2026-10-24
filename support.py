"""1:1 문의 중계: 사용자가 봇에 보낸 문의를 문의방(그룹)으로 전달하고, 팀원이 그 글에 답장하면 사용자에게 돌려보낸다.
문의방은 그룹에서 /setsupportchat 으로 정한다. 정하기 전에는 '1:1 문의' 버튼이 기존처럼 링크로 연결된다."""
import logging
import re
import sqlite3
import time

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, Update
from telegram.error import Forbidden
from telegram.ext import (ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import admin
import settings

log = logging.getLogger("support")
WINDOW = 1800  # 마지막 문의 뒤 30분 동안은 보내는 글을 모두 문의로 본다
REPLY_WINDOW = 86400  # 운영팀이 답장하면 하루 동안 사용자의 다음 글도 문의로 이어 준다
LIMIT_PER_HOUR = 20
MAXLEN = 1500
_recent = {}  # user_id -> 최근 1시간 전송 시각


def _db():
    c = sqlite3.connect(settings.DB)
    c.execute("CREATE TABLE IF NOT EXISTS support_msgs (room INTEGER, msg_id INTEGER, user_id INTEGER, name TEXT, PRIMARY KEY (room, msg_id))")
    return c


def room():
    v = settings.get("support_chat")
    return int(v) if v else None


def enabled():
    return room() is not None


def _remember(r, ids, uid, name):
    with _db() as c:
        c.executemany("INSERT OR REPLACE INTO support_msgs VALUES (?, ?, ?, ?)", [(r, i, uid, name) for i in ids])


def _lookup(r, msg_id):
    with _db() as c:
        return c.execute("SELECT user_id, name FROM support_msgs WHERE room=? AND msg_id=?", (r, msg_id)).fetchone()


PROMPT = ("💬 1:1 문의\n\n문의 내용을 이 대화에 보내 주세요. 글과 사진을 보낼 수 있어요.\n"
          "운영팀이 확인하면 이 대화로 답변드려요.")


async def open_private(chat, user_data):
    user_data["support_until"] = time.time() + WINDOW
    user_data.pop("support_acked", None)
    await chat.send_message(PROMPT, reply_markup=M([[B("문의 끝내기", callback_data="support_end")]]))


async def support_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.data == "support_end":
        ctx.user_data.pop("support_until", None)
        await q.answer("문의를 마쳤어요.")
        try:
            await q.edit_message_text("문의를 마쳤어요. 필요하면 메뉴의 1:1 문의를 다시 눌러 주세요.")
        except Exception:
            pass
        return
    if q.message.chat.type != "private":  # 그룹에서 누르면 봇과의 1:1 대화로 이동
        await q.answer(url=f"https://t.me/{ctx.bot.username}?start=support")
        return
    await q.answer()
    if not enabled():
        await q.message.chat.send_message("지금은 문의를 받을 수 없어요. 잠시 후 다시 이용해 주세요.")
        return
    await open_private(q.message.chat, ctx.user_data)


async def user_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """문의 중인 사용자의 글을 문의방으로 전달한다. 문의 중이 아니면 다른 처리기로 넘긴다."""
    if time.time() > ctx.user_data.get("support_until", 0) or not enabled():
        return
    if ctx.user_data.get("adm_await") or ctx.user_data.get("await_team"):
        return  # 관리자 패널·관심 팀 입력 중이면 건드리지 않음
    user, msg, r = update.effective_user, update.message, room()
    now = time.time()
    hist = [t for t in _recent.get(user.id, []) if now - t < 3600]
    if len(hist) >= LIMIT_PER_HOUR:
        await msg.reply_text("문의가 너무 많이 들어왔어요. 잠시 뒤에 다시 보내 주세요.")
        raise ApplicationHandlerStop
    _recent[user.id] = hist + [now]
    header = f"📩 문의 · {user.full_name}" + (f" (@{user.username})" if user.username else "") + f" · ID {user.id}"
    try:
        if msg.text:
            ids = [(await ctx.bot.send_message(r, f"{header}\n\n{msg.text[:MAXLEN]}")).message_id]
        elif msg.photo:
            cap = f"{header}\n\n{msg.caption or ''}"[:1000]
            ids = [(await ctx.bot.send_photo(r, msg.photo[-1].file_id, caption=cap)).message_id]
        else:  # 음성·파일 등은 머리글과 함께 그대로 복사
            h = await ctx.bot.send_message(r, f"{header}\n(첨부)")
            c = await ctx.bot.copy_message(r, update.effective_chat.id, msg.message_id)
            ids = [h.message_id, c.message_id]
    except Exception:
        log.exception("문의 전달 실패")
        await msg.reply_text("지금은 문의를 전달하지 못했어요. 잠시 후 다시 보내 주세요.")
        raise ApplicationHandlerStop
    _remember(r, ids, user.id, user.full_name)
    ctx.user_data["support_until"] = now + WINDOW
    if now > ctx.user_data.get("support_acked", 0):
        ctx.user_data["support_acked"] = now + WINDOW
        await msg.reply_text("✅ 접수했어요. 운영팀이 확인하면 이 대화로 답변드려요.",
                             reply_markup=M([[B("문의 끝내기", callback_data="support_end")]]))
    raise ApplicationHandlerStop


async def staff_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """문의방에서 문의 글에 답장하면 그 사용자에게 전달한다."""
    r = room()
    if not r or update.effective_chat.id != r:
        return
    msg = update.message
    if msg.from_user.is_bot or not msg.reply_to_message:
        return
    row = _lookup(r, msg.reply_to_message.message_id)
    if not row:
        return
    uid, name = row
    try:
        if msg.text:
            await ctx.bot.send_message(uid, f"💬 운영팀 답변\n\n{msg.text}")
        else:
            await ctx.bot.send_message(uid, "💬 운영팀 답변")
            await ctx.bot.copy_message(uid, r, msg.message_id)
    except Forbidden:
        await msg.reply_text("전달하지 못했어요. 사용자가 봇을 차단했거나 대화를 지웠을 수 있어요.")
        raise ApplicationHandlerStop
    except Exception:
        log.exception("답변 전달 실패")
        await msg.reply_text("전달하지 못했어요. 잠시 후 다시 보내 주세요.")
        raise ApplicationHandlerStop
    ctx.application.user_data[uid]["support_until"] = time.time() + REPLY_WINDOW  # 사용자의 다음 글도 이어서 문의로
    settings.log_change(msg.from_user.id, msg.from_user.full_name, f"{name}님 문의에 답변했어요")
    await msg.reply_text(f"✅ {name}님께 전달했어요")
    raise ApplicationHandlerStop


async def setsupportchat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    if not await admin.can_admin(ctx.bot, user, chat):
        return
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("문의를 받을 그룹방에서 이 명령을 보내 주세요.")
        return
    note = ""
    try:
        me = await ctx.bot.get_chat_member(chat.id, ctx.bot.id)
        if me.status != "administrator":
            note = "\n\n⚠️ 봇을 이 그룹의 관리자로 올려 주세요. 관리자여야 팀원의 답장을 읽을 수 있어요."
    except Exception:
        note = "\n\n⚠️ 봇의 권한을 확인하지 못했어요."
    settings.set_("support_chat", str(chat.id))
    settings.log_change(user.id, user.full_name, f"문의 받는 방을 '{chat.title}'(으)로 정했어요")
    await update.message.reply_text("✅ 이 방을 문의방으로 정했어요.\n메뉴의 '1:1 문의'를 누른 사용자의 글이 여기에 올라오고, "
                                    "팀원이 그 글에 답장하면 사용자에게 전달돼요." + note)


async def unsetsupportchat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await admin.can_admin(ctx.bot, update.effective_user, update.effective_chat):
        settings.delete("support_chat")
        await update.message.reply_text("문의방 설정을 풀었어요. '1:1 문의' 버튼은 다시 링크로 연결돼요.")


def register(app):
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, user_message), group=-3)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.REPLY & ~filters.COMMAND, staff_reply), group=-3)
    app.add_handler(CommandHandler("setsupportchat", setsupportchat))
    app.add_handler(CommandHandler("unsetsupportchat", unsetsupportchat))
    app.add_handler(CallbackQueryHandler(support_cb, pattern="^support(_end)?$"))
