"""지휘자를 띄울 때 환경변수를 정리한다(설계 §6.1).

Agent SDK 는 부모의 환경을 자식 세션에 거의 그대로 넘긴다. Claude Code 세션 안에서 지휘자를 띄우면
그 세션의 변수(CLAUDE_CODE_* 등)가 따라 들어가고, ANTHROPIC_API_KEY 가 남아 있으면 구독이 아니라
API 요금으로 나간다. 그래서 띄우기 전에 지운다. 값은 어디에도 찍지 않는다 — 이름만 돌려준다.
"""
from __future__ import annotations

import os
from typing import Iterable, MutableMapping

EXACT = ("CLAUDECODE", "CLAUDE_EFFORT", "CLAUDE_PID", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "ELECTRON_RUN_AS_NODE",
         "AI_AGENT", "MCP_CONNECTION_NONBLOCKING", "GIT_ASKPASS")
PREFIXES = ("CLAUDE_CODE_", "VSCODE_", "ANTHROPIC_")
WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "WEBHOOK")
KEEP = ("CLAUDE_CONFIG_DIR",)
SESSION_ENV = ("GAF_SESSION", "GAF_ROLE", "GAF_PORT", "GAF_PYTHON", "GAF_HOOK")


def should_remove(name: str, keep: Iterable[str] = ()) -> bool:
    upper = name.upper()
    if upper in {item.upper() for item in (*KEEP, *keep)}:
        return False
    return upper in EXACT or upper.startswith(PREFIXES) or any(word in upper for word in WORDS)


def clean_env(environ: MutableMapping[str, str] | None = None, *, pass_env: Iterable[str] = ()) -> list[str]:
    """지울 이름을 environ(없으면 os.environ)에서 지우고, 지운 이름 목록(정렬)을 돌려준다."""
    target = os.environ if environ is None else environ
    keep = tuple(pass_env)
    removed = sorted(name for name in list(target) if should_remove(name, keep))
    for name in removed:
        del target[name]
    return removed
