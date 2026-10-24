"""공개 텔레그램 채널(t.me/s/<채널>)에서 경기 결과 글을 가져온다."""
import html
import os
import re

import httpx


async def get_results(limit=5):
    channel = os.getenv("RESULT_CHANNEL", "")
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as c:
        r = await c.get(f"https://t.me/s/{channel}")
    items = []
    for b in r.text.split("tgme_widget_message_wrap")[1:]:
        pid = re.search(r'data-post="([^"]+)"', b)
        txt = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
        if not pid or not txt:
            continue
        text = re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", txt.group(1)))
        text = html.unescape(text).strip()
        title = text.split("\n", 1)[0]
        if "경기 결과" in title:
            items.append({"title": title, "url": f"https://t.me/{pid.group(1)}"})
    return items[::-1][:limit]


SCORE_RE = re.compile(r"^\d+\.\s*(\d{1,2}:\d{2})\s+(.+?)\s+(\d+)\s*:\s*(\d+)\s+(.+?)\s*$")
_cache = {"t": 0, "day": None, "data": {}}


async def get_today_scores(day_mmdd, pages=6):
    """오늘자 '경기 결과' 글에서 {리그: [(시간, '팀 점수 : 점수 팀 종료'), ...]} 를 뽑는다. (60초 캐시)"""
    import time
    if _cache["day"] == day_mmdd and time.time() - _cache["t"] < 60:
        return _cache["data"]
    channel = os.getenv("RESULT_CHANNEL", "")
    data, before, seen_old = {}, "", False
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as c:
        for _ in range(pages):
            r = await c.get(f"https://t.me/s/{channel}", params={"before": before} if before else None)
            blocks = r.text.split("tgme_widget_message_wrap")[1:]
            if not blocks:
                break
            ids = []
            for b in blocks:
                pid = re.search(r'data-post="[^/]+/(\d+)"', b)
                txt = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
                if pid:
                    ids.append(int(pid.group(1)))
                if not pid or not txt:
                    continue
                text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", txt.group(1)))).strip()
                m = re.match(r"✅\s*(.+?)\s*경기 결과\s*·\s*(\d{1,2}\.\d{1,2})", text)
                if not m:
                    continue
                if m.group(2) != day_mmdd:
                    seen_old = seen_old or m.group(2) < day_mmdd
                    continue
                for line in text.split("\n"):
                    s = SCORE_RE.match(line.strip())
                    if s:
                        t, a, sa, sb, bn = s.groups()
                        data.setdefault(m.group(1), {})[(t, a, bn)] = f"{a} {sa} : {sb} {bn} 종료"
            if seen_old or not ids:
                break
            before = str(min(ids))
    result = {lg: sorted((k[0], v) for k, v in games.items()) for lg, games in data.items()}
    _cache.update(t=time.time(), day=day_mmdd, data=result)
    return result
