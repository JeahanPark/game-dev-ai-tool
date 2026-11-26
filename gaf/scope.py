"""역할이 쓸 수 있는 경로와 셸 명령을 확인한다."""
from __future__ import annotations

import os
import re
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path, PurePath

from .paths import run_dir, user_dir

ALWAYS_DENY = (".git/**", ".gaf/**", ".claude/**", "**/.env*", "CLAUDE.md", "AGENTS.md",
               "gaf.json", "team/공통.md", "team/*/역할.md", "team/*/세션.json",
               "team/CUSTOM.md", "team/*/CUSTOM.md", "wiki/raw/**", "wiki/.obsidian/**",
               # 하위 폴더의 저장소 속 · 세션 설정(훅 · 권한)도 막는다
               "**/.git/**", "**/.claude/**")
# 사람의 접근 값. 세션이 읽으면 사람인 척 명령을 칠 수 있으므로 읽기 · 셸에서도 막는다.
# 값은 프로젝트 밖의 폴더(paths.run_dir)에 있다 — 뿌리를 뒤지는 도구가 읽어 오지 않게(11-12 QA 결함-21). 프로젝트 안의
# .gaf/server.json 은 읽기 도구도 막는다. Grep · Glob 은 .gaf 폴더와 글롭도, 셸은 bin · uploads 밖의 .gaf 경로도 막는다.
# 같은 PC · 같은 계정의 프로세스라 끝까지 막지는 못한다(실수로 읽는 것을 막는 문이다 — 설계 §7).
SECRET_FILE = ".gaf/server.json"
_GAF_IN_COMMAND = re.compile(r"\.gaf[\\/]+(?!(?:bin|uploads)(?:[\\/]|\b))", re.IGNORECASE)
_SECRET_IN_COMMAND = re.compile(r"server\.json", re.IGNORECASE)
_NAME_END = r"(?![\w.\-])"                        # 이름이 더 이어지지 않는 자리(`…/gaf-old` · `…/run.py` 는 다른 이름이다)
# 모르는 부분이 든 낱말은 기본 자리의 이름으로도 알아본다: `…/gaf/run` 과, 바로 윗폴더의 알려진 자리
# (`AppData/Local/gaf` · `%LOCALAPPDATA%\gaf` · `.local/state/gaf`). 다 풀린 낱말은 _names_run_folder 가 경로로 견준다.
_RUN_IN_COMMAND = re.compile(rf"[\\/]gaf[\\/]+run{_NAME_END}", re.IGNORECASE)
_RUN_HOME_IN_COMMAND = re.compile(rf"(?:appdata[\\/]+local|localappdata[%}}]?|\.local[\\/]+state)[\\/]+gaf{_NAME_END}",
                                  re.IGNORECASE)
SECRET_REASON = "접근 값 파일과 이 도구가 제한하는 지휘자 경로는 세션이 읽지 않는다 — 결과는 도구 `result`(없으면 셸 명령 `gaf result`), 올린 그림은 Read 로 본다"
# 세션의 표(GAF_SESSION)를 지우거나 비운 채 `gaf …` 를 치면 사람이 친 명령으로 통한다(11-12 QA 명령-019) — 그 이름이 든 셸 명령을 막는다.
# 알아볼 수 있는 꼴만 막는다(`env -i` · 스크립트로 지우는 것은 못 막는다 — 설계 §7 의 한계).
_SESSION_TOKEN = re.compile(r"GAF_SESSION", re.IGNORECASE)
SESSION_REASON = "세션의 표(GAF_SESSION)를 읽거나 지우거나 바꾸는 명령은 세션이 하지 않는다 — 누가 친 명령인지 지휘자가 그 표로 가린다"
# PC 를 사설망 · 인터넷에 여는 일(Tailscale 의 serve · funnel · 설정)은 사람이 한다(11-16 공개 길) — 세션이 스스로 넘김을 걸면
# 등록한 기기만 들이는 문은 그대로여도, 공개 문의 따로 둔 한도를 비켜 지휘자의 본 포트가 인터넷에 놓인다.
# 읽기만 하는 것(`tailscale status` · `ip` · `version`)은 그대로 둔다. 알아볼 수 있는 꼴만 막는다(스크립트 속의 호출은 못 막는다).
_TAILNET_COMMAND = re.compile(r"\btailscale(?:\.exe)?[\"']?\s+(?:funnel|serve|set|up|down|login|logout|cert|switch)\b",
                              re.IGNORECASE)
TAILNET_REASON = ("원격 접속(Tailscale 의 serve · funnel · 설정)은 세션이 켜고 끄지 않는다 — "
                  "사람이 PC 화면의 설정이나 `python -m gaf remote` 로 한다")
LOOSE_PATCH_REASON = ("이 세션의 작업 폴더는 프로젝트 뿌리가 아니다. "
                      "패치의 경로는 전체 경로({root}/…)로 적는다: {paths}")


# 세션이 쓰지 않는 도구(11-12 사용자 결정 「권장으로 해줘」): 예약 · 깨우기 · 클라우드 실행 · 여러 에이전트 돌리기.
# 깨우기와 예약은 지휘자가 한다 — 세션(총괄 역할 포함)이 스스로 걸면 지휘자 밖에서 일이 돌고 사용량을 쓴다.
# Claude 엔진은 이 이름들을 세션의 도구 목록에서도 뺀다(engines/claude.py). 문은 이름이 `Cron` 으로 시작하는 것까지 막는다.
TOOLS_DENIED = ("CronCreate", "CronDelete", "CronList", "ScheduleWakeup", "RemoteTrigger", "Workflow")
_TOOL_DENIED = re.compile(r"Cron\w*|ScheduleWakeup|RemoteTrigger|Workflow", re.IGNORECASE)
TOOLS_REASON = "이 도구는 세션이 쓰지 않는다 — 예약 · 깨우기 · 클라우드 실행 · 여러 에이전트 돌리기는 지휘자가 한다"
BACKGROUND_REASON = "배경으로 띄우지 않는다(run_in_background) — 일이 닫힌 뒤에도 도는 작업은 지휘자가 보지 못한다. 끝날 때까지 기다리는 꼴로 다시 부른다(총괄은 긴 명령을 도구 `job_run`(없으면 셸 명령 `gaf job run`)으로 띄운다)"


def _background(data) -> bool:
    # 11-12 QA 결함-27: 도구 이름과 상관없이 지휘자 밖에서 계속 도는 일을 막는다.
    value = data.get("run_in_background") if isinstance(data, dict) else None
    return value is True or value == 1 or (isinstance(value, str) and value.casefold() == "true")


def tool_denied(tool_name) -> bool:
    """세션이 쓰지 않는 도구인가(이름만 본다)."""
    return _TOOL_DENIED.fullmatch(str(tool_name or "")) is not None


def _case(text: str) -> str:
    return text.casefold() if os.name == "nt" else text


