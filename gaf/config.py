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
    # 기본 검토(11-20 사용자 「음 이해했어 그럼 적용해」): 맞는 파일이 바뀌면 지휘자가 늘 돌리는 검토 — 총괄(AI)이 붙일지 정하지 않는다.
    "reviews": [],
    "server": {"port": 0},
    "tools": [],
    # 프로젝트 밖 폴더의 이름 선언(1.14.0 — 이름 → 설명 · 경로는 적지 않는다): 역할 글의 write 에 `@이름/글롭` 으로 쓰고, 진짜 폴더는 PC 마다
    # 사람이 고른다(gaf/outside.py). 선언은 권한이 아니다 — 짝이 없으면 아무것도 열리지 않는다.
    "folders": {},
    # 이 프로젝트가 바라는 가장 낮은 프로그램 판(1.14.0 부터 본다 · 빈 글 = 따지지 않는다). 옛 판은 이 키를 모른다 — 새 표기를 쓰는 프로젝트는
    # 쓰는 PC 가 모두 그 판이 된 뒤에 옮겨 적는다.
    "min_version": "",
}
FOLDERS_SINCE = "1.14.0"                 # 이름을 선언하는 프로젝트의 가장 낮은 판 — 그 앞의 판은 `@이름` 을 프로젝트 안의 폴더로 읽는다
_VERSION = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}")


class ConfigError(ValueError):
    pass


class VersionError(ConfigError):
    """이 프로젝트가 이 프로그램보다 높은 판을 바란다(min_version) — 깨진 설정과 달리 「고칠 때까지 앞의 값으로 돈다」가 아니다."""


class _Twice(dict):
    """같은 키를 두 번 적은 JSON 객체 — 뒤의 값만 남지만 무엇이 겹쳤는지 쥐고 있다(folders 의 이름이 조용히 사라지지 않게 본다)."""
    twice: tuple = ()


def _pairs(items):
    keys = [key for key, _ in items]
    twice = tuple(dict.fromkeys(key for key in keys if keys.count(key) > 1))
    if not twice:
        return dict(items)
    found = _Twice(items)
    found.twice = twice
    return found


def _read(paths: Paths) -> dict:
    """gaf.json 을 읽는다(메모장 등이 붙이는 UTF-8 BOM 도 건너뛴다 — 11-12 QA 결함-05). 같은 키를 두 번 적은 객체는 그것을 쥐고 있다."""
    return json.loads(paths.config.read_text(encoding="utf-8-sig"), object_pairs_hook=_pairs)


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
    reviews: list = field(default_factory=list)
    folders: dict = field(default_factory=dict)      # 프로젝트 밖 폴더의 이름 → 설명(1.14.0)


def _line_name(value) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and 1 <= len(value) <= 40
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


REVIEW_KEYS = ("name", "when", "reviewers", "files", "brief", "small", "keep_going")
REVIEW_SMALL = ("area", "lines", "chars")
REVIEW_FILES_MAX = 12


def validate_reviews(value) -> list[dict]:
    """기본 검토 규칙을 확인하고 빠진 칸을 기본값으로 채운다. 역할이 있는지는 지휘자가 돌릴 때 본다(설정은 역할을 모른다)."""
    if not isinstance(value, list) or len(value) > 8:
        raise ConfigError("reviews: 기본 검토는 8개 이하의 목록이어야 한다")
    result, names = [], set()
    for index, item in enumerate(value):
        key = f"reviews[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{key}: 객체여야 한다")
        for name in item:
            if name not in REVIEW_KEYS:
                raise ConfigError(f"{key}.{name}: 모르는 칸이다({' · '.join(REVIEW_KEYS)})")
        name = item.get("name")
        if not _line_name(name):
            raise ConfigError(f"{key}.name: 줄바꿈 없는 1~40자의 이름이어야 한다")
        if name in names:
            raise ConfigError(f"{key}.name: 프로젝트 안에서 이름이 겹친다")
        when = item.get("when")
        if (not isinstance(when, list) or not 1 <= len(when) <= 20
                or not all(isinstance(p, str) and p.strip() for p in when)):
            raise ConfigError(f"{key}.when: 바뀐 파일을 가릴 글롭 1~20개의 목록이어야 한다")
        reviewers = item.get("reviewers")
        if (not isinstance(reviewers, list) or not 1 <= len(reviewers) <= 3
                or not all(_line_name(r) for r in reviewers) or len(set(reviewers)) != len(reviewers)):
            raise ConfigError(f"{key}.reviewers: 겹치지 않는 검토 역할 이름 1~3개의 목록이어야 한다")
        files = item.get("files", [])
        if not isinstance(files, list) or len(files) > REVIEW_FILES_MAX:
            raise ConfigError(f"{key}.files: 검토가 볼 경로 · 글롭 {REVIEW_FILES_MAX}개 이하의 목록이어야 한다")
        for pattern in files:
            path = pattern.replace("\\", "/") if isinstance(pattern, str) else ""
            if (not path.strip() or path.startswith(("/", "~")) or ":" in path or ".." in path.split("/")
                    or any(ord(c) < 32 for c in path)):
                raise ConfigError(f"{key}.files: 프로젝트 뿌리 아래의 상대 경로 · 글롭이어야 한다: {pattern!r}")
        brief = item.get("brief", "")
        if not isinstance(brief, str) or len(brief) > 4000:
            raise ConfigError(f"{key}.brief: 4000자 이내의 글이어야 한다")
        small = item.get("small", {})
        if not isinstance(small, dict):
            raise ConfigError(f"{key}.small: 작음의 기준(area · lines · chars)을 적은 객체여야 한다")
        for part, number in small.items():
            if part not in REVIEW_SMALL:
                raise ConfigError(f"{key}.small.{part}: 모르는 칸이다(area · lines · chars)")
            if part == "area":
                if type(number) not in (int, float) or not math.isfinite(number) or not 0 < number <= 100:
                    raise ConfigError(f"{key}.small.area: 0보다 크고 100 이하의 수(바뀐 넓이 %)여야 한다")
            elif type(number) is not int or number < 1:
                raise ConfigError(f"{key}.small.{part}: 1 이상의 정수여야 한다")
        keep_going = item.get("keep_going", True)
        if type(keep_going) is not bool:
            raise ConfigError(f"{key}.keep_going: true 또는 false 여야 한다")
        names.add(name)
        result.append(dict(name=name, when=list(when), reviewers=list(reviewers), files=list(files), brief=brief,
                           small={part: small[part] for part in REVIEW_SMALL if part in small}, keep_going=keep_going))
    return result


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


