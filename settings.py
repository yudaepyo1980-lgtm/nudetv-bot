"""봇 설정 저장소: 관리자 목록, 버튼 이름·링크·숨김, 변경 기록. 값은 userdata.db(SQLite)에 저장되어 재시작해도 남는다."""
import os
import sqlite3
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "userdata.db")


def _c():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, name TEXT, added_by INTEGER, ts TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS changes (ts TEXT, user_id INTEGER, name TEXT, what TEXT)")
    return c


def get(key, default=None):
    with _c() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_(key, value):
    with _c() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))


def delete(key):
    with _c() as c:
        c.execute("DELETE FROM settings WHERE key=?", (key,))


# ---- 관리자 ----
def owners():
    """.env 의 ADMIN_IDS: 소유자. 관리자 추가·삭제는 소유자만 할 수 있고, 목록에서 지울 수 없다."""
    return [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]


def extra_admins():
    with _c() as c:
        return [(r[0], r[1]) for r in c.execute("SELECT user_id, name FROM admins ORDER BY ts")]


def is_owner(uid):
    return uid in owners()


def is_admin(uid):
    return is_owner(uid) or any(uid == a for a, _ in extra_admins())


def add_admin(uid, name, by):
    with _c() as c:
        c.execute("INSERT OR REPLACE INTO admins VALUES (?, ?, ?, ?)", (uid, name, by, _now()))


def remove_admin(uid):
    with _c() as c:
        c.execute("DELETE FROM admins WHERE user_id=?", (uid,))


# ---- 변경 기록 ----
def _now():
    return datetime.now(KST).strftime("%m-%d %H:%M")


def log_change(uid, name, what):
    with _c() as c:
        c.execute("INSERT INTO changes VALUES (?, ?, ?, ?)", (_now(), uid, name, what))


def recent_changes(n=10):
    with _c() as c:
        return list(c.execute("SELECT ts, name, what FROM changes ORDER BY rowid DESC LIMIT ?", (n,)))


# ---- 메뉴 버튼 ----
def _support_url():
    u = os.getenv("SUPPORT_USERNAME", "").strip()
    return f"https://t.me/{u}" if u else ""


# kind: url(링크 버튼) / cb(눌렀을 때 봇이 반응하는 버튼)
BUTTONS = [
    {"key": "home", "label": "누드티비 바로가기", "kind": "url", "style": "success", "icon": "ICON_HOME",
     "url": lambda: os.getenv("SCORE_URL", "https://sports.daum.net/")},
    {"key": "official", "label": "누드티비 공식채널 바로가기", "kind": "url", "style": "success", "icon": "ICON_OFFICIAL", "url": lambda: ""},
    {"key": "picks", "label": "분석 및 추천픽", "kind": "cb", "style": "danger", "icon": "ICON_PICKS", "cb": "picks"},
    {"key": "events", "label": "이벤트", "kind": "cb", "style": "danger", "icon": "ICON_EVENTS", "cb": "events"},
    {"key": "score", "label": "오늘의 경기결과 스코어", "kind": "cb", "style": "danger", "icon": "ICON_SCORE", "cb": "analysis"},
    {"key": "fav", "label": "관심 팀 경기", "kind": "cb", "style": "primary", "icon": "ICON_FAV", "cb": "fav"},
    {"key": "support", "label": "1:1 문의", "kind": "url", "style": "primary", "icon": "ICON_SUPPORT", "url": _support_url},
]
BY_KEY = {b["key"]: b for b in BUTTONS}


def button_state(key):
    """현재 적용 값: (이름, 링크, 숨김 여부). 링크가 없는 링크 버튼은 숨겨진 것으로 본다."""
    b = BY_KEY[key]
    label = get(f"btn.{key}.label") or b["label"]
    url = get(f"btn.{key}.url") or (b["url"]() if b["kind"] == "url" else "")
    hidden = get(f"btn.{key}.hidden") == "1" or (b["kind"] == "url" and not url)
    return label, url, hidden


def reset_button(key):
    for f in ("label", "url", "hidden"):
        delete(f"btn.{key}.{f}")
