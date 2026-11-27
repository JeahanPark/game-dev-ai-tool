"""REA 를 이 PC 에서 켰는지 — 사람이 PC 마다 한 번 정한다(기본은 꺼짐 · 게임마다 묻지 않는다).

접근 값 폴더(paths.run_dir — 세션이 읽지도 쓰지도 못한다)의 `rea.json` 에 켰는지 · 켠 판 · 바꾼 때를 적는다(research/consent.py 와 같은
잠금 · 바꿔치기 쓰기). git 으로 같이 받는 파일에는 적지 않는다 — 다른 사람의 PC 에서는 그 사람이 정한다. 파일이 없거나 깨졌으면 꺼진 것이다.
끄는 것은 깐 것을 지우지 않는다.
"""
from __future__ import annotations

import datetime
import os

from . import VERSION
from ..paths import _atomic_json, _read_json, file_lock, run_dir

FILE = "rea.json"


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def info() -> dict:
    """적힌 것 그대로(꼴이 맞을 때만) → {"enabled": bool, "version": str, "at": str}. 켠 적이 없거나 깨졌으면 {}."""
    data = _read_json(run_dir() / FILE)
    if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
        return {}
    return {"enabled": data["enabled"], "version": data.get("version") if isinstance(data.get("version"), str) else "",
            "at": data.get("at") if isinstance(data.get("at"), str) else ""}


def enabled() -> bool:
    return info().get("enabled") is True


def human_only(what: str) -> None:
    """세션이 띄운 프로세스(환경에 세션의 표가 있다 — 지휘자가 세션 · 배경 일 · 검사를 띄울 때 넣는다)면 PermissionError.

    REA 를 켜고 끄고 까는 것은 사람이 한다(화면의 승인 카드 · 사람의 터미널). 세션의 셸이 `python -c "…"` 로 이 꾸러미의 함수를 바로
    부르는 꼴은 셸의 문이 이름으로 다 막지 못한다(11-27 다른 눈의 검수) — 그래서 하는 쪽에서도 본다. 지휘자와 사람의 터미널에는 그
    표가 없다."""
    if os.environ.get("GAF_SESSION"):
        raise PermissionError(f"{what} 것은 사람이 한다(화면의 승인 카드 · 사람의 터미널) — 세션이 띄운 프로세스에서는 하지 않는다")


def _write(on: bool) -> dict:
    human_only("REA 를 이 PC 에서 켜고 끄는")
    target = run_dir() / FILE
    record = {"enabled": on, "version": VERSION, "at": _now()}
    with file_lock(target):
        _atomic_json(target, record)
    return record


def enable() -> dict:
    return _write(True)


def disable() -> dict:
    return _write(False)