def validate_folders(value) -> dict:
    """프로젝트 밖 폴더의 이름 선언(이름 → 설명)을 본다 → 그대로의 사전. 이름 규칙 · 대소문자와 정규화만 다른 이름 · 설명의 꼴 · 수."""
    from . import outside

    if not isinstance(value, dict):
        raise ConfigError("folders: 키와 값이 있는 객체여야 한다(이름 → 설명)")
    if getattr(value, "twice", ()):
        raise ConfigError(f"folders: 같은 이름을 두 번 적었다: {', '.join(str(name) for name in value.twice)}")
    if len(value) > outside.NAMES_MAX:
        raise ConfigError(f"folders: 이름은 {outside.NAMES_MAX}개까지다")
    seen: dict[str, str] = {}
    for name, about in value.items():
        if outside.name_error(name):
            raise ConfigError(f"folders: {outside.NAME_RULE}(받은 것: {name!r})")
        key = outside.name_key(name)
        if key in seen:
            raise ConfigError(f"folders: 같은 이름이 둘이다(대소문자나 풀어 쓴 글자만 다르다): {seen[key]} · {name}")
        seen[key] = name
        if not isinstance(about, str) or len(about) > outside.ABOUT_MAX or any(ord(char) < 32 or ord(char) == 127 for char in about):
            raise ConfigError(f"folders.{name}: 설명은 한 줄 글이어야 한다({outside.ABOUT_MAX}자까지)")
    return dict(value)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def check_min_version(value) -> None:
    """이 프로그램이 그 프로젝트가 바라는 판보다 낮으면 열지 않는다(ConfigError) — 새 표기를 옛 뜻으로 읽고 도는 것을 막는다."""
    if value == "":
        return
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ConfigError("min_version: 판 번호 꼴(예 1.14.0)이어야 한다")
    import gaf

    current = str(gaf.__version__)
    if _VERSION.fullmatch(current) and _version(value) > _version(current):
        raise VersionError(f"min_version: 이 프로젝트는 프로그램 {value} 이상이 필요하다(지금 {current}) — 프로그램을 새 판으로 올린다")


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
        if key == "reviews":
            validate_reviews(value)
        if key not in ("tools", "checks", "reviews") and not all(isinstance(item, str) for item in value):
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
            supplied = _read(paths)
        # 11-12 QA 결함-07: 너무 깊은 JSON도 깨진 JSON과 같은 설정 오류로 알린다.
        except (OSError, ValueError, RecursionError) as exc:
            raise ConfigError(f"gaf.json: 설정 JSON을 읽을 수 없다: {exc}") from exc
        _validate(supplied, DEFAULTS, "")
        validate_folders(supplied.get("folders", {}))    # 합치기 전의 것으로 본다(같은 이름을 두 번 적은 것은 합치면 안 보인다)
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
    check_min_version(raw["min_version"])
    return Config(raw["project"], raw["lead"]["role"], raw["defaults"], raw["limits"],
                  raw["session"], raw["bash_deny"], raw["claude"], raw["server"], raw["tools"], raw,
                  codex=raw["codex"], hooks=raw["hooks"], checks=validate_checks(raw["checks"]),
                  reviews=validate_reviews(raw["reviews"]), folders=validate_folders(raw["folders"]))