def plain_glob(pattern) -> str:
    """쓰기 글롭에서 뜻 없는 꼴을 걷는다 — 역슬래시는 `/` 로, 앞의 `./` 는 뗀다(여러 번).

    문 · 쓰기 폴더 계산 · 프로젝트 밖 승인(gaf/outside.py)이 같은 글을 본다: `./C:/자료/**` 를 한쪽은 절대 글롭으로, 다른 쪽은 상대 글롭으로
    읽으면 승인 없이 열린다(11-26 Opus 검수 — 1.13.0 도 쓰기 폴더 계산만 `./` 를 떼어 그 폴더를 OS 에 열었다)."""
    text = str(pattern).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def sandbox_dirs(globs, root) -> list[str] | None:
    """쓰기 글롭에서 OS 에 열 폴더를 고른다(디스크는 보지 않는다).

    뿌리 안은 상대 posix 경로, 밖은 절대 posix 경로다. 절대 경로는 Windows 의 드라이브 경로나
    다른 OS 의 `/` 경로만 받는다. 뿌리까지 열어야 하거나 드라이브 뿌리 · 모르는 꼴이면 None 이다.
    """
    base = str(root).replace("\\", "/").rstrip("/") if root else None
    folders: list[str] = []
    for pattern in globs:
        if not isinstance(pattern, str) or not pattern.strip():
            return None
        text = plain_glob(pattern)
        parts = text.split("/")
        if text.startswith(("//", "~")) or ".." in parts:
            return None
        drive = re.match(r"^[A-Za-z]:", text)
        absolute = bool(re.match(r"^[A-Za-z]:/", text)) if os.name == "nt" else text.startswith("/")
        if (drive or text.startswith("/")) and not absolute:
            return None
        if absolute and base is not None:
            if _case(text.rstrip("/")) == _case(base):
                return None
            if _case(text).startswith(_case(base) + "/"):
                text = text[len(base) + 1:]
                parts = text.split("/")
                absolute = False
        end = next((index for index, part in enumerate(parts) if any(char in part for char in "*?[")), len(parts) - 1)
        folder = "/".join(part for part in parts[:end] if part not in ("", "."))
        if absolute and os.name != "nt":
            folder = "/" + folder
        if not folder or folder == "/" or re.fullmatch(r"[A-Za-z]:/?", folder):
            return None
        key = _case(folder)
        if any(key == _case(known) or key.startswith(_case(known) + "/") for known in folders):
            continue
        folders = [known for known in folders if not _case(known).startswith(key + "/")]
        folders.append(folder)
    return folders or None


def _win_part(part: str) -> str:
    """Windows 가 파일 이름에서 떼어 내는 것을 미리 뗀다: 끝의 점 · 공백, `:` 뒤의 것(딸린 흐름 — `파일::$DATA`).

    아직 없는 파일은 resolve() 가 이름을 고쳐 주지 않는다 — `AGENTS.md.` 로 쓰면 늘 막는 곳을 지나서 `AGENTS.md` 가 생겼다(11-12 QA 결함-02).
    """
    return part.split(":", 1)[0].rstrip(". ")


# ---- 문이 보는 곳과 도구가 여는 곳이 같아야 한다(11-12 QA 결함-24: `~/…` 를 뿌리 아래의 `~` 폴더로 보고 통과시켰다).
# 11-12 이 PC 의 Claude Code 도구로 확인: Read · Grep · Glob(path) · Write 는 `~/…` 를 집 폴더로, Read · Grep 은 `/c/…` 를 `C:\…` 로
# 풀고, Read 는 `\\?\C:\…` · `\\.\C:\…` · `\\localhost\C$\…` 도 연다(`~\…` · `~이름/…` · `%이름%` · `$이름` 은 풀지 않는다).
# 엔진 · 도구마다 푸는 법이 달라, 읽을 수 있는 꼴(_spellings)을 다 본다 — 쓰기는 다 범위 안이어야 하고, 읽기는 하나라도 막는 곳이면 막는다.
_TILDE = re.compile(r"~(?=[\\/]|\Z)")                                         # `~` · `~/…` (낱말이 `~` 로 시작만 하는 것은 이름이다)
_MSYS_DRIVE = re.compile(r"[\\/](?:cygdrive[\\/])?([A-Za-z])(?=[\\/]|\Z)", re.IGNORECASE)   # Git Bash 의 `/c/…`
_DEVICE_HEAD = re.compile(r"[\\/]{2}[?.][\\/]")                               # `\\?\…` · `\\.\…`
_DEVICE_PLAIN = re.compile(r"[\\/]{2}[?.][\\/](?:(UNC)[\\/]|(?=[A-Za-z]:))", re.IGNORECASE)
_LOOPBACK_DRIVE = re.compile(r"^//(?:localhost|127\.0\.0\.1)/([A-Za-z])\$(?=/)", re.IGNORECASE)
DEVICE_REASON = "장치 경로 꼴(`\\\\?\\…` · `\\\\.\\…`)로는 쓰지 않는다 — 보통 경로로 적는다"


def _plain(text: str) -> str:
    """Windows 가 같은 곳으로 읽는 머리를 뗀다: `\\\\?\\C:\\…` → `C:\\…` · `\\\\?\\UNC\\서버\\…` → `\\\\서버\\…`(볼륨 이름 같은 다른 꼴은 둔다)."""
    if os.name != "nt":
        return text
    found = _DEVICE_PLAIN.match(text)
    if found is None:
        return text
    return ("//" if found.group(1) else "") + text[found.end():]


def _homes() -> list[str]:
    """`~` 가 가리킬 수 있는 집 폴더들 — 도구(Node) · PowerShell 은 USERPROFILE, Git Bash 는 HOME 을 쓴다. 서로 다르면 다 본다."""
    found = [os.path.expanduser("~"), os.environ.get("USERPROFILE"), os.environ.get("HOME")]
    if os.environ.get("HOMEPATH"):
        found.append((os.environ.get("HOMEDRIVE") or "") + os.environ["HOMEPATH"])
    return list(dict.fromkeys(item for item in found if item and item != "~"))


def _spellings(path) -> list[str]:
    """도구가 이 경로를 읽을 수 있는 꼴들(적힌 그대로가 먼저): `~/…` 는 집 폴더로도, Windows 의 `/c/…` 는 `C:/…` 로도."""
    text = str(path)
    found = [text]
    if _TILDE.match(text):
        rest = text[1:].lstrip("\\/")
        found += [os.path.join(home, rest) if rest else home for home in _homes()]
    if os.name == "nt":
        drive = _MSYS_DRIVE.match(text)
        if drive:
            found.append(f"{drive.group(1).upper()}:/" + text[drive.end():].lstrip("\\/"))
        # 자기 PC 의 관리 공유는 꺼져 있어도 같은 드라이브를 가리키는 이름이다.
        plain = _plain(text.replace("\\", "/"))
        local = _LOOPBACK_DRIVE.sub(lambda match: match.group(1) + ":", plain)
        if local != plain:
            found.append(local)
    return found


