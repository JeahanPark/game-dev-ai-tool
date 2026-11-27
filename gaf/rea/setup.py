"""REA 를 이 PC 에 깐다 · 띄울 명령과 환경을 만든다 · 무엇이 있는지 본다(막는 함수들 — 지휘자는 스레드에서 부른다).

깔 자리는 이 PC 사용자의 공통 폴더(paths.user_dir()/tools)다 — 프로젝트 · 세션이 쓰는 자리가 아니다.
- Node: 이 프로그램이 받아 둔 것이 있으면 그것을, 없으면 이 PC 의 `node` 가 NODE_RANGE 에 맞을 때 그것을 쓴다. 둘 다 없으면 nodejs.org 에서
  NODE_VERSION 을 받아 SHA-256 을 먼저 맞춰 본 뒤 tools/node/ 에 푼다(PC 에 깔지 않는다 · PATH 를 바꾸지 않는다).
- REA: `npm install --prefix tools/rea/<VERSION> rea-agents@<VERSION>`. 띄울 스크립트는 package.json 의 bin 에서 읽는다(못 박지 않는다).
- Ghidra · JDK(1.17.1): 받지 않는다. 사람이 tools/ghidra/ · tools/jdk/ 에 풀어 둔 것을 환경변수(GHIDRA_INSTALL_DIR · JAVA_HOME)보다 먼저
  쓴다 — PC 전체의 값을 바꾸면 그 PC 의 다른 도구(다른 판의 REA · Java 를 쓰는 빌드)가 깨질 수 있어서다(2026-11-28).
자식 REA 의 환경은 정한 OS 값만 넘긴다(GAF_* · 키 · 토큰은 가지 않는다). 자식의 임시 폴더는 여기서 역할의 작업 폴더 안(work/tmp)으로
주고, 지휘자가 띄울 때 그 띄우기만의 폴더로 덮는다(gaf/rea/host.py 의 _make_temp — Windows 는 PC 의 임시 폴더의 gaf-rea/<지문> · 1.17.1).
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import http.client
import io
import json
import os
import platform
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PureWindowsPath

from . import (CHILD_ENV, GHIDRA_JAVA_MIN, GHIDRA_LINE, GHIDRA_MIN_MINOR, JAVA_MIN_DEFAULT, NODE_RANGE, NODE_SHA256, NODE_URL,
               NODE_VERSION, PACKAGE, VERSION, state)
from .. import __version__
from ..envclean import should_remove
from ..paths import _replace, file_lock, user_dir

NODE_MAX_BYTES = 100 * 1024 * 1024       # 받는 Node 묶음의 상한(win-x64.zip 은 약 36MB)
UNPACKED_MAX_BYTES = 600 * 1024 * 1024   # 푼 크기의 상한
FETCH_SECONDS = 60.0                     # 한 번 읽기를 기다리는 시간
FETCH_TOTAL_SECONDS = 900.0              # 다 받기를 기다리는 가장 긴 시간
INSTALL_SECONDS = 900                    # npm install 을 기다리는 가장 긴 시간
INSTALL_WAIT = INSTALL_SECONDS + 120     # 다른 곳(지휘자 · 다른 터미널)이 깔고 있으면 기다리는 시간
VERSION_SECONDS = 10                     # `node --version` 을 기다리는 시간
JAVA_SECONDS = 5                         # `java -version` 을 기다리는 시간
META_MAX_BYTES = 1024 * 1024             # 읽을 package.json 의 상한
PROPERTIES_MAX_BYTES = 64 * 1024         # 읽을 Ghidra/application.properties · JDK 의 release 의 상한(12.1.4 의 것은 659B ·
                                         # 이 PC 의 JDK 21.0.12.1 의 release 는 1295B)
TAIL_CHARS = 600                         # 실패한 npm 의 말에서 돌려주는 끝 글자 수
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# REA 6.10.1 은 Windows 에서 Java 를 JAVA_HOME 으로만 쓴다(<JAVA_HOME>\bin\java.exe) — JAVA_HOME 없이 PATH 맨 앞에 JDK 25 를 둬도
# `runtime_unavailable`(java.exe: Owned Windows process requires an absolute executable path · 2026-11-28 실측). Windows 가 아닌 곳의
# REA 안내 글은 PATH 도 된다고 읽힌다(재 보지는 못했다). 시험이 갈아 끼운다(os.name 은 바꾸지 않는다).
WINDOWS = os.name == "nt"

# 자식 REA 에게 넘기는 OS 값 — 이것만 넘긴다(있는 것만). GAF_* · 키 · 토큰 · 프록시 · NODE_OPTIONS 는 가지 않는다.
# REA_ILSPY_CMD_PATH 는 넘기지 않는다: 6.10.1 의 MCP 길은 그 값을 설정에 옮겨 담기만 하고(dist/mcp/OwnedMcpServers-*.js) 어느 도구도
# 읽지 않는다 — 쓰는 곳은 `rea doctor`(dist/application/Doctor.js) 뿐이다. 절대 경로가 아니면 initialize 전에 죽는다(설정 검사).
SERVER_ENV_KEEP = ("SystemRoot", "windir", "SystemDrive", "PATH", "PATHEXT", "COMSPEC", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
                   "LOCALAPPDATA", "APPDATA", "ProgramData", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                   "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "HOME", "LANG", "LC_ALL",
                   "GHIDRA_INSTALL_DIR", "JAVA_HOME")
ABSOLUTE_ONLY = ("GHIDRA_INSTALL_DIR", "JAVA_HOME")        # 온전한 경로가 아니면 넘기지 않는 값(server_env)

# REA 를 처음 켤 때 사람에게 보이는 법의 선(근거: docs/조사/REA/계획-v1-2026-11-27.md 3절 — 받아 쓰는 사람에게는 그 글이 없으니
# 같이 실리는 README 의 절을 가리킨다)
NOTICE = ("돌려 보며 관찰하고 아이디어 · 원리를 참고하는 것은 한국 · EU · 영국의 법이 허락한다.",
          "남의 게임의 코드를 디컴파일하는 것은 세 곳 모두 호환 목적에만 허락한다 — 그 밖의 쓰임은 쓰는 사람의 책임이다(스토어 약관도 금지한다).",
          "보호 장치(암호화 · DRM)를 푸는 것과 뽑은 코드 · 에셋을 내 게임에 넣는 것은 하지 않는다. 조문과 근거는 README 의 「게임 뜯어보기」 절에 있다.")

_VERSION = re.compile(r"v?([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})")
_RANGE_PART = re.compile(r"(\^|>=)\s*([0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9})")
# Ghidra 의 판 — REA 의 GHIDRA_RELEASE_PATTERN(`^(\d+)\.(\d+)(?:\.(\d+))?([-+].*)?$`)과 같은 꼴(길이에만 상한)
_GHIDRA_VERSION = re.compile(r"([0-9]{1,9})\.([0-9]{1,9})(?:\.([0-9]{1,9}))?([-+][0-9A-Za-z._+-]{0,40})?")
_GHIDRA_KEY = re.compile(r"application\.version\s*[=:]\s*(\S.*?)\s*")
_GHIDRA_JAVA_KEY = re.compile(r"application\.java\.min\s*[=:]\s*(\S.*?)\s*")
_GHIDRA_JAVA_MAX_KEY = re.compile(r"application\.java\.max\s*[=:]\s*(\S.*?)\s*")     # 12.2 · 12.1.4 는 빈 값(맞지 않는다 → None)
_JAVA_FEATURE = re.compile(r"[1-9][0-9]{0,3}")                                     # application.java.min · max 의 값
_JAVA_RELEASE = re.compile(r'JAVA_VERSION\s*=\s*"([^"\s]{1,40})"\s*')      # JDK 의 release 파일 — JAVA_VERSION_DATE 같은 줄은 맞지 않는다
_JAVA_MAJOR = re.compile(r"([0-9]{1,9})(?![0-9])(?:\.([0-9]{1,9})(?![0-9]))?")
_PUT = " · `~/.gaf/tools/{}/` 에 풀어 두면 이 프로그램만 쓴다(PC 의 설정은 그대로)"   # 경로는 글자 그대로(진짜 집 폴더를 찍지 않는다)
_MINE = " · 이 프로그램의 자리에 둔 것"


# ---------------------------------------------------------------- 자리
def tools_dir() -> Path:
    return user_dir() / "tools"


def rea_dir() -> Path:
    return tools_dir() / "rea" / VERSION


def _node_home() -> Path:
    return tools_dir() / "node"


GHIDRA_FOLDER = "ghidra"     # tools_dir() 아래 사람이 Ghidra 를 풀어 두는 폴더 — 줄의 `~/.gaf/tools/…` 와 같은 이름
JDK_FOLDER = "jdk"
# 그 Ghidra 폴더는 쓸 수 있어야 한다 — Ghidra 는 처음 돌 때 제 설치 폴더에 쓴다(Ghidra/Processors/*/data/languages/ 에 빈 *.sla.lock
# 135개 · 12.2 · 2026-11-28 실측).


def _ghidra_home() -> Path:
    return tools_dir() / GHIDRA_FOLDER


def _jdk_home() -> Path:
    return tools_dir() / JDK_FOLDER


def _unpacked(root: Path) -> list[Path]:
    """root 아래 폴더와 그 한 칸 아래 폴더(묶음 이름의 폴더를 하나 더 만들고 그 안에 푸는 압축 프로그램이 있다) — 이름 차례.
    「.」 으로 시작하는 폴더(반쯤 푼 것 따위)는 보지 않는다."""
    def folders(base: Path) -> list[Path]:
        try:
            return sorted(path for path in base.iterdir() if path.is_dir() and not path.name.startswith("."))
        except OSError:
            return []
    return [found for top in folders(root) for found in (top, *folders(top))]


# ---------------------------------------------------------------- 판
def _parse(text) -> tuple[int, int, int] | None:
    if not isinstance(text, str) or len(text) > 40:
        return None
    found = _VERSION.fullmatch(text.strip())
    return (int(found[1]), int(found[2]), int(found[3])) if found else None


def satisfies(version: str, spec: str = NODE_RANGE) -> bool:
    """판이 범위에 드나. 범위는 `^a.b.c` · `>=a.b.c` 를 `||` 로 이은 꼴만 읽는다 — 읽지 못하는 조각이 하나라도 있으면 False."""
    have = _parse(version)
    if have is None or not isinstance(spec, str) or not spec.strip() or len(spec) > 200:
        return False
    hit = False
    for part in spec.split("||"):
        found = _RANGE_PART.fullmatch(part.strip())
        if not found:
            return False
        base = _parse(found[2])
        if found[1] == ">=":
            hit = hit or have >= base
            continue
        major, minor, patch = base          # npm 의 ^ — 맨 왼쪽의 0 아닌 자리를 넘지 않는다
        upper = (major + 1, 0, 0) if major else (0, minor + 1, 0) if minor else (0, 0, patch + 1)
        hit = hit or base <= have < upper
    return hit


# ---------------------------------------------------------------- Node 찾기
def _archive_name() -> str | None:
    """이 PC(OS · 칩)에 맞는 Node 묶음의 이름(NODE_SHA256 의 키) — 없으면 None."""
    system = {"windows": "win", "linux": "linux", "darwin": "darwin"}.get(platform.system().lower())
    machine = {"amd64": "x64", "x86_64": "x64", "x64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower())
    if not system or not machine:
        return None
    return next((name for name in NODE_SHA256 if name.startswith(f"{system}-{machine}.")), None)


def _layout(folder: Path, windows: bool) -> tuple[Path, Path]:
    """nodejs.org 묶음을 푼 폴더 → (node 실행 파일, npm-cli.js)."""
    if windows:
        return folder / "node.exe", folder / "node_modules" / "npm" / "bin" / "npm-cli.js"
    return folder / "bin" / "node", folder / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"


def _npm(node_path: str, cli: Path | None) -> list[str]:
    """그 Node 로 npm 을 돌리는 명령의 앞부분 — npm-cli.js 를 못 찾으면 PATH 의 npm(그 Node 의 폴더를 PATH 맨 앞에 둔다)."""
    if cli is not None and cli.is_file():
        return [node_path, str(cli)]
    return ["cmd", "/c", "npm"] if os.name == "nt" else ["npm"]


def _private() -> dict | None:
    """이 프로그램이 받아 둔 Node 가운데 범위에 드는 가장 높은 판."""
    name = _archive_name()
    if name is None:
        return None
    stem = name.split(".", 1)[0]
    pattern = re.compile(rf"node-v([0-9]{{1,9}}\.[0-9]{{1,9}}\.[0-9]{{1,9}})-{re.escape(stem)}")
    best = None
    try:
        folders = [path for path in _node_home().iterdir() if path.is_dir()]
    except OSError:
        return None
    for folder in folders:
        found = pattern.fullmatch(folder.name)
        if not found or not satisfies(found[1]):
            continue
        binary, cli = _layout(folder, stem.startswith("win-"))
        if binary.is_file() and (best is None or _parse(found[1]) > best[0]):
            best = (_parse(found[1]), found[1], binary, cli)
    if best is None:
        return None
    return {"path": str(best[2]), "version": best[1], "private": True, "npm": _npm(str(best[2]), best[3])}


def which(name: str) -> str | None:
    """PATH 에 적힌 **온전한 폴더**에서만 실행 파일을 찾는다 → 온전한 경로나 None.

    `shutil.which` 는 Windows 에서 지금 폴더를 PATH 보다 먼저 본다(`NoDefaultCurrentDirectoryInExePath` 가 없을 때). 지휘자와 `gaf doctor`
    · `gaf rea status` 는 프로젝트 뿌리에서 뜬다 — 거기에 `node.cmd` 를 둘 수 있는 역할이 제 프로그램을 돌리게 할 수 있다(11-27 다른
    눈의 검수). PATH 의 빈 칸 · `.` · 상대 폴더는 건너뛴다(사람이 그 폴더를 온전한 경로로 PATH 에 적어 두었으면 그것은 쓴다)."""
    windows = os.name == "nt"
    tails = [ext for ext in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep) if ext] if windows else [""]
    if windows and Path(name).suffix:
        tails = [""]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        folder = folder.strip().strip('"')
        if not folder or not os.path.isabs(folder):
            continue
        for tail in tails:
            candidate = os.path.join(folder, name + tail)
            if os.path.isfile(candidate) and (windows or os.access(candidate, os.X_OK)):
                return candidate
    return None


def _system(which, run) -> dict | None:
    """PATH 의 node 와 그 판(범위와 상관없이) → {"path", "version"}. 판을 못 읽으면 version 은 빈 글."""
    found = which("node")
    if not found:
        return None
    version = ""
    try:
        done = run([found, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=VERSION_SECONDS, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        said = (getattr(done, "stdout", "") or "").strip() if getattr(done, "returncode", 1) == 0 else ""
        version = said[1:] if said.startswith("v") and _parse(said) else ""
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return {"path": str(found), "version": version}


def _find(which, run) -> tuple[dict | None, dict | None]:
    """(쓸 Node, PATH 에서 본 node). 받아 둔 것이 있으면 PATH 는 보지 않는다."""
    private = _private()
    if private is not None:
        return private, None
    seen = _system(which, run)
    if seen is None or not satisfies(seen["version"]):
        return None, seen
    binary = Path(seen["path"])
    with contextlib.suppress(OSError, RuntimeError):
        binary = binary.resolve()               # nvm 같은 연결을 따라가 npm-cli.js 를 찾는다
    cli = next((path for path in (binary.parent / "node_modules" / "npm" / "bin" / "npm-cli.js",
                                  binary.parent.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js") if path.is_file()), None)
    return {"path": seen["path"], "version": seen["version"], "private": False, "npm": _npm(seen["path"], cli)}, seen


def node(*, which=None, run=None) -> dict | None:
    """쓸 Node → {"path", "version", "private", "npm": npm 을 돌리는 명령의 앞부분}. 범위에 드는 것이 없으면 None."""
    return _find(which or globals()["which"], run or subprocess.run)[0]


# ---------------------------------------------------------------- Node 받기
class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.URLError("https 가 아닌 곳으로 넘겨주어 따라가지 않았다")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url: str, clock=time.monotonic) -> bytes:
    """https 로만 받는다 — 한 번 읽기마다 시간 제한 · 다 받는 시간과 크기에 상한."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("https 로만 받는다")
    opener = urllib.request.build_opener(_HttpsOnly())
    request = urllib.request.Request(url, headers={"User-Agent": f"GameDevAITool/{__version__}", "Accept-Encoding": "identity"})
    end = clock() + FETCH_TOTAL_SECONDS
    with opener.open(request, timeout=FETCH_SECONDS) as response:
        size = str(response.headers.get("Content-Length") or "")
        if size.isdigit() and int(size) > NODE_MAX_BYTES:
            raise ValueError("묶음이 상한보다 크다")
        chunks, total = [], 0
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > NODE_MAX_BYTES:
                raise ValueError("묶음이 상한보다 크다")
            if clock() > end:
                raise TimeoutError("제때 다 받지 못했다")
            chunks.append(chunk)
    return b"".join(chunks)


