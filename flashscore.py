"""플래시스코어 라이브스코어 피드에서 종목별 오늘 경기를 읽어 리그별 페이지 글로 만든다."""
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

KST = timezone(timedelta(hours=9))
FEED = "https://global.flashscore.ninja/34/x/feed/f_{sport}_0_9_ko_1"
# 접속 키는 .env 의 FLASHSCORE_SIGN (없으면 요청이 거절되어 네이버 스코어로 대체됨)
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
HEADERS = {"User-Agent": "Mozilla/5.0", "x-fsign": os.getenv("FLASHSCORE_SIGN", ""), "Referer": "https://www.flashscore.co.kr/"}
SPORT_ID = {"football": 1, "basketball": 3, "baseball": 6, "volleyball": 12}
# 목록 맨 위에 올릴 리그 (이름에 포함되면 해당 순서)
PRIORITY = ["대한민국: K리그 1", "대한민국: K리그 2", "대한민국: KBO", "대한민국: KBL", "대한민국: V리그",
            "잉글랜드: 프리미어리그", "스페인: 라리가", "독일: 분데스리가", "이탈리아: 세리에 A", "프랑스: 리그 1",
            "유럽: 챔피언스리그", "미국: NBA", "미국: MLB"]
_cache = {}  # sport -> (time, leagues)


def _parse(text):
    leagues, cur = [], None
    for rec in text.split("~")[1:]:
        d = dict(kv.split("÷", 1) for kv in rec.split("¬") if "÷" in kv)
        if "ZA" in d:
            cur = {"name": d["ZA"], "games": []}
            leagues.append(cur)
        elif "AA" in d and cur is not None:
            cur["games"].append(d)
    return [lg for lg in leagues if lg["games"]]


async def _leagues(sport, refresh=False):
    """백그라운드(posts.warm_cache)가 1분마다 refresh=True 로 받아 두므로, 버튼은 5분 안의 저장값을 바로 쓴다."""
    hit = _cache.get(sport)
    if hit and not refresh and time.time() - hit[0] < 300:
        return hit[1]
    async with httpx.AsyncClient(timeout=20, headers=HEADERS) as c:
        r = await c.get(FEED.format(sport=SPORT_ID[sport]))
        r.raise_for_status()
    leagues = _parse(r.text)
    _cache[sport] = (time.time(), leagues)
    return leagues


def _line(g):
    t = datetime.fromtimestamp(int(g["AD"]), KST).strftime("%H:%M")
    ab = g.get("AB")
    home, away = g.get("AE", "?"), g.get("AF", "?")
    if ab in ("2", "3") and g.get("AG", "") != "":
        score = f"{g['AG']} : {g.get('AH', '-')}"
    else:
        score = "vs"
    st = {"2": " 🔴LIVE", "3": " 종료"}.get(ab, "")
    if ab not in ("1", "2", "3"):
        st = " 연기/취소"
    return t, f"{t} {home} {score} {away}{st}"


def _rank(lg):
    live = any(g.get("AB") == "2" for g in lg["games"])
    for i, k in enumerate(PRIORITY):
        if lg["name"].startswith(k):
            return (0, i, lg["name"])
    return (1, 0 if live else 1, lg["name"])


# 주요 리그: 이름이 이 값들로 시작하면 '주요' 화면에 나온다 (한국 리그는 전부)
MAJOR_PREFIX = ["대한민국:", "유럽: 챔피언스리그", "유럽: 유로파", "유럽: 컨퍼런스"]  # 이 글자로 시작하면 주요
MAJOR_EXACT = {"잉글랜드: 프리미어리그", "스페인: 라리가", "독일: 분데스리가", "이탈리아: 세리에 A", "프랑스: 리그 1",
               "일본: J1 리그", "일본: NPB", "미국: NBA", "미국: MLB", "미국: WNBA", "미국: NFL"}  # 정확히 같아야 주요


def _is_major(lg):
    n = lg["name"]
    return n in MAJOR_EXACT or any(n.startswith(k) for k in MAJOR_PREFIX)


async def pages(sport, label, mode="m", limit_chars=1800):
    """mode: m=주요 리그, l=진행중(LIVE)만, a=전체. 4096자 제한/가독성 때문에 여러 페이지로 나눈다."""
    day = datetime.now(KST).strftime("%m.%d")
    leagues = sorted(await _leagues(sport), key=_rank)
    title = {"m": "주요 경기", "l": "🔴 LIVE", "a": "전체 경기"}[mode]
    head = f"🦊 {label} {title} ({day})"
    out, cur, size = [], [], len(head)
    for lg in leagues:
        games = lg["games"]
        if mode == "m" and not _is_major(lg):
            continue
        if mode == "l":
            games = [g for g in games if g.get("AB") == "2"]
            if not games:
                continue
        rows = sorted(_line(g) for g in games)
        block = f"\n\n🦊 {lg['name']}\n" + "\n".join(r for _, r in rows)
        while len(block) > limit_chars:  # 한 리그가 너무 길면 쪼갬
            cut = block.rfind("\n", 0, limit_chars)
            cur.append(block[:cut])
            out.append(cur)
            cur, size = [], len(head)
            block = "\n\n" + block[cut + 1:]
        if size + len(block) > limit_chars and cur:
            out.append(cur)
            cur, size = [], len(head)
        cur.append(block)
        size += len(block)
    if cur:
        out.append(cur)
    if not out:
        return [head + "\n\n해당하는 경기가 없습니다."]
    n = len(out)
    return [head + (f"  [{i + 1}/{n}]" if n > 1 else "") + "".join(p) for i, p in enumerate(out)]