def _root(root) -> Path:
    return Path(str(root).replace("\\", "/")).resolve()


def _canon(path, base: Path) -> str:
    """경로 하나를 견줄 꼴로: 절대 경로로 · 링크와 `..` 를 풀고 · Windows 가 떼는 것(끝 점 · `:` 뒤 · `\\\\?\\`)을 떼고 · `/` 로."""
    candidate = Path(_plain(str(path).replace("\\", "/")))
    candidate = (candidate if candidate.is_absolute() else base / candidate).resolve()
    if os.name == "nt" and candidate.parts:
        parts = (_win_part(part) for part in candidate.parts[1:])
        candidate = Path(candidate.parts[0], *(part for part in parts if part))
    return _plain(candidate.as_posix())


def _readings(path, base: Path) -> list[str]:
    """도구가 이 경로로 열 수 있는 곳들(정리된 전체 경로 · 같은 것은 한 번)."""
    return list(dict.fromkeys(_canon(text, base) for text in _spellings(path)))


def _under(full: str, base: Path) -> tuple[bool, str]:
    """정리된 경로가 뿌리 아래인가 → (그런가, 뿌리 기준 상대 경로 · 밖이면 전체 경로)."""
    root = base.as_posix()
    if _case(full) == _case(root):
        return True, "."
    prefix = root if root.endswith("/") else root + "/"
    if _case(full).startswith(_case(prefix)):
        return True, full[len(prefix):]
    return False, full


def norm(path, root) -> tuple[bool, str]:
    """적힌 그대로의 경로를 뿌리 기준으로 → (뿌리 아래인가, 상대 경로 · 밖이면 전체 경로). 다른 꼴은 _readings 가 본다."""
    base = _root(root)
    return _under(_canon(path, base), base)


@lru_cache(maxsize=512)
def _glob_regex(pattern: str) -> re.Pattern:
    parts = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*" and pattern[index:index + 2] == "**":
            if pattern[index + 2:index + 3] == "/":
                parts.append("(?:.*/)?")
                index += 3
            else:
                parts.append(".*")
                index += 2
        elif char == "*":
            parts.append("[^/]*")
            index += 1
        elif char == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(char))
            index += 1
    return re.compile("^" + "".join(parts) + "$")


def glob_match(path_posix: str, pattern: str) -> bool:
    path = _case(path_posix.replace("\\", "/")).rstrip("/")
    pattern = _case(pattern.replace("\\", "/")).rstrip("/")
    if not any(char in pattern for char in "*?"):
        return path == pattern or path.startswith(pattern + "/")
    if pattern.endswith("/**") and path == pattern[:-3]:
        return True
    return _glob_regex(pattern).fullmatch(path) is not None


def can_write(path, globs, root, *, library_raw=False) -> tuple[bool, str]:
    """쓸 수 있는가. 도구가 이 경로를 읽을 수 있는 꼴마다 다 범위 안이어야 한다 — `~/x` 는 뿌리 아래의 `~/x` 로도, 집 폴더의 `x` 로도 본다."""
    if os.name == "nt" and _DEVICE_HEAD.match(str(path)):
        return False, DEVICE_REASON
    base = _root(root)
    for full in _readings(path, base):
        allowed, reason = _can_write_place(full, globs, base, library_raw=library_raw)
        if not allowed:
            return False, reason
    return True, ""


def _can_write_place(full: str, globs, base: Path, *, library_raw=False) -> tuple[bool, str]:
    if _same_place(full, _canon(user_dir() / "CUSTOM.md", base)):
        return False, "늘 막는 곳이다: Custom · 전체 프로젝트"
    # 접근 값 폴더는 쓰기 범위가 아무리 넓어도 쓰지 않는다(1.13.0 · 11-25 GPT 검토) — 읽기만 막고 쓰기는 열려 있어, 넓은 절대 글롭
    # 하나로 등록한 기기 목록 · 훅 믿음 · 프로젝트 밖 쓰기의 승인을 세션이 고칠 수 있었다. 글자로도 파일 번호로도 견준다(_reaches).
    if _reaches(full, _run_place(), search=False):
        return False, "늘 막는 곳이다: 접근 값 폴더(등록한 기기 · 믿음 · 승인을 두는 곳 — 사람만 고친다)"
    inside, relative = _under(full, base)
    # 절대 경로 비교에도 정리된 전체 경로를 쓴다.
    absolute = (base / relative).resolve().as_posix() if inside else relative
    for pattern in ALWAYS_DENY:
        # 지휘자의 서랍 복사만 예외다. 세션 도구는 이 값을 넘기지 않는다. 다른 금지와 쓰기 범위는 그대로 본다.
        if library_raw and pattern == "wiki/raw/**" and inside and glob_match(relative, "wiki/raw/서랍/**"):
            continue
        if glob_match(relative, pattern):
            return False, f"늘 막는 곳이다: {relative}"
    for pattern in globs:
        pattern = plain_glob(pattern)                # `./` 는 뜻 없는 앞머리다 — 쓰기 폴더 계산은 떼고 문은 안 떼어 둘이 어긋났다(11-26 GPT 검토)
        is_absolute = Path(pattern).is_absolute()
        if not inside and not is_absolute:
            continue
        if is_absolute:
            pattern = os.path.normpath(pattern).replace("\\", "/")
        if glob_match(absolute if is_absolute else relative, pattern):
            return True, ""
    return False, f"쓰기 범위 밖이다: {relative} (범위: {', '.join(globs)})"


# ---- git 을 부르는 꼴을 한 가지로 고친다 — `git -C . push` · `git.exe push` · `& 'git' push` 도 막는 정규식에 걸리게(11-12 QA 결함-01)
_GIT_WORD = re.compile(r"(?:.*[\\/])?git(?:\.(?:exe|cmd|bat|com))?", re.IGNORECASE | re.DOTALL)
# 값을 따로 받는 git 전역 옵션(`--git-dir=x` 처럼 붙여 쓰면 낱말 하나다). 나머지 `-…` 는 값이 없는 것으로 본다.
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
                                "--config-env", "--attr-source"})
_GIT_REST_WORDS, _GIT_WORD_CHARS, _GIT_DEPTH = 12, 200, 4       # 고친 글의 낱말 수 · 낱말 길이 · 속 글을 다시 푸는 깊이
_PARENS = re.compile(r"[()]")
_GROUP_MARKS = re.compile(r"[\"'()`]")
_QUOTES = re.compile(r"[\"']")
_ESCAPE = re.compile(r"\\(?!\r?\n)")                             # 줄 이음이 아닌 역슬래시


