"""이 PC에서 연 프로젝트 목록 — 프로젝트 밖(접근 값 폴더 · paths.run_dir)의 projects.json 에 적는다.

프로젝트 폴더는 건드리지 않는다. 접근 값 파일(`<32 hex>.json`)은 열지 않는다. PC마다 다른 것(이 PC에서 정해 둔 지휘자 포트 ·
원격으로 여는 포트)도 여기에 둔다 — gaf.json 은 팀이 git 으로 같이 쓰는 파일이라 PC의 사정을 적지 않는다.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .paths import Paths, _atomic_json, _read_json, file_lock, run_dir

FILE = "projects.json"
KEEP = 50                       # 적어 두는 프로젝트 수의 한도(오래 안 연 것부터 버린다)


def _resolved(root) -> str:
    return str(Path(root).resolve())


def _same(root) -> str:
    return os.path.normcase(_resolved(root))


def _file() -> Path:
    return run_dir() / FILE


def _entry(raw) -> dict | None:
    """파일의 한 항목을 바른 모양으로 고친다 → 뿌리가 없거나 틀리면 None."""
    if not isinstance(raw, dict):
        return None
    root = raw.get("root")
    if not isinstance(root, str) or not root:
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        name = Path(root).name or root
    port = raw.get("port")
    if type(port) is not int or not 0 <= port <= 65535:
        port = 0
    serve = raw.get("serve")
    if type(serve) is not int or not 0 <= serve <= 65535:
        serve = 0
    public_port = raw.get("public_port")
    if type(public_port) is not int or not 0 <= public_port <= 65535:
        public_port = 0
    opened = raw.get("opened_at")
    return {"root": root, "name": name, "opened_at": opened if isinstance(opened, str) else "", "port": port,
            "serve": serve, "https": raw.get("https") is True,
            "tailscale": raw.get("tailscale") is True and serve != 0,
            "public": raw.get("public") is True and serve != 0, "public_port": public_port}


def entries() -> list[dict]:
    """적어 둔 프로젝트 — 최근에 연 것 먼저. 파일이 없거나 깨졌으면 빈 목록."""
    data = _read_json(_file())
    items = data.get("projects") if data else None
    if not isinstance(items, list):
        return []
    return [entry for entry in map(_entry, items) if entry is not None]


def _write(rows: list[dict]) -> None:
    _atomic_json(_file(), {"v": 1, "projects": rows[:KEEP]})


def remember(root, name: str, *, clock: Callable[[], float] = time.time) -> None:
    """프로젝트를 목록 맨 앞에 적는다. 같은 뿌리는 하나만 두고 이름 · 시각을 고친다(port 는 그대로).

    못 써도 오류를 올리지 않는다 — 목록을 못 고친다고 지휘자가 못 뜨면 안 된다. 읽고-고치고-쓰는 동안 파일을 잠근다(다른
    지휘자가 같은 때 적어도 서로의 것을 지우지 않게 — 11-13 QA 결함-31).
    """
    try:
        key = _same(root)
        opened = datetime.fromtimestamp(clock()).astimezone().isoformat(timespec="seconds")
        with file_lock(_file()):
            _remember(root, name, key, opened)
    except (OSError, ValueError, OverflowError):
        pass


def _remember(root, name, key, opened) -> None:
    rows = entries()
    old = next((row for row in rows if _same(row["root"]) == key), {})
    rows = [row for row in rows if _same(row["root"]) != key]
    rows.insert(0, {"root": _resolved(root), "name": str(name), "opened_at": opened, "port": old.get("port", 0),
                    "serve": old.get("serve", 0), "https": old.get("https", False),
                    "tailscale": old.get("tailscale", False), "public": old.get("public", False),
                    "public_port": old.get("public_port", 0)})
    _write(rows)


def forget(root) -> bool:
    """목록에서 뺀다 → 있었으면 True. 프로젝트 폴더는 건드리지 않는다."""
    key = _same(root)
    with file_lock(_file()):
        rows = entries()
        kept = [row for row in rows if _same(row["root"]) != key]
        if len(kept) == len(rows):
            return False
        _write(kept)
    return True


def port_of(root) -> int:
    """이 PC에서 그 프로젝트에 정해 둔 지휘자 포트(없으면 0)."""
    key = _same(root)
    return next((row["port"] for row in entries() if _same(row["root"]) == key), 0)


def set_port(root, port: int) -> None:
    """정해 둔 포트를 적는다(0 = 정해 두지 않음). 목록에 없는 프로젝트이거나 0~65535 정수가 아니면 ValueError."""
    _set_port(root, port, "port")


def public_port_of(root) -> int:
    key = _same(root)
    return next((row["public_port"] for row in entries() if _same(row["root"]) == key), 0)


def set_public_port(root, port: int) -> None:
    """공개 문이 이 PC에서 듣는 포트(0 = 안 정함)."""
    _set_port(root, port, "public_port")


def _set_port(root, port, field):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError(f"포트는 0~65535 사이의 정수여야 한다: {port!r}")
    key = _same(root)
    with file_lock(_file()):
        rows = entries()
        for row in rows:
            if _same(row["root"]) == key:
                row[field] = port
                _write(rows)
                return
    raise ValueError(f"목록에 없는 프로젝트다: {root}")


def serve_of(root) -> tuple[int, bool]:
    """이 프로젝트를 원격으로 여는 포트와 https 여부(안 열면 (0, False)) — 사설망 앞단이 그 포트를 지휘자 포트로 넘긴다."""
    key = _same(root)
    return next(((row["serve"], row["https"]) for row in entries() if _same(row["root"]) == key), (0, False))


def serve_by_tailscale(root) -> bool:
    """그 넘김을 이 프레임워크가 Tailscale 에 걸었는가(`gaf remote on`). `--host` 로 이름만 적은 것(다른 앞단 — 사람이 건다)은
    False 다. 끌 때 우리가 건 것만 끈다 — 걸지 않은 넘김에 `tailscale serve … off` 를 보내지 않는다(11-13)."""
    key = _same(root)
    return next((row["tailscale"] for row in entries() if _same(row["root"]) == key), False)


def serve_public(root) -> bool:
    key = _same(root)
    return next((row["public"] for row in entries() if _same(row["root"]) == key), False)


def set_serve(root, serve: int, https: bool = False, *, tailscale: bool = False, public: bool = False) -> None:
    """원격으로 여는 포트를 적는다(0 = 열지 않음). tailscale = 그 넘김을 우리가 Tailscale 에 걸었다.
    목록에 없는 프로젝트이거나 0~65535 정수가 아니면 ValueError."""
    if type(serve) is not int or not 0 <= serve <= 65535:
        raise ValueError(f"포트는 0~65535 사이의 정수여야 한다: {serve!r}")
    key = _same(root)
    with file_lock(_file()):
        rows = entries()
        for row in rows:
            if _same(row["root"]) == key:
                row["serve"], row["https"] = serve, bool(https) and serve != 0
                row["tailscale"] = bool(tailscale) and serve != 0
                row["public"] = bool(public) and serve != 0
                _write(rows)
                return
    raise ValueError(f"목록에 없는 프로젝트다: {root}")


def _usable(root: str) -> bool:
    folder = Path(root)
    return folder.is_dir() and (folder / "gaf.json").is_file()


def last() -> Path | None:
    """가장 최근에 연 것 가운데 폴더와 gaf.json 이 아직 있는 프로젝트의 뿌리."""
    for row in entries():
        if _usable(row["root"]):
            return Path(row["root"])
    return None


def overview(entry: dict, *, alive: Callable[[int], bool] | None = None) -> dict:
    """프로젝트 하나의 지금 모습 — 파일만 읽는다(HTTP · 접근 값 파일 없음)."""
    if alive is None:
        from .app import pid_alive as alive           # gaf.app 쪽이 이 모듈을 쓰므로 여기서 늦게 가져온다
    root = str(entry.get("root", ""))
    paths = Paths(Path(root))
    exists = _usable(root)
    server = _read_json(paths.server) if exists else None
    running = bool(server) and bool(alive(server.get("pid")))
    live_port = server.get("port") if running else None
    state = (_read_json(paths.state) if exists else None) or {}
    decisions = state.get("decisions")
    roles = state.get("roles")
    decisions = len(decisions) if isinstance(decisions, list) else 0
    statuses = [role.get("status") for role in roles if isinstance(role, dict)] if isinstance(roles, list) else []
    working = sum(1 for status in statuses if status in ("working", "waiting"))
    blocked = sum(1 for status in statuses if status == "blocked")
    title = state.get("project")
    name = title if isinstance(title, str) and title else entry.get("name", "")
    if not exists:
        status = "missing"
    elif not running:
        status = "off"
    elif decisions:
        status = "needs_input"
    elif blocked:
        status = "blocked"
    elif working:
        status = "working"
    else:
        status = "idle"
    return {"name": name, "root": root, "port": entry.get("port", 0), "serve": entry.get("serve", 0),
            "https": entry.get("https", False), "public": entry.get("public", False), "exists": exists, "running": running,
            "live_port": live_port, "decisions": decisions, "working": working, "blocked": blocked,
            "status": status}


def listing(*, alive: Callable[[int], bool] | None = None) -> list[dict]:
    return [overview(entry, alive=alive) for entry in entries()]


_WORDS = {"missing": "폴더 없음", "off": "꺼짐", "needs_input": "내 확인 필요", "blocked": "막힘",
          "working": "일하는 중", "idle": "쉼"}


def table(rows: list[dict]) -> str:
    """사람이 보는 표(탭 구분)."""
    if not rows:
        return "적어 둔 프로젝트가 없다"
    lines = ["프로젝트\t상태\t정할 것\t일하는 역할\t폴더"]
    for row in rows:
        lines.append(f"{row['name']}\t{_WORDS.get(row['status'], row['status'])}\t{row['decisions']}\t"
                     f"{row['working']}\t{row['root']}")
    return "\n".join(lines)
