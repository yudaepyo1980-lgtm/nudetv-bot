"""게시판 글 가져오기. 사이트 API가 JSON을 주면 사용, 안 되면 /addpick 으로 등록한 글을 사용."""
import json
import re
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

PICKS_FILE = Path(__file__).with_name("picks.json")
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": os.getenv("SITE_URL", "").rstrip("/") + "/community/sports-talk",
}


def _load_local():
    try:
        return json.loads(PICKS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_local(items):
    PICKS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def add_local(title, url):
    items = _load_local()
    items.insert(0, {"title": title, "url": url})
    save_local(items[:20])


def clear_local():
    save_local([])


async def _fetch_site(limit, scan=None):
    author = os.getenv("AUTHOR_NICKNAME", "").strip()
    params = {"board": os.getenv("BOARD_NAME", "sports_talk"), "page": 1, "pageSize": scan or (50 if author else limit)}
    async with httpx.AsyncClient(timeout=45, headers=HEADERS, follow_redirects=True) as c:
        r = await c.get(os.getenv("BOARD_API", ""), params=params)
    if "json" not in r.headers.get("content-type", ""):
        raise ValueError("JSON 아님")
    data = r.json()
    rows = data.get("posts") or data.get("items") or data.get("data") or []
    if author:
        rows = [r for r in rows if r.get("author_nickname") == author]
    tpl = os.getenv("POST_URL_TEMPLATE", "")
    return [
        {"title": str(p.get("title", "(제목 없음)")), "url": tpl.format(id=p.get("id")), "id": p.get("id"),
         "created": p.get("created_at", "")}
        for p in rows[:limit]
        if p.get("id") is not None
    ]


async def get_posts(limit=5, scan=None):
    """(글 목록, 출처) 반환. 출처: 'site' 또는 'local'"""
    try:
        items = await _fetch_site(limit, scan)
        if items:
            return items, "site"
    except Exception:
        pass
    return _load_local()[:limit], "local"


# 종목 분류: (키, 이름, 제목에 들어있는 글자들). 위에서부터 먼저 맞는 것으로 분류한다.
CATEGORIES = [
    ("dofb", "국내축구", ["K리그", "K League", "K3", "K4리그", "코리아컵", "FA컵"]),
    ("hock", "하키", ["NHL", "KHL", "하키"]),
    ("base", "야구", ["KBO", "MLB", "NPB", "CPBL", "야구"]),
    ("bask", "농구", ["NBA", "KBL", "WKBL", "농구", "유로리그"]),
    ("voll", "배구", ["V리그", "KOVO", "VNL", "배구"]),
    ("esp", "E-SPORTS", ["LCK", "LPL", "LoL", "롤드컵", "발로란트", "E-SPORTS", "e스포츠", "이스포츠", "도타", "CS2", "오버워치"]),
    ("ovfb", "해외축구", ["프리미어리그", "라리가", "분데스리가", "세리에", "리그 1", "리그1", "에레디비시", "챔피언스리그",
                      "유로파", "컨퍼런스", "슈퍼리그", "J리그", "J1", "J2", "MLS", "프리메이라", "A매치", "월드컵",
                      "챔피언십", "FA컵", "축구", "프리뷰"]),
]
CAT_NAME = {k: n for k, n, _ in CATEGORIES}


def categorize(title):
    for key, _, words in CATEGORIES:
        if any(w.lower() in title.lower() for w in words):
            return key
    return None


_intro_cache = {}


def _summary_lines(content, max_lines=2, width=60):
    """본문 HTML에서 '◆ 팀 — 한줄 요약' 제목줄만 뽑는다. (결론·픽 줄은 쓰지 않음)"""
    import html as _html
    import re
    text = re.sub(r"<img[^>]*>", "", content or "")
    text = re.sub(r"</?(b|strong|span|em|i|u|font|mark|a|s)(\s[^>]*)?>", "", text)  # 글자 꾸밈 태그는 줄바꿈으로 쓰지 않음
    text = _html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"<br\s*/?>|</p>", "\n", text)))
    lines = []
    for ln in text.split("\n"):
        ln = ln.strip()
        if ln.startswith("◆") and "결론" not in ln:
            ln = ln.lstrip("◆ ").strip()
            lines.append(ln if len(ln) <= width else ln[: width - 1] + "…")
    if not lines:  # ◆ 형식이 아닌 글(농구 등)은 '포인트:' 줄을 사용 (추천/픽 줄은 쓰지 않음)
        for ln in text.split("\n"):
            ln = ln.strip().lstrip("·").strip()
            if ln.startswith("포인트"):
                ln = ln.split(":", 1)[-1].strip()
                lines.append(ln if len(ln) <= 80 else ln[:79] + "…")
                break
    if not lines:  # 야구(선발)·배구(승부 분석) 글은 해당 섹션의 첫 문장을 사용 (결론·픽은 쓰지 않음)
        body = [ln.strip() for ln in text.split("\n") if ln.strip()]
        for i, ln in enumerate(body):
            if ln in ("선발", "승부 분석") and i + 1 < len(body):
                chunk = []
                for b in body[i + 1:]:
                    if b in ("선발", "불펜", "타선", "결론", "승부 분석"):
                        break
                    chunk.append(b)
                chunk = " ".join(chunk)
                first = re.split(r"(?<=[.다요])\s", chunk, maxsplit=1)[0].strip()
                lines.append(first if len(first) <= 80 else first[:79] + "…")
                break
    lines = lines[:max_lines]
    m = re.search(r"▶\s*픽\s*:\s*([^\n]+)", text)
    if m:
        lines.append("PICK:" + m.group(1).strip())
    return lines


