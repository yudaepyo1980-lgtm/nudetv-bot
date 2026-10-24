"""실행 중인 봇의 데이터(DB·기록 파일)를 안전하게 복사한다. 사용: python deploy/gh_save.py <대상폴더>"""
import os
import shutil
import sqlite3
import sys

DST = sys.argv[1]
os.makedirs(DST, exist_ok=True)
for name in ("userdata.db", "chat.db"):
    if os.path.exists(name):  # 쓰는 중에도 일관되게 복사하려고 SQLite 백업 기능 사용
        src, dst = sqlite3.connect(name), sqlite3.connect(os.path.join(DST, name))
        src.backup(dst)
        dst.close()
        src.close()
for name in ("today_picks.json", "notice.json", "picks.json"):
    if os.path.exists(name):
        shutil.copy2(name, os.path.join(DST, name))