def _group_end(text: str, start: int) -> int:
    """`(` · 백틱으로 연 묶음을 닫는 글자의 자리(없으면 글의 끝). 괄호는 겹친 것을 센다."""
    if text[start] == "`":
        end = text.find("`", start + 1)
        return len(text) if end < 0 else end
    depth = 0
    for found in _PARENS.finditer(text, start):
        depth += 1 if found.group() == "(" else -1
        if depth == 0:
            return found.start()
    return len(text)


def _shell_words(command: str) -> tuple[list[list[str]], list[str]]:
    """셸 글을 명령 조각(`;` · `|` · `&` · 중괄호 · 꺾쇠 · 줄바꿈에서 나뉜다)마다 낱말들로 읽는다 → (조각들, 속 글들).

    따옴표는 벗긴다. 괄호 `( … )` · 백틱 `` ` … ` `` 으로 묶인 것은 낱말의 한 부분(자리 표시)으로 두고 그 안의 글은
    「속 글」로 따로 돌려준다 — `git -C $(pwd) push` 를 한 명령으로 읽으면서 묶음 안의 명령도 살피게.
    bash 와 PowerShell 을 한 가지로 대강 읽는다. 역슬래시는 글자로 둔다(Windows 경로) — 줄 끝의 것만 줄 이음이다.
    """
    text = re.sub(r"\\\r?\n", "", command)       # bash 의 줄 이음(`\` + 줄바꿈)
    text = re.sub(r"`\r?\n", " ", text)          # PowerShell 의 줄 이음(백틱 + 줄바꿈)
    segments: list[list[str]] = []
    nested: list[str] = []
    words: list[str] = []
    word: list[str] = []
    started, quote, index, size = False, "", 0, len(text)

    def end_word() -> None:
        nonlocal started
        if started:
            words.append("".join(word))
        word.clear()
        started = False

    while index < size:
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            elif quote == '"' and char in "\\`" and text[index + 1:index + 2] == '"':
                word.append('"')                 # 큰따옴표 안의 \" · `"
                index += 1
            else:
                word.append(char)
        elif char in "'\"":
            if word and word[-1] == "$":
                word.pop()                       # bash 의 $'…' · $"…" 는 따옴표와 같다(`$'git' push`)
            quote, started = char, True
        elif char in "(`":
            end = _group_end(text, index)
            if end > index + 1:
                nested.append(text[index + 1:end])
            word.append("()" if char == "(" else "``")
            started = True
            index = end
        elif char == "{" and text[index + 1:index + 2] == "}":
            word.append("{}")                    # `find -exec … {}` 의 자리 표시는 낱말이다
            started = True
            index += 1
        elif char in ";|&){}<>\n":
            end_word()
            if words:
                segments.append(words)
                words = []
        elif char.isspace():
            end_word()
        else:
            word.append(char)
            started = True
        index += 1
    end_word()
    if words:
        segments.append(words)
    return segments, nested


def git_calls(command: str, _depth: int = 0) -> list[str]:
    """명령 글에서 git 을 부르는 곳마다 `git <하위 명령> <인자…>` 꼴로 고쳐 돌려준다(같은 것은 한 번만).

    전역 옵션(`-C <폴더>` · `-c <이름=값>` · `--no-pager` …) · 따옴표 · `.exe` · 앞의 경로 · 줄 이음을 걷어 낸다.
    따옴표 안에 통째로 든 명령(`bash -c "git -C . push"`)과 묶음 안의 명령(`$(…)` · `(…)` · 백틱)도 다시 풀어 본다.
    변수 · 별칭 · 스크립트 · 다른 프로그램을 거쳐 부르는 것은 풀지 못한다(문은 알아볼 수 있는 꼴만 막는다 — 설계 §7).
    """
    if _depth >= _GIT_DEPTH:                      # 너무 깊다 — 따옴표 · 묶음을 헤쳐 한 번에 본다(더 들어가지 않는다)
        command = _GROUP_MARKS.sub(" ", command)
    segments, nested = _shell_words(command)
    calls: list[str] = []
    for words in segments:
        index = 0
        while index < len(words):
            word = words[index]
            index += 1
            if not _GIT_WORD.fullmatch(word):
                continue
            while index < len(words) and len(words[index]) > 1 and words[index].startswith("-"):
                index += 2 if words[index] in _GIT_VALUE_OPTIONS else 1
            rest = words[index:index + _GIT_REST_WORDS]
            if rest:
                calls.append("git " + " ".join(item[:_GIT_WORD_CHARS] for item in rest))
        nested.extend(word for word in words if any(char.isspace() for char in word))
    for text in nested:
        if "git" in text.casefold():
            calls.extend(git_calls(text, _depth + 1))
    return list(dict.fromkeys(calls))


def bash_denied(command: str, patterns: list[str]) -> str | None:
    """명령이 막는 정규식에 맞으면 그 정규식. 적힌 글 그대로와, git 을 부르는 곳을 한 가지 꼴로 고친 글(git_calls)을 다 본다."""
    if not patterns:
        return None
    texts = [command]
    calls = git_calls(command)
    if "\\" in command:                          # bash 는 낱자 앞의 역슬래시를 지운다(`g\it push`) — 뺀 글로도 한 번 더 읽는다
        calls += git_calls(_ESCAPE.sub("", command))
    if "'" in command or '"' in command:
        # 따옴표를 벗긴 글로도 읽는다 — PowerShell 이 배치 파일(.cmd)에 넘긴 인자는 cmd.exe 가 다시 읽어서,
        # 따옴표 안에 있던 `&` · 괄호가 명령을 잇는다(`gaf ask 'a&git' push` 가 `git push` 를 돌린다)
        calls += git_calls(_QUOTES.sub(" ", command))
    if calls:
        texts.append("\n".join(dict.fromkeys(calls)))
    return next((pattern for pattern in patterns
                 if any(re.search(pattern, text, re.IGNORECASE) for text in texts)), None)


# ---- `gaf assign|tell|ask|send` · `SendMessage` 한 번뿐인 명령 — 인자와 본문은 지휘자에 넘기는 글이다.
# 글로 치는 꼴은 좁게 잡는다: 조금이라도 다르면 명령 글을 통째로 살핀다(넘치게 막는 쪽이 새는 쪽보다 낫다).
_TEXT_HEAD = re.compile(r"[ \t]*(?:gaf[ \t]+(?:assign|tell|ask|send)|SendMessage)(?=[ \t]|\Z)", re.IGNORECASE)
_BARE_WORD = r"[\w\-=.:/]+"                       # 따옴표 없이 쓴 낱말: 글자 · 숫자 · _ - = . : /
# 따옴표 안에도 못 오는 글자 — 셸이 푸는 것(`$` · 백틱 · 역슬래시) · cmd.exe 가 푸는 것(`& | < > ^ % !` — PowerShell 은
# `gaf.cmd` 를 cmd.exe 로 돌린다) · 굽은 따옴표(PowerShell 은 따옴표로 읽는다) · 줄바꿈 · 제어 문자
_NOT_PLAIN = r"\\`$&|<>^%!\u2018-\u201f\x00-\x08\x0a-\x1f\x7f\x85\u2028\u2029"
_PLAIN_ARG = re.compile(rf"[ \t]+(?:{_BARE_WORD}|'[^'\"{_NOT_PLAIN}]*'|\"[^\"{_NOT_PLAIN}]*\")(?=[ \t]|\Z)")
_BODY_MARK = re.compile(r"[ \t]+<<(-?)[ \t]*(['\"]?)([A-Za-z_]\w*)\2(?=[ \t]|\Z)")