async def get_intro(limit=5, cat=None):
    """소개용 [{title, url, lines}] (5분 캐시). cat 이 있으면 그 종목 글만. 본문을 못 가져오면 lines 는 빈 리스트."""
    import asyncio
    import time
    hit = _intro_cache.get(cat)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    items, _ = await get_posts(100 if cat else limit, scan=100 if cat else None)
    if cat:
        items = [i for i in items if categorize(i["title"]) == cat][:limit]
    api = os.getenv("BOARD_API", "")

    async def one(c, it):
        try:
            pid = it["url"].rstrip("/").rsplit("/", 1)[-1]
            r = await c.get(f"{api}/{pid}")
            it["lines"] = _summary_lines(r.json()["post"]["content"])
        except Exception:
            it["lines"] = []
        return it

    async with httpx.AsyncClient(timeout=45, headers=HEADERS, follow_redirects=True) as c:
        items = await asyncio.gather(*(one(c, dict(i)) for i in items))
    _intro_cache[cat] = (time.time(), list(items))
    return list(items)


_list_cache = [0, []]  # [시각, 글 목록]
_detail_cache = {}  # 글 id -> (time, lines, kickoff)


async def _detail(c, it):
    import re
    import time
    hit = _detail_cache.get(it.get("id"))
    if hit and time.time() - hit[0] < 1500:
        return hit[1], hit[2], hit[3]
    api = os.getenv("BOARD_API", "")
    try:
        r = await c.get(f"{api}/{it['id']}")
        content = r.json()["post"]["content"]
        lines = _summary_lines(content)
        ms = re.findall(r"(\d{4})\.(\d{2})\.(\d{2})\([가-힣]\)\s*(\d{2}:\d{2})", re.sub(r"<[^>]+>", " ", content))
        kick = f"{ms[0][1]}.{ms[0][2]} {ms[0][3]}" if ms else ""
        last = f"{ms[-1][0]}-{ms[-1][1]}-{ms[-1][2]} {ms[-1][3]}" if ms else ""  # 묶음 글은 마지막 경기 기준
    except Exception:
        return [], "", ""
    _detail_cache[it["id"]] = (time.time(), lines, kick, last)
    return lines, kick, last


_SUFFIX = re.compile(r"\s*(핵심 정리|선발 매치업|분석|프리뷰)\s*$")
_CLUB_WORDS = re.compile(r"\b(FC|AFC|CF|SC|AC|AS|US|RC|SSC|CA|RCD|FK|BK|SV|VfB|VfL|RB|1\.|04|1907|AT)\b", re.I)


def _norm(s):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", _CLUB_WORDS.sub("", s))


def _same(a, b):
    a, b = _norm(a), _norm(b)
    return len(a) >= 2 and len(b) >= 2 and (a in b or b in a)


def _pairs(title):
    """제목에서 (홈, 원정) 쌍들을 뽑는다. 묶음 글은 여러 쌍."""
    body = _SUFFIX.sub("", re.sub(r"\s*외 \d+경기", "", title.split("]", 1)[-1]))
    out = []
    for part in re.split(r"\s*·\s*", body):
        if " vs " in part:
            h, a = (x.strip() for x in part.split(" vs ", 1))
            out.append((h, _SUFFIX.sub("", a)))
    return out


