"""한 번에 켜기 — 지휘자를 배경에서 띄우고, 준비되면 화면을 연다(11-13 사용자 「실행시키는 방법」).

`python -m gaf serve` 는 터미널을 쥐고 돌고 터미널을 닫으면 꺼진다. 여기서는 같은 명령을 창 없이, 이 터미널과 떨어뜨려 띄운다.
주소(접근 값이 들어 있다)를 찍는 표준 출력은 버리고, 오류만 프로젝트의 .gaf/serve.log 에 받는다 — 주소는 접근 값 폴더의
기록(app.read_server)에서 읽는다.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from .app import read_server
from . import desktop
from .envclean import SESSION_ENV
from .paths import Paths, run_dir

READY_SECONDS = 30                 # 뜨기를 기다리는 시간(초)
LOG = "serve.log"
BREAKAWAY = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)


def _python() -> str:
    """지휘자를 띄울 파이썬. 창 없는 pythonw(바탕화면 아이콘)로 불렸어도 곁의 python 을 쓴다.

    pythonw 로 뜬 지휘자에는 콘솔이 없다(아래의 창 없는 콘솔은 콘솔 프로그램만 받는다) — 그러면 세션의 엔진(claude.exe 등)이
    뜰 때마다 Windows 가 빈 콘솔 창을 새로 띄운다(11-23 사용자 「아이콘을 켜서 … 갑자기 클로드 CMD 창이 켜졌어」 · 1.5.4).
    """
    called = Path(sys.executable)
    if os.name == "nt" and called.name.lower().startswith("pythonw"):
        console = called.with_name("python" + called.name[len("pythonw"):])
        if console.is_file():
            return str(console)
    return sys.executable


def spawn(root, *, popen=subprocess.Popen):
    """`python -m gaf serve --root <폴더>` 를 창 없이 띄운다 → 띄운 프로세스."""
    root = Path(root).resolve()
    state = Paths(root).state_dir
    state.mkdir(parents=True, exist_ok=True)
    command = [_python(), "-X", "utf8", "-m", "gaf", "serve", "--root", str(root)]
    # 세션 안에서 띄워도 사람이 띄운 지휘자여야 한다 — 세션에 준 값은 물려주지 않는다.
    options = dict(cwd=str(Path(__file__).resolve().parent.parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   env={key: value for key, value in os.environ.items() if key not in SESSION_ENV}, close_fds=True)

    def run(**extra):
        with (state / LOG).open("wb") as log:          # 띄운 쪽은 제 손잡이를 따로 쥔다 — 여기 것은 바로 닫는다
            return popen(command, stderr=log, **options, **extra)

    if os.name != "nt":
        return run(start_new_session=True)
    # 숨은 콘솔 하나를 따로 가진다(창 없음 · 이 터미널을 닫아도 산다 · 세션이 띄우는 프로그램도 창을 띄우지 않는다).
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # 터미널 프로그램이 제 자식을 한 묶음(Job)으로 쥐고 닫을 때 같이 끄는 곳이 있다 — 될 때는 그 묶음에서 나온다.
        return run(creationflags=flags | BREAKAWAY)
    except OSError:
        return run(creationflags=flags)


def probe(record) -> bool:
    """떠 있는 지휘자가 화면의 물음에 답하나(뜨는 중이면 503 이다)."""
    request = urllib.request.Request(f"http://127.0.0.1:{record.get('port')}/api/state",
                                     headers={"X-GAF-Key": str(record.get("key") or "")})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # 로컬 요청에 PC의 프록시 설정을 물려주지 않는다
    try:
        with opener.open(request, timeout=5) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def _log_tail(paths: Paths) -> str:
    try:
        text = (paths.state_dir / LOG).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text[-800:]


def start(root, *, popen=subprocess.Popen, probe=probe, sleep=time.sleep, clock=time.monotonic,
          timeout: float = READY_SECONDS) -> tuple[dict | None, str, bool]:
    """떠 있으면 그 기록을, 꺼져 있으면 띄워서 준비된 기록을 준다 → (기록, 오류 글, 이번에 띄웠나)."""
    paths = Paths(Path(root).resolve())
    record = read_server(paths)
    if record is not None:
        if record.get("url"):
            return record, "", False
        return None, f"떠 있는 지휘자(pid {record.get('pid')})의 접근 값 파일을 읽지 못했다 — 지휘자를 띄운 계정으로 한다", False
    if not paths.config.is_file():
        return None, f"gaf 프로젝트가 아니다: {paths.root} — 먼저 python -m gaf init --root <폴더>", False
    try:
        process = spawn(paths.root, popen=popen)
    except OSError as error:
        return None, f"지휘자를 띄우지 못했다 — {error}", False
    deadline = clock() + timeout
    while True:
        record = read_server(paths)
        if record is not None and record.get("url") and probe(record):
            return record, "", True
        code = process.poll()
        if code is not None and record is None:
            tail = _log_tail(paths)
            return None, f"지휘자가 뜨지 못했다(종료 {code})" + (f" — {tail}" if tail else ""), False
        if clock() >= deadline:
            return None, f"{timeout:g}초 안에 뜨지 않았다 — 기록을 본다: {paths.state_dir / LOG}", True
        sleep(0.1)


def open_project(root, *, browser=webbrowser.open, app=False, app_opener=None, popen=subprocess.Popen) -> int:
    """프로젝트를 켠다. app은 프로그램 창을 먼저 열고, browser=None이면 화면을 열지 않는다."""
    record, error, started = start(root, popen=popen)
    if record is None:
        if sys.stderr is None:
            desktop.message_box("Game Dev AI Tool", error)
        else:
            print(error, file=sys.stderr)
        return 1
    if sys.stdout is not None:
        # 기존 명령의 출력은 그대로 둔다. 프로그램 창으로 켤 때에는 접근 값 주소를 찍지 않는다.
        print(("켰다: " if started else "이미 떠 있다: ") + (str(Path(root).resolve()) if app else str(record["url"])), flush=True)
        print(f"지휘자: 떠 있다(pid {record['pid']} · 포트 {record['port']}) — 상태 python -m gaf status · 끄기 python -m gaf shutdown", flush=True)
    if browser is not None:
        if app:
            with contextlib.suppress(Exception):
                if (app_opener or desktop.open_app)(record["url"], profile=run_dir() / "app-profile"):
                    return 0
        with contextlib.suppress(Exception):
            browser(record["url"])
    return 0