def _text_call(line: str) -> tuple[bool, re.Match | None]:
    """한 줄이 `gaf assign|tell|ask|send` · `SendMessage` 와 글자 그대로의 인자 · 본문 표시뿐인가.

    인자는 따옴표 없는 낱말이거나, 따옴표 하나로 통째로 싼 글이다(따옴표를 이어 붙인 꼴 · 낱말에 붙인 꼴은 아니다 —
    PowerShell 은 `"a""b"` 를 따옴표가 든 글 하나로 읽는다). 이어 붙인 명령(`;` `|` `&`) · 꺾쇠 · 묶음 · `$` · 백틱 ·
    역슬래시가 하나라도 있으면 아니다.
    """
    head = _TEXT_HEAD.match(line)
    if head is None:
        return False, None
    index, mark = head.end(), None
    while True:
        found = _PLAIN_ARG.match(line, index)
        if found is None and mark is None:
            found = mark = _BODY_MARK.match(line, index)
        if found is None:
            break
        word = found.group().strip(" \t")
        if found is not mark and word[0] in "'\"" and ("(" in word or ")" in word) and " " not in word and "\t" not in word:
            return False, None                   # 빈칸 없는 글은 따옴표 없이 cmd.exe 로 간다 — 괄호가 배치 파일의 묶음을 건드릴 수 있다
        index = found.end()
    return not line[index:].strip(" \t"), mark


def _body_end(lines: list[str], mark: re.Match) -> int | None:
    """본문(`<<표` 뒤의 줄들)을 끝내는 줄의 번호. 끝 표시가 없거나, 셸이 본문을 풀게 되어 있으면 None.

    표를 따옴표로 싸지 않으면(`<<GAF`) bash 가 본문의 `$( … )` · 백틱을 돌린다 — 그런 글자가 든 본문은 글로 치지 않는다.
    """
    dash, quoted, end = mark.group(1), mark.group(2), mark.group(3)
    for index, line in enumerate(lines[1:], 1):
        if (line.lstrip("\t") if dash else line) == end:
            return index
        if not quoted and ("$" in line or "`" in line):
            return None
    return None


def command_part(command: str) -> str:
    """접근 값 파일을 가리키는지 살필 부분. 글로 치는 명령과 `<<'표'` 한 덩이면 첫 줄만 돌려준다.

    그 본문은 지휘자에 넘기는 글(표준 입력)이지 셸이 돌리는 명령이 아니다. 첫 줄에 다른 명령이 이어지거나(`; bash <<'표'` 는
    본문을 셸이 돌린다), 끝 표시 뒤에 무엇이든 더 있으면 통째로 돌려준다.
    """
    lines = command.replace("\r\n", "\n").split("\n")
    pure, mark = _text_call(lines[0])
    if not pure or mark is None:
        return command
    end = _body_end(lines, mark)
    if end is None or any(part.strip() for part in lines[end + 1:]):
        return command
    return lines[0]


def text_only(command: str, tool_name: str = "Bash") -> bool:
    """`gaf assign|tell|ask|send` · `SendMessage` 한 번뿐이고, 인자와 본문을 셸이 풀거나 돌리지 않는가.

    그러면 문은 막는 정규식을 보지 않는다 — 맡기는 글의 「git commit 은 하지 마라」, 사람에게 묻는 글의 「git push 해도
    될까요?」는 명령이 아니다(커밋 · 푸시는 총괄이 사람에게 꼭 물어야 하는 것이다). 두 꼴만 알아본다:
      한 줄:  gaf ask "git push 해도 될까요?" --option "한다" --option "안 한다"      (bash · PowerShell)
      본문:   gaf assign 기획 "제목" - <<'GAF' … GAF                                 (bash 만 · 끝 표시 뒤에 아무것도 없을 때)
    """
    lines = command.replace("\r\n", "\n").split("\n")
    while len(lines) > 1 and not lines[-1].strip(" \t"):
        lines.pop()
    pure, mark = _text_call(lines[0])
    if not pure:
        return False
    if mark is None:
        return len(lines) == 1
    return tool_name == "Bash" and _body_end(lines, mark) == len(lines) - 1


def _run_place() -> str:
    """접근 값 폴더(프로젝트 밖 — paths.run_dir)의 정리된 경로(`/`)."""
    try:
        return _plain(run_dir().resolve().as_posix())
    except OSError:
        return run_dir().as_posix()


def _own_id(path) -> tuple[int, int] | None:
    """그 경로의 파일 번호(볼륨 · 파일). 없거나, 번호를 주지 않는 파일 시스템이면 None."""
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    return (info.st_dev, info.st_ino) if info.st_ino else None


def _ids_upward(path: str):
    """그 경로와 윗폴더들의 파일 번호(있는 것만 · 아래에서 위로)."""
    current = PurePath(path)
    for item in (current, *current.parents):
        found = _own_id(item)
        if found is not None:
            yield found


def _reaches(full: str, place: str, *, search: bool) -> bool:
    """정리된 경로(full)가 그곳(place) 안을 가리키는가. search(뒤지는 도구)면 그곳을 품은 윗폴더도 친다 — 뒤지다 닿는다.

    글자로 견주고, 있는 경로면 파일 번호로도 견준다 — 글자가 달라도 같은 곳일 수 있다(자기 PC 로 돌아 들어가는 공유
    `\\\\localhost\\C$\\…` · 이어 붙인 드라이브 · 정리하지 못한 꼴). 11-12 이 PC: 그 공유 꼴과 `C:\\…` 의 파일 번호가 같았다.
    """
    target, folder = _case(full).rstrip("/") + "/", _case(place).rstrip("/") + "/"
    if target.startswith(folder) or (search and folder.startswith(target)):
        return True
    wanted = _own_id(place)
    if wanted is None:
        return False
    if wanted in _ids_upward(full):
        return True
    if not search:
        return False
    mine = _own_id(full)
    return mine is not None and mine in _ids_upward(place)


def _same_place(full: str, place: str) -> bool:
    """정리된 두 경로가 같은 곳인가(글자로 · 있는 경로면 파일 번호로도)."""
    if _case(full).rstrip("/") == _case(place).rstrip("/"):
        return True
    mine = _own_id(full)
    return mine is not None and mine == _own_id(place)


