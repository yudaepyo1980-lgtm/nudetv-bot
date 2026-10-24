"""봇이 두 개 동시에 돌지 않게 하는 잠금. 이미 실행 중이면 안내하고 종료한다."""
import fcntl
import os
import sys

_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot.lock")
_handle = None  # 프로세스가 끝날 때까지 열어 둬야 잠금이 유지됨


def acquire():
    global _handle
    _handle = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("봇이 이미 실행 중입니다. 다시 켜려면 ./restart.sh 를 사용하세요.", file=sys.stderr)
        sys.exit(0)
    _handle.write(str(os.getpid()))
    _handle.flush()
