"""엔진과 읽기 전용 캐시에서 한도 관찰을 고른다."""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass
class Limit:
    engine: str
    window: str
    pct: float | None
    status: str
    resets_at: int | None
    observed_at: float | None
    source: str
    raw_status: str | None = None


def _number(value) -> float | None:
    if type(value) not in (int, float):
        return None
    return float(value) if math.isfinite(value) else None


def from_event(data: dict, now: float, engine: str = "claude") -> Limit:
    window = data.get("window") or "seven_day"
    windows = data.get("windows") or {}
    detail = windows.get(window, {}) if isinstance(windows, dict) else {}
    if not isinstance(detail, dict):
        detail = {}
    pct = _number(data.get("pct"))
    if pct is None:
        pct = _number(detail.get("pct"))
    raw_status = data.get("status") or detail.get("status")
    resets_at = data.get("resets_at")
    if resets_at is None:
        resets_at = detail.get("resets_at")
    resets_at = int(resets_at) if _number(resets_at) is not None else None
    status = "stop" if raw_status == "rejected" else "ok" if pct is not None else "unknown"
    return Limit(engine, window, pct, status, resets_at, now, "rate_event", raw_status)


def read_oauth_cache(path: Path | None = None, now: float | None = None) -> Limit | None:
    path = Path(path) if path is not None else Path.home() / ".claude/context-guard/state/_oauth_usage.json"
    try:
        with path.open("r", encoding="utf-8") as stream:
            observed_at = os.fstat(stream.fileno()).st_mtime
            raw = json.load(stream)
        rate = raw["rate"]["seven_day"]
        if not isinstance(rate, dict):
            return None
        data = {**rate, "window": "seven_day"}
        limit = from_event(data, observed_at)
        return replace(limit, source="oauth_cache")
    except (OSError, ValueError, TypeError, KeyError):
        return None


def pick(a: Limit | None, b: Limit | None, now: float, stale: float = 900) -> Limit:
    candidates = [limit for limit in (a, b) if limit is not None]
    if not candidates:
        return Limit("claude", "seven_day", None, "unknown", None, None, "unknown")
    newest = max(candidates, key=lambda limit: float("-inf") if limit.observed_at is None else limit.observed_at)
    if newest.observed_at is None or now - newest.observed_at > stale:
        return replace(newest, status="unknown")
    return replace(newest)


def judge(limit: Limit, hold: float, stop: float) -> str:
    if limit.status == "unknown":
        return "unknown"
    if limit.raw_status == "rejected":
        return "stop"
    if limit.pct is None:
        return "unknown"
    if limit.pct >= stop:
        return "stop"
    if limit.pct >= hold:
        return "hold"
    return "ok"


def free_ram_mb() -> int | None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD),
                        *[(name, ctypes.c_ulonglong) for name in
                          ("total_phys", "avail_phys", "total_page", "avail_page", "total_virtual",
                           "avail_virtual", "avail_extended")]]

        try:
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.avail_phys // (1024 * 1024))
        except (OSError, AttributeError):
            pass
    elif sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
        except (OSError, ValueError, IndexError):
            pass
    return None
