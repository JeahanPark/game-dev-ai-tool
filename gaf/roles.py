"""역할 글, 인계, 세션 기록을 다룬다."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config
from .engines.base import Engine
from .engines.codex import CodexEngine
from .paths import Paths, _atomic_json, _atomic_text, _read_json, _replace

_TEMPLATES = Path(__file__).parent / "templates"
# 인계에 적을 항목 — 재촉 · 압축 글(지휘자)은 이 값에서 만들고, 틀 셋(역할.md · 총괄.md · 인계.md)에는 같은 항목이 글자로 적혀
# 있다(시험이 같은지 본다). 틀에 이 항목의 자리표시를 두지 않는다: 탐침과 QA 탐침이 틀을 `str.format(name=…, duty=…, engine=…,
# model=…, effort=…, write=…)` 로 바로 채우거나 총괄 틀을 그대로 베껴 쓴다 — 자리가 늘면 그쪽이 깨진다(11-17 진짜 세션 탐침이 잡음).
HANDOFF_ITEMS = ("지금 일", "다음 일", "검증", "사람이 정한 것", "기다리는 것", "남은 배경 작업")
LEAD_TEMPLATE_NAME = "총괄"            # 총괄 틀에 적힌 이름 — 총괄 틀은 자리표시 없이 그대로도 역할 글이다
LEAD_MODELS = {"claude": ("claude-opus-5-5", "max"), "codex": ("gpt-6-astra", "ultra")}
FALLBACK_ERROR = 'fallback: {"engine": …} 꼴의 객체여야 한다'


def render_template(text: str, **values) -> str:
    """틀의 정해 둔 자리({name} · {duty} · {engine} · {model} · {effort} · {write})만 바꾼다 — 본문의 다른 중괄호와 끼워 넣은 글은 그대로 둔다."""
    return re.sub(r"\{(name|duty|engine|model|effort|write)\}", lambda match: str(values.get(match[1], match[0])), text)


def _lead_text(base: str, values: dict, duty: str) -> str:
    """총괄 틀의 머리말 값(엔진 · 모델 · 세기 · 쓰기 범위)과 이름을 만들 때의 값으로 갈아 끼운다.

    그 앞에는 틀을 바이트 그대로 써서 `gaf.json` 의 `defaults` 와 만들 때 준 값이 무시됐고, `lead.role` 을 바꾼 프로젝트에서도
    글이 「# 총괄」 · `team/총괄/인계.md` 를 가리켰다(11-16 최종 검수). 다른 쪽 엔진의 fallback 은 세기 바로 뒤에 넣는다.
    """
    lines = base.splitlines(keepends=True)
    end = _header_end(lines)
    for index in range(1, end or 0):
        key = lines[index].split(":", 1)[0].strip()
        if key in ("engine", "model", "effort", "write"):
            newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
            lines[index] = f"{key}: {values[key]}{newline}"
            if key == "effort" and values["engine"] in LEAD_MODELS:
                other = "codex" if values["engine"] == "claude" else "claude"
                model, effort = LEAD_MODELS[other]
                # 계정 기본값으로 만든 총괄(구독이 Max 아래 — 1.3.0 첫 설치)은 대체 엔진도 그 계정의 기본값으로 둔다
                fallback = {"engine": other} if values["model"] == "" else {"engine": other, "model": model, "effort": effort}
                lines[index] += f"fallback: {json.dumps(fallback, ensure_ascii=False)}{newline}"
    name = values["name"]
    for index in range((end or -1) + 1, len(lines)):                 # 제목 줄 — 줄 끝(LF · CRLF)은 틀의 것을 따른다
        if lines[index].strip() == f"# {LEAD_TEMPLATE_NAME}":
            newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
            lines[index] = f"# {name}{newline}"
            if duty:                                # 만들 때 준 하는 일은 제목 아래에 그대로 둔다(자리표시로 읽지 않는다)
                lines[index + 1:index + 1] = [newline, duty + newline]
            break
    text = "".join(lines)
    return text if name == LEAD_TEMPLATE_NAME else text.replace(f"team/{LEAD_TEMPLATE_NAME}/", f"team/{name}/")


class RoleError(ValueError):
    pass


@dataclass
class Role:
    name: str
    dir: Path
    engine: str
    model: str
    effort: str | None
    write: list[str]
    skills: list | None
    plugins: list
    mcp: str | None
    permission: str
    session: str
    peers: bool
    off: bool
    lead: bool
    body: str
    meta: dict
    fallback: dict | None = None
    # 프로젝트 밖 폴더의 이름 참조(1.14.0) — write 목록 안의 객체 `{"folder": 이름, "glob": 글롭}`. write(글 목록)와 따로 둔다:
    # 지휘자가 이 PC 의 짝으로 풀어 절대 글롭으로 write 에 붙인다(gaf/outside.py). 꼴의 검사는 풀 때 한다 — 잘못 적은 줄 하나로 역할이 사라지지 않게.
    refs: list = field(default_factory=list)
    # REA(게임 빌드를 뜯어보는 분석 도구 — 1.17.0)를 이 역할의 세션에만 내어 준다: 머리말의 `rea: true`. 지휘자가 REA 를 쥐고
    # 이 역할에게 도구 둘(rea_tools · rea_call)을 붙인다(gaf/rea/host.py). 총괄에게는 붙지 않는다.
    rea: bool = False

    @property
    def handoff_path(self) -> Path:
        return self.dir / "인계.md"

    @property
    def past_dir(self) -> Path:
        return self.dir / "지난-인계"

    @property
    def session_path(self) -> Path:
        return self.dir / "세션.json"

    @property
    def role_path(self) -> Path:
        return self.dir / "역할.md"


def _header_end(lines: list[str]) -> int | None:
    # 메모장 같은 편집기가 붙이는 BOM 은 건너뛰고 본다(파일의 BOM 은 그대로 둔다).
    if not lines or lines[0].lstrip("﻿").rstrip("\r\n") != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            return index
    raise RoleError("역할.md: 머리말을 닫는 --- 줄이 없다")


def parse_role_text(text: str) -> tuple[dict, str]:
    lines = text.splitlines(keepends=True)
    end = _header_end(lines)
    if end is None:
        return {}, text
    meta = {}
    for line in lines[1:end]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise RoleError(f"역할.md: 머리말은 '키: 값'으로 쓴다: {line}")
        key, value = (part.strip() for part in line.split(":", 1))
        if not key:
            raise RoleError("역할.md: 머리말 키가 비어 있다")
        if key == "fallback":
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise RoleError(FALLBACK_ERROR) from exc
            if (not isinstance(value, dict) or not isinstance(value.get("engine"), str)
                    or any(key in value and not isinstance(value[key], str) for key in ("model", "effort"))):
                raise RoleError(FALLBACK_ERROR)
        elif (value.startswith(("[", "{", '"')) or value in ("true", "false")
                or re.fullmatch(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value)):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise RoleError(f"역할.md의 {key}: JSON 값이 잘못됐다") from exc
        meta[key] = value
    return meta, "".join(lines[end + 1:])


def _check_name(name: str) -> None:
    if (not isinstance(name, str) or not 1 <= len(name) <= 30
            or name.startswith(("_", ".")) or re.search(r'[/\\:*?"<>|]', name)):
        raise RoleError('역할 이름은 1~30자이고 _ 또는 .으로 시작하거나 / \\ : * ? " < > |를 넣을 수 없다')
    # 11-12 QA 결함-10: Windows가 바꾸거나 장치로 읽는 이름은 폴더를 만들기 전에 모든 OS에서 막는다.
    if name.endswith((".", " ")):
        raise RoleError("역할 이름은 점이나 공백으로 끝날 수 없다")
    if any(ord(char) < 32 for char in name):
        raise RoleError("역할 이름에 제어 문자를 넣을 수 없다")
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", name.split(".", 1)[0], re.IGNORECASE):
        raise RoleError("역할 이름에 Windows 예약 이름을 쓸 수 없다")


def default_model(config: Config, engine) -> str:
    """역할 글에 model 이 없을 때 쓰는 모델. 기본 엔진이 아니면 그 엔진의 설정(gaf.json 의 `<엔진>.model`)을 본다 —
    Codex 역할에 Claude 모델 이름(defaults.model)이 들어가지 않게. 빈 글이면 엔진이 제 기본 모델을 쓴다."""
    if engine == config.defaults["engine"]:
        return config.defaults["model"]
    own = config.raw.get(engine) if isinstance(engine, str) else None
    model = own.get("model") if isinstance(own, dict) else None
    return model if isinstance(model, str) else ""


def load_role(paths: Paths, config: Config, name: str) -> Role:
    _check_name(name)
    directory = paths.role_dir(name)
    try:
        text = (directory / "역할.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RoleError(f"역할을 읽을 수 없다: {name}") from exc
    meta, body = parse_role_text(text)
    values = {key: meta.get(key, config.defaults[key]) for key in ("engine", "effort", "permission")}
    values["model"] = meta.get("model", default_model(config, values["engine"]))
    for key, value in values.items():
        if not isinstance(value, str):
            raise RoleError(f"{name}의 {key}: 글이어야 한다")
    for key in ("write", "skills", "plugins"):
        value = meta.get(key, None if key == "skills" else [])
        if key == "skills" and value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) or (key == "write" and isinstance(item, dict)) for item in value):
            raise RoleError(f"{name}의 {key}: 글 목록이어야 한다" + ('(프로젝트 밖 폴더의 이름은 {"folder": "이름", "glob": "하위/**"} 객체로 적는다)'
                                                                if key == "write" else ""))
    for key in ("peers", "off", "rea"):
        if type(meta.get(key, False)) is not bool:
            raise RoleError(f"{name}의 {key}: true 또는 false여야 한다")
    if meta.get("mcp") is not None and not isinstance(meta["mcp"], str):
        raise RoleError(f"{name}의 mcp: 경로 글이어야 한다")
    session = meta.get("session", "auto")
    if session not in ("auto", "fresh", "keep"):
        raise RoleError(f"{name}의 session: auto, fresh, keep 가운데 하나여야 한다")
    write = [item for item in meta.get("write", []) if isinstance(item, str)]
    refs = [dict(item) for item in meta.get("write", []) if isinstance(item, dict)]      # 프로젝트 밖 폴더의 이름 참조(1.14.0)
    own = f"team/{name}/**"
    if own not in write:
        write.append(own)
    return Role(name=name, dir=directory, **values, write=write, skills=meta.get("skills"),
                plugins=list(meta.get("plugins", [])), mcp=meta.get("mcp"), session=session,
                peers=meta.get("peers", False), off=meta.get("off", False),
                lead=name == config.lead_role, body=body, meta=meta, fallback=meta.get("fallback"), refs=refs,
                rea=meta.get("rea", False))


def _role_folders(paths: Paths) -> list[Path]:
    """역할 글(역할.md)이 있는 역할 폴더들 — `_` · `.` 으로 시작하는 폴더는 역할이 아니다. 글을 읽지는 않는다."""
    if not paths.team.is_dir():
        return []
    return [folder for folder in sorted(paths.team.iterdir(), key=lambda p: p.name)
            if folder.is_dir() and not folder.name.startswith(("_", ".")) and (folder / "역할.md").is_file()]


def has_roles(paths: Paths) -> bool:
    """역할이 하나라도 있나(글이 바른지는 보지 않는다) — 총괄 없이 다른 역할만 둔 프로젝트를 알아보는 데 쓴다."""
    try:
        return bool(_role_folders(paths))
    except OSError:
        return False


def load_roles(paths: Paths, config: Config) -> dict[str, Role]:
    return {folder.name: load_role(paths, config, folder.name) for folder in _role_folders(paths)}


REA_TEMPLATE = "reverse-engineer"      # 서랍의 역할 글 「역분석가」 — 이 글로 만든 역할에만 REA 가 붙는다


def create_role(paths: Paths, config: Config, name: str, *, duty="", engine=None, model=None,
                effort=None, write=None, lead=False, template=None) -> Role:
    _check_name(name)
    directory = paths.role_dir(name)
    if directory.exists():
        raise RoleError(f"이미 있는 역할 폴더다: {name}")
    lead_template = lead or name == config.lead_role
    values = {"name": name, "duty": duty,
              "write": json.dumps(write if write is not None else ["team/**", "wiki/**", "docs/**"] if lead_template else [], ensure_ascii=False)}
    for key, value in (("engine", engine), ("effort", effort)):
        values[key] = config.defaults[key] if value is None else value
    values["model"] = default_model(config, values["engine"]) if model is None else model
    if lead_template and values["engine"] in LEAD_MODELS:
        lead_model, lead_effort = LEAD_MODELS[values["engine"]]
        if model is None:
            values["model"] = lead_model
        if effort is None:
            values["effort"] = lead_effort
    efforts = CodexEngine.efforts if values["engine"] == "codex" else Engine.efforts
    if values["effort"] and values["effort"] not in efforts:
        raise RoleError(f"모르는 세기다: {values['effort']} ({' · '.join(efforts)})")
    base = (_TEMPLATES / ("총괄.md" if lead_template else "역할.md")).read_bytes().decode("utf-8")
    text = _lead_text(base, values, duty) if lead_template else render_template(base, **values)
    if template is not None:
        from . import library

        if lead_template:
            raise RoleError("총괄 역할에는 서랍 template 을 쓸 수 없다")
        template = library.role_id(template)
        try:
            _, body = library.load("roles", template)
        except library.LibraryError as exc:
            raise RoleError(f"{exc} · python -m gaf library list --kind roles 로 찾는다") from exc
        header_end = _header_end(text.splitlines(keepends=True))
        header = "".join(text.splitlines(keepends=True)[:header_end + 1])
        if template == REA_TEMPLATE:              # 서랍의 역분석가 — REA 를 이 역할의 세션에 내어 준다
            before, closing = header.rstrip("\n").rsplit("\n", 1)
            header = f"{before}\nrea: true\n{closing}\n"
        text = (header + f"# {name}\n\n" + (f"{duty}\n\n" if duty else "") + body.strip("\n") + "\n\n"
                + render_template(base[base.index("## 일하는 법"):], **values).rstrip() + "\n\n"
                + f"<!-- 역할 서랍 roles/{template} 에서 꺼냄 · 출처와 라이선스: 프레임워크의 gaf/library/NOTICE.md -->\n")
    # 잘못된 목록 등은 폴더를 만들기 전에 확인한다.
    meta, _ = parse_role_text(text)
    if not isinstance(meta.get("write"), list) or not all(isinstance(p, (str, dict)) for p in meta["write"]):
        raise RoleError('write: 글 목록이어야 한다(프로젝트 밖 폴더의 이름은 {"folder": "이름", "glob": "하위/**"} 객체로 적는다)')
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise RoleError(f"이미 있는 역할 폴더다: {name}") from exc
    _atomic_text(directory / "역할.md", text)
    _atomic_text(directory / "인계.md", render_template((_TEMPLATES / "인계.md").read_text(encoding="utf-8"), name=name))
    return load_role(paths, config, name)


def set_role_model(role: Role, model: str, effort: str) -> None:
    """역할 글 머리말의 model · effort 만 고쳐 쓴다(다른 줄 · 본문 · 줄 끝은 그대로 · 없는 키는 머리말 끝에 더한다).

    빈 글이면 값만 비운다 — 엔진이 제 기본을 쓴다(키를 지우면 gaf.json 의 기본값이 들어간다). 값의 검사는 부르는 쪽이 한다.
    """
    values = {"model": model, "effort": effort}
    text = role.role_path.read_bytes().decode("utf-8")
    lines = text.splitlines(keepends=True)
    end = _header_end(lines)
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    if end is None:
        text = f"---{newline}" + "".join(f"{key}: {value}".rstrip() + newline for key, value in values.items()) + f"---{newline}" + text
    else:
        for key, value in values.items():
            found = False
            for index in range(1, end):
                match = re.match(rf"^([ \t]*{key}[ \t]*:)", lines[index])
                if match:
                    ending = "\r\n" if lines[index].endswith("\r\n") else "\n" if lines[index].endswith("\n") else ""
                    lines[index] = match[1] + (" " + value if value else "") + ending
                    found = True
            if not found:
                lines.insert(end, f"{key}: {value}".rstrip() + newline)
                end += 1
        text = "".join(lines)
    _atomic_text(role.role_path, text)
    role.model, role.effort = model, effort
    role.meta.update(values)


def set_role_off(role: Role, off: bool) -> None:
    text = role.role_path.read_bytes().decode("utf-8")
    lines = text.splitlines(keepends=True)
    end = _header_end(lines)
    value = "true" if off else "false"
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    if end is None:
        text = f"---{newline}off: {value}{newline}---{newline}" + text
    else:
        found = False
        for index in range(1, end):
            match = re.match(r"^([ \t]*off[ \t]*:[ \t]*)", lines[index])
            if match:
                ending = "\r\n" if lines[index].endswith("\r\n") else "\n" if lines[index].endswith("\n") else ""
                lines[index] = match[1] + value + ending
                found = True
        if not found:
            lines.insert(end, f"off: {value}{newline}")
        text = "".join(lines)
    _atomic_text(role.role_path, text)
    role.off = off
    role.meta["off"] = off


def archive_handoff(role: Role, now: float | None = None) -> Path | None:
    if not role.handoff_path.exists():
        return None
    role.past_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y%m%d-%H%M%S")
    target = role.past_dir / f"{stamp}.md"
    number = 1
    while target.exists():
        target = role.past_dir / f"{stamp}-{number}.md"
        number += 1
    _replace(role.handoff_path, target)
    _atomic_text(role.handoff_path, render_template((_TEMPLATES / "인계.md").read_text(encoding="utf-8"), name=role.name))
    return target


def read_session(role: Role) -> dict | None:
    return _read_json(role.session_path)


def write_session(role: Role, data: dict) -> None:
    _atomic_json(role.session_path, data)


def clear_session(role: Role) -> None:
    role.session_path.unlink(missing_ok=True)


def handoff_mtime(role: Role) -> float | None:
    try:
        return role.handoff_path.stat().st_mtime
    except FileNotFoundError:
        return None