def _member_ok(name: str, top: str) -> bool:
    """묶음 안의 이름이 `top/` 아래에 머무나 — 절대 경로 · 드라이브 · `..` · 콜론(드라이브 · NTFS 흐름)을 받지 않는다."""
    if not isinstance(name, str) or not name or "\x00" in name or ":" in name:
        return False
    if name.startswith(("/", "\\")) or PureWindowsPath(name).anchor:
        return False
    parts = [part for part in re.split(r"[\\/]+", name) if part not in ("", ".")]
    return bool(parts) and ".." not in parts and parts[0] == top


def _unzip(data: bytes, dest: Path, top: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        bad = next((info.filename for info in members if not _member_ok(info.filename, top)), None)
        if bad is not None:
            return f"묶음에 제 폴더 밖을 가리키는 이름이 있다({bad[:80]}) — 풀지 않았다"
        if sum(info.file_size for info in members) > UNPACKED_MAX_BYTES:
            return "묶음을 풀면 상한보다 크다 — 풀지 않았다"
        base = dest.resolve()
        for info in members:
            target = (dest / info.filename).resolve()
            if base not in target.parents:
                return f"묶음에 제 폴더 밖을 가리키는 이름이 있다({info.filename[:80]}) — 풀다 멈췄다"
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as sink:
                shutil.copyfileobj(source, sink, 1 << 20)
            mode = (info.external_attr >> 16) & 0o777
            if os.name != "nt" and mode:
                os.chmod(target, mode)
    return ""


def _untar(data: bytes, dest: Path, top: str, mode: str) -> str:
    with tarfile.open(fileobj=io.BytesIO(data), mode=mode) as archive:
        members = archive.getmembers()
        total = 0
        for member in members:
            if not _member_ok(member.name, top):
                return f"묶음에 제 폴더 밖을 가리키는 이름이 있다({member.name[:80]}) — 풀지 않았다"
            if member.issym():
                joined = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname))
                if member.linkname.startswith(("/", "\\")) or not _member_ok(joined, top):
                    return f"묶음에 제 폴더 밖을 가리키는 연결이 있다({member.name[:80]}) — 풀지 않았다"
            elif member.islnk():
                if not _member_ok(member.linkname, top):
                    return f"묶음에 제 폴더 밖을 가리키는 연결이 있다({member.name[:80]}) — 풀지 않았다"
            elif not (member.isfile() or member.isdir()):
                return f"묶음에 파일 · 폴더가 아닌 것이 있다({member.name[:80]}) — 풀지 않았다"
            total += max(0, member.size)
        if total > UNPACKED_MAX_BYTES:
            return "묶음을 풀면 상한보다 크다 — 풀지 않았다"
        archive.extractall(dest, members=members, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    return ""


def install_node(fetch=None) -> dict:
    """nodejs.org 에서 NODE_VERSION 을 받아 tools/node/ 에 푼다 — 맞는 Node 가 이 PC 에 없을 때만 부른다.

    SHA-256 을 풀기 전에 맞춰 본다. 임시 폴더에 푼 뒤 제자리로 옮긴다(반쯤 푼 것을 쓰지 않게). 이미 풀어 둔 것이 있으면 받지 않는다.
    → {"ok", "path": node 실행 파일, "error"}. fetch(url) -> bytes 는 시험이 갈아 끼운다.
    """
    name = _archive_name()
    if name is None:
        return {"ok": False, "path": "", "error": f"이 PC(OS · 칩)에 맞는 Node 묶음을 이 프로그램이 받지 못한다 — 맞는 판({NODE_RANGE} · "
                                                  f"예: {NODE_VERSION})의 Node 를 nodejs.org 에서 직접 깔고 다시 친다"}
    stem = name.split(".", 1)[0]
    windows = stem.startswith("win-")
    top = f"node-v{NODE_VERSION}-{stem}"
    home = _node_home()
    final = home / top
    binary, _ = _layout(final, windows)
    if binary.is_file():
        return {"ok": True, "path": str(binary), "error": ""}
    url = NODE_URL.format(version=NODE_VERSION, name=name)
    try:
        data = (fetch or _fetch)(url)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return {"ok": False, "path": "", "error": f"Node 를 받지 못했다({type(exc).__name__}) — 인터넷을 확인하고 다시 친다"}
    if not isinstance(data, (bytes, bytearray)) or len(data) > NODE_MAX_BYTES:
        return {"ok": False, "path": "", "error": "받은 Node 묶음이 상한보다 크거나 꼴이 아니다 — 풀지 않았다"}
    digest = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(digest, NODE_SHA256[name]):
        return {"ok": False, "path": "", "error": f"받은 Node 묶음의 SHA-256 이 nodejs.org 에 적힌 값과 다르다({digest[:12]}…) — 풀지 않고 버렸다"}
    work = None
    try:
        home.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".unpack-", dir=home))
        problem = _unzip(bytes(data), work, top) if name.endswith(".zip") else _untar(
            bytes(data), work, top, "r:xz" if name.endswith(".tar.xz") else "r:gz")
        if problem:
            return {"ok": False, "path": "", "error": problem}
        if not _layout(work / top, windows)[0].is_file():
            return {"ok": False, "path": "", "error": "받은 묶음에 node 실행 파일이 없다 — 풀어 둔 것을 버렸다"}
        if final.exists():                      # 실행 파일이 없는 반쪽 — 갈아 놓는다
            shutil.rmtree(final)
        _replace(work / top, final)
    except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError, ValueError) as exc:
        return {"ok": False, "path": "", "error": f"Node 를 풀어 놓지 못했다({type(exc).__name__}) — 같은 명령을 다시 친다"}
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
    return {"ok": True, "path": str(binary), "error": ""}


