"""사람이 본 바뀜을 다시 확인한 뒤 커밋 · 푸시한다. 지휘자는 모른다."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from . import changes, scope


class CommitError(Exception):
    """사람과 총괄에게 그대로 보일 실패 글이다."""


def _git(root, *args):
    try:
        return changes._git(Path(root), *args)
    except changes._Failed as exc:
        raise CommitError("git 을 돌리지 못했다 — 저장소를 확인하고 다시 청한다") from exc


def _read(root, *args):
    code, raw = _git(root, *args)
    if code:
        raise CommitError("git 저장소를 읽지 못했다 — 저장소를 확인하고 다시 청한다")
    return raw.decode("utf-8", "replace").strip()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def clean_paths(root, paths) -> list[str]:
    """1~500개 경로를 뿌리 아래의 경로로 고르고 겹침을 뺀다."""
    if not isinstance(paths, list) or not 1 <= len(paths) <= 500:
        raise CommitError("경로는 1~500개의 글 목록으로 적는다")
    found = []
    for path in paths:
        if not isinstance(path, str) or not path.strip() or "\0" in path:
            raise CommitError("경로는 빈 글이 아닌 글로 적는다")
        try:
            inside, relative = scope.norm(path, root)
        except (OSError, ValueError, RuntimeError) as exc:
            raise CommitError("경로를 읽지 못했다 — 파일 경로를 다시 본다") from exc
        if not inside:
            raise CommitError("뿌리 밖 경로는 커밋하지 않는다")
        if relative == ".":
            raise CommitError("뿌리 전체는 고르지 못한다 — 파일 경로를 골라 적는다")
        if any(part.casefold() == ".git" for part in relative.split("/")):
            raise CommitError(".git 아래는 커밋하지 않는다")
        if relative not in found:
            found.append(relative)
    return found


def _repository(root):
    code, raw = _git(root, "rev-parse", "--is-inside-work-tree")
    if code or raw.strip() != b"true":
        raise CommitError("git 저장소가 아니다")


def _head(root):
    code, raw = _git(root, "rev-parse", "--verify", "HEAD")
    return raw.decode("ascii", "replace").strip() if not code else ""


def _batches(paths):
    """Windows 명령줄에도 들어가도록 인용 부호까지 세어 나눈다."""
    batch = []
    for path in paths:
        if len(subprocess.list2cmdline([*changes._BASE, "status", "--porcelain=v1", "-z",
                                       "--untracked-files=all", "--", path])) > 20000:
            raise CommitError("경로가 너무 길다 — 짧은 파일 경로로 다시 청한다")
        if batch and len(subprocess.list2cmdline([*changes._BASE, "status", "--porcelain=v1", "-z",
                                                  "--untracked-files=all", "--", *batch, path])) > 20000:
            yield batch
            batch = []
        batch.append(path)
    if batch:
        yield batch


def preview(root, paths) -> dict:
    """색인을 고쳐 쓰지 않고 상태 줄과 실제 파일 내용으로 서명을 만든다."""
    root = Path(root)
    paths = clean_paths(root, paths)
    _repository(root)
    git_dir = Path(_read(root, "rev-parse", "--absolute-git-dir"))
    if any((git_dir / marker).exists() for marker in
           ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")):
        raise CommitError("저장소가 병합 · 리베이스 중이다 — 사람이 끝낸 뒤 다시 청한다")
    head = _head(root)
    rows, found = set(), {}
    for batch in _batches(paths):
        code, raw = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *batch)
        if code:
            raise CommitError("git 상태를 읽지 못했다 — 경로를 다시 본다")
        entries = iter(raw.decode("utf-8", "surrogateescape").split("\0"))
        for entry in entries:
            if len(entry) < 4:
                continue
            status, path = entry[:2], entry[3:]
            old = next(entries, "") if any(char in status for char in "RC") else ""
            rows.add((entry, old))
            found[path] = "new" if status == "??" or "A" in status or "R" in status or "C" in status else (
                "deleted" if "D" in status else "modified")
            if old and "R" in status:
                found[old] = "deleted"
    if not found:
        raise CommitError("커밋할 바뀜이 없다 — 경로를 다시 본다")
    if len(found) > 5000:
        raise CommitError(f"파일이 너무 많다({len(found)}개) — 나눠 청한다")
    files, hashes = [], []
    counts = {"new": 0, "modified": 0, "deleted": 0}
    for path, kind in sorted(found.items()):
        files.append({"path": path, "kind": kind})
        counts[kind] += 1
        target = root / path
        try:
            mode = target.lstat().st_mode
            if stat.S_ISREG(mode):
                digest = hashlib.sha256()
                with target.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                hashes.append((path, digest.hexdigest()))
            elif stat.S_ISLNK(mode):
                hashes.append((path, _digest(os.readlink(target))))
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise CommitError(f"파일을 읽지 못했다: {path} — 다시 청한다") from exc
    return {"head": head, "files": files, "counts": counts, "signature": _digest([head, sorted(rows), hashes])}


def _write_git(root, args, timeout, verb, *, staged=False):
    """훅 · 서명 · 필터는 사람이 칠 때처럼 돌린다."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    error = ""
    try:
        done = subprocess.run(["git", "--literal-pathspecs", *args], cwd=root, env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if done.returncode == 0:
            return
        error = done.stdout.decode("utf-8", "replace")[-600:].strip()
    except subprocess.TimeoutExpired as exc:
        raw = exc.output or b""
        error = "시간을 넘겼다: " + (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)[-600:].strip()
    except OSError as exc:
        error = type(exc).__name__
    tail = " (고른 파일은 스테이징된 채로 남았다)" if staged else ""
    raise CommitError(f"{verb}하지 못했다: {error}{tail}")


def commit(root, paths, message, signature, *, scratch: Path) -> dict:
    changed = "청한 뒤 파일이나 저장소가 바뀌었다 — 커밋하지 않았다. 바뀐 것을 보고 다시 청한다"
    try:
        view = preview(root, paths)
    except CommitError as exc:
        if str(exc).startswith("커밋할 바뀜이 없다"):
            raise CommitError(changed) from exc
        raise
    if view["signature"] != signature:
        raise CommitError(changed)
    if not isinstance(message, str) or not message.strip() or len(message) > 10000:
        raise CommitError("커밋 글은 빈 글이 아닌 10,000자 안의 글로 적는다")
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    temporary = []
    try:
        selected = [row["path"] for row in view["files"]]
        missing = [row["path"] for row in view["files"] if row["kind"] == "deleted" and not (Path(root) / row["path"]).exists()]
        tracked = set()
        for batch in _batches(missing):
            code, raw = _git(root, "ls-files", "--cached", "-z", "--", *batch)
            if code:
                raise CommitError("커밋하지 못했다: 스테이징된 파일 목록을 읽지 못했다")
            tracked.update(os.fsdecode(path) for path in raw.split(b"\0") if path)
        # 이미 스테이징된 삭제는 색인에서도 사라져 add가 받지 않는다. 커밋 목록에는 그대로 둔다.
        add_paths = [path for path in selected if path not in missing or path in tracked]
        pack = lambda paths: b"".join(os.fsencode(path) + b"\0" for path in paths)
        for data in (message.encode("utf-8"), pack(add_paths)):
            with tempfile.NamedTemporaryFile(prefix="commit-", dir=scratch, delete=False) as stream:
                temporary.append(Path(stream.name))
                stream.write(data)
        text_file, paths_file = temporary
        pathspec = [f"--pathspec-from-file={paths_file}", "--pathspec-file-nul"]
        _write_git(root, ["add", *pathspec], 300, "커밋")
        paths_file.write_bytes(pack(selected))
        _write_git(root, ["commit", "--only", "-F", str(text_file), *pathspec], 600, "커밋", staged=True)
        return {"commit": _read(root, "rev-parse", "--short", "HEAD"),
                "subject": message.splitlines()[0], "files": len(view["files"])}
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)