_VARIABLE = re.compile(r"\$\{(?:env:)?(\w+)(?:[:+%#=\-][^{}]*)?\}|\$(?:env:)?(\w+)|%(\w+)%", re.IGNORECASE)
_SUBSTITUTION = re.compile(r"\$\([^()]*\)|`[^`\r\n]*`")
_SIMPLE_VALUE = re.compile(r"\s*(?:(?:echo\s+|printf\s+%s\s+))?(\$(?:env:)?\w+|\$\{(?:env:)?\w+\})\s*", re.IGNORECASE)
_TILDE_WORD = re.compile(r"(?<![\w~.\-\\/])~(?=[\\/\s;|&)\"']|\Z)")            # 낱말 머리의 `~`
_TEXT_QUOTES = re.compile(r"[\"']")
_SLASHES = re.compile(r"/{2,}")
_DRIVE_PLACE = re.compile(r"([A-Za-z]):/")
_EXPAND_COUNT, _EXPAND_VALUE = 256, 1024         # 값으로 바꾸는 횟수 · 바꿔 넣는 값의 길이(아주 긴 명령이 글을 부풀리지 못하게)


def _expanded(text: str) -> list[str]:
    """셸 글과, 그 글의 변수(`$이름` · `${이름}` · `$env:이름` · `%이름%`) · 낱말 머리의 `~` 를 값으로 바꾼 글들(적힌 그대로가 먼저).

    값은 지휘자의 환경변수에서 읽는다(세션은 그 환경을 물려받는다). 집 폴더 후보(_homes)가 여럿이면 후보마다 글 하나다.
    """
    if not any(mark in text for mark in "$%~`"):
        return [text]
    homes = _homes()

    def value(found: re.Match) -> str:
        name = found.group(1) or found.group(2) or found.group(3)
        known = os.environ.get(name)
        if known is None and name.upper() == "HOME" and homes:
            known = homes[0]                     # PowerShell 의 $HOME 은 환경변수가 없어도 있다
        return found.group() if known is None or len(known) > _EXPAND_VALUE else known

    # 11-12 QA 결함-25: 값을 그대로 찍는 묶음만 푼다. 나머지는 모르는 부분으로 남긴다.
    def substitution(found: re.Match) -> str:
        body = found.group()[2:-1] if found.group().startswith("$(") else found.group()[1:-1]
        simple = _SIMPLE_VALUE.fullmatch(body)
        return _VARIABLE.sub(value, simple.group(1)) if simple else found.group()

    filled = _SUBSTITUTION.sub(substitution, text, count=_EXPAND_COUNT)
    filled = _VARIABLE.sub(value, filled, count=_EXPAND_COUNT)
    found = [text, filled]
    if "~" in filled:
        found += [_TILDE_WORD.sub(lambda _found, home=home: home, filled, count=_EXPAND_COUNT) for home in homes]
    return list(dict.fromkeys(found))


@lru_cache(maxsize=16)
def _place_in_text(run: str) -> re.Pattern:
    """접근 값 폴더(와, 이름이 `gaf` 라 우리 것뿐인 바로 윗폴더)의 경로를 글에서 찾는 정규식. 글은 `/` 하나 · (Windows) 소문자로 고쳐 넘긴다.

    드라이브 경로는 세 꼴로 찾는다: `c:/…` · Git Bash 의 `/c/…` · 공유의 `c$/…`.
    """
    run = _SLASHES.sub("/", _case(run)).rstrip("/")
    parent = run.rpartition("/")[0]
    places = [parent if parent.rpartition("/")[2] == "gaf" else run]      # 윗폴더가 우리 것이면 그것 하나로 둘 다 걸린다
    for place in list(places):
        drive = _DRIVE_PLACE.match(place)
        if drive:
            rest = place[drive.end():]
            places += [f"/{drive.group(1)}/{rest}", f"{drive.group(1)}$/{rest}"]
    return re.compile("(?:" + "|".join(re.escape(place) for place in places) + ")" + _NAME_END)


_PATH_WORD = re.compile(r'''(?:\$\([^()]*\)|`[^`\r\n]*`|"[^"]*"|'[^']*'|[^\s;|&<>()"'`]+)+''')
_WORD_PIECE = re.compile(r"[^\s;|&<>()]+")
_BRACES = re.compile(r"(?<!\$)\{([^{}]*,[^{}]*)\}")
_BASH_ESCAPE = re.compile(r"\\(?:\r?\n|(.))", re.DOTALL)
_PS_ESCAPE = re.compile(r"`(?:\r?\n|(.))", re.DOTALL)
_PATH_COUNT, _BRACE_COUNT = 256, 64


def _brace_words(word: str) -> list[str]:
    """중괄호의 쉼표 갈래를 펼친다. 낱말 하나가 만드는 갈래와 푸는 횟수는 64까지다."""
    words = [word]
    for _ in range(_BRACE_COUNT):
        result, changed = [], False
        for item in words:
            found = _BRACES.search(item)
            if found is None:
                result.append(item)
            else:
                changed = True
                for choice in found.group(1).split(",", _BRACE_COUNT):
                    result.append(item[:found.start()] + choice + item[found.end():])
                    if len(result) == _BRACE_COUNT:
                        break
            if len(result) == _BRACE_COUNT:
                break
        words = result
        if not changed:
            break
    return words


def _glob_reaches(word: str, place: str, base: Path) -> bool:
    """글롭이 그 폴더 깊이까지 닿는가. 더 얕은 `Local/*` 는 지나가고 `**` 는 여러 조각으로 본다."""
    target = _case(place).rstrip("/").split("/")
    for spelling in _spellings(word):
        path = _plain(spelling.replace("\\", "/"))
        if not os.path.isabs(path):
            path = str(base / path)
        parts = _case(os.path.normpath(path).replace("\\", "/")).rstrip("/").split("/")
        # 각 조각을 읽고 나서 닿을 수 있는 목표의 깊이다. ** 의 갈래를 되풀이해서 헤매지 않는다.
        depths = {0}
        for index, part in enumerate(parts):
            reached = set()
            for depth in depths:
                if depth == len(target):
                    return True
                if part == "**":
                    reached.update(range(depth, len(target) + 1))
                elif (fnmatchcase(target[depth], part)
                      and not (part == "*" and index == len(parts) - 1 and depth + 1 == len(target))):
                    # `Local/*` 는 윗폴더의 목록뿐이다. 뒤의 경로 조각이나 ** 가 있어야 안으로 내려간다.
                    reached.add(depth + 1)
            depths = reached
            if not depths:
                break
        if len(target) in depths:
            return True
    return False


