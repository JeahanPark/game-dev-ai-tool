"""사람이 믿은 프로젝트의 일 앞뒤 명령을 지휘자 권한으로 돌린다."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from . import paths

COMMAND_TIMEOUT = 60               # 명령 하나가 끝나기를 기다리는 시간(초)이다.


def digest(hooks: dict, checks=()) -> str:
    commands = {key: hooks.get(key, []) for key in ("before_work", "after_work")}
    if checks:
        commands["checks"] = checks
    if not any(commands.values()):
        return ""
    text = json.dumps(commands, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def trusted(root) -> str:
    """프로젝트의 믿은 지문이다. 없거나 기록이 깨졌으면 빈 글이다."""
    key = os.path.normcase(str(Path(root).resolve()))
    data = paths._read_json(paths.run_dir() / "hooks.json") or {}
    value = next((value for name, value in data.items() if os.path.normcase(name) == key), "")
    return value if isinstance(value, str) else ""


def trust(root, digest: str) -> None:
    """믿음을 프로젝트 밖에 적는다. 빈 지문이면 그 프로젝트의 기록을 지운다."""
    resolved = str(Path(root).resolve())
    key = os.path.normcase(resolved)
    target = paths.run_dir() / "hooks.json"
    with paths.file_lock(target):
        data = paths._read_json(target) or {}
        data = {name: value for name, value in data.items() if os.path.normcase(name) != key}
        if digest:
            data[resolved] = digest
        paths._atomic_json(target, data)


def run(commands, *, cwd, env, timeout=COMMAND_TIMEOUT, runner=subprocess.run) -> tuple[bool, str]:
    """명령을 차례로 돌린다. 실패하면 멈추고 까닭 한 줄만 돌려준다. 출력은 화면에 싣지 않는다."""
    for command in commands:
        head = command[:60]
        try:
            done = runner(command, shell=True, cwd=cwd, env=env, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return False, f"{head} → {timeout:g}초 안에 안 끝났다"
        except Exception as exc:  # noqa: BLE001 — 띄우다 난 오류(없는 셸 · NUL 이 든 명령 …)는 모두 실패 글로 돌려준다
            return False, f"{head} → {type(exc).__name__}"
        if done.returncode != 0:
            tail = next((line.strip()[:200] for line in reversed((done.stdout or "").splitlines()) if line.strip()), "")
            return False, f"{head} → 종료 {done.returncode}: {tail}"
    return True, ""