_feed_cache = [0, []]  # [시각, 경기 목록]


async def _feed_games(refresh=False):
    """플래시스코어 오늘 경기 전체: (홈, 원정, 상태, 시작시각, 홈점수, 원정점수). 상태 3=종료.
    받는 데 10초쯤 걸려서 백그라운드(warm_cache)가 미리 받아 두고, 버튼은 저장된 값을 바로 쓴다."""
    import asyncio
    import time
    import flashscore
    if not refresh and _feed_cache[1] and time.time() - _feed_cache[0] < 180:
        return _feed_cache[1]

    async def one(sp):
        try:
            return [(g.get("AE", ""), g.get("AF", ""), g.get("AB"), int(g["AD"]), g.get("AG", ""), g.get("AH", ""))
                    for lg in await flashscore._leagues(sp, refresh=refresh) for g in lg["games"]]
        except Exception:
            return None

    got = await asyncio.gather(*(one(sp) for sp in ("football", "baseball", "basketball", "volleyball")))
    if all(x is None for x in got):
        return _feed_cache[1]  # 전부 실패하면 이전 값 유지
    games = [g for x in got if x for g in x]
    _feed_cache[0], _feed_cache[1] = time.time(), games
    return games


def _match_game(pair, games):
    h, a = pair
    for g in games:
        if (_same(h, g[0]) and _same(a, g[1])) or (_same(h, g[1]) and _same(a, g[0])):
            return g
    return None


_cat_cache = {}  # 종목 -> (시각, 결과)


async def get_category(cat, hours=30, refresh=False):
    """종목 글 전체 [{title, lines, kickoff}] — 최근 hours 시간 안에 올라온 것만, 경기 시간순.
    결과는 백그라운드가 1분마다 새로 만들어 두고, 버튼은 저장된 값을 바로 쓴다."""
    import time
    hit = _cat_cache.get(cat)
    if hit and not refresh and time.time() - hit[0] < 180:
        return hit[1]
    out = await _build_category(cat, hours)
    _cat_cache[cat] = (time.time(), out)
    return out


async def _build_category(cat, hours):
    import asyncio
    from datetime import datetime, timedelta, timezone
    import time
    if time.time() - _list_cache[0] > 60:
        _list_cache[1], _ = await get_posts(100, scan=100)
        _list_cache[0] = time.time()
    items = _list_cache[1]
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    def recent(i):
        try:
            return datetime.fromisoformat(i["created"].replace("Z", "+00:00")) >= since
        except Exception:
            return True  # 시각을 모르면 포함

    items = [i for i in items if categorize(i["title"]) == cat and recent(i)]
    games = await _feed_games()
    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst).date()

    def alive(i):
        """자정(한국시간) 기준 오늘 글만, 그리고 경기가 모두 끝난 글은 뺀다. 경기를 못 찾으면 올라온 날짜로 판단."""
        found = [_match_game(pr, games) for pr in _pairs(i["title"])]
        known = [g for g in found if g]
        if len(known) < len(found) or not known:
            try:
                made = datetime.fromisoformat(i["created"].replace("Z", "+00:00")).astimezone(kst).date()
            except Exception:
                return True
            return made == today
        return any(g[2] != "3" for g in known)

    items = [i for i in items if alive(i)]
    sem = asyncio.Semaphore(6)

    async def one(c, it):
        async with sem:
            lines, kick, _last = await _detail(c, it)
        if not kick:  # 글에 시간이 없으면 플래시스코어 시작시각 사용
            g = next((x for x in (_match_game(pr, games) for pr in _pairs(it["title"])) if x), None)
            if g:
                kick = datetime.fromtimestamp(g[3], kst).strftime("%m.%d %H:%M")
        return {"title": it["title"], "lines": lines, "kickoff": kick}

    async with httpx.AsyncClient(timeout=45, headers=HEADERS, follow_redirects=True) as c:
        out = await asyncio.gather(*(one(c, i) for i in items))
    return sorted(out, key=lambda x: (x["kickoff"] == "", x["kickoff"]))


async def warm_cache():
    """처음 누르면 느리므로, 백그라운드에서 경기 정보와 종목별 글을 1분마다 미리 받아 둔다."""
    import asyncio
    while True:
        try:
            await _feed_games(refresh=True)
        except Exception:
            pass
        for key, _, _ in CATEGORIES:
            try:
                await get_category(key, refresh=True)
            except Exception:
                pass
        await asyncio.sleep(60)
