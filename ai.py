"""누드티비 봇 AI 대화 (Claude API). ANTHROPIC_API_KEY 가 없으면 비활성."""
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from anthropic import AsyncAnthropic

KST = timezone(timedelta(hours=9))
MODEL = os.getenv("AI_MODEL", "claude-haiku-5-5")
DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "0"))  # 0 = 제한 없음
SYSTEM = (
    "너는 '누드TV' 텔레그램 봇의 안내 도우미야. 해외축구, EPL, NBA, MLB, KBO 등 스포츠 중계 일정, "
    "라이브스코어, 경기 분석, 하이라이트 같은 스포츠 정보를 친근한 한국어로 짧게(5문장 이내) 알려줘. "
    "모르는 실시간 정보는 지어내지 말고 메뉴의 '오늘의 경기결과 스코어'나 '누드티비 바로가기'를 안내해. "
    "베팅이나 도박을 권유하거나 승패를 장담하지 마."
)

_client = None
_used = defaultdict(int)  # (날짜, user_id) -> 횟수


def enabled():
    return bool(os.getenv("ANTHROPIC_API_KEY"))


async def reply(user_id, text):
    global _client
    key = (datetime.now(KST).strftime("%Y-%m-%d"), user_id)
    if DAILY_LIMIT and _used[key] >= DAILY_LIMIT:
        return f"오늘은 AI 대화 {DAILY_LIMIT}회를 모두 사용했어요. 내일 다시 만나요 🙏"
    _used[key] += 1
    _client = _client or AsyncAnthropic()
    msg = await _client.messages.create(
        model=MODEL, max_tokens=500, system=SYSTEM, messages=[{"role": "user", "content": text[:1000]}]
    )
    return "".join(b.text for b in msg.content if b.type == "text") or "답변을 만들지 못했어요."
