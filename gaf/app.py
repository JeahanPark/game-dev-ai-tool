"""기존 부품을 묶고, 떠 있는 지휘자를 찾는다."""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config, load_config
from .conductor import Conductor
from .engines.fake import FakeEngine
from .paths import Paths, _atomic_json, _read_json, run_dir
from .store import Store


@dataclass
class App:
    paths: Paths
    config: Config
    store: Store
    conductor: Conductor


def build(root: Path, *, engines: dict | None = None, clock=time.time,
          sleep=asyncio.sleep, tick_seconds=60.0) -> App:
    paths = Paths(Path(root).resolve())
    config = load_config(paths)
    store = Store(paths, clock=clock)
    if engines is None:
        from .engines.claude import ClaudeEngine, ClaudeEngineOptions

        from .engines.codex import CodexEngine, CodexEngineOptions

        engines = {
            "claude": ClaudeEngine(ClaudeEngineOptions(
                setting_sources=config.claude["setting_sources"],
                system_prompt=config.claude["system_prompt"])),
            # Codex SDK 는 세션을 열 때 불러온다 — 안 깔려 있으면 그 엔진을 쓰는 역할에만 「쓸 수 없다」가 뜬다.
            "codex": CodexEngine(CodexEngineOptions(codex_bin=config.codex.get("bin") or None)),
            "fake": FakeEngine(),
        }
    conductor = Conductor(paths, config, store, engines, clock=clock,
                          sleep=sleep, tick_seconds=tick_seconds)
    return App(paths, config, store, conductor)


SH_SHIM = '#!/bin/sh\nexec "${GAF_PYTHON:-python}" -X utf8 "$(dirname "$0")/gaf_cli.py" "$@"\n'
# `%*` 를 괄호 묶음(if ( … ) else ( … )) 안에 두지 않는다 — 인자에 든 `)` 가 묶음을 닫아 그 뒤의 인자가 명령으로 돈다.
CMD_SHIM = ('@echo off\r\nsetlocal\r\nif "%GAF_PYTHON%"=="" set "GAF_PYTHON=python"\r\n'
            '"%GAF_PYTHON%" -X utf8 "%~dp0gaf_cli.py" %*\r\n')
# `SendMessage <받는 이> "<글>"` = `gaf send <받는 이> "<글>"` — 세션끼리 글을 보내는 명령(Claude Code 의 내장 도구와 같은 이름 ·
# 2026-11-12 사용자 「외부적으로는 똑같이 SendMessage」). 엔진이 무엇이든 셸에서 같은 꼴로 친다.
SEND_SH_SHIM = SH_SHIM.replace('gaf_cli.py" "$@"', 'gaf_cli.py" send "$@"')
SEND_CMD_SHIM = CMD_SHIM.replace('gaf_cli.py" %*', 'gaf_cli.py" send %*')


def install_commands(app: App, port: int) -> Path:
    """세션이 셸에서 `gaf …` 를 칠 수 있게 한다(MCP 도구 대신 쓰는 길 — 2026-11-12 사용자 결정).

    .gaf/bin 에 실행 파일(gaf · gaf.cmd · SendMessage · SendMessage.cmd · gaf_cli.py · gaf_hook.py)을 쓰고, 세션에 줄 환경변수(GAF_PORT · GAF_PYTHON ·
    GAF_HOOK · PATH)를 지휘자에 넣는다. 세션마다 다른 GAF_ROLE · GAF_SESSION 은 지휘자가 세션을 열 때 더한다.
    gaf_hook.py 는 문을 콜백으로 못 받는 엔진(Codex)의 PreToolUse 훅이 부른다 — 지휘자에게 「이 도구를 써도 되나」를 묻는다(gaf/hook.py).
    """
    folder = app.paths.state_dir / "bin"
    folder.mkdir(parents=True, exist_ok=True)
    package_parent = Path(__file__).resolve().parent.parent
    launcher = ("import sys\n"
                f"sys.path.insert(0, {str(package_parent)!r})\n"
                "from gaf.cli import main\n"
                "raise SystemExit(main())\n")
    hook = launcher.replace("from gaf.cli import main", "from gaf.hook import main")
    for name, text, encoding in (("gaf_cli.py", launcher, "utf-8"), ("gaf_hook.py", hook, "utf-8"),
                                 ("gaf", SH_SHIM, "ascii"), ("gaf.cmd", CMD_SHIM, "ascii"),
                                 ("SendMessage", SEND_SH_SHIM, "ascii"), ("SendMessage.cmd", SEND_CMD_SHIM, "ascii")):
        target = folder / name
        data = text.encode(encoding)
        if not target.is_file() or target.read_bytes() != data:      # 같은 내용이면 다시 쓰지 않는다
            target.write_bytes(data)
    if os.name != "nt":
        for name in ("gaf", "SendMessage"):
            (folder / name).chmod(0o755)
    app.conductor.session_env = {
        "GAF_PORT": str(port),
        "GAF_PYTHON": Path(sys.executable).as_posix(),
        "GAF_HOOK": (folder / "gaf_hook.py").as_posix(),
        "PATH": str(folder) + os.pathsep + os.environ.get("PATH", ""),
    }
    return folder


def pid_alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        if pid > 0xFFFFFFFF:
            return False
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return True


_RUN_ID = re.compile(r"[0-9a-f]{32}")


def _key_file(run_id) -> Path | None:
    return run_dir() / f"{run_id}.json" if isinstance(run_id, str) and _RUN_ID.fullmatch(run_id) else None


def write_server(paths: Paths, *, port: int, key: str, url: str, started_at: str) -> None:
    """떠 있는 지휘자를 적는다. 프로젝트 안(.gaf/server.json)에는 pid · 포트 · 이름표만 두고, 접근 값(key · 그 값이 든 url)은
    프로젝트 밖의 폴더(paths.run_dir)에 둔다 — 뿌리를 뒤지는 도구가 접근 값을 읽어 오지 않게(11-12 QA 결함-21).
    """
    folder = run_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        with contextlib.suppress(OSError):
            folder.chmod(0o700)
    for stale in folder.glob("*.json"):              # 죽은 지휘자가 남긴 파일을 치운다
        if _RUN_ID.fullmatch(stale.stem) and not pid_alive((_read_json(stale) or {}).get("pid")):
            with contextlib.suppress(OSError):
                stale.unlink()
    run_id = secrets.token_hex(16)
    _atomic_json(folder / f"{run_id}.json", {"pid": os.getpid(), "key": key, "url": url, "root": str(paths.root)})
    _atomic_json(paths.server, {"pid": os.getpid(), "port": port, "run": run_id, "started_at": started_at})


def clear_server(paths: Paths) -> None:
    """이 프로세스가 적은 것이면 지운다(끌 때)."""
    record = _read_json(paths.server)
    if record and record.get("pid") == os.getpid():
        secret = _key_file(record.get("run"))
        if secret is not None:
            with contextlib.suppress(OSError):
                secret.unlink(missing_ok=True)
        paths.server.unlink(missing_ok=True)


def read_server(paths: Paths) -> dict | None:
    """떠 있는 지휘자(pid · port · key · url) → 없으면 None. 접근 값 파일을 못 읽으면 key · url 이 없다."""
    record = _read_json(paths.server)
    if not record or not pid_alive(record.get("pid")):
        return None
    secret_file = _key_file(record.get("run"))
    secret = _read_json(secret_file) if secret_file is not None else None
    if secret and secret.get("pid") == record.get("pid"):
        record = {**record, "key": secret.get("key"), "url": secret.get("url")}
    return record
