"""관심 팀: 사용자별로 팀을 저장하고, 그 팀의 오늘/다음 경기를 네이버 스포츠 일정에서 찾는다."""
import asyncio
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import httpx

import sports

KST = timezone(timedelta(hours=9))
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "userdata.db")
MAX_TEAMS = 5
_cache = {"t": 0.0, "games": []}


def _conn():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS favs (user_id INTEGER, team TEXT, PRIMARY KEY (user_id, team))")
    return c


def list_teams(user_id):
    with _conn() as c:
        return [r[0] for r in c.execute("SELECT team FROM favs WHERE user_id=? ORDER BY rowid", (user_id,))]


def add_team(user_id, team):
    """저장 결과: 'ok' / 'dup' / 'full'"""
    if team in list_teams(user_id):
        return "dup"
    if len(list_teams(user_id)) >= MAX_TEAMS:
        return "full"
    with _conn() as c:
        c.execute("INSERT INTO favs VALUES (?, ?)", (user_id, team))
    return "ok"


def remove_team(user_id, team):
    with _conn() as c:
        c.execute("DELETE FROM favs WHERE user_id=? AND team=?", (user_id, team))


async def _games(days=7):
    """오늘부터 days일 뒤까지 모든 종목 경기 (2분 캐시)"""
    if time.time() - _cache["t"] < 120 and _cache["games"]:
        return _cache["games"]
    now = datetime.now(KST)
    frm, to = now.strftime("%Y-%m-%d"), (now + timedelta(days=days)).strftime("%Y-%m-%d")
    cats = sorted({c for _, cs in sports.SPORTS.values() for c in cs})
    async with httpx.AsyncClient(timeout=20, headers=sports.HEADERS) as c:
        res = await asyncio.gather(
            *[c.get(sports.API, params={"fields": "basic,schedule", "upperCategoryId": cat,
                                        "fromDate": frm, "toDate": to, "size": 500}) for cat in cats],
            return_exceptions=True)
    games, ok = [], 0
    for r in res:
        if isinstance(r, Exception):
            continue
        ok += 1
        games += r.json().get("result", {}).get("games", [])
    if not ok:
        raise RuntimeError("네이버 일정 조회 실패")
    games = [g for g in games if g.get("homeTeamName") and g.get("awayTeamName") and not g.get("cancel")]
    games.sort(key=lambda g: g["gameDateTime"])
    _cache.update(t=time.time(), games=games)
    return games


def _match(g, kw):
    return kw.lower() in f"{g['homeTeamName']} {g['awayTeamName']}".lower()


async def exists(keyword):
    """7일 안에 이 이름이 들어간 팀의 경기가 있는지"""
    return any(_match(g, keyword) for g in await _games())


def _line(g):
    st = sports.STATUS.get(g.get("statusCode"), "")
    live = g.get("statusCode") in ("RESULT", "STARTED")
    score = f"{g['homeTeamScore']} : {g['awayTeamScore']}" if live else "vs"
    return f"{g['gameDateTime'][11:16]} {g['homeTeamName']} {score} {g['awayTeamName']} {st}".rstrip()


async def summary(teams):
    """관심 팀별 오늘 경기, 없으면 다음 경기"""
    games = await _games()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    out = []
    for kw in teams:
        mine = [g for g in games if _match(g, kw)]
        todays = [g for g in mine if g["gameDate"] == today]
        out.append(f"\n🦊 {kw}")
        if todays:
            out += [_line(g) for g in todays]
            continue
        later = [g for g in mine if g["gameDate"] > today]
        if later:
            n = later[0]
            out.append(f"오늘 경기 없음 · 다음 {n['gameDate'][5:].replace('-', '.')} {_line(n)}")
        else:
            out.append("앞으로 7일 안에 예정된 경기가 없어요.")
    return "\n".join(out)
