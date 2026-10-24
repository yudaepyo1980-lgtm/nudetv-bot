"""오늘의 추천픽 기록: 하루 5경기를 한 번 정해 저장해 두고, 경기가 끝나면 적중/미적중을 판정해 누적 적중률을 보여준다.
날짜는 한국시간 자정 기준. 기록은 today_picks.json 에 쌓인다."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import posts

FILE = Path(__file__).with_name("today_picks.json")
KST = timezone(timedelta(hours=9))
COUNT = 5


def _today():
    return datetime.now(KST).strftime("%Y-%m-%d")


def _load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data):
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _clean_token(tok):
    """'무승부@3.00' → '무승부' (배당 표기 제거)"""
    return re.sub(r"\s*@\s*\d+(?:\.\d+)?\s*$", "", tok.strip())


def item_results(pick, home, away, hs, as_, cat):
    """픽의 항목별 판정 [(항목, True/False/None)]. None 은 판정할 수 없는 항목.
    home/away: 글에 적힌 팀 이름 순서, hs/as_: 그 팀들의 점수."""
    out = []
    for raw in re.split(r"\s*/\s*|\s*,\s*", pick):
        raw = raw.strip()
        if not raw:
            continue
        tok = _clean_token(raw)
        m = re.search(r"(\d+(?:\.\d+)?)?\s*(오버|언더|over|under)", tok, re.I)
        if m:
            line = float(m.group(1)) if m.group(1) else (2.5 if cat in ("ovfb", "dofb") else None)
            if line is None:
                out.append((raw, None))
                continue
            over = m.group(2).lower() in ("오버", "over")
            total = hs + as_
            out.append((raw, total > line if over else total < line))
        elif tok in ("무", "무승부") or tok.endswith("무"):
            out.append((raw, hs == as_))
        elif tok.endswith("승"):
            name = tok[:-1].strip()
            if posts._same(name, home):
                out.append((raw, hs > as_))
            elif posts._same(name, away):
                out.append((raw, as_ > hs))
            else:
                out.append((raw, None))
        else:
            out.append((raw, None))
    return out


def judge(pick, home, away, hs, as_, cat):
    """True=적중, False=미적중, None=판정 불가. 판정할 수 있는 항목이 모두 맞아야 적중."""
    res = [v for _, v in item_results(pick, home, away, hs, as_, cat) if v is not None]
    return all(res) if res else None


def pick_line(r):
    """'🎯 픽: 항목 ✅ / 항목 ❌' — 경기가 끝나 판정된 경우에만 항목별 표시를 붙인다."""
    if r["result"] is None or ":" not in r.get("score", ""):
        return f"🎯 픽: {r['pick']}"
    hs, as_ = (int(x) for x in r["score"].split(":"))
    items = item_results(r["pick"], r["home"], r["away"], hs, as_, r["key"])
    mark = lambda v: "✅" if v else ("❌" if v is False else "")
    return "🎯 픽: " + " / ".join(f"{t} {mark(v)}".rstrip() for t, v in items) if items else f"🎯 픽: {r['pick']}"


ALL_LIMIT = 10  # 화제 경기가 하나도 없는 날은 올라온 경기를 이 개수까지 모두 보여준다


def _entry(sc, key, it, pick, pairs):
    league, match = _split(it["title"])
    return {"title": it["title"], "key": key, "league": league, "match": match, "pick": pick, "sc": sc,
            "kickoff": it.get("kickoff", ""), "home": pairs[0][0], "away": pairs[0][1], "result": None, "score": ""}


async def ensure_snapshot(cands):
    """cands: [(점수, 종목키, 글, 픽)] 점수순. 점수가 높은(화제) 경기로 5경기를 채운다.
    화제 경기가 모자라면 그날 올라온 다른 경기로 채우고, 하나도 없는 날은 올라온 경기를 모두 보여준다(최대 ALL_LIMIT).
    나중에 화제 경기가 올라오면 아직 끝나지 않은 '채움' 경기를 그 경기로 바꾼다."""
    data = _load()
    day = _today()
    rec = data.setdefault(day, [])
    has_star = any(c[0] > 0 for c in cands) or any(r.get("sc", 1) > 0 for r in rec)
    limit = COUNT if has_star else ALL_LIMIT
    singles = [(sc, key, it, pick, posts._pairs(it["title"])) for sc, key, it, pick in cands]
    singles = [c for c in singles if len(c[4]) == 1]
    have = {r["title"] for r in rec}
    for sc, key, it, pick, pairs in singles:  # 1) 빈 자리 채우기 (점수 높은 순)
        if len(rec) >= limit:
            break
        if it["title"] not in have:
            rec.append(_entry(sc, key, it, pick, pairs))
            have.add(it["title"])
    for sc, key, it, pick, pairs in singles:  # 2) 화제 경기가 새로 생겼으면 채움 경기와 교체
        if sc <= 0 or it["title"] in have:
            continue
        fill = [r for r in rec if r.get("sc", 1) <= 0 and r["result"] is None]
        if not fill:
            break
        rec[rec.index(fill[-1])] = _entry(sc, key, it, pick, pairs)
        have.add(it["title"])
    if has_star:  # 3) 화제 경기가 있는 날은 5경기로 맞춤 (끝나지 않은 채움 경기부터 뺌)
        while len(rec) > COUNT:
            fill = [r for r in rec if r.get("sc", 1) <= 0 and r["result"] is None]
            if not fill:
                break
            rec.remove(fill[-1])
    _save(data)
    return rec


def _split(title):
    m = re.match(r"\s*\[([^\]]*)\]\s*(.*)", title)
    league, rest = (m.group(1), m.group(2)) if m else ("", title)
    league = re.sub(r"\s*\d+경기|\s*(프리뷰|핵심 정리|선발 매치업|분석)$", "", league).strip()
    return league, posts._SUFFIX.sub("", rest).strip()


async def update_results():
    """끝난 경기를 찾아 판정해 저장한다. (끝난 경기는 한 번 판정하면 다시 바꾸지 않음)"""
    data = _load()
    rec = data.get(_today(), [])
    todo = [r for r in rec if r["result"] is None]
    if not todo:
        return
    games = await posts._feed_games()
    for r in todo:
        g = posts._match_game((r["home"], r["away"]), games)
        if not g:
            r["state"] = "unknown"  # 오늘 경기 목록에서 못 찾음 (다른 날 경기이거나 이름이 달라 못 찾은 경우)
            continue
        scored = len(g) >= 6 and g[4] != "" and g[5] != ""
        if scored:
            swapped = posts._same(r["home"], g[1]) and not posts._same(r["home"], g[0])
            hs, as_ = (int(g[5]), int(g[4])) if swapped else (int(g[4]), int(g[5]))
        if g[2] == "3" and scored:
            res = judge(r["pick"], r["home"], r["away"], hs, as_, r["key"])
            r["score"] = f"{hs}:{as_}"
            r["result"] = "none" if res is None else ("hit" if res else "miss")
            r.pop("state", None)
            r.pop("live", None)
        elif g[2] == "2":
            r["state"] = "live"
            r["live"] = f"{hs}:{as_}" if scored else ""
        else:
            r["state"] = "pre"
    _save(data)


def stats():
    """누적 (적중, 판정된 경기 수) 와 오늘 (적중, 판정된 경기 수)."""
    data, day = _load(), _today()
    tot = [0, 0]
    today = [0, 0]
    for d, rec in data.items():
        for r in rec:
            if r["result"] in ("hit", "miss"):
                tot[1] += 1
                tot[0] += r["result"] == "hit"
                if d == day:
                    today[1] += 1
                    today[0] += r["result"] == "hit"
    return tot, today


def status_label(r, live_keys=()):
    if r["result"] == "hit":
        return f"✅ 적중 ({r['score']})"
    if r["result"] == "miss":
        return f"❌ 미적중 ({r['score']})"
    if r["result"] == "none":
        return f"🏁 종료 ({r['score']})"
    st = r.get("state")
    if st == "live":
        return "🔴 진행 중" + (f" ({r['live']})" if r.get("live") else "")
    if st == "pre":
        return "⏳ 경기 전"
    if st == "unknown":
        return "⏳ 경기 시간 확인 중"
    return ""


def summary_line(rec):
    """📊 오늘 …: 판정된 경기와 아직 끝나지 않은 경기를 나눠서 보여준다."""
    hit = sum(r["result"] == "hit" for r in rec)
    miss = sum(r["result"] == "miss" for r in rec)
    pending = sum(r["result"] is None for r in rec)
    if not hit and not miss and not pending:
        return ""
    if pending:
        return f"📊 오늘 적중 {hit} · 미적중 {miss} · 진행/대기 {pending}"
    return f"📊 오늘 {hit}/{hit + miss} 적중"


def render(rec):
    icons = {"ovfb": "⚽", "dofb": "🇰🇷", "bask": "🏀", "base": "⚾", "hock": "🏒", "voll": "🏐", "esp": "🎮"}
    blocks = []
    for r in rec:
        head = " · ".join(x for x in (r["kickoff"] and f"⏰ {r['kickoff']}", f"{icons.get(r['key'], '')} {r['league']}".strip()) if x)
        b = f"{head}\n🦊 {r['match']}\n{pick_line(r)}"
        lab = status_label(r)
        if lab:
            b += f"\n{lab}"
        blocks.append(b)
    text = "\n\n".join(blocks)
    tot, _ = stats()
    line = summary_line(rec)
    if line:
        text += f"\n\n{line}"
    if tot[1]:
        text += f"\n📈 누적 {tot[0]}/{tot[1]} ({round(tot[0] * 100 / tot[1])}%)"
    return text
