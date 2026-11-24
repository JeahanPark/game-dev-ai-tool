"""뒷문 — 「주소 하나로 모든 프로젝트」의 길 안내(1.12.0 · 2026-11-24 사용자 「2번으로해줘」).

공개 길(Tailscale Funnel)이 넘겨 주는 이 PC 의 포트(앞문)는 켜져 있는 지휘자 가운데 하나만 쥘 수 있다. 그래서 공개 길이 켜져 있는
동안 지휘자마다 이 PC 안(127.0.0.1)에 문을 하나 더 열고(뒷문 — 번호는 OS 가 고른다), 앞문을 쥔 지휘자가 등록한 기기의 요청을
고른 프로젝트의 뒷문으로 그대로 넘긴다(gaf/server.py). 뒷문도 공개 문과 똑같이 가린다(들일 이름 · 등록한 기기 · PC 전용 길) —
포트 번호를 아는 것만으로는 아무것도 열리지 않는다.

이 모듈은 「어느 프로젝트의 뒷문이 몇 번인가 · 그 지휘자가 어느 앞문을 쥐었나」를 적는 파일만 다룬다. 접근 값 폴더(paths.run_dir)에
`door-<표>.json` 으로 둔다 — 세션은 그 폴더를 읽지 못한다(gaf/scope.py). 표는 프로젝트 폴더 경로의 해시 앞 12자다: 원격 기기에
폴더 경로를 주지 않고도 프로젝트를 가리킨다.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
from pathlib import Path

from .paths import _atomic_json, _read_json, run_dir

IDENT = re.compile(r"[0-9a-f]{12}")


def ident(root) -> str:
    """프로젝트의 표 — 폴더 경로에서만 나온다(다시 켜도 같다)."""
    return hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode("utf-8")).hexdigest()[:12]


def _file(value) -> Path | None:
    return run_dir() / f"door-{value}.json" if isinstance(value, str) and IDENT.fullmatch(value) else None


def born(pid) -> int | None:
    """그 프로세스가 뜬 시각(OS 가 주는 수 그대로 — 견주기만 한다) → 알 수 없으면 None.

    pid 는 다시 쓰인다: 죽은 지휘자의 문 파일에 적힌 pid 를 다른 프로그램이 받으면 「살아 있다」로 보인다. 뜬 시각을 같이 적어
    그런 파일을 죽은 것으로 가린다(Windows · /proc 이 있는 OS). 읽을 수 없는 OS 에서는 pid 만 본다."""
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
                return None
            return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        finally:
            kernel.CloseHandle(handle)
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        return int(text.rsplit(")", 1)[1].split()[19])          # 22번째 칸(starttime) — 이름에 빈칸 · 괄호가 있어도 맞게 센다
    except (OSError, ValueError, IndexError):
        return None


def _read(file: Path) -> tuple[dict | None, bool]:
    """문 파일 → (적힌 것, 적은 지휘자가 살아 있나). 읽지 못했으면 (None, False) — 「죽었다」와 가린다."""
    from .app import pid_alive                  # gaf.app 이 이 꾸러미의 다른 모듈을 쓰므로 여기서 늦게 가져온다

    record = _read_json(file)
    if record is None:
        return None, False
    pid = record.get("pid")
    alive = pid_alive(pid) and (record.get("born") is None or born(pid) == record["born"])
    return record, alive


def note(value: str, port: int, fronts=()) -> None:
    """이 지휘자의 뒷문 포트와 쥐고 있는 앞문 포트들을 적는다. 죽은 지휘자가 남긴 문 파일은 이때 치운다."""
    file = _file(value)
    if file is None:
        raise ValueError("프로젝트의 표가 아니다")
    file.parent.mkdir(parents=True, exist_ok=True)
    for stale in file.parent.glob("door-*.json"):
        if stale == file or not IDENT.fullmatch(stale.stem[5:]):
            continue
        record, alive = _read(stale)
        if record is not None and not alive:        # 읽지 못한 파일은 두고(쓰는 중 · 잠김일 수 있다) 죽은 것만 치운다
            with contextlib.suppress(OSError):
                stale.unlink()
    _atomic_json(file, {"pid": os.getpid(), "born": born(os.getpid()), "port": port, "fronts": sorted(fronts)})


def same(value: str, port: int, fronts=()) -> bool:
    """문 파일에 이 프로세스가 지금의 값 그대로 적혀 있나(아니면 문지기가 다시 적는다)."""
    file = _file(value)
    record = _read_json(file) if file is not None else None
    return (bool(record) and record.get("pid") == os.getpid() and record.get("port") == port
            and record.get("fronts") == sorted(fronts))


def clear(value: str) -> None:
    """이 프로세스가 적은 것이면 지운다(문을 닫을 때)."""
    file = _file(value)
    record = _read_json(file) if file is not None else None
    if record and record.get("pid") == os.getpid():
        with contextlib.suppress(OSError):
            file.unlink(missing_ok=True)


def stamp(value) -> tuple[int, int] | None:
    """문 파일의 (고친 시각, 크기) → 없으면 None. 넘기는 쪽이 「쥐고 있는 포트가 아직 그 파일의 것인가」를 읽지 않고 본다."""
    file = _file(value)
    try:
        status = file.stat() if file is not None else None
    except OSError:
        return None
    return (status.st_mtime_ns, status.st_size) if status is not None else None


def port_of(value) -> int:
    """그 표의 프로젝트가 떠 있고 뒷문을 열어 두었으면 그 포트 → 아니면 0."""
    file = _file(value)
    record, alive = _read(file) if file is not None else (None, False)
    port = record.get("port") if record and alive else 0
    return port if type(port) is int and 0 < port <= 65535 else 0


def front_held(port: int) -> bool:
    """그 앞문 포트를 살아 있는 지휘자가 쥐고 있나(다른 프로그램이 쓰는 것과 가린다)."""
    try:
        files = list(run_dir().glob("door-*.json"))
    except OSError:
        return False
    for file in files:
        record, alive = _read(file)
        fronts = record.get("fronts") if record and alive else None
        if isinstance(fronts, list) and port in fronts:
            return True
    return False