def plain_url(url: str) -> str:
    """원격 주소에서 로그인 정보(사용자 · 비밀번호 · 토큰)를 뗀다 — 화면과 총괄에게 보이는 글이다."""
    found = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*://)[^/@]*@(.*)$", url)
    return (found.group(1) + found.group(2) if found else url)[:200]


def push_preview(root) -> dict:
    _repository(root)
    code, raw = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    if code or not raw.strip():
        raise CommitError("가지 위가 아니다(떨어진 HEAD) — 사람이 가지를 정한 뒤 다시 청한다")
    branch = raw.decode("utf-8", "replace").strip()
    values = []
    for key in ("remote", "merge"):
        code, raw = _git(root, "config", "--get", f"branch.{branch}.{key}")
        if code or not raw.strip():
            raise CommitError("올릴 곳(upstream)이 없다 — 사람이 처음 한 번 `git push -u <원격> <가지>` 를 한다")
        values.append(raw.decode("utf-8", "replace").strip())
    remote, merge = values
    code, raw = _git(root, "remote", "get-url", "--push", remote)
    address = raw.decode("utf-8", "replace").strip() if not code else ""
    upstream = _read(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    count = int(_read(root, "rev-list", "--count", "@{u}..HEAD"))
    if not count:
        raise CommitError("올릴 커밋이 없다")
    rows = _read(root, "log", "--format=%h %s", "-n", "30", "@{u}..HEAD")
    head = _head(root)
    return {"branch": branch, "remote": remote, "upstream": upstream,
            "commits": [{"hash": row.partition(" ")[0], "subject": row.partition(" ")[2]} for row in rows.splitlines()],
            "count": count, "head": head, "url": plain_url(address),
            # 주소 전문(가린 것이 아니라)을 서명에 넣는다 — 사람이 본 뒤 원격 주소가 바뀌면 올리지 않는다.
            "signature": _digest([head, remote, merge, address])}


def push(root, signature) -> dict:
    changed = "청한 뒤 가지나 커밋이 바뀌었다 — 푸시하지 않았다. 다시 청한다"
    try:
        view = push_preview(root)
    except CommitError as exc:
        if str(exc) == "올릴 커밋이 없다":
            raise CommitError(changed) from exc
        raise
    if view["signature"] != signature:
        raise CommitError(changed)
    merge = _read(root, "config", "--get", f"branch.{view['branch']}.merge")
    _write_git(root, ["push", "--", view["remote"], f"HEAD:{merge}"], 900, "푸시")
    return {key: view[key] for key in ("branch", "upstream", "count")}
