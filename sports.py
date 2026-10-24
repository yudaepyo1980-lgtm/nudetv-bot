"""네이버 스포츠 일정/스코어 API로 종목별 오늘 경기 스코어 문자열을 만든다."""
from datetime import datetime, timedelta, timezone

import os

import httpx

import channel

API = "https://api-gw.sports.naver.com/schedule/games"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.sports.naver.com/"}
KST = timezone(timedelta(hours=9))

SPORTS = {
    "football": ("⚽ 축구", ["kfootball", "wfootball"]),
    "baseball": ("⚾ 야구", ["kbaseball", "wbaseball"]),
    "basketball": ("🏀 농구", ["kbasketball", "wbasketball"]),
    "volleyball": ("🏐 배구", ["kvolleyball"]),
}
# 목록 맨 위에 올릴 리그
PRIORITY = ["K리그1", "K리그2", "KBO리그", "프로농구", "남자배구", "여자배구", "프리미어리그", "라리가",
            "분데스리가", "세리에A", "리그1", "챔피언스리그", "메이저리그", "NBA"]
# 채널 글에는 종목이 없어서 리그 이름으로 종목을 구분한다
LEAGUE_SPORT = {
    "football": ["K리그", "프리미어리그", "라리가", "분데스리가", "세리에", "리그1", "챔피언스리그", "유로파", "컨퍼런스", "에레디비시", "프리메이라", "A매치", "월드컵", "축구"],
    "baseball": ["KBO", "메이저리그", "MLB", "NPB", "프로야구", "야구"],
    "basketball": ["KBL", "WKBL", "NBA", "프로농구", "농구"],
    "volleyball": ["V리그", "남자배구", "여자배구", "배구"],
}
ALIAS = {"KBL": "프로농구", "V리그남자": "남자배구", "V리그여자": "여자배구"}
STATUS = {"RESULT": "종료", "STARTED": "🔴LIVE", "BEFORE": "", "CANCEL": "취소"}


async def _fetch(client, cat, day):
    r = await client.get(API, params={
        "fields": "basic,schedule", "upperCategoryId": cat,
        "fromDate": day, "toDate": day, "size": 500})
    return r.json().get("result", {}).get("games", [])


async def _next_games(sport, cats, days=7, max_lines=12):
    """오늘 경기가 없을 때, 가장 가까운 경기일의 예정 경기를 보여준다."""
    start = datetime.now(KST) + timedelta(days=1)
    frm, to = start.strftime("%Y-%m-%d"), (start + timedelta(days=days - 1)).strftime("%Y-%m-%d")
    games = []
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as c:
            for cat in cats:
                r = await c.get(API, params={"fields": "basic,schedule", "upperCategoryId": cat,
                                             "fromDate": frm, "toDate": to, "size": 500})
                games += r.json().get("result", {}).get("games", [])
    except Exception:
        return "\n\n예정 경기를 불러오지 못했습니다. 잠시 후 다시 눌러주세요."
    games = [g for g in games if g.get("homeTeamName") and g.get("awayTeamName") and not g.get("cancel")]
    if not games:
        return f"\n\n앞으로 {days}일 안에 예정된 경기도 없습니다. 시즌이 쉬는 기간일 수 있어요."
    first = min(g["gameDate"] for g in games)
    nxt = sorted((g for g in games if g["gameDate"] == first), key=lambda g: g["gameDateTime"])
    lines = [f"\n\n📅 다음 경기일 {first[5:].replace('-', '.')}"]
    for g in nxt[:max_lines]:
        lines.append(f"{g['gameDateTime'][11:16]} {g['homeTeamName']} vs {g['awayTeamName']} · {g.get('categoryName', '')}".rstrip(" ·"))
    if len(nxt) > max_lines:
        lines.append(f"… 외 {len(nxt) - max_lines}경기")
    lines.append("\n점수는 경기가 시작되면 이 버튼에서 확인할 수 있어요.")
    return "\n".join(lines)


async def scores_text(sport, limit_chars=3800):
    label, cats = SPORTS[sport]
    day = datetime.now(KST).strftime("%Y-%m-%d")
    games = []
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as c:
        for cat in cats:
            games += await _fetch(c, cat, day)
    games = [g for g in games if g.get("homeTeamName") and g.get("awayTeamName")]
    leagues = {}  # 리그 -> [(시간, 줄)]
    if os.getenv("SCORE_NAVER_BACKUP", "1") != "0":
        for g in games:
            if g.get("cancel"):
                st = "취소"
            else:
                st = STATUS.get(g.get("statusCode"), "")
            score = f"{g['homeTeamScore']} : {g['awayTeamScore']}" if g.get("statusCode") in ("RESULT", "STARTED") else "vs"
            line = f"{g['homeTeamName']} {score} {g['awayTeamName']} {st}".rstrip()
            leagues.setdefault(g.get("categoryName", "기타"), []).append((g["gameDateTime"][11:16], line))
    try:  # (선택) 텔레그램 채널 결과가 있는 리그는 채널 내용으로 덮어쓴다. SCORE_USE_CHANNEL=1 일 때만
        if os.getenv("SCORE_USE_CHANNEL", "0") != "1":
            raise StopIteration
        for lg, rows in (await channel.get_today_scores(day[5:].replace("-", "."))).items():
            if any(k in lg for k in LEAGUE_SPORT[sport]):
                norm = ALIAS.get(lg.replace(" ", ""), lg.replace(" ", ""))
                for old in [n for n in leagues if ALIAS.get(n.replace(" ", ""), n.replace(" ", "")) == norm]:
                    del leagues[old]  # 네이버의 같은 리그(이름만 다름)는 지움
                leagues[lg] = rows
    except (Exception, StopIteration):
        pass
    games = [r for rows in leagues.values() for r in rows]
    order = sorted(leagues, key=lambda n: (PRIORITY.index(n) if n in PRIORITY else 99, n))
    head = f"🦊 {label} 오늘 경기 ({day[5:].replace('-', '.')})"
    if not games:
        return head + "\n\n오늘 예정된 경기가 없습니다." + await _next_games(sport, cats)
    out, size, shown = [head], len(head), 0
    for name in order:
        block = [f"\n🦊 {name}"]
        for t, line in sorted(leagues[name]):
            block.append(f"{t} {line}")
        text = "\n".join(block)
        if size + len(text) > limit_chars:
            out.append(f"\n… 외 {len(games) - shown}경기")
            break
        out.append(text)
        size += len(text)
        shown += len(leagues[name])
    return "\n".join(out)
