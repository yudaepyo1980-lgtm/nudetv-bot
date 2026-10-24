"""검색 노출(SEO)용 봇 소개·채널/그룹 설명 문구를 적용한다."""
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

import settings

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
_SITE = os.getenv("SITE_URL", "").strip().rstrip("/")
_SITE_TAIL = f" {_SITE}/" if _SITE else ""  # 사이트 주소는 .env 의 SITE_URL 에서

log = logging.getLogger(__name__)

# 텔레그램 글자 수 제한: 짧은 소개 120자, 봇 소개 512자, 채널/그룹 설명 255자
BOT_SHORT = "누드TV(누드티비) NO.1 스포츠 중계 | 무료 중계, 초고화질 라이브, 해외축구·MLB·KBO 실시간 중계"

def bot_about():
    """봇 소개 문구 (문의 아이디는 .env 의 SUPPORT_USERNAME)"""
    contact = os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@")
    text = (
        "🦊 𝗡𝗨𝗗𝗘 𝗧𝗩 · 누드TV(누드티비) 안내봇 🦊\n\n"
        "📺 NO.1 스포츠 중계 · 해외축구 · MLB · KBO 실시간 중계\n\n"
        "➮ 오늘의 경기결과 · 실시간 스코어 !\n"
        "➮ 오늘의 스포츠분석 · 추천픽 · 적중 결과 !\n"
        "➮ 관심 팀 경기 모아보기 !\n"
        "➮ 누드TV 이벤트 안내 !\n\n"
        + (f"💬 이벤트·문의 : @{contact}\n\n" if contact else "")
        + "아래 [시작]을 눌러 메뉴를 확인하세요 👇"
    )
    assert len(text) <= 512, "BOT_ABOUT 글자 수 초과"
    return text


CHANNEL_ABOUT = (
    "누드TV(누드티비) 공식채널 | NO.1 스포츠 중계, 무료 중계 사이트 안내. "
    "해외축구·MLB·KBO 실시간 중계, 초고화질 라이브, 오늘의 추천픽, 스코어, 무료쿠폰·포인트 이벤트 공지."
    + _SITE_TAIL
)

GROUP_ABOUT = (
    "누드TV(누드티비) 스포츠 중계 커뮤니티 | 해외축구·MLB·KBO 실시간 중계, 추천픽, 스코어를 함께 확인하세요. "
    "'메뉴' 또는 '누드티비'를 입력하면 봇 메뉴가 열립니다."
    + _SITE_TAIL
)

_LIMITS = {"BOT_SHORT": 120, "CHANNEL_ABOUT": 255, "GROUP_ABOUT": 255}
for _k, _n in _LIMITS.items():
    assert len(globals()[_k]) <= _n, f"{_k} 글자 수 초과"


async def apply_bot(bot):
    """봇 소개 문구를 적용한다(시작할 때마다 호출, 같은 값이면 변화 없음)."""
    try:
        await bot.set_my_short_description(BOT_SHORT)
        await bot.set_my_description(bot_about())
        log.info("봇 소개 문구 적용")
    except Exception:
        log.exception("봇 소개 문구 적용 실패")


async def setdesc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/setdesc  → 지금 그룹방 설명 변경 (그룹에서)
    /setdesc <채팅ID> channel|group  → 지정한 채널/그룹 설명 변경 (1:1 대화에서)
    /setdesc bot  → 봇 소개 다시 적용"""
    if not settings.is_admin(update.effective_user.id):
        return
    msg, args = update.message, ctx.args
    if args and args[0] == "bot":
        await apply_bot(ctx.bot)
        await msg.reply_text("봇 소개 문구를 적용했습니다.")
        return
    if len(args) == 2 and args[1] in ("channel", "group"):
        chat_id, kind = args[0], args[1]
    elif not args and msg.chat.type in ("group", "supergroup"):
        chat_id, kind = msg.chat.id, "group"
    else:
        await msg.reply_text("사용법: 그룹방에서 /setdesc\n또는 1:1에서 /setdesc <채팅ID> channel|group\n봇 소개만: /setdesc bot")
        return
    text = CHANNEL_ABOUT if kind == "channel" else GROUP_ABOUT
    try:
        await ctx.bot.set_chat_description(chat_id, text)
        await msg.reply_text("설명을 변경했습니다.")
    except Exception as e:
        await msg.reply_text(f"실패: {e}\n봇이 해당 방의 관리자이고 '채널 정보 변경' 권한이 있는지 확인하세요.")


def register(app):
    app.add_handler(CommandHandler("setdesc", setdesc))