def change_folders(paths: Paths, *, declare=None, undeclare=()) -> Config:
    """프로젝트 밖 폴더의 이름 선언을 고친다(사람이 PC 화면 · 명령줄에서) → 새 설정. folders 밖의 키는 그대로 둔다.

    declare = {이름: 설명}(있는 이름이면 그 줄을 바꾼다 — 대소문자만 달라도 같은 이름이다) · undeclare = 지울 이름들. 하나라도 틀리면 아무것도
    적지 않는다(ConfigError). 이름을 적는 프로젝트는 min_version 을 1.14.0 이상으로 둔다(낮추지는 않는다). 선언은 권한이 아니라 훅의 믿음과 상관없다."""
    from . import outside
    from .paths import file_lock, _atomic_json

    declare, undeclare = dict(declare or {}), list(undeclare or ())
    if not declare and not undeclare:
        raise ConfigError("folders: 더할 이름이나 지울 이름을 적는다")
    with file_lock(paths.state_dir / "gaf.json"):
        try:
            supplied = _read(paths) if paths.config.exists() else {}
        except (OSError, ValueError, RecursionError) as exc:
            raise ConfigError("gaf.json: 설정 JSON을 읽을 수 없어 이름을 바꾸지 않았다") from exc
        _validate(supplied, DEFAULTS, "")
        folders = validate_folders(supplied.get("folders", {}))
        validate_folders(declare)
        known = {outside.name_key(name) for name in folders}
        missing = [name for name in undeclare if not isinstance(name, str) or outside.name_key(name) not in known]
        if missing:
            raise ConfigError(f"folders: 선언에 없는 이름이다: {', '.join(str(name) for name in missing)}")
        gone, fresh = {outside.name_key(name) for name in undeclare}, {outside.name_key(name) for name in declare}
        if gone & fresh:
            raise ConfigError("folders: 같은 이름을 더하면서 지울 수 없다")
        updated = {name: about for name, about in folders.items() if outside.name_key(name) not in gone | fresh}
        updated.update(declare)
        validate_folders(updated)
        if updated:
            supplied["folders"] = updated
        else:
            supplied.pop("folders", None)
        current = supplied.get("min_version", "")
        if declare and not (isinstance(current, str) and _VERSION.fullmatch(current) and _version(current) >= _version(FOLDERS_SINCE)):
            supplied["min_version"] = FOLDERS_SINCE
        check_min_version(supplied.get("min_version", ""))       # 적은 뒤 이 프로그램이 못 여는 파일을 만들지 않는다
        _atomic_json(paths.config, supplied)
        return load_config(paths)


def change_review(paths: Paths, kind: str, rule: dict) -> tuple[Config, bool]:
    """사람이 승인한 기본 검토 규칙만 반영한다(change_check 와 같은 잠금 · 믿음 규칙). reviews 밖의 키는 그대로 둔다."""
    from . import hooks
    from .paths import file_lock, _atomic_json
    with file_lock(paths.state_dir / "gaf.json"):
        try:
            supplied = json.loads(paths.config.read_text(encoding="utf-8-sig")) if paths.config.exists() else {}
        except (OSError, ValueError, RecursionError) as exc:
            raise ConfigError("gaf.json: 설정 JSON을 읽을 수 없어 기본 검토를 바꾸지 않았다") from exc
        _validate(supplied, DEFAULTS, "")
        old_checks = validate_checks(supplied.get("checks", []))
        old_reviews = validate_reviews(supplied.get("reviews", []))
        old_hooks = supplied.get("hooks", {})
        fingerprint = hooks.digest(old_hooks, old_checks, old_reviews)
        was_trusted = not fingerprint or hooks.trusted(paths.root) == fingerprint
        if kind == "review_add":
            updated = validate_reviews([*old_reviews, rule])
        elif kind == "review_remove":
            current = next((row for row in old_reviews if row["name"] == rule.get("name")), None)
            if current is None:
                raise ConfigError("reviews.name: 없는 기본 검토다")
            if len(rule) > 1 and validate_reviews([rule])[0] != current:
                raise ConfigError("reviews: 청한 뒤 기본 검토 내용이 바뀌었다 — 다시 청한다")
            updated = [row for row in old_reviews if row["name"] != current["name"]]
        else:
            raise ConfigError("모르는 기본 검토 명령이다")
        supplied["reviews"] = updated
        _atomic_json(paths.config, supplied)
        if was_trusted:
            hooks.trust(paths.root, hooks.digest(old_hooks, old_checks, updated))
        return load_config(paths), was_trusted


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
        old_reviews = validate_reviews(supplied.get("reviews", []))
        old_hooks = supplied.get("hooks", {})
        fingerprint = hooks.digest(old_hooks, old_checks, old_reviews)
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
            hooks.trust(paths.root, hooks.digest(old_hooks, updated, old_reviews))
        return load_config(paths), was_trusted
