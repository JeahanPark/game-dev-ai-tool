"""출처의 허락 — 조사 낱말을 바깥으로 보내도 되는지는 사람이 이 PC 에서 프로젝트마다 정한다(기본은 모두 꺼짐).

접근 값 폴더(paths.run_dir — 세션이 읽지도 쓰지도 못한다)의 `research.json` 에 프로젝트 뿌리마다 켠 출처와 켠 때를 적는다.
git 으로 같이 받는 파일에는 적지 않는다 — 다른 사람의 PC 에서는 그 사람이 정한다. 파일이 없거나 깨졌으면 아무것도 켜지 않은 것이다.
같은 계정의 프로세스를 막는 장치는 아니다(설계 §7 의 한계 그대로) — 정상 세션이 사람 모르게 바깥으로 보내지 못하게 하는 문이다.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

from ..paths import _atomic_json, _read_json, file_lock, run_dir
from .plan import SOURCES

FILE = "research.json"


def _root_key(root) -> str:
    return os.path.normcase(str(Path(root).resolve()))


def _mine(data, key: str) -> dict:
    row = next((value for name, value in data.items() if os.path.normcase(name) == key), {}) if isinstance(data, dict) else {}
    return {name: when for name, when in row.items() if name in SOURCES and isinstance(when, str)} if isinstance(row, dict) else {}


def allowed(root) -> list[str]:
    """이 PC 가 그 프로젝트에 켠 출처(정해진 차례로)."""
    mine = _mine(_read_json(run_dir() / FILE), _root_key(root))
    return [name for name in SOURCES if name in mine]


def _change(root, add=(), drop=()) -> list[str]:
    unknown = [name for name in (*add, *drop) if name not in SOURCES]
    if unknown:
        raise ValueError(f"모르는 출처다: {' · '.join(map(str, unknown))}(쓸 수 있는 것: {' · '.join(SOURCES)})")
    resolved = str(Path(root).resolve())
    key = os.path.normcase(resolved)
    target = run_dir() / FILE
    with file_lock(target):
        data = _read_json(target)
        data = data if isinstance(data, dict) else {}
        mine = _mine(data, key)
        now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        for name in add:
            mine.setdefault(name, now)
        for name in drop:
            mine.pop(name, None)
        data = {name: value for name, value in data.items() if os.path.normcase(name) != key}
        if mine:
            data[resolved] = mine
        _atomic_json(target, data)
    return [name for name in SOURCES if name in mine]


def allow(root, sources) -> list[str]:
    return _change(root, add=tuple(sources))


def deny(root, sources) -> list[str]:
    return _change(root, drop=tuple(sources))
