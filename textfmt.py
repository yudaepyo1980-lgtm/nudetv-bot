"""글자 모양 꾸미기: 제목, 경기 이름, 픽 내용을 굵게 만드는 글자 꾸밈(MessageEntity) 목록을 만든다.
텔레그램은 글꼴 자체는 바꿀 수 없고 굵게·기울임·인용 같은 꾸밈만 지원한다. 오프셋은 UTF-16 기준."""
from telegram import MessageEntity as E

_SECTION = ("🔹", "📌", "✍️", "📝", "⭐", "🎁", "📢", "🔍")


def u16(s):
    return len(s.encode("utf-16-le")) // 2


def _line_offsets(lines):
    offs, pos = [], 0
    for ln in lines:
        offs.append(pos)
        pos += u16(ln) + 1
    return offs


def format_entities(text):
    """봇 화면 글(🦊 제목 🦊, 추천픽 블록)에 굵은 글씨를 입힌다."""
    ents = []
    lines = text.split("\\n")
    offs = _line_offsets(lines)
    first = lines[0] if lines else ""
    if first.startswith("🦊 ") and first.count("🦊") >= 2:
        end = first.rindex("🦊") + 1
        ents.append(E(E.BOLD, 0, u16(first[:end])))
    i, n = 0, len(lines)
    while i < n:
        j = i
        while j < n and lines[j].strip():
            j += 1
        block = range(i, j)
        if any(lines[k].startswith("🎯 픽:") for k in block):
            fox = next((k for k in block if lines[k].startswith("🦊 ")), None)
            if fox is not None:
                ents.append(E(E.BOLD, offs[fox] + u16("🦊 "), u16(lines[fox]) - u16("🦊 ")))
            for k in block:
                if lines[k].startswith("🎯 픽:"):
                    rest = lines[k][len("🎯 픽:"):]
                    lead = len(rest) - len(rest.lstrip())
                    if rest.strip():
                        ents.append(E(E.BOLD, offs[k] + u16("🎯 픽:") + u16(rest[:lead]), u16(rest.strip())))
        i = j + 1
    return ents


def event_entities(text):
    """이벤트 공지글: 제목(첫 줄)과 🔹📌 같은 소제목 줄을 굵게"""
    ents = []
    lines = text.split("\\n")
    offs = _line_offsets(lines)
    if lines and lines[0].strip():
        ents.append(E(E.BOLD, 0, u16(lines[0].rstrip())))
    for k, ln in enumerate(lines[1:], 1):
        if ln.startswith(_SECTION) and len(ln) <= 40:
            ents.append(E(E.BOLD, offs[k], u16(ln.rstrip())))
    return ents
