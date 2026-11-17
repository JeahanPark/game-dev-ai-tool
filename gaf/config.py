"""선택 설정을 기본값과 합치고 형식을 확인한다."""
from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .paths import Paths

JOB_COMMAND_MAX = 8000

DEFAULTS = {
    "project": Path.cwd().name,
    "lead": {"role": "총괄"},
    "defaults": {"engine": "claude", "model": "sonnet", "effort": "medium", "permission": "default"},
    # 한도 기준은 엔진마다 따로 본다(<엔진>_hold_pct 에서 새 일을 보류 · <엔진>_stop_pct 에서 멈춤).
    # max_hops = 사람의 말 없이 세션끼리 글이 건너갈 수 있는 횟수. 0 = 한도 없음(11-13 사용자 「빼줘」 — 그 전 기본값은 8).
    "limits": {"claude_hold_pct": 95, "claude_stop_pct": 100, "codex_hold_pct": 95, "codex_stop_pct": 100,
               "min_free_mb": 2048, "max_parallel": 2, "max_hops": 0, "turn_timeout_min": 60},
    # compact_above_pct = 문맥 창의 압축 기준(%) · fresh_above_tokens = 토큰 수로도 기준을 둘 때 쓰는 값(0 이면 쓰지 않는다).
    "session": {"warm_minutes": 50, "compact_above_pct": 50, "fresh_above_tokens": 0, "linger_seconds": 60, "gather_seconds": 2},
    # 세션(총괄도)이 셸로 하지 않는 것. 명령 글 그대로와, git 을 부르는 곳을 `git <하위 명령> …` 꼴로 고친 글
    # (scope.git_calls)에 다 맞춰 본다. 하위 명령을 넓힌 것은 11-12 QA 의견(커밋을 만드는 것 · 안 한 커밋을 날리는 것).
    "bash_deny": [
        # 커밋을 만들거나 내보내는 것 · 이력을 고치는 것 — 사람이 한다
        r"\bgit\s+(commit|push|merge|pull|cherry-pick|revert|am|rebase|filter-branch|filter-repo)(?![-\w])",
        # 커밋하지 않은 일을 버리거나 감추는 것 · 가지를 바꾸는 것 — 같은 폴더에서 다른 역할 · 사람이 일한다
        r"\bgit\s+(clean|restore|checkout|switch|stash(?!\s+(list|show)\b))(?![-\w])",
        r"\bgit\s+reset\b[^;&|\n]{0,200}\s(--(hard|soft|merge|keep)\b|(HEAD|@)[~^@{]|ORIG_HEAD\b)",
        r"\bgit\s+branch\b[^;&|\n]{0,200}\s(-[a-z]*[df][a-z]*\b|--delete\b|--force\b)",
    ],
    # setting_sources 를 비워 둔다 = 설정 파일 · CLAUDE.md 를 세션에 싣지 않는다(11-12 사용자 결정 — 규칙은 team/공통.md · 역할.md 로만).
    "claude": {"setting_sources": [], "system_prompt": "preset", "pass_env": []},
    # bin = Codex CLI 실행 파일 경로(비면 PATH 의 codex 에서 찾는다) · model = 역할 글에 model 이 없는 Codex 역할이 쓸 모델(비면 Codex 기본)
    "codex": {"bin": "", "model": ""},
    "hooks": {"before_work": [], "after_work": []},
    "checks": [],
    "server": {"port": 0},
    "tools": [],
}


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    project: str
    lead_role: str
    defaults: dict
    limits: dict
    session: dict
    bash_deny: list[str]
    claude: dict
    server: dict
    tools: list
    raw: dict
    codex: dict = field(default_factory=dict)
    hooks: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)


def validate_checks(value) -> list[dict]:
    if not isinstance(value, list) or len(value) > 8:
        raise ConfigError("checks: 기본 검사는 8개 이하의 목록이어야 한다")
    result, names = [], set()
    for index, item in enumerate(value):
        key = f"checks[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{key}: 객체여야 한다")
        name = item.get("name")
        if (not isinstance(name, str) or not name.strip() or not 1 <= len(name) <= 40
                or any(c in name for c in "\r\n") or any(ord(c) < 32 for c in name)):
            raise ConfigError(f"{key}.name: 줄바꿈 없는 1~40자의 이름이어야 한다")
        if name in names:
            raise ConfigError(f"{key}.name: 프로젝트 안에서 이름이 겹친다")
        command = item.get("command")
        if not isinstance(command, str) or not command.strip() or len(command) > JOB_COMMAND_MAX:
            raise ConfigError(f"{key}.command: 빈 글이 아닌 {JOB_COMMAND_MAX}자 이내의 명령이어야 한다")
        when = item.get("when", [])
        if not isinstance(when, list) or not all(isinstance(p, str) and p.strip() for p in when):
            raise ConfigError(f"{key}.when: 빈 글이 없는 글롭 목록이어야 한다")
        shell = item.get("shell", "")
        if shell not in ("", "bash", "powershell"):
            raise ConfigError(f"{key}.shell: 빈 글 · bash · powershell 이어야 한다")
        timeout = item.get("timeout_min", 10)
        if type(timeout) not in (int, float) or not 0 < timeout <= 120:
            raise ConfigError(f"{key}.timeout_min: 0보다 크고 120 이하의 수여야 한다")
        names.add(name)
        result.append(dict(name=name, command=command, when=list(when), shell=shell, timeout_min=timeout))
    return result