def _names_run_folder(text: str, root) -> bool:
    """셸 낱말마다 접근 값 폴더를 견준다. 모르는 부분과 한도를 넘은 낱말만 이름으로 본다.

    변수에 담아 잇기 · 스크립트 · cd 뒤의 상대 경로 · 더 윗폴더를 통째로 뒤지기는 못 본다(설계 §7 의 한계).
    """
    run, base = _run_place(), _root(root)
    parent = run.rpartition("/")[0]
    own_parent = parent if _case(parent.rpartition("/")[2]) == "gaf" else None
    place = _place_in_text(run)
    seen, checked = set(), set()

    def named(word: str) -> bool:
        return bool(_RUN_IN_COMMAND.search(word) or _RUN_HOME_IN_COMMAND.search(word)
                    or place.search(_SLASHES.sub("/", _case(word).replace("\\", "/"))))

    def forbidden(word: str) -> bool:
        if word in seen:
            return False
        seen.add(word)
        for branch in _brace_words(word):
            for candidate in _expanded(branch):
                if candidate in checked:
                    continue
                # 기본 자리(`AppData/Local/gaf` · `.local/state/gaf`)는 다 풀린 낱말에도 이름으로 본다 — 문이 풀지 못하는 꼴이 있고
                # (WSL 의 `/mnt/c/…`), 그 이름의 폴더는 우리 것뿐이라 넘치게 막을 일이 없다.
                if _RUN_HOME_IN_COMMAND.search(candidate):
                    return True
                # 11-12 QA 결함-26: 다 풀린 경로에는 `/gaf/run` 이름 규칙을 대지 않는다.
                unknown = bool(_VARIABLE.search(candidate) or "$(" in candidate or "`" in candidate)
                if unknown or len(checked) >= _PATH_COUNT:
                    if named(candidate):
                        return True
                    continue
                if not ("/" in candidate or "\\" in candidate or candidate.startswith(("~", "."))
                        or re.match(r"[A-Za-z]:", candidate)):
                    continue
                checked.add(candidate)
                if any(char in candidate for char in "*?["):
                    if _glob_reaches(candidate, own_parent or run, base):
                        return True
                else:
                    for full in _readings(candidate, base):
                        if (_reaches(full, run, search=False)
                                or (own_parent and _reaches(full, own_parent, search=False))):
                            return True
        return False

    # 11-12 QA 결함-25: 두 셸의 이스케이프 · 따옴표 묶음 · 글 속의 경로 조각을 모두 본다.
    variants = [text]
    if "\\" in text:
        variants.append(_BASH_ESCAPE.sub(lambda found: found.group(1) or "", text))
    if "`" in text:
        variants.append(_PS_ESCAPE.sub(lambda found: found.group(1) or "", text))
    for variant in dict.fromkeys(variants):
        for found in _PATH_WORD.finditer(variant):
            word = _TEXT_QUOTES.sub("", found.group())
            if forbidden(word) or ("=" in word and forbidden(word.split("=", 1)[1])):
                return True
        for found in _WORD_PIECE.finditer(_TEXT_QUOTES.sub("", variant)):
            word = found.group()
            if forbidden(word) or ("=" in word and forbidden(word.split("=", 1)[1])):
                return True
    return False


_CUSTOM_NAME = re.compile(r"custom\.md", re.IGNORECASE)


def _names_custom(text: str, root) -> bool:
    """셸에 드러난 Custom 경로를 막는다. 변수 · 따옴표 · 절대 경로도 같은 곳으로 견준다.

    낱말은 한 번씩만 보고, 경로로 풀어 견주는 것은 _PATH_COUNT 개까지다 — 넘으면 이름(CUSTOM.md)으로만 본다
    (_names_run_folder 와 같은 꼴 · 1MB 짜리 같은 낱말 되풀이가 2초 안에 끝나게 — 11-17 시험).
    """
    base = _root(root)
    user = _canon(user_dir() / "CUSTOM.md", base)
    seen, checked = set(), set()
    variants = [text, _BASH_ESCAPE.sub(lambda m: m.group(1) or "", text),
                _PS_ESCAPE.sub(lambda m: m.group(1) or "", text)]
    for variant in dict.fromkeys(variants):
        words = [_TEXT_QUOTES.sub("", m.group()) for m in _PATH_WORD.finditer(variant)]
        words += [m.group() for m in _WORD_PIECE.finditer(_TEXT_QUOTES.sub("", variant))]
        for word in words:
            if word in seen:
                continue
            seen.add(word)
            for branch in _brace_words(word.split("=", 1)[-1]):
                for candidate in _expanded(branch):
                    if candidate in checked:
                        continue
                    if len(checked) >= _PATH_COUNT:
                        if _CUSTOM_NAME.search(candidate):
                            return True
                        continue
                    checked.add(candidate)
                    for full in _readings(candidate, base):
                        _, relative = _under(full, base)
                        if (_same_place(full, user) or glob_match(relative, "team/CUSTOM.md")
                                or glob_match(relative, "team/*/CUSTOM.md")):
                            return True
    return False


def _state_glob(pattern: str) -> bool:
    """-g 로 .ignore 를 넘는 지휘자 폴더 글롭인가. 그림과 실행 부품은 그대로 읽는다."""
    # 11-12 QA 관찰: ripgrep 의 -g 가 .gaf/.ignore 보다 앞서서 기록을 읽는다.
    parts = pattern.replace("\\", "/").casefold().split("/")
    return any(part == ".gaf" and (index + 1 == len(parts) or parts[index + 1] not in ("bin", "uploads"))
               for index, part in enumerate(parts))


def reads_secret(tool_name, tool_input, root) -> bool:
    """읽기 도구 · 셸 명령이 접근 값 파일 · 지휘자 폴더를 가리키는가(알아볼 수 있는 모양만 본다)."""
    if tool_name in ("Read", "Grep", "Glob"):
        search = tool_name != "Read"
        values = [value for value in (tool_input.get(key) for key in ("file_path", "path"))
                  if isinstance(value, (str, Path)) and str(value)]
        if search and not values:
            values = ["."]                       # 뒤지는 도구는 경로를 안 주면 작업 폴더(뿌리)를 뒤진다 — 뿌리가 접근 값 폴더를 품었으면 막는다
        base = _root(root)
        run, state, secret = _run_place(), _canon(".gaf", base), _canon(SECRET_FILE, base)
        for value in values:
            for full in _readings(value, base):
                if (_reaches(full, run, search=search) or _same_place(full, secret)
                        or _reaches(full, _canon(".gaf/지시문", base), search=False)
                        or (search and _same_place(full, state))):
                    return True
        glob = tool_input.get("glob") if tool_name == "Grep" else tool_input.get("pattern") if tool_name == "Glob" else None
        return isinstance(glob, str) and bool(_SECRET_IN_COMMAND.search(glob) or _state_glob(glob)
                                              or _names_run_folder(glob, root))
    if tool_name in ("Bash", "PowerShell"):
        command = str(tool_input.get("command") or "")
        if tool_name == "Bash":
            command = command_part(command)
        return bool(_GAF_IN_COMMAND.search(command) or _names_run_folder(command, root)
                    or (_SECRET_IN_COMMAND.search(command) and ".gaf" in command.casefold()))
    return False


_PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.MULTILINE)
_SHELL_TEXT_KEYS = ("command", "cmd", "chars", "input", "script")


def _shell_texts(data) -> list[str]:
    """도구 입력에서 셸에 들어갈 글들(command · cmd · chars · input · script 가운데 빈 글이 아닌 것)."""
    if not isinstance(data, dict):
        return []
    return [data[key] for key in _SHELL_TEXT_KEYS if isinstance(data.get(key), str) and data[key].strip()]


def patch_paths(patch: str) -> list[str]:
    """apply_patch 글(`*** Begin Patch` … `*** End Patch`)이 건드리는 파일 경로(옮길 곳 포함 · 적힌 그대로)."""
    return [found.strip() for found in _PATCH_FILE.findall(str(patch or "").replace("\r\n", "\n")) if found.strip()]


def loose_patch_paths(tool_name, tool_input, cwd, root) -> list[str]:
    """뿌리와 다른 작업 폴더에서 쓴 패치의 상대 경로만 돌려준다. 집 폴더 꼴은 지금 문이 본다."""
    if tool_name != "apply_patch" or not cwd or not root:
        return []
    base = _root(root)
    if _case(_canon(cwd, base)) == _case(_canon(root, base)):
        return []
    data = tool_input if isinstance(tool_input, dict) else {}
    text = next((data[key] for key in ("command", "patch", "input") if isinstance(data.get(key), str)), "")
    return [path for path in patch_paths(text)
            if not path.startswith("~") and not Path(path.replace("\\", "/")).is_absolute()]


def hook_checks(tool_name, tool_input, cwd) -> list[tuple[str, dict]]:
    """Codex 의 PreToolUse 훅이 주는 도구 모양을 check_tool 이 아는 모양으로 바꾼다(11-12 실측 `probes/codex_probe.py hookblock`).

    Codex 는 셸을 `Bash`(안의 command 는 Windows 에서 PowerShell 글), 파일 고치기를 `apply_patch`(command = 패치 글)로 알린다.
    패치는 파일마다 Edit 하나로 푼다 — 경로를 못 읽으면 빈 경로를 돌려 check_tool 이 막게 한다.
    그 밖의 도구도 셸에 들어갈 글(열어 둔 셸에 써 넣는 글 등)이 있으면 셸 명령으로 살핀다.
    """
    name = str(tool_name or "")
    data = tool_input if isinstance(tool_input, dict) else {}
    if tool_denied(name) or _background(data):
        return [(name, data)]                     # 도구 이름이나 배경 실행 값을 check_tool 이 그대로 보고 막는다
    shell = "PowerShell" if os.name == "nt" else "Bash"
    if name == "apply_patch":
        text = next((data[key] for key in ("command", "patch", "input") if isinstance(data.get(key), str)), "")
        base = Path(str(cwd).replace("\\", "/"))
        paths = patch_paths(text)
        if not paths:
            return [("Edit", {"file_path": ""})]
        checks = [("Edit", {"file_path": path if Path(path.replace("\\", "/")).is_absolute() else str(base / path)})
                  for path in paths]
        # `~/…` 는 적힌 그대로도 한 번 더 본다 — 집 폴더로 푸는 도구가 있다(작업 폴더에 붙이면 그 꼴이 사라진다)
        return checks + [("Edit", {"file_path": path}) for path in paths if _TILDE.match(path)]
    if name in ("Bash", "PowerShell", "shell", "local_shell", "exec_command"):
        command = data.get("command")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        return [(shell, {"command": str(command or "")})]
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Read", "Grep", "Glob"):
        return [(name, data)]
    texts = _shell_texts(data)
    return [(shell, {"command": "\n".join(texts)})] if texts else []


def check_tool(tool_name, tool_input, *, write, root, bash_deny) -> tuple[bool, str]:
    if tool_denied(tool_name):
        return False, TOOLS_REASON
    if _background(tool_input):
        return False, BACKGROUND_REASON
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not isinstance(path, (str, Path)) or not str(path):
            return False, "쓸 파일 경로가 없다"
        return can_write(path, write, root)
    if tool_name not in ("Bash", "PowerShell", "Read", "Grep", "Glob"):
        # 이름을 모르는 도구 — 셸에 들어갈 글이 있으면 셸 명령으로 살핀다. Claude Code 의 Monitor 는 command 를 Bash 와 같은 셸에서
        # 돌린다(11-12: SDK 세션의 도구 목록에 있다) — 이 줄 전에는 `Monitor` 로 친 `git push` 가 문을 지났다.
        texts = _shell_texts(tool_input)
        if not texts:
            return True, ""
        tool_name, tool_input = "Bash", {"command": "\n".join(texts)}
    if reads_secret(tool_name, tool_input, root):
        return False, SECRET_REASON
    if tool_name in ("Bash", "PowerShell"):
        command = str(tool_input.get("command") or "")
        if text_only(command, tool_name):
            return True, ""                         # 지휘자에 넘기는 글뿐이다 — 글 안의 낱말로 막지 않는다
        if _names_custom(command, root):
            return False, "Custom 은 세션이 고치지 않는다 — 총괄의 custom 청을 사람이 승인하거나 화면에서 고친다"
        if _SESSION_TOKEN.search(command):
            return False, SESSION_REASON
        if _TAILNET_COMMAND.search(command):
            return False, TAILNET_REASON
        pattern = bash_denied(command, bash_deny)
        if pattern:
            hint = ""
            if re.match(r"\s*(?:gaf|SendMessage)\s", command, re.IGNORECASE):  # 맡기는 글 · 묻는 글의 낱말일 수 있다
                long_text = ("긴 글은 `-` 와 <<'GAF' … GAF(표준 입력)나 --file 로 넘긴다" if tool_name == "Bash"
                             else "긴 글은 파일에 써서 --file 로 넘긴다")
                hint = (" — 글 안의 낱말 때문이라면: 한 줄 글은 따옴표 하나로 싸고 그 안에 `$` · 백틱 · 역슬래시 · "
                        f"`& | < > ^ % !` 를 쓰지 않는다(그러면 글로 본다). {long_text}")
            return False, ("이 명령은 역할이 하지 않는다(커밋 · 푸시는 총괄을 거친다 · 고친 것을 버리거나 가지를 바꾸는 것은 "
                           f"사람이 한다): {pattern} — 총괄은 도구 `commit` · `push`(없으면 셸 명령 `gaf commit` · `gaf push`)로 사람의 승인을 청한다{hint}")
    return True, ""