# ---------------------------------------------------------------- REA 깔기
def _package() -> tuple[str, Path | None]:
    """rea_dir() 에 깔린 PACKAGE → (판, 띄울 스크립트). 판이 VERSION 이 아니거나 스크립트가 없으면 스크립트는 None."""
    folder = rea_dir() / "node_modules" / PACKAGE
    meta = folder / "package.json"
    try:
        if meta.stat().st_size > META_MAX_BYTES:
            return "", None
        data = json.loads(meta.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return "", None
    if not isinstance(data, dict):
        return "", None
    version = data["version"] if isinstance(data.get("version"), str) else ""
    bins = data.get("bin")
    if isinstance(bins, dict):
        script = next((bins[key] for key in ("rea", PACKAGE) if isinstance(bins.get(key), str)),
                      next((value for value in bins.values() if isinstance(value, str)), None))
    else:
        script = bins if isinstance(bins, str) else None
    if version != VERSION or not script:
        return version[:40], None
    try:
        base = folder.resolve()
        entry = (folder / script).resolve()
    except (OSError, RuntimeError, ValueError):
        return version, None
    if base not in entry.parents or not entry.is_file():
        return version, None
    return version, entry


def rea_entry() -> Path | None:
    """rea_dir() 에 PACKAGE 가 VERSION 으로 깔려 있으면 띄울 스크립트(package.json 의 bin)의 절대 경로."""
    return _package()[1]


def _npm_cache() -> Path:
    return tools_dir() / "npm-cache"


def _npm_temp() -> Path:
    return tools_dir() / "tmp"


def _put(env: dict[str, str], name: str, value: str) -> None:
    """대소문자만 다른 같은 이름을 지우고 넣는다 — Windows 의 환경은 대소문자를 가리지 않아 둘이 겹치면 어느 것이 쓰일지 모른다."""
    for key in [key for key in env if key.upper() == name.upper()]:
        del env[key]
    env[name] = value


def _npm_env(found: dict) -> dict[str, str]:
    """npm 에게 주는 환경 — 이 PC 의 값에서 GAF_* 와 키 · 토큰 · 세션 값을 뺀다(깔리는 꾸러미의 스크립트가 돈다). 그 Node 의 폴더를 PATH 맨 앞에.

    캐시와 임시 폴더는 우리 자리(tools_dir() 아래 · 임시 폴더는 만든다)다 — 주지 않으면 npm 의 캐시가 %LOCALAPPDATA%/npm-cache 에(96MB),
    node 의 compile cache 가 시스템 %TEMP% 에 생겼다(6.10.1 실측 · 2026-11-27). 둘 다 install_rea 가 끝나면 치운다.
    """
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GAF_") and not should_remove(key)}
    key = next((name for name in env if name.upper() == "PATH"), "PATH")
    folder = str(Path(found["path"]).parent)
    env[key] = folder + os.pathsep + env[key] if env.get(key) else folder
    temp = _npm_temp()
    temp.mkdir(parents=True, exist_ok=True)
    _put(env, "npm_config_cache", str(_npm_cache()))
    for name in ("TEMP", "TMP", "TMPDIR"):
        _put(env, name, str(temp))
    _put(env, "NO_UPDATE_NOTIFIER", "1")
    _put(env, "npm_config_update_notifier", "false")
    return env


def _tidy(*folders: Path) -> None:
    """치운다 — 못 치워도(누가 쥐고 있다) 넘어간다. 다음 깔기가 다시 쓰고 다시 치운다."""
    for folder in folders:
        with contextlib.suppress(OSError):
            shutil.rmtree(folder, ignore_errors=True)


def install_rea(run=subprocess.run) -> dict:
    """`npm install --prefix <rea_dir()> PACKAGE@VERSION` → {"ok", "error"}. 됐는지는 rea_entry() 가 풀리는지로 본다.

    npm 의 캐시 · 임시 폴더는 되든 안 되든 끝나면 치운다 — 깐 뒤 tools_dir() 에는 node/ · rea/ 와 잠금 파일만 남는다.
    """
    found = node()
    if found is None:
        return {"ok": False, "error": f"맞는 Node 가 없다(REA 가 바라는 판: {NODE_RANGE}) — Node 부터 받는다"}
    try:
        return _npm_install(found, run)
    finally:
        _tidy(_npm_cache(), _npm_temp())


def _npm_install(found: dict, run) -> dict:
    prefix = rea_dir()
    argv = [*found["npm"], "install", "--prefix", str(prefix), f"{PACKAGE}@{VERSION}", "--no-audit", "--no-fund", "--loglevel=error"]
    try:
        prefix.mkdir(parents=True, exist_ok=True)
        done = run(argv, cwd=str(prefix), env=_npm_env(found), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=INSTALL_SECONDS, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"npm 이 {INSTALL_SECONDS // 60}분 안에 끝나지 않았다 — 인터넷을 확인하고 다시 친다"}
    except OSError as exc:
        return {"ok": False, "error": f"npm 을 돌리지 못했다({type(exc).__name__})"}
    code = getattr(done, "returncode", 1)
    if code == 0 and rea_entry() is not None:
        return {"ok": True, "error": ""}
    said = "\n".join(part.strip() for part in (getattr(done, "stdout", "") or "", getattr(done, "stderr", "") or "") if part.strip())
    head = f"npm 이 실패했다(종료 코드 {code})" if code else f"npm 은 끝났으나 {PACKAGE} {VERSION} 의 띄울 스크립트를 찾지 못했다"
    return {"ok": False, "error": head + (f" — npm 의 말: {said[-TAIL_CHARS:]}" if said else "")}


def _install(say) -> dict:
    found = node()
    if found is None:
        say(f"Node {NODE_VERSION} — nodejs.org 에서 받는다(약 36MB)")
        got = install_node()
        if not got["ok"]:
            return {"ok": False, "node": None, "entry": "", "error": got["error"]}
        found = node()
        if found is None:
            return {"ok": False, "node": None, "entry": "", "error": "받아 푼 Node 를 찾지 못했다 — 같은 명령을 다시 친다"}
    entry = rea_entry()
    if entry is None:
        say(f"{PACKAGE} {VERSION} — npm 으로 깐다(1분 남짓 걸린다)")
        done = install_rea()
        entry = rea_entry() if done["ok"] else None
        if entry is None:
            return {"ok": False, "node": found, "entry": "", "error": done["error"] or "깐 REA 를 찾지 못했다"}
    return {"ok": True, "node": found, "entry": str(entry), "error": ""}


def install(progress=None) -> dict:
    """맞는 Node 가 없을 때만 Node 를 받고, 그다음 REA 를 깐다 — 다 있으면 아무것도 받지 않는다(몇 번 불러도 같다).

    두 곳(지휘자 · 터미널)이 같이 깔지 않게 잠근다. progress(글) 는 받기 · 깔기를 시작할 때 한 줄씩 부른다.
    → {"ok", "node": {...} | None, "entry": 띄울 스크립트, "error"}.
    """
    say = progress if callable(progress) else (lambda text: None)
    try:
        state.human_only("REA 를 이 PC 에 까는")          # 세션이 띄운 프로세스에서는 받지도 깔지도 않는다(11-27 다른 눈의 검수)
    except PermissionError as exc:
        return {"ok": False, "node": None, "entry": "", "error": str(exc)}
    try:
        with file_lock(tools_dir() / "rea-install", wait=INSTALL_WAIT):
            return _install(say)
    except TimeoutError:
        return {"ok": False, "node": None, "entry": "", "error": "다른 곳(지휘자 · 다른 터미널)에서 REA 를 깔고 있다 — 끝난 뒤 다시 친다"}
    except OSError as exc:
        return {"ok": False, "node": None, "entry": "", "error": f"깔 자리를 쓰지 못했다({type(exc).__name__}): {tools_dir()}"}


def install_plan(data: dict | None = None) -> list[str]:
    """`rea on` 이 받을 것 한 줄씩(이미 있으면 빈 목록) — 사람에게 미리 보인다(승인 카드에는 줄마다 글머리표 하나).

    REA 의 크기 · 시간 · 받는 곳은 6.10.1 을 이 PC 에 깔아 잰 값이다(2026-11-27): 깐 뒤 328MB(선택 부품 Frida 140MB) · 60초 ·
    npm 로그의 받는 곳은 registry.npmjs.org 뿐, Frida 의 설치 스크립트(prebuild-install)가 GitHub 릴리스에서 50MB 를 받았다.
    """
    found = data.get("node") if isinstance(data, dict) else node()
    installed = (data.get("rea") or {}).get("installed") if isinstance(data, dict) else rea_entry() is not None
    need = []
    if found is None:
        need.append(f"Node {NODE_VERSION} — nodejs.org 에서 받는다(약 36MB · 이 프로그램의 자리에만 푼다 · PC 의 Node 는 그대로)"
                    if _archive_name() else f"Node — 이 PC(OS · 칩)에 맞는 묶음이 없어 받지 못한다 · {NODE_RANGE} 에 드는 판을 직접 깐다")
    if not installed:
        need.append(f"{PACKAGE} {VERSION} — npm 저장소(registry.npmjs.org)에서 받아 깐다(MIT · 깐 뒤 약 328MB · 1분 남짓)")
        need.append("그 가운데 Frida(REA 의 선택 부품 · LGPL-2.0 과 wxWindows 예외 · 약 140MB) — 깔 때 GitHub 에서 미리 지은 파일(약 50MB)을 받는다")
    return need


# ---------------------------------------------------------------- 띄우기
def server_argv() -> list[str] | None:
    """[node, 띄울 스크립트, "mcp"] — 깔려 있지 않거나 맞는 Node 가 없으면 None."""
    entry = rea_entry()
    if entry is None:
        return None
    found = node()
    return None if found is None else [found["path"], str(entry), "mcp"]


def server_env(work: Path) -> dict[str, str]:
    """자식 REA 의 환경 — 빈 데서 시작해 SERVER_ENV_KEEP 가운데 있는 것만 · 그 Node 의 폴더를 PATH 맨 앞에 · TEMP/TMP/TMPDIR 는
    work/tmp(만든다) · CHILD_ENV. GAF_* 는 절대 넣지 않는다. 경로 값(GHIDRA_INSTALL_DIR · JAVA_HOME)은 온전한 경로일 때만 넘긴다 —
    REA 6.10.1 은 아니면 설정 오류로 initialize 전에 끝난다(그러면 REA 를 아예 못 쓴다 · 빼면 네이티브 길만 못 쓴다).

    이 프로그램의 자리에 둔 Ghidra(판이 맞는 것)가 있으면 그것을 이 PC 의 값보다 먼저 준다(1.17.1). JAVA_HOME 은 상태의 Java 와 같은
    차례(_pick_java: 이 프로그램의 자리 → java 가 든 JAVA_HOME → PATH 의 JDK 폴더)로 고른다 — REA 는 Windows 에서 JAVA_HOME 의 JDK 만
    쓴다(2026-11-28 실측). 고른 것이 없으면 지금처럼 이 PC 의 JAVA_HOME 을 (온전한 경로면) 그대로 넘긴다. 고른 JDK 의 bin 은 PATH 의 Node
    의 폴더 다음에 둔다 — REA 가 부르는 다른 도구가 같은 JDK 를 쓰게(PATH 에서 고른 것이면 이미 거기 있어 건드리지 않는다).
    여기서는 java 를 돌리지 않는다(판은 보지 않는다)."""
    temp = (Path(work) / "tmp").resolve()
    temp.mkdir(parents=True, exist_ok=True)
    env = {name: os.environ[name] for name in SERVER_ENV_KEEP if name in os.environ}
    ghidra, java = _private_ghidra(), _pick_java(which)
    if ghidra is not None and os.path.isabs(ghidra["path"]):
        env["GHIDRA_INSTALL_DIR"] = ghidra["path"]
    java_bin = None
    if java is not None and java["home"] and os.path.isabs(java["home"]):
        env["JAVA_HOME"] = java["home"]
        java_bin = str(Path(java["path"]).parent) if java["from"] != "path" else None
    for name in ABSOLUTE_ONLY:
        if name in env and not os.path.isabs(env[name].strip()):
            del env[name]
    found = node()
    front = ([str(Path(found["path"]).parent)] if found is not None else []) + ([java_bin] if java_bin else [])
    if front:
        env["PATH"] = os.pathsep.join([*front, env["PATH"]]) if env.get("PATH") else os.pathsep.join(front)
    for name in ("TEMP", "TMP", "TMPDIR"):
        env[name] = str(temp)
    env.update(CHILD_ENV)
    return {name: value for name, value in env.items() if not name.upper().startswith("GAF_")}


# ---------------------------------------------------------------- 보기
def _small_text(path: Path) -> str | None:
    """작은 설정 파일의 글 — 없거나 · 못 읽거나 · PROPERTIES_MAX_BYTES 보다 크면 None."""
    try:
        with open(path, "rb") as stream:
            raw = stream.read(PROPERTIES_MAX_BYTES + 1)
    except OSError:
        return None
    return None if len(raw) > PROPERTIES_MAX_BYTES else raw.decode("utf-8", errors="replace")


def _ghidra_facts(home: Path) -> tuple[str, int | None, int | None]:
    """<home>/Ghidra/application.properties → (`application.version=`(REA 가 판을 보는 자리) · `application.java.min=` ·
    `application.java.max=`(REA 가 JDK 를 가리는 두 값 · 주판)). 못 읽거나 꼴이 아니면 빈 글 · None — 같은 키가 둘이면 Java 의 Properties
    처럼 마지막 것."""
    text = _small_text(home / "Ghidra" / "application.properties")
    found = {_GHIDRA_KEY: "", _GHIDRA_JAVA_KEY: "", _GHIDRA_JAVA_MAX_KEY: ""}
    for line in (text or "").splitlines():
        line = line.strip()
        key = next((key for key in found if key.fullmatch(line)), None)
        if key is not None:
            found[key] = key.fullmatch(line)[1]
    version, low, high = found.values()
    return (version if _GHIDRA_VERSION.fullmatch(version) else "",
            int(low) if _JAVA_FEATURE.fullmatch(low) else None, int(high) if _JAVA_FEATURE.fullmatch(high) else None)


def _ghidra_seen(value: str, home: Path | None, private: bool) -> dict:
    """한 Ghidra 폴더 → {"path", "ok": 폴더가 있나, "version": 읽은 판(못 읽으면 빈 글), "fits": VERSION 이 받는 판인가(GHIDRA_LINE.x 의
    부판 GHIDRA_MIN_MINOR 이상 · 판을 못 읽었으면 None), "private": 이 프로그램의 자리에 둔 것인가, "java_min" · "java_max": 받는 JDK 의
    아래 · 위 주판(못 읽거나 빈 값이면 None)}."""
    ok = home is not None and home.is_dir()
    version, java_min, java_max = _ghidra_facts(home) if ok else ("", None, None)
    parts = _GHIDRA_VERSION.fullmatch(version) if version else None
    fits = None if parts is None else int(parts[1]) == GHIDRA_LINE and int(parts[2]) >= GHIDRA_MIN_MINOR
    return {"path": value, "ok": ok, "version": version, "fits": fits, "private": private, "java_min": java_min, "java_max": java_max}


def _ghidra_order(item: dict) -> tuple:
    """높은 판이 큰 값 — 같은 판이면 꼬리표(-DEV 따위)가 없는 것이 크다."""
    parts = _GHIDRA_VERSION.fullmatch(item["version"])
    return int(parts[1]), int(parts[2]), int(parts[3] or 0), parts[4] is None


def _private_ghidras() -> list[dict]:
    """tools/ghidra/ 아래(한 칸 아래까지) 판을 읽은 Ghidra 들."""
    seen = [_ghidra_seen(str(folder), folder, True) for folder in _unpacked(_ghidra_home())]
    return [item for item in seen if item["version"]]


def _private_ghidra(found: list[dict] | None = None) -> dict | None:
    """이 프로그램의 자리에 둔 Ghidra 가운데 판이 맞는 가장 높은 판."""
    fits = [item for item in (_private_ghidras() if found is None else found) if item["fits"]]
    return max(fits, key=_ghidra_order) if fits else None


def _ghidra() -> dict:
    """쓸 Ghidra(_ghidra_seen 의 꼴) — 이 프로그램의 자리에 둔 것 가운데 판이 맞는 가장 높은 판, 없으면 GHIDRA_INSTALL_DIR.

    이 프로그램의 자리에 판이 안 맞는 것만 있으면 환경변수 쪽을 본다(둘 다 안 맞으면 환경변수 쪽의 「판이 낮다」). 환경변수의 폴더가 없을
    때만 그 안 맞는 것(가장 높은 판)을 돌려준다 — 「없음」 대신 둔 것의 판이 낮다고 알리게."""
    private = _private_ghidras()
    chosen = _private_ghidra(private)
    if chosen is not None:
        return chosen
    value = os.environ.get("GHIDRA_INSTALL_DIR", "")
    seen = _ghidra_seen(value, Path(value) if value else None, False)
    return seen if seen["ok"] or not private else max(private, key=_ghidra_order)


def _java_binary(home: Path) -> Path:
    return home / "bin" / ("java.exe" if os.name == "nt" else "java")


def _java_major(version) -> int | None:
    """판의 맨 앞 수 — `21.0.12.1` → 21 · `25-ea` → 25 · 옛 꼴 `1.8.0_402` → 8. 못 읽으면 None."""
    if not isinstance(version, str) or len(version) > 40:
        return None
    hit = _JAVA_MAJOR.match(version.strip())
    if not hit:
        return None
    major = int(hit[1])
    return int(hit[2]) if major == 1 and hit[2] else major


def _release_version(home: Path) -> str:
    """<home>/release(JDK 묶음마다 있다)의 `JAVA_VERSION="…"` — 못 읽으면 빈 글. 같은 키가 둘이면 마지막 것."""
    found = ""
    for line in (_small_text(home / "release") or "").splitlines():
        hit = _JAVA_RELEASE.fullmatch(line.strip())
        if hit:
            found = hit[1]
    return found


def _private_java() -> dict | None:
    """tools/jdk/ 아래(한 칸 아래까지 · 맥은 <폴더>/Contents/Home) java 실행 파일이 있는 JDK 가운데 판이 가장 높은 것
    → {"path", "home", "version": release 로 읽은 판(못 읽으면 빈 글)}. 판은 release 로, 못 읽으면 폴더 이름의 수로 견준다(돌리지 않는다)."""
    best = None
    for folder in _unpacked(_jdk_home()):
        for home in (folder, folder / "Contents" / "Home"):
            binary = _java_binary(home)
            if not binary.is_file():
                continue
            version = _release_version(home)
            order = tuple(int(part) for part in re.findall(r"[0-9]{1,9}", version or folder.name)[:6])
            if best is None or order > best[0]:
                best = (order, {"path": str(binary), "home": str(home), "version": version})
    return None if best is None else best[1]


def _java_said(path: str, run) -> str:
    """`java -version` 이 말한 판(JAVA_SECONDS 안) — 못 읽으면 빈 글."""
    try:
        done = run([path, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=JAVA_SECONDS, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        found = re.search(r'version "([^"\s]{1,40})"', (getattr(done, "stderr", "") or "") + (getattr(done, "stdout", "") or ""))
        return found[1] if found else ""
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return ""


def _jdk_of(binary: Path) -> str:
    """PATH 에서 찾은 java 가 JDK 폴더의 것(<home>/bin/java · <home>/release 가 있다)이면 그 폴더 — 연결(심볼릭 링크)은 따라간다.
    대신 띄우는 파일(Windows 의 javapath 따위)이면 빈 글."""
    with contextlib.suppress(OSError, RuntimeError):
        binary = binary.resolve()
    home = binary.parent.parent
    return str(home) if binary.parent.name.lower() == "bin" and (home / "release").is_file() else ""


def _pick_java(which) -> dict | None:
    """쓸 Java 를 고른다(돌리지 않는다 · `_java` 와 `server_env` 가 같이 쓴다) — 이 프로그램의 자리(tools/jdk/) → 환경변수 JAVA_HOME
    (온전한 경로이고 그 안에 java 실행 파일이 있을 때) → PATH 의 java. → {"path", "home": JAVA_HOME 으로 줄 폴더(PATH 의 java 가 JDK
    폴더의 것이 아니면 빈 글), "version": release 로 읽은 판(이 프로그램의 자리의 것만 · 아니면 빈 글), "from": "private" | "env" | "path"}."""
    private = _private_java()
    if private is not None:
        return {**private, "from": "private"}
    home = os.environ.get("JAVA_HOME", "").strip()
    if home and os.path.isabs(home) and _java_binary(Path(home)).is_file():
        return {"path": str(_java_binary(Path(home))), "home": home, "version": "", "from": "env"}
    found = which("java")
    return {"path": str(found), "home": _jdk_of(Path(found)), "version": "", "from": "path"} if found else None


def _java(which, run) -> dict:
    """쓸 Java(_pick_java 의 차례) → {"path", "ok", "version", "private": 이 프로그램의 자리에 둔 것인가, "home": JAVA_HOME 으로 줄 폴더
    (못 주면 빈 글), "major": 판의 맨 앞 수(못 읽으면 None)}. 이 프로그램의 자리의 것은 판을 release 파일로 읽고 못 읽을 때만, 나머지는
    늘 `-version` 을 돌린다(JAVA_SECONDS 안)."""
    picked = _pick_java(which)
    if picked is None:
        return {"path": "", "ok": False, "version": "", "private": False, "home": "", "major": None}
    version = picked["version"] or _java_said(picked["path"], run)
    return {"path": picked["path"], "ok": True, "version": version, "private": picked["from"] == "private", "home": picked["home"],
            "major": _java_major(version)}


def status(*, run=None, which=None) -> dict:
    """이 PC 에 무엇이 있나(깔지 않고 보기만) — 켰는지 · 쓸 Node · REA · REA 가 쓰는 것(Ghidra · Java — 이 프로그램의 자리에 둔 것을 먼저).

    ILSpy 는 보지 않는다 — 6.10.1 의 MCP 도구는 ILSpy 를 부르지 않는다(닷넷의 클래스 · 메서드 · IL 은 inspect_managed_members 가
    ILSpy 없이 보인다 · REA_ILSPY_CMD_PATH 를 읽는 곳은 `rea doctor` 뿐이다).
    """
    run, which = run or subprocess.run, which or globals()["which"]
    chosen, seen = _find(which, run)
    version, entry = _package()
    return {"enabled": state.enabled(), "node": chosen, "system_node": seen, "node_download": _archive_name() is not None,
            "rea": {"installed": entry is not None, "version": version, "entry": str(entry) if entry else ""},
            "ghidra": _ghidra(), "java": _java(which, run)}


def status_rows(data: dict | None = None) -> list[tuple[str, bool, str]]:
    """(이름, 갖췄나, 한 줄) — `gaf rea status` 와 `gaf doctor` 가 쓴다. 경로는 싣지 않는다(계정 이름이 들어 있다)."""
    data = status() if data is None else data
    rea, found, seen = data.get("rea") or {}, data.get("node"), data.get("system_node")
    on = data.get("enabled") is True
    if rea.get("installed"):
        rows = [("REA", on, f"깔림 {VERSION} · 켜짐" if on else f"깔림 {VERSION} · 꺼짐 — `python -m gaf rea on` 으로 켠다")]
    else:
        notes = ([f"있는 판 {rea['version']} · 쓰는 판 {VERSION}"] if rea.get("version") else []) + (["켜 둠"] if on else [])
        rows = [("REA", False, f"안 깔림{'(' + ' · '.join(notes) + ')' if notes else ''} — `python -m gaf rea on` 으로 "
                               f"{'다시 ' if on else ''}깐다")]
    if found:
        rows.append(("Node", True, f"{found['version']}({'이 프로그램이 받은 것' if found.get('private') else '이 PC 에 깔린 것'})"))
    else:
        fix = (f"`rea on` 이 이 프로그램의 자리에 받는다(Node {NODE_VERSION} · nodejs.org)" if data.get("node_download", True)
               else f"{NODE_RANGE} 에 드는 판을 직접 깐다(이 PC 에 맞는 묶음을 이 프로그램이 받지 못한다)")
        if seen and seen.get("version"):
            rows.append(("Node", False, f"맞는 판 없음 — 이 PC 의 판({seen['version']})은 REA 가 바라는 판({NODE_RANGE})이 아니다 · {fix}"))
        else:
            rows.append(("Node", False, f"{'판을 읽지 못했다' if seen else '없음'} — {fix}"))
    ghidra = data.get("ghidra") or {}
    want = f"{GHIDRA_LINE}.{GHIDRA_MIN_MINOR} 이상"
    mine = _MINE if ghidra.get("private") else ""
    if ghidra.get("ok") and ghidra.get("version") and ghidra.get("fits") is True:
        rows.append(("Ghidra", True, f"있음({ghidra['version']}){mine}"))
    elif ghidra.get("ok") and ghidra.get("version") and ghidra.get("fits") is False:
        rows.append(("Ghidra", False, f"있음({ghidra['version']}){mine} — REA {VERSION} 은 {want}({GHIDRA_LINE}.x)을 바란다 · "
                                      "네이티브 실행 파일의 코드는 못 읽는다(고를 것)" + _PUT.format(GHIDRA_FOLDER)))
    elif ghidra.get("ok"):
        rows.append(("Ghidra", True, f"있음(판은 읽지 못했다 — REA {VERSION} 은 {want}을 바란다)"))
    else:
        where = "GHIDRA_INSTALL_DIR 의 폴더가 없다" if ghidra.get("path") else "없음"
        rows.append(("Ghidra", False, f"{where} — 네이티브 실행 파일의 코드는 못 읽는다(고를 것)" + _PUT.format(GHIDRA_FOLDER)))
    rows.append(_java_row(data.get("java") or {}, ghidra))
    return rows


def _java_row(java: dict, ghidra: dict) -> tuple[str, bool, str]:
    """Java 의 줄 — 고른 Ghidra 가 받는 JDK(그 폴더의 application.java.min · max — min 을 못 읽으면 JAVA_MIN_DEFAULT · max 가 없으면
    위는 열림)와 판의 맨 앞 수를 견준다. 판을 못 읽었으면 지금처럼 「있음」으로 둔다(Ghidra 가 받는 판을 읽었으면 그것을 붙인다).
    Windows 에서 JAVA_HOME 으로 줄 수 없는 java(PATH 의 대신 띄우는 파일 따위)는 REA 가 못 쓴다(2026-11-28 실측) — home 칸이 없는 예전
    꼴은 판으로만 본다."""
    read = (lambda key: ghidra.get(key) if ghidra.get("ok") and isinstance(ghidra.get(key), int) else None)
    known, top = read("java_min"), read("java_max")
    need = known or JAVA_MIN_DEFAULT
    span = f"{need}~{top} 를" if top else f"{need} 이상을"
    put = _PUT.format(JDK_FOLDER)
    no_native = " · 네이티브 실행 파일의 코드는 못 읽는다(고를 것)" + put
    if ghidra.get("ok"):
        wants = f"{'이 Ghidra(' + ghidra['version'] + ')' if ghidra.get('version') else '이 Ghidra '}는 JDK {span} 바란다"
    else:
        wants = f"Ghidra 는 JDK {need} 이상을 바란다({GHIDRA_LINE}.{GHIDRA_MIN_MINOR} 는 {GHIDRA_JAVA_MIN} 이상)"
    if not java.get("ok"):
        need_text = (f"이 Ghidra 는 JDK {need}~{top}" if top else f"이 Ghidra 는 JDK {need} 이상") if ghidra.get("ok") else (
            f"JDK 는 Ghidra 가 바라는 판 — {GHIDRA_LINE}.{GHIDRA_MIN_MINOR} 는 {GHIDRA_JAVA_MIN} 이상")
        return "Java", False, f"없음 — Ghidra 가 쓴다({need_text} · 고를 것)" + put
    mine = _MINE if java.get("private") else ""
    version = java.get("version") or ""
    major = java["major"] if isinstance(java.get("major"), int) else _java_major(version)
    shown = f"있음 {version}{mine}" if major is not None else f"있음(판은 읽지 못했다){mine}"
    if WINDOWS and "home" in java and not java["home"]:
        return "Java", False, f"{shown} — REA 는 Windows 에서 JAVA_HOME 의 JDK 만 쓴다(PATH 의 java 는 못 쓴다)" + no_native
    if major is None:
        return "Java", True, shown + (f" — {wants}" if known or top else "")
    if major < need or (top is not None and major > top):
        return "Java", False, f"{shown} — {wants}" + no_native
    return "Java", True, shown


def status_lines(data: dict | None = None) -> list[str]:
    """`gaf rea status` 와 `gaf doctor` 의 줄(한국어 · 한 줄씩)."""
    return [f"{name}: {text}" for name, _, text in status_rows(data)]
