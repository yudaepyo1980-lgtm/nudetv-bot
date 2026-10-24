"""오늘의 추천픽 결과 모아 올리기: 5경기가 모두 끝나면 적중/미적중을 한 번에 정리해 결과방에 올린다.
픽이 여러 항목(예: '레버쿠젠 승 / 언더')이면 항목마다 ✅/❌를 붙이고, 전부 맞아야 적중으로 친다."""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

import admin
import alerts
import flashscore
import picks_today
import posts
import settings

log = logging.getLogger("picks_report")
KST = timezone(timedelta(hours=9))
CHECK_EVERY = 300
DEADLINE_HOUR = 12  # 다음 날 낮 12시까지 안 끝난 경기가 있어도 그때는 올림
_decorate = lambda text: None  # bot.py 가 register() 로 넘겨 줌


def _now():
    return datetime.now(KST)


item_results = picks_today.item_results
pick_line = picks_today.pick_line


def result_line(r):
    lab = picks_today.status_label(r)
    return lab or "⏳ 아직 결과가 집계되지 않았어요"


def build_text(day, rec, final):
    icons = {"ovfb": "⚽", "dofb": "🇰🇷", "bask": "🏀", "base": "⚾", "hock": "🏒", "voll": "🏐", "esp": "🎮"}
    title = f"🦊 오늘의 추천픽 결과 · {day[5:].replace('-', '.')} 🦊" + ("" if final else "\n(중간 집계)")
    blocks = []
    for r in rec:
        head = " · ".join(x for x in (r["kickoff"] and f"⏰ {r['kickoff']}", f"{icons.get(r['key'], '')} {r['league']}".strip()) if x)
        blocks.append(f"{head}\n🦊 {r['match']}\n{pick_line(r)}\n{result_line(r)}")
    text = title + "\n\n" + "\n\n".join(blocks)
    line = picks_today.summary_line(rec)
    text += f"\n\n{line}" if line else ""
    tot = picks_today.stats()[0]
    if tot[1]:
        text += f"\n📈 누적 {tot[0]}/{tot[1]} ({round(tot[0] * 100 / tot[1])}%)"
    return text


async def _games_for(offset):
    """플래시스코어 경기 (홈, 원정, 상태, 시작시각, 홈점수, 원정점수). offset: 0 오늘, -1 어제"""
    if offset == 0:
        return await posts._feed_games(refresh=True)
    games = []
    async with httpx.AsyncClient(timeout=25, headers=flashscore.HEADERS) as c:
        for sid in flashscore.SPORT_ID.values():
            try:
                r = await c.get(flashscore.FEED.format(sport=sid).replace("_0_9_", f"_{offset}_9_"))
                r.raise_for_status()
            except Exception:
                continue
            games += [(g.get("AE", ""), g.get("AF", ""), g.get("AB"), int(g["AD"]), g.get("AG", ""), g.get("AH", ""))
                      for lg in flashscore._parse(r.text) for g in lg["games"]]
    return games


async def refresh_day(day):
    """그날 기록 중 결과가 없는 경기를 판정한다. 자정이 지난 어제 경기는 어제 피드로 찾는다."""
    data = picks_today._load()
    todo = [r for r in data.get(day, []) if r["result"] is None]
    if not todo:
        return
    age = (_now().date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    if age > 1:
        return
    games = await _games_for(-age)
    if age == 1:  # 어제 시작해 오늘로 넘어간 경기가 오늘 피드에 있을 수도 있음
        games = games + await _games_for(0)
    changed = {}
    for r in todo:
        g = posts._match_game((r["home"], r["away"]), games)
        if not g or g[2] != "3" or len(g) < 6 or g[4] == "" or g[5] == "":
            continue
        swapped = posts._same(r["home"], g[1]) and not posts._same(r["home"], g[0])
        hs, as_ = (int(g[5]), int(g[4])) if swapped else (int(g[4]), int(g[5]))
        res = picks_today.judge(r["pick"], r["home"], r["away"], hs, as_, r["key"])
        changed[r["title"]] = (f"{hs}:{as_}", "none" if res is None else ("hit" if res else "miss"))
    if changed:  # 기다리는 동안 다른 곳이 파일을 고쳤을 수 있어 다시 읽어서 반영
        data = picks_today._load()
        for r in data.get(day, []):
            if r["title"] in changed and r["result"] is None:
                r["score"], r["result"] = changed[r["title"]]
        picks_today._save(data)


def _is_final(day, rec):
    if rec and all(r["result"] is not None for r in rec):
        return True
    deadline = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=KST) + timedelta(days=1, hours=DEADLINE_HOUR)
    return _now() >= deadline


async def post_day(bot, day, chat_id, final):
    rec = picks_today._load().get(day, [])
    text = build_text(day, rec, final)
    await bot.send_message(chat_id, text, entities=_decorate(text))


def auto_off():
    """/admin 에서 '추천픽 결과 자동 게시'를 꺼 두었는지"""
    return settings.get("picks_report.auto") == "0"


def result_chat():
    v = settings.get("result_chat")
    return int(v) if v else None


