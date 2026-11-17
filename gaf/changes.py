"""작업 폴더에서 바뀐 파일과 그 차이 — 화면의 「바뀐 코드」가 읽는다(11-13 사용자 「폰은 나중에하고 기능만 먼저해줘」).

git 을 **읽기만** 부른다. 바꾸는 명령(add · commit · checkout · stash …)은 여기에 없다.
- 색인 파일을 고쳐 쓰지 않는다 — 세션이 치는 git 과 잠금을 다투지 않는다. `git status` 는 `GIT_OPTIONAL_LOCKS=0` 으로, `git diff` 는
  `diff.autoRefreshIndex=false` 로 막는다(`git diff` 는 내용이 같고 시각만 바뀐 파일을 보면 색인을 새로 쓴다 — 11-15 QA 바뀐코드-004).
  그래서 「바뀌었다」는 `git status` 가 정한다(내용을 견준다): `git diff` 에만 나오는 파일은 시각만 바뀐 것이라 뺀다.
- 저장소 설정이 바깥 프로그램을 띄우는 길을 끈다: 외부 diff(`--no-ext-diff`) · textconv(`--no-textconv`) · fsmonitor
  (`-c core.fsmonitor=false`) · pager(`--no-pager`). 이 설정들은 세션이 고칠 수 있는 파일(`.gitattributes`)과 맞물려 명령을 돌린다.
- 파일 하나의 차이는 「지금 바뀐 파일」로 git 이 알려 준 경로에만 준다(그 밖의 경로 · 무시한 파일 · 프로젝트 밖 · `.git` · `.gaf` 는 없음).
  링크는 따라가지 않는다. 하위 모듈 · 속에 든 다른 저장소 안은 보지 않는다(`--ignore-submodules=all`).
- 받은 경로는 마디마다 디스크의 이름과 글자 그대로 같아야 한다(`_spelled`). Windows 는 다른 꼴을 같은 곳으로 열고(마디 끝의 점 · 빈칸
  `.git./config` · 짧은 이름 `GIT~1/config` · `:` 뒤), git 은 그 꼴 그대로 「새 파일」이라고 알린다 — 그런 꼴은 없는 파일로 답한다
  (11-15 QA 결함-33). 다른 곳으로 이어진 폴더 안의 파일도 제 이름이 아니므로 내용을 읽지 않는다(`_inside`).
- 끄지 못하는 것: 저장소 · 사용자 설정에 이미 있는 clean 필터(`filter.<이름>.clean` — 예: git-lfs)는 git 이 작업 폴더의 파일을
  견줄 때 돌린다(지휘자가 일의 앞뒤에 치는 `git status` 와 같다). 새 명령을 적으려면 `.git/config` 를 고쳐야 하고, 그것은 문이 막는다.
비교 대상은 마지막 커밋(HEAD)과 지금 작업 폴더다 — 색인에 올린 것 · 안 올린 것 · 추적하지 않는 새 파일을 한데 본다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from . import scope

LIST_LIMIT = 300            # 목록에 싣는 파일 수(넘는 것은 수만 알린다)
DIFF_BYTES = 300_000        # 파일 하나의 차이 글 한도(넘으면 줄 끝에서 자른다)
COUNT_BYTES = 1_000_000     # 새 파일의 줄 수는 이 크기까지만 센다
TIMEOUT = 15.0              # git 명령 하나를 기다리는 시간(초)
HIDDEN = (".gaf", ".git")   # 보이지 않는 폴더(상태 폴더 · 저장소 속)
FAILED = "git 을 돌리지 못했다"

_BASE = ["git", "--no-pager", "--literal-pathspecs", "-c", "core.quotepath=off", "-c", "core.fsmonitor=false",
         "-c", "diff.autoRefreshIndex=false"]
_DIFF = ["--no-ext-diff", "--no-textconv", "--no-renames", "--no-color", "--ignore-submodules=all"]


class _Failed(Exception):
    """git 을 못 돌렸거나 제때 답하지 않았다."""


def _git(root: Path, *args: str) -> tuple[int, bytes]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        done = subprocess.run([*_BASE, *args], cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=TIMEOUT, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        raise _Failed from exc
    return done.returncode, done.stdout


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def _empty(git: bool = False, branch: str = "", **more) -> dict:
    return {"git": git, "branch": branch, "files": [], "more": 0, "adds": 0, "dels": 0, **more}


def _hidden(path: str) -> bool:
    low = path.casefold()                # Windows 는 `.GIT` 도 `.git` 이다
    return any(low == name or low.startswith(name + "/") for name in HIDDEN)


def _has_head(root: Path) -> bool:
    return _git(root, "rev-parse", "--verify", "--quiet", "HEAD")[0] == 0


def _branch(root: Path) -> str:
    code, out = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    if code == 0 and out.strip():
        return _text(out).strip()
    code, out = _git(root, "rev-parse", "--short", "HEAD")           # 가지가 아니라 커밋에 서 있다
    return _text(out).strip() if code == 0 else ""


def _status(root: Path, *paths: str) -> dict[str, str]:
    """{경로: 상태 두 글자} — 추적하지 않는 새 파일은 하나씩 다 나온다(무시한 파일은 안 나온다).

    내용을 견줘서 정한다(시각만 바뀐 파일은 안 나온다). 이름 바꾸기는 묶지 않는다(`--no-renames` — 옛 이름은 지움, 새 이름은 새 파일).
    """
    code, out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=all", "--no-renames",
                     "--", *paths)
    if code != 0:
        raise _Failed
    entries, found, index = _text(out).split("\0"), {}, 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        if entry[0] in "RC":                # 이름 바꾸기 · 복사는 옛 경로가 한 칸 더 온다
            index += 1
        found[entry[3:]] = entry[:2]
    return found


def _tracked(root: Path, *paths: str) -> dict[str, dict]:
    """마지막 커밋과 다른 추적 파일 → {경로: {"status", "adds", "dels", "binary", "head"}}.

    head 는 마지막 커밋의 그 파일 값(blob 이름)이다 — 모드까지 같을 때만 싣는다(작업 폴더의 내용이 같은지 견줄 때 쓴다 · 밖으로 내보내지 않는다).
    """
    code, out = _git(root, "diff", "HEAD", "--raw", "--numstat", "-z", "--no-abbrev", *_DIFF, "--", *paths)
    if code != 0:
        raise _Failed
    tokens, found, index = _text(out).split("\0"), {}, 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token.startswith(":"):           # :<옛 모드> <새 모드> <옛 값> <새 값> <상태> \0 <경로>
            if index < len(tokens):
                fields = token[1:].split(" ")
                letter = fields[-1][:1]
                item = found.setdefault(tokens[index], {})
                item["status"] = letter if letter in "AD" else "M"
                item["head"] = fields[2] if len(fields) >= 5 and fields[0] == fields[1] else ""
                index += 1
        elif token.count("\t") >= 2:        # <더한 줄>\t<뺀 줄>\t<경로> — 바이너리는 「-」
            adds, dels, path = token.split("\t", 2)
            binary = adds == "-" or dels == "-"
            found.setdefault(path, {}).update(adds=None if binary else int(adds), dels=None if binary else int(dels),
                                              binary=binary)
    return {path: {"status": item.get("status", "M"), "adds": item.get("adds"), "dels": item.get("dels"),
                   "binary": item.get("binary", False), "head": item.get("head", "")} for path, item in found.items()}


def _same_as_head(root: Path, path: str, head: str) -> bool:
    """작업 폴더의 파일이 마지막 커밋의 내용과 같은가(git 이 색인에 넣을 때와 같은 변환을 거친 값으로 견준다 · 아무것도 쓰지 않는다).

    git 은 바이너리의 내용이 같은지 알려 주지 않는다(줄 수 대신 「-」) — 색인에 올렸다가 작업 폴더에서 되돌린 바이너리를 가릴 때만 부른다.
    """
    if not head or set(head) <= {"0"}:
        return False
    code, out = _git(root, "hash-object", "--", path)
    return code == 0 and _text(out).strip() == head


def _spelled(root: Path, parts: Sequence[str]) -> bool:
    """마디마다 디스크에 있는 이름과 글자 그대로 같은가 — 없는 마디에서 끝난다(지운 파일 · 없는 파일은 git 이 가린다).

    Windows 는 다른 꼴을 같은 곳으로 연다: 마디 끝의 점 · 빈칸을 떼고(`.git./config`), 짧은 이름을 풀고(`GIT~1/config`), `:` 뒤를
    딸린 흐름으로 읽는다. git 은 그런 꼴의 폴더를 열어 그 안의 파일을 받은 꼴 그대로 「새 파일」이라고 알린다 — 무시 규칙도 `.git`
    건너뛰기도 그 꼴에는 걸리지 않는다(11-15 QA 결함-33: `.git` · `.gaf` · 무시한 폴더 · 바뀌지 않은 파일의 내용이 나갔다).
    """
    if os.name == "nt" and any(part != part.rstrip(". ") or ":" in part for part in parts):
        return False
    folder = root
    for part in parts:
        try:
            names = os.listdir(folder)
        except NotADirectoryError:
            return True     # 앞 마디가 파일이다 — 그 아래에는 디스크에 아무것도 없다(폴더를 파일로 바꾼 뒤의 지운 파일 · git 이 가린다)
        except OSError:
            return False                                    # 폴더를 열지 못한다
        if part not in names:
            return not os.path.lexists(folder / part)       # 없는 마디면 여기까지다 · 있는데 이름이 다르면 다른 꼴이다
        folder = folder / part
    return True


def _inside(root: Path, relative: str) -> Path | None:
    """프로젝트 안의 보통 파일이고 relative 가 그 진짜 경로 그대로면 (링크를 다 푼) 그 경로 — 아니면 None.

    None 인 것: 링크 · 폴더 · 밖으로 나가는 경로 · 진짜 경로의 이름이 다른 것(다른 곳으로 이어진 폴더 안 — 이음이 `.git` 을 가리켜도
    그 안의 파일은 `.git/…` 이지 `이음/…` 이 아니다 · Windows 가 같은 곳으로 여는 다른 꼴) · 이름이 둘 이상인 파일(하드 링크 — 보통
    파일로 보이지만 프로젝트 밖의 파일과 한 몸일 수 있다 · 11-15 QA 바뀐코드-003).
    """
    try:
        base = root.resolve()
        path = base / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink > 1:
            return None
        real = path.resolve()
        return real if real.is_relative_to(base) and real.relative_to(base).as_posix() == relative else None
    except (OSError, ValueError):
        return None


def _new_file(root: Path, relative: str) -> dict:
    """새 파일(추적하지 않거나 색인에만 올린 것)의 줄 수 — 작업 폴더의 파일을 읽는다."""
    path = _inside(root, relative)
    if path is None:
        return {"status": "A", "adds": None, "dels": None, "binary": True}        # 링크 등: 내용을 읽지 않는다
    try:
        size = path.stat().st_size
        with path.open("rb") as source:
            head = source.read(8000)
            if b"\0" in head:
                return {"status": "A", "adds": None, "dels": None, "binary": True}
            if size > COUNT_BYTES:
                return {"status": "A", "adds": None, "dels": 0, "binary": False}
            data = head + source.read()
    except OSError:
        return {"status": "A", "adds": None, "dels": None, "binary": True}
    lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    return {"status": "A", "adds": lines, "dels": 0, "binary": False}


_NEW: dict = {"status": "A", "adds": None, "dels": None, "binary": False}      # 새 파일 — 줄 수는 아직 세지 않았다(_counted 가 센다)


def _counted(root: Path, path: str, item: dict) -> dict:
    """새 파일이면 작업 폴더의 파일을 읽어 줄 수를 채운다.

    목록에 보이는 파일에만 부른다 — 추적하지 않는 파일이 수만 개일 때 다 읽으면 오래 걸린다(11-15 압력 탐침: 30,000개에 12초 · 보이는 것은 300개).
    """
    return _new_file(root, path) if item is _NEW else item


def _changed(root: Path, *paths: str) -> dict[str, dict]:
    """지금 바뀐 파일 전부(또는 paths 만) → {경로: {"status", "adds", "dels", "binary"}}. 상태 폴더는 뺀다.
    새 파일의 값은 `_NEW` 다(줄 수를 아직 세지 않았다 — 쓰기 전에 `_counted` 를 거친다).

    「바뀌었다」는 `git status` 가 정한다 — `git diff` 에만 나오는 파일은 내용이 같고 시각만 바뀐 것이다(색인을 새로 쓰지 않게
    `diff.autoRefreshIndex` 를 꺼서 나온다). 마지막 커밋과 작업 폴더가 같은 것(색인에 올렸다가 되돌린 고침 · 올렸다가 지운 새 파일)도 뺀다.
    """
    status = _status(root, *paths)
    head = _has_head(root)
    tracked = _tracked(root, *paths) if head else {}
    found = {}
    for path, code in status.items():
        if _hidden(path) or path.endswith("/"):
            continue
        item = tracked.get(path)
        if item is None:
            # 마지막 커밋과 견준 차이에 없다: 추적하지 않는 새 파일이거나(커밋이 없는 저장소면 색인에 올린 것도) 색인에서만 달라진 것이다
            if (head and code != "??") or code[1:] == "D":
                continue
            found[path] = _NEW
            continue
        if item["status"] == "A":                        # 색인에 올린 새 파일 — 줄 수는 작업 폴더의 파일에서 센다(_counted)
            found[path] = _NEW
            continue
        if item["status"] == "M" and item["adds"] is None and not item["binary"]:
            continue        # git 이 줄 수를 내지 않았다 = 마지막 커밋과 내용이 같다(색인에 올렸다가 작업 폴더에서 되돌린 고침)
        if (item["status"] == "M" and item["binary"] and " " not in code and "?" not in code
                and _same_as_head(root, path, item.get("head", ""))):
            continue        # 바이너리는 git 이 같은지 알려 주지 않는다 — 색인도 작업 폴더도 달라졌다고 할 때만 값을 견준다
        found[path] = {key: item[key] for key in ("status", "adds", "dels", "binary")}
    return found


def listing(root, *, globs: Sequence[str] | None = None) -> dict:
    """바뀐 파일 목록 → {"git", "branch", "files": [{"path", "status": A|M|D, "adds", "dels", "binary"}], "more", "adds", "dels"}.

    globs 를 주면 그 글롭(역할의 쓰기 범위) 안의 파일만 싣는다. git 저장소의 뿌리가 아니면 {"git": False}, git 을 못 돌렸으면
    거기에 "error" 를 더한다. 파일이 LIST_LIMIT 를 넘으면 앞의 것만 싣고 나머지 수를 "more" 에 적는다.
    """
    root = Path(root)
    if not (root / ".git").exists():
        return _empty()
    try:
        branch = _branch(root)
        found = _changed(root)
    except _Failed:
        return _empty(error=FAILED)
    paths = sorted(found)
    if globs is not None:
        paths = [path for path in paths if any(scope.glob_match(path, glob) for glob in globs)]
    shown = paths[:LIST_LIMIT]
    files = [{"path": path, **_counted(root, path, found[path])} for path in shown]
    return {"git": True, "branch": branch, "files": files, "more": len(paths) - len(shown),
            "adds": sum(item["adds"] or 0 for item in files), "dels": sum(item["dels"] or 0 for item in files)}


def _cut(text: str) -> tuple[str, bool]:
    """차이 글을 한도 안으로 — 줄 끝에서 자른다 → (글, 잘랐나)."""
    raw = text.encode("utf-8")
    if len(raw) <= DIFF_BYTES:
        return text, False
    kept = raw[:DIFF_BYTES]
    end = kept.rfind(b"\n")
    return kept[:end if end > 0 else 0].decode("utf-8", "ignore"), True


def file_diff(root, path) -> dict | None:
    """바뀐 파일 하나의 차이 → {"path", "status", "binary", "truncated", "text"} · 지금 바뀐 파일이 아니면 None.

    text 는 통합 diff 의 덩이들이다(`@@ -a,b +c,d @@` 줄과 그 아래의 ` ` · `+` · `-` 줄). 새 파일은 모든 줄이 `+` 다.
    """
    root = Path(root)
    if not isinstance(path, str) or not path or "\0" in path or not (root / ".git").exists():
        return None
    relative = path.replace("\\", "/")
    parts = relative.split("/")
    if (relative.startswith("/") or ":" in parts[0] or any(part in ("", ".", "..") for part in parts)
            or _hidden(relative) or not _spelled(root, parts)):
        return None
    try:
        item = _changed(root, relative).get(relative)       # git 이 「바뀌었다」고 한 바로 그 경로만
        if item is None:
            return None
        item = _counted(root, relative, item)
        base = {"path": relative, "status": item["status"], "binary": bool(item["binary"]), "truncated": False, "text": ""}
        if item["binary"]:
            return base
        if item["status"] == "A":
            source = _inside(root, relative)
            if source is None:
                return {**base, "binary": True}
            with source.open("rb") as handle:
                data = handle.read(DIFF_BYTES * 2 + 1)
            body = _text(data)
            lines = body.split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            count = item["adds"] if item["adds"] is not None else len(lines)
            text = "\n".join([f"@@ -0,0 +1,{count} @@", *("+" + line.rstrip("\r") for line in lines)])
            cut, truncated = _cut(text)
            return {**base, "text": cut, "truncated": truncated or len(data) > DIFF_BYTES * 2}
        if item["status"] == "M" and _inside(root, relative) is None:
            return {**base, "binary": True}     # 고친 파일이 링크 · 하드 링크 · 이어진 폴더 안이다 — git 에게도 읽히지 않는다
        code, out = _git(root, "diff", "HEAD", "-U3", *_DIFF, "--", relative)
        if code != 0:
            return None
        whole = _text(out).replace("\r\n", "\n")
        start = whole.find("\n@@ ")
        if start < 0:
            return {**base, "binary": "Binary files " in whole}
        cut, truncated = _cut(whole[start + 1:].rstrip("\n"))
        return {**base, "text": cut, "truncated": truncated}
    except (_Failed, OSError):
        return None