def _validate(value, sample, key: str) -> None:
    if isinstance(sample, dict):
        if not isinstance(value, dict):
            raise ConfigError(f"{key or 'gaf.json'}: 키와 값이 있는 객체여야 한다")
        for name, default in sample.items():
            if name in value:
                _validate(value[name], default, f"{key}.{name}".lstrip("."))
    elif isinstance(sample, list):
        if not isinstance(value, list):
            raise ConfigError(f"{key}: 목록이어야 한다")
        if key == "checks":
            validate_checks(value)
        if key not in ("tools", "checks") and not all(isinstance(item, str) for item in value):
            raise ConfigError(f"{key}: 글 목록이어야 한다")
        if key in ("hooks.before_work", "hooks.after_work") and any(not item.strip() for item in value):
            raise ConfigError(f"{key}: 빈 명령이 있다")
        if key == "tools":
            for index, tool in enumerate(value):
                if not isinstance(tool, dict):
                    raise ConfigError(f"tools[{index}]: 객체여야 한다")
                for field in ("name", "makes", "how", "terms_checked", "ledger"):
                    if field in tool and not isinstance(tool[field], str):
                        raise ConfigError(f"tools[{index}].{field}: 글이어야 한다")
                if "on" in tool and type(tool["on"]) is not bool:
                    raise ConfigError(f"tools[{index}].on: true 또는 false여야 한다")
    elif isinstance(sample, str):
        if not isinstance(value, str):
            raise ConfigError(f"{key}: 글이어야 한다")
    elif key == "session.compact_above_pct":
        if type(value) not in (int, float) or not 0 < value <= 100:
            raise ConfigError("session.compact_above_pct: 0 보다 크고 100 이하여야 한다")
    elif key == "limits.turn_timeout_min":
        # 11-12 QA 결함-06: 1분 미만이면 턴이 바로 끝날 수 있어 설정을 읽을 때 막는다.
        if type(value) not in (int, float) or not math.isfinite(value) or value < 1:
            raise ConfigError(f"{key}: 1 이상의 유한한 수여야 한다")
    elif type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ConfigError(f"{key}: 0 이상의 유한한 수여야 한다")
    elif key in ("limits.max_parallel", "limits.max_hops", "session.fresh_above_tokens", "server.port"):
        if type(value) is not int or (key == "limits.max_parallel" and value < 1):
            raise ConfigError(f"{key}: 알맞은 정수여야 한다")
        if key == "server.port" and value > 65535:
            raise ConfigError("server.port: 0~65535여야 한다")


def load_config(paths: Paths) -> Config:
    raw = copy.deepcopy(DEFAULTS)
    raw["project"] = paths.root.name
    if paths.config.exists():
        try:
            # 11-12 QA 결함-05: 메모장 등이 붙이는 UTF-8 BOM도 건너뛰고 읽는다.
            supplied = json.loads(paths.config.read_text(encoding="utf-8-sig"))
        # 11-12 QA 결함-07: 너무 깊은 JSON도 깨진 JSON과 같은 설정 오류로 알린다.
        except (OSError, ValueError, RecursionError) as exc:
            raise ConfigError(f"gaf.json: 설정 JSON을 읽을 수 없다: {exc}") from exc
        _validate(supplied, DEFAULTS, "")
        for key, value in supplied.items():
            if isinstance(raw.get(key), dict):
                raw[key].update(value)
            else:
                raw[key] = value
    for index, pattern in enumerate(raw["bash_deny"]):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"bash_deny[{index}]: 정규식이 잘못됐다: {exc}") from exc
    return Config(raw["project"], raw["lead"]["role"], raw["defaults"], raw["limits"],
                  raw["session"], raw["bash_deny"], raw["claude"], raw["server"], raw["tools"], raw,
                  codex=raw["codex"], hooks=raw["hooks"], checks=validate_checks(raw["checks"]))


def change_check(paths: Paths, kind: str, check: dict) -> tuple[Config, bool]:
    """사람이 승인한 값만 반영한다. 쓰기 직전 다시 읽고 checks 외의 키는 보존한다."""
    from . import hooks
    from .paths import file_lock, _atomic_json, _os_lock
    with file_lock(paths.state_dir / "gaf.json"):
        # 옛 잠금을 쥔 프로그램이 있으면 그 파일은 남겨 둔다.
        legacy = paths.config.with_name(paths.config.name + ".lock")
        try:
            with legacy.open("r+b") as handle:
                _os_lock(handle, True)
                _os_lock(handle, False)
            legacy.unlink()
        except OSError:
            pass
        try:
            supplied = json.loads(paths.config.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, RecursionError) as exc:
            raise ConfigError("gaf.json: 설정 JSON을 읽을 수 없어 기본 검사를 바꾸지 않았다") from exc
        _validate(supplied, DEFAULTS, "")
        # 위에서 읽은 바로 그 디스크 내용으로 믿음도 판단한다.
        old_checks = validate_checks(supplied.get("checks", []))
        old_hooks = supplied.get("hooks", {})
        fingerprint = hooks.digest(old_hooks, old_checks)
        was_trusted = not fingerprint or hooks.trusted(paths.root) == fingerprint
        if kind == "check_add":
            updated = validate_checks([*old_checks, check])
        else:
            if "command" in check and next((row for row in old_checks if row["name"] == check["name"]), None) != check:
                raise ConfigError("checks: 청한 뒤 기본 검사 내용이 바뀌었다 — 다시 청한다")
            updated = [row for row in old_checks if row["name"] != check["name"]]
            if len(updated) == len(old_checks):
                raise ConfigError("checks.name: 없는 기본 검사다")
        supplied["checks"] = updated
        _atomic_json(paths.config, supplied)
        if was_trusted:
            hooks.trust(paths.root, hooks.digest(old_hooks, updated))
        return load_config(paths), was_trusted