async def maybe_post(bot):
    """자동 발송: 끝나지 않은 날 중 5경기가 모두 끝났거나 기한이 지난 날을 결과방에 한 번만 올린다."""
    chat = result_chat()
    if not chat or auto_off():
        return
    data = picks_today._load()
    days = [d for d in sorted(data) if data[d] and d >= (_now() - timedelta(days=2)).strftime("%Y-%m-%d")]
    for day in days:
        if settings.get(f"picks_posted.{day}"):
            continue
        await refresh_day(day)
        rec = picks_today._load().get(day, [])
        if not _is_final(day, rec):
            continue
        try:
            await post_day(bot, day, chat, final=True)
            settings.set_(f"picks_posted.{day}", "1")
        except Exception as e:
            log.exception("추천픽 결과 올리기 실패")
            await alerts.notify(bot, f"🔴 추천픽 결과를 결과방에 올리지 못했어요\n{type(e).__name__}: {alerts._clean(e)}", key="picks_report_fail")


async def report_loop(bot):
    await asyncio.sleep(90)
    while True:
        try:
            await maybe_post(bot)
        except Exception:
            log.exception("추천픽 결과 점검 오류")
        await asyncio.sleep(CHECK_EVERY)


async def setresultchat(update, ctx):
    chat, user = update.effective_chat, update.effective_user
    if not await admin.can_admin(ctx.bot, user, chat):
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
        await update.message.reply_text("결과를 올릴 그룹에서 이 명령을 보내거나, /setresultchat @채널이름 처럼 보내 주세요.")
        return
    note = ""
    try:
        me = await ctx.bot.get_chat_member(target.id, ctx.bot.id)
        if target.type == "channel" and me.status != "administrator":
            note = "\n\n⚠️ 채널에는 봇을 관리자로 올려야 글을 올릴 수 있어요."
    except Exception:
        note = "\n\n⚠️ 봇이 그 방에 있는지 확인하지 못했어요."
    settings.set_("result_chat", str(target.id))
    settings.log_change(user.id, user.full_name, f"추천픽 결과 올릴 방을 '{target.title or target.id}'(으)로 정했어요")
    await update.message.reply_text(f"✅ '{target.title or target.id}' 방에 오늘의 추천픽이 모두 끝나면 결과를 한 번에 올릴게요.\n지금 바로 올려 보려면 /picksreport 를 보내세요." + note)


async def unsetresultchat(update, ctx):
    if await admin.can_admin(ctx.bot, update.effective_user, update.effective_chat):
        settings.delete("result_chat")
        await update.message.reply_text("결과방 설정을 풀었어요. 이제 자동으로 올리지 않아요.")


async def picksreport(update, ctx):
    """지금까지의 결과를 요청한 사람의 1:1 대화로만 보내 준다 (방에는 올리지 않음). 방에 올리려면 아래 버튼을 눌러야 한다."""
    chat, user = update.effective_chat, update.effective_user
    if not await admin.can_admin(ctx.bot, user, chat):
        return
    data = picks_today._load()
    days = [d for d in sorted(data) if data[d]]
    if not days:
        await update.message.reply_text("저장된 추천픽이 아직 없어요.")
        return
    day = days[-1]
    await refresh_day(day)
    rec = picks_today._load().get(day, [])
    final = _is_final(day, rec)
    text = build_text(day, rec, final)
    from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
    markup = M([[B("📣 결과방에 올리기", callback_data=f"pr_post_{day}")]]) if result_chat() else None
    try:
        await ctx.bot.send_message(user.id, text, entities=_decorate(text), reply_markup=markup)
    except Exception:
        await update.message.reply_text("봇과의 1:1 대화에서 /start 를 한 번 보낸 뒤 다시 해 주세요. 결과는 방이 아니라 1:1 대화로만 보내 드려요.")
        return
    if chat.type != "private":
        await update.message.reply_text("1:1 대화로 보내 드렸어요 ✅ (방에는 올리지 않았어요)")


async def pr_post(update, ctx):
    """1:1 대화에서 '결과방에 올리기'를 눌렀을 때만 방에 올린다."""
    q = update.callback_query
    if not await admin.can_admin(ctx.bot, q.from_user, q.message.chat):
        await q.answer("관리자만 할 수 있어요.", show_alert=True)
        return
    day = q.data[len("pr_post_"):]
    chat = result_chat()
    if not chat:
        await q.answer("결과방이 정해져 있지 않아요.", show_alert=True)
        return
    await q.answer()
    rec = picks_today._load().get(day, [])
    final = _is_final(day, rec)
    try:
        await post_day(ctx.bot, day, chat, final)
    except Exception as e:
        await q.message.reply_text(f"올리지 못했어요: {type(e).__name__}")
        return
    if final:
        settings.set_(f"picks_posted.{day}", "1")  # 자동 발송이 같은 날을 또 올리지 않게
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await q.message.reply_text("결과방에 올렸어요 ✅")


def register(app, decorate):
    from telegram.ext import CommandHandler
    global _decorate
    _decorate = decorate
    app.add_handler(CommandHandler("setresultchat", setresultchat))
    app.add_handler(CommandHandler("unsetresultchat", unsetresultchat))
    app.add_handler(CommandHandler("picksreport", picksreport))
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(pr_post, pattern=r"^pr_post_\d{4}-\d{2}-\d{2}$"))
