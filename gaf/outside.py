"""프로젝트 밖 쓰기의 승인(1.13.0 · 2026-11-25 사용자 「어 작업해」 — GPT 검토의 판정 「고쳐서 넣는다」).

역할의 `write` 에 프로젝트 뿌리 밖의 절대 경로 글롭을 적으면 그대로 열렸다 — git 으로 받은 역할 글 한 줄이, 총괄이 만든 역할의
`write` 한 줄이 이 PC 의 다른 폴더를 사람 모르게 열었다. 이제 밖 글롭은 **이 PC 에서 사람이 승인한 폴더 안일 때만** 적용한다.

- 승인 단위는 「실제로 열리는 폴더」(앵커)다: 글롭의 글자 부분(첫 `*` · `?` · `[` 앞까지의 폴더) · 파일 하나짜리 글롭은 그 윗폴더 —
  OS 샌드박스가 여는 폴더(scope.sandbox_dirs)와 같은 규칙이다. 링크를 끝까지 푼 진짜 자리로 본다(연결 이름으로 적어도 같다).
- 승인은 접근 값 폴더(paths.run_dir — 세션이 읽지도 쓰지도 못한다)의 `outside.json` 에 프로젝트 뿌리마다 적는다(hooks.json 과 같은 꼴).
- 드라이브 뿌리 · 공유 경로 · 집 폴더와 그 위 · 접근 값 폴더 · 엔진과 이 도구의 설정 폴더 · 이 프로그램을 깐 폴더 · 이 프로젝트 ·
  다른 프로젝트와 겹치는 곳 · 이름에 글롭 글자가 든 폴더는 승인해도 열지 않는다.

같은 계정으로 도는 셸을 가두는 장치는 아니다(Claude 엔진의 셸은 OS 로 못 좁힌다 — 설계 §7 의 한계 그대로). 사람이 모르는 사이
프로젝트 밖이 열리는 것을 막고, 무엇이 열려 있는지 보이게 하는 문이다.

이름(1.14.0 · 2026-11-26 사용자 「근데 이건 여러명이 사용하기위한 프레임워크잖아」): 역할 글은 여러 사람이 git 으로 같이 받는다 — 거기에 한 사람의
PC 경로를 적으면 다른 PC 에서 맞지 않는다. 그래서 역할 글의 write 에는 이름 참조 `{"folder": "이름", "glob": "하위/**"}` 를 적고(이름은
gaf.json 의 folders 에 선언 · 경로 없음 · 화면과 알림에는 `@이름/하위/**` 로 줄여 보인다), 이름 → 이 PC 의 진짜 폴더(짝)는 접근 값 폴더의
`outside-names.json` 에 프로젝트 뿌리마다 둔다. 짝은 사람이 이 PC 에서 고른다 — 그것이 그 폴더의 승인이다. 선언만으로는 아무것도 열리지 않는다.
참조를 글(`@이름/…`)이 아니라 객체로 적는 까닭: 1.13.0 이하의 판은 write 의 글을 모두 프로젝트 안의 경로로 읽는다 — 글로 적으면 옛 판이
프로젝트 안에 `@이름` 폴더를 만들어 거기에 쓴다. 객체는 옛 판이 「글 목록이어야 한다」로 읽기를 거절한다(11-26 GPT 검토 7절).
"""
from __future__ import annotations

import contextlib
import os
import re
import unicodedata
from pathlib import Path

from . import scope
from .paths import _atomic_json, _read_json, file_lock, run_dir, user_dir

FILE = "outside.json"
UNAPPROVED = "이 PC 에서 승인하지 않은 프로젝트 밖 폴더다"
SHARE = "공유 · 장치 경로는 프로젝트 밖 쓰기 폴더로 열 수 없다"
GLOBBY = "이름에 글롭 글자(* ? [)가 든 폴더는 열 수 없다"
NAMES_FILE = "outside-names.json"      # 이름 → 이 PC 의 진짜 폴더(짝). 승인 목록(outside.json)과 따로 둔다 — 옛 판(1.13.0)이 제 파일을 그대로 읽게
NAME_RULE = "이름은 한글 · 영문 · 숫자 · _ · - 로 1~30자다(첫 글자는 - 가 아니다)"
BAD_NAME = "이름 참조를 잘못 적었다"
_BAD_CHARS = '\\:"<>|[]'              # 이름 참조의 글롭에 못 쓰는 글자 — 구분자는 `/` 하나 · `[` 는 문과 쓰기 폴더 계산이 달리 읽는다
_DEVICE = re.compile(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?")      # Windows 의 장치 이름(확장자가 붙어도 장치다)
UNDECLARED = "gaf.json 의 folders 에 없는 이름이다"
UNPAIRED = "이 PC 에서 그 이름의 폴더를 아직 고르지 않았다"
ESCAPED = "짝지은 폴더 밖을 가리킨다(그 안의 연결이 밖으로 나간다)"
MOVED = "짝지은 폴더가 다른 곳을 가리키는 연결이 됐다 — 다시 고른다"
MISSING = "짝지은 폴더가 지금 없다(드라이브가 빠졌거나 폴더를 옮겼다)"
INWARD = "프로젝트 밖의 경로로 적었지만 연결을 따라가면 프로젝트 안이다 — 프로젝트 안은 상대 글롭으로 적는다"
NAMES_MAX, ABOUT_MAX = 32, 200         # 프로젝트 하나의 이름 수 · 설명 길이
_NAME = re.compile(r"[0-9A-Za-z가-힣_][0-9A-Za-z가-힣_-]{0,29}")      # 첫 글자는 `-` 가 아니다(명령줄의 옵션과 헷갈린다)
_DRIVE = re.compile(r"[A-Za-z]:")
_GLOB = "*?["


class OutsideError(ValueError):
    pass


def _posix(value) -> str:
    return str(value).replace("\\", "/")


def _key(text) -> str:
    """견줄 꼴 — 파일 시스템의 규칙으로 본다(Windows 는 대소문자를 가리지 않는다). casefold 가 아니다: `ß` 와 `ss` 는 다른 폴더다."""
    return os.path.normcase(_posix(text)).replace("\\", "/").rstrip("/")


def _share(text: str) -> bool:
    """공유(UNC) · 장치 경로 꼴인가. 이런 글은 디스크에서 보지 않는다 — 없는 서버를 찾느라 지휘자가 수십 초 멈추고(11-25 검수: 21초)
    Windows 가 그 서버에 인증을 보낸다(git 으로 받은 역할 글 한 줄이면 된다)."""
    return text.startswith("//")


def name_key(name) -> str:
    """이름을 견줄 꼴 — 정규화(NFC)하고 대소문자를 접는다. 풀어 쓴 한글 · 대소문자만 다른 이름은 같은 이름이다(파일 시스템마다 달리 보이지 않게)."""
    return unicodedata.normalize("NFC", str(name)).casefold()


def name_error(name) -> str:
    """프로젝트 밖 폴더의 이름으로 쓸 수 없는 까닭 → 쓸 수 있으면 빈 글."""
    return "" if isinstance(name, str) and _NAME.fullmatch(unicodedata.normalize("NFC", name)) else NAME_RULE


def ref_parts(ref) -> tuple[str, str]:
    """이름 참조 `{"folder": 이름, "glob": 글롭}` → (이름, 글롭). 잘못 적었으면 OutsideError.

    glob 을 빼면 그 폴더 전체(`**`)다. 글롭은 짝지은 폴더 아래의 상대 글롭이고 구분자는 `/` 하나다 — 빈 마디 · `.` · `..` · 역슬래시 ·
    드라이브와 ADS(`:`) · 집(`~`) · 제어 문자 · `"<>|` · 대괄호(문과 쓰기 폴더 계산이 달리 읽는다) · 끝이 점이나 공백인 마디 ·
    Windows 의 장치 이름은 받지 않는다. 와일드카드는 `*` · `?` · `**` 다."""
    if not isinstance(ref, dict) or not set(ref) <= {"folder", "glob"} or "folder" not in ref:
        raise OutsideError(f'{BAD_NAME}: {shown(ref)} — {{"folder": "이름", "glob": "하위/**"}} 꼴로 적는다')
    name, glob = ref["folder"], ref.get("glob", "**")
    if name_error(name):
        raise OutsideError(f"{BAD_NAME}: {shown(ref)} — {NAME_RULE}")
    if not isinstance(glob, str) or not glob:
        raise OutsideError(f"{BAD_NAME}: {shown(ref)} — glob 은 그 폴더 아래의 상대 글롭이다(빼면 폴더 전체)")
    for part in glob.split("/"):
        if (not part or part in (".", "..") or part.startswith("~") or part.endswith((".", " ")) or _DEVICE.fullmatch(part)
                or any(char in _BAD_CHARS or ord(char) < 32 or ord(char) == 127 for char in part)):
            raise OutsideError(f"{BAD_NAME}: {shown(ref)} — glob 에는 그 폴더 아래의 상대 글롭만 적는다(`/` 로 나누고 * ? ** 만 쓴다)")
    return unicodedata.normalize("NFC", name), glob


def shown(ref) -> str:
    """이름 참조를 화면 · 알림에 줄여 보이는 꼴(`@이름/글롭`). 잘못 적은 것은 적힌 그대로(JSON)."""
    if isinstance(ref, dict) and isinstance(ref.get("folder"), str) and isinstance(ref.get("glob", "**"), str) and set(ref) <= {"folder", "glob"}:
        return f"@{ref['folder']}/{ref.get('glob', '**')}"
    try:
        import json

        return json.dumps(ref, ensure_ascii=False)[:120]
    except (TypeError, ValueError):
        return str(ref)[:120]


def _split(pattern) -> tuple[str, str] | None:
    """절대 경로 글롭 → (글자 폴더, 나머지 글롭). 절대 글롭이 아니면 None(문이 절대 글롭으로 보는 기준 — Path.is_absolute 와 같다)."""
    if not isinstance(pattern, str) or not pattern.strip():
        return None
    text = scope.plain_glob(pattern)                 # 문과 같은 글을 본다 — `./C:/자료/**` 는 절대 글롭이다(11-26 Opus 검수)
    if not Path(text).is_absolute():
        return None
    text = os.path.normpath(text).replace("\\", "/")
    parts = text.split("/")
    first = 3 if _share(text) else 0                 # `//?/C:/…` 의 `?` · 서버 이름은 와일드카드로 읽지 않는다(공유 · 장치 경로로 닫는다)
    end = next((index for index, part in enumerate(parts) if index >= first and any(char in part for char in _GLOB)), None)
    if end is None:                                  # 와일드카드가 없다: 지금 있는 폴더면 그 폴더 · 아니면(파일 · 없는 것) 그 윗폴더
        end = len(parts) if not _share(text) and Path(text).is_dir() else len(parts) - 1
    folder = "/".join(parts[:end])
    if _DRIVE.fullmatch(folder) or not folder:
        folder += "/"
    return folder, "/".join(parts[end:])


def _folder(found: tuple[str, str], base: Path) -> str | None:
    """글자 폴더의 진짜 자리(링크를 푼 것). 프로젝트 뿌리 안이면 None — 밖이 아니다. 공유 · 장치 경로는 풀지 않고 적힌 그대로 돌려준다."""
    if _share(found[0]):
        return found[0].rstrip("/") or found[0]
    real = scope._canon(found[0], base)
    return None if scope._under(real, base)[0] else real


def anchor(pattern, root) -> str | None:
    """밖 절대 글롭이 실제로 여는 폴더(진짜 자리 · `/`). 안쪽 글롭 · 상대 글롭이면 None."""
    found = _split(pattern)
    return _folder(found, scope._root(root)) if found else None


def _real(path) -> str:
    try:
        return scope._plain(Path(path).resolve().as_posix())
    except (OSError, RuntimeError):
        return scope._plain(_posix(path))


def _run_places() -> list[str]:
    """접근 값 폴더의 자리들 — 적힌 자리와, 안의 파일을 풀었을 때 나오는 진짜 자리(Microsoft Store 파이썬은 AppData 를 딴 곳으로 돌린다)."""
    places = [scope._run_place()]
    try:
        child = next(iter(run_dir().iterdir()), None)
        if child is not None:
            real = scope._plain(child.resolve().parent.as_posix())
            if _key(real) != _key(places[0]):
                places.append(real)
    except OSError:
        pass
    return places


class Places:
    """못 여는 곳의 기준들 — 한 번 셈해 여러 글롭에 쓴다(글롭마다 집 폴더 · 접근 값 폴더 · 다른 프로젝트를 다시 풀지 않는다).

    rows = (자리, 까닭, 그 안쪽은 열 수 있는가). 차례가 까닭의 차례다."""

    def __init__(self, root, others=()):
        self.run = _run_places()
        self.rows: list[tuple[str, str, bool]] = [(scope._root(root).as_posix(), "이 프로젝트의 폴더를 품는 곳은 열 수 없다", False)]
        self.rows += [(place, RUN, False) for place in self.run]
        try:
            home = _real(Path.home())
        except (OSError, RuntimeError):
            home = ""
        if home:
            self.rows.append((home, "집 폴더와 그 위는 열 수 없다", True))
            self.rows += [(f"{home}/{name}", ENGINE, False) for name in (".codex", ".claude")]
        for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR"):          # 엔진의 설정 폴더를 다른 곳으로 옮겨 쓰는 PC
            value = os.environ.get(name, "").strip()
            if value and not _share(_posix(value)):
                self.rows.append((_real(value), ENGINE, False))
        self.rows.append((_real(user_dir()), "이 도구의 사용자 설정 폴더와 겹치는 곳은 열 수 없다", False))
        from .newproject import PROGRAM              # 꾸러미의 다른 모듈을 쓰는 모듈이라 여기서 늦게 가져온다

        self.rows.append((_real(PROGRAM), "이 프로그램을 깐 폴더와 겹치는 곳은 열 수 없다(세션이 문의 코드를 고칠 수 있다)", False))
        for other in others:
            place = Path(str(other))
            self.rows.append((_real(place), f"다른 프로젝트({place.name})와 겹치는 곳은 열 수 없다", False))


RUN = "접근 값 폴더(등록한 기기 · 믿음 · 승인을 두는 곳)와 겹치는 곳은 열 수 없다"
ENGINE = "엔진의 설정 폴더와 겹치는 곳은 열 수 없다"


def refusal(folder, root, *, others=(), places: Places | None = None) -> str:
    """그 폴더를 프로젝트 밖 쓰기 폴더로 열 수 없는 까닭 → 열 수 있으면 빈 글. 공유 · 장치 경로는 디스크를 보기 전에 거절한다."""
    text = _posix(folder)
    if _share(text):
        return SHARE
    text = text.rstrip("/")
    path = Path(text + "/") if (not text or _DRIVE.fullmatch(text)) else Path(text)
    if path.parent == path:
        return "드라이브 뿌리는 열 수 없다"
    if any(char in text for char in _GLOB):        # 쓰기 폴더 계산(scope.sandbox_dirs)이 `[` 도 와일드카드로 읽어 윗폴더가 열린다
        return GLOBBY
    places = places if places is not None else Places(root, others)
    key = _key(text) + "/"
    for place, why, inside_ok in places.rows:
        spot = _key(place) + "/"
        if spot.startswith(key) or (not inside_ok and key.startswith(spot)):
            return why
        if why == RUN and scope._reaches(text, place, search=True):       # 글자가 달라도 같은 곳일 수 있다(파일 번호로도 견준다)
            return why
    return ""


def exists(folder) -> bool:
    """그 폴더가 지금 있는가. 공유 · 장치 경로는 디스크에서 보지 않는다(없는 서버를 찾느라 멈춘다) — 없는 것으로 친다."""
    text = _posix(folder) if isinstance(folder, str) else ""
    try:
        return bool(text.strip()) and not _share(text) and Path(text).is_dir()
    except OSError:
        return False


def covered(folder, allowed) -> bool:
    """그 폴더가 승인한 폴더와 같은 곳이거나 그 아래인가(글자로 — 둘 다 진짜 자리다)."""
    key = _key(folder)
    return any(key == item or key.startswith(item + "/")
               for item in (_key(value) for value in allowed if isinstance(value, str) and value.strip()))


def apply(write, root, allowed, *, others=(), refs=(), names=(), pairs=None) -> tuple[list[str], list[dict]]:
    """역할의 write → (적용할 write, 닫은 것들). 밖 글롭은 승인한 폴더 안일 때만 남기고, 글자 부분을 진짜 자리로 바꿔 적는다.

    닫은 것 = {"pattern": 적은 글롭, "folder": 실제로 열릴 폴더, "why": 까닭, "refused": 승인해도 못 여는 곳인가}.
    이름 참조가 닫힌 줄에는 "name"(이름) · "state"(undeclared · unpaired · missing · invalid · refused · escaped)가 더 있다."""
    applied, closed, _ = resolve(write, root, allowed, others=others, refs=refs, names=names, pairs=pairs)
    return applied, closed


def resolve(write, root, allowed, *, others=(), places: Places | None = None, refs=(), names=(), pairs=None) -> tuple[list[str], list[dict], list[str]]:
    """apply 와 같은 판정 + 열린 밖 글롭의 폴더들 → (적용할 write, 닫은 것들, 열린 폴더들). 지휘자가 화면에 줄 표를 한 번에 만든다.

    refs = 이름 참조들(`{"folder", "glob"}`) · names = 선언한 이름들(gaf.json 의 folders) · pairs = 이름 → 이 PC 의 진짜 폴더. 이름 참조는 선언과
    짝이 다 있고 그 폴더가 지금 있을 때만 그 폴더 아래의 절대 글롭으로 바꿔 적용한다(적용할 write 의 끝에 붙는다) — 절대 글롭의
    승인(allowed)과는 따로다(짝지었다고 그 폴더를 가리키는 절대 글롭까지 열리지 않고, 승인했다고 이름이 열리지도 않는다)."""
    base = scope._root(root)
    applied: list[str] = []
    closed: list[dict] = []
    opened: list[str] = []
    for pattern in write:
        found = _split(pattern)
        folder = _folder(found, base) if found else None
        if folder is None:
            if found and not _share(found[0]) and not scope._under(os.path.normpath(found[0]).replace("\\", "/"), base)[0]:
                # 적힌 자리는 프로젝트 밖인데 연결을 따라가 보니 프로젝트 안이다 — 문은 글롭을 글자로 견줘 어차피 안 맞고, 쓰기 폴더 계산은
                # 그 연결을 OS 에 열어 역할의 범위보다 넓은 안쪽이 셸에 열린다(11-26 GPT 검토 4절). 안쪽은 상대 글롭으로 적는다.
                closed.append({"pattern": pattern, "folder": scope._canon(found[0], base), "why": INWARD, "refused": True})
                continue
            applied.append(pattern)
            continue
        if places is None and not _share(folder):   # 밖 글롭이 있을 때만 기준을 셈한다(안쪽 글롭만 가진 역할은 디스크를 더 보지 않는다)
            places = Places(root, others)
        why = refusal(folder, root, others=others, places=places)
        if why:
            closed.append({"pattern": pattern, "folder": folder, "why": why, "refused": True})
        elif covered(folder, allowed):
            applied.append(folder.rstrip("/") + ("/" + found[1] if found[1] else ""))
            if folder not in opened:
                opened.append(folder)
        else:
            closed.append({"pattern": pattern, "folder": folder, "why": UNAPPROVED, "refused": False})
    declared = {name_key(name) for name in (names or ())}
    paired = {name_key(name): folder for name, folder in (pairs or {}).items() if isinstance(folder, str) and folder.strip()}

    def shut(ref, name, state, why, folder="", refused=False) -> None:
        closed.append({"pattern": shown(ref), "folder": folder, "why": why, "refused": refused, "name": name, "state": state})

    for ref in refs or ():
        try:
            name, glob = ref_parts(ref)
        except OutsideError as error:
            shut(ref, "", "invalid", str(error), refused=True)
            continue
        key = name_key(name)
        if key not in declared:
            shut(ref, name, "undeclared", UNDECLARED)
            continue
        home = paired.get(key)
        if not home:
            shut(ref, name, "unpaired", UNPAIRED)
            continue
        home = home.replace("\\", "/").rstrip("/")
        if places is None and not _share(home) and Path(home).is_absolute():
            places = Places(root, others)
        state, why = pair_problem(home, root, others=others, places=places)
        if state:                                    # 짝 폴더 자체를 못 쓴다(못 여는 곳 · 없음 · 다른 곳으로 가는 연결) — 그 이름은 준비가 안 된 것이다
            shut(ref, name, state, why, home, state != "missing")
            continue
        found = _split(home + "/" + glob)
        real = scope._canon(found[0], base)          # 글자 부분의 연결을 푼 진짜 자리 — 프로젝트 안팎을 가르기 전에 늘 짝 폴더 안인지 본다
        if not covered(real, [home]):                # 이 줄만 닫는다(그 이름의 폴더는 멀쩡하다)
            shut(ref, name, "escaped", ESCAPED, real, True)
            continue
        why = refusal(real, root, others=others, places=places)
        if why:
            shut(ref, name, "barred", why, real, True)
            continue
        applied.append(real.rstrip("/") + ("/" + found[1] if found[1] else ""))
        if real not in opened:
            opened.append(real)
    return applied, closed, opened


def _root_key(root) -> str:
    return os.path.normcase(str(Path(root).resolve()))


def _rows(data, key: str) -> list[str]:
    rows = next((value for name, value in data.items() if os.path.normcase(name) == key), []) if isinstance(data, dict) else []
    return sorted({item for item in rows if isinstance(item, str) and item}) if isinstance(rows, list) else []


def approved(root) -> list[str]:
    """이 PC 가 그 프로젝트에 승인한 프로젝트 밖 폴더들(진짜 자리). 없거나 기록이 깨졌으면 빈 목록이다."""
    return _rows(_read_json(run_dir() / FILE), _root_key(root))


def _change(root, *, add=(), drop=()) -> list[str]:
    """승인 목록을 잠그고 고친다. drop = (적은 글, 견줄 꼴들) — 하나라도 목록에 없으면 아무것도 지우지 않는다(OutsideError)."""
    resolved = str(Path(root).resolve())
    key = os.path.normcase(resolved)
    target = run_dir() / FILE
    with file_lock(target):
        data = _read_json(target)
        data = data if isinstance(data, dict) else {}
        rows = {*_rows(data, key), *add}
        gone: set[str] = set()
        for text, forms in drop:
            hit = {row for row in rows if _key(row) in forms}
            if not hit:
                raise OutsideError(f"승인 목록에 없는 폴더다: {text}")
            gone |= hit
        rows -= gone
        data = {name: value for name, value in data.items() if os.path.normcase(name) != key}
        if rows:
            data[resolved] = sorted(rows)
        _atomic_json(target, data)
    return sorted(rows)


def _checked(root, folder, places: Places, *, verb="승인") -> str:
    """사람이 고른 프로젝트 밖 폴더 → 진짜 자리. 절대 경로가 아닌 것 · 프로젝트 안 · 못 여는 곳 · 파일이면 OutsideError(디스크를 보기 전에 가릴 것은 먼저)."""
    base = scope._root(root)
    text = _posix(folder).strip() if isinstance(folder, (str, Path)) else ""
    if not text or not Path(text).is_absolute():
        raise OutsideError(f"{verb}할 폴더는 절대 경로로 적는다")
    text = os.path.normpath(text).replace("\\", "/")
    for bad, why in ((_share(text), SHARE), (any(char in text for char in _GLOB), GLOBBY)):       # 디스크를 보기 전에 가린다
        if bad:
            raise OutsideError(f"{why}: {text}")
    real = scope._canon(text, base)
    if scope._under(real, base)[0]:
        raise OutsideError(f"프로젝트 안의 폴더는 {verb}할 것이 없다: {real}")
    why = refusal(real, root, places=places)
    if why:
        raise OutsideError(f"{why}: {real}")
    if Path(real).is_file():
        raise OutsideError(f"파일은 {verb}할 폴더가 아니다(그 윗폴더를 고른다): {real}")
    return real


def approve(root, folders, *, others=()) -> list[str]:
    """사람이 승인한 폴더를 적는다 → 그 프로젝트의 승인 목록. 못 여는 곳 · 절대 경로가 아닌 것 · 파일이 하나라도 있으면 아무것도 적지 않는다."""
    places = None
    clean = []
    for folder in folders:
        places = places or Places(root, others)
        clean.append(_checked(root, folder, places))
    return _change(root, add=clean)


def revoke(root, folders) -> list[str]:
    """승인을 거둔다 → 남은 승인 목록. 다르게 적어도(연결 이름 · `..` · 끝 구분자 · 대소문자) 같은 폴더면 지운다.

    목록에 없는 폴더가 하나라도 있으면 아무것도 지우지 않는다(OutsideError) — 안 지웠는데 「거뒀다」고 답하지 않게."""
    base = scope._root(root)
    wanted = []
    for folder in folders:
        text = _posix(folder).strip() if isinstance(folder, (str, Path)) else ""
        if not text:
            raise OutsideError("거둘 폴더를 적는다")
        forms = {_key(text)}
        plain = os.path.normpath(text).replace("\\", "/")
        if Path(text).is_absolute() and not _share(plain) and not any(char in plain for char in _GLOB):
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                forms.add(_key(scope._canon(text, base)))
        wanted.append((text, forms))
    return _change(root, drop=wanted)


# ---- 이름의 짝(이름 → 이 PC 의 진짜 폴더 · 1.14.0)

def _pair_rows(data, key: str) -> dict[str, str]:
    rows = next((value for name, value in data.items() if os.path.normcase(name) == key), {}) if isinstance(data, dict) else {}
    if not isinstance(rows, dict):
        return {}
    return {name: folder for name, folder in sorted(rows.items())
            if not name_error(name) and isinstance(folder, str) and folder.strip()}


def pairs(root) -> dict[str, str]:
    """이 PC 가 그 프로젝트의 이름마다 고른 진짜 폴더(이름 → 폴더). 없거나 기록이 깨졌으면 빈 사전이다."""
    return _pair_rows(_read_json(run_dir() / NAMES_FILE), _root_key(root))


def _pair_change(root, *, put=None, drop=()) -> dict[str, str]:
    """짝을 잠그고 고친다. drop 의 이름이 하나라도 없으면 아무것도 지우지 않는다(OutsideError). 이름은 견줄 꼴(name_key)로 찾는다."""
    resolved = str(Path(root).resolve())
    key = os.path.normcase(resolved)
    target = run_dir() / NAMES_FILE
    with file_lock(target):
        data = _read_json(target)
        data = data if isinstance(data, dict) else {}
        rows = _pair_rows(data, key)
        gone = {name_key(name) for name in drop}
        missing = [name for name in drop if name_key(name) not in {name_key(have) for have in rows}]
        if missing:
            raise OutsideError(f"짝이 없는 이름이다: {', '.join(str(name) for name in missing)}")
        fresh = {name_key(name) for name in (put or {})}
        rows = {name: folder for name, folder in rows.items() if name_key(name) not in gone | fresh}
        rows.update(put or {})
        data = {name: value for name, value in data.items() if os.path.normcase(name) != key}
        if rows:
            data[resolved] = dict(sorted(rows.items()))
        _atomic_json(target, data)
    return dict(sorted(rows.items()))


def check_pair(root, name, folder, *, others=(), places: Places | None = None) -> tuple[str, str]:
    """짝지을 수 있는가 → (이름, 진짜 폴더). 이름이 규칙에 어긋나거나 · 못 여는 곳이거나 · 지금 있는 폴더가 아니면 OutsideError. 적지는 않는다."""
    if name_error(name):
        raise OutsideError(f"{NAME_RULE}: {name!r}")
    real = _checked(root, folder, places or Places(root, others), verb="짝지을")
    if not Path(real).is_dir():                      # 없는 곳을 짝지으면 엔진마다 다르게 군다(만들거나 · 못 쓰거나) — 만들지도 다른 곳으로 돌리지도 않는다
        raise OutsideError(f"지금 있는 폴더만 짝지을 수 있다(없는 폴더다): {real}")
    return unicodedata.normalize("NFC", name), real


def pair_problem(folder, root, *, others=(), places: Places | None = None) -> tuple[str, str]:
    """짝지은 폴더를 지금 그 이름으로 쓸 수 없는 까닭 → (상태, 까닭) · 쓸 수 있으면 ("", ""). 상태는 refused · missing · moved.

    푸는 곳(resolve) · 화면의 이름 표 · 명령줄의 path 가 같은 판정을 쓴다 — 못 쓰는 짝의 경로를 어디서도 내주지 않게(11-26 Opus 검수)."""
    text = (_posix(folder) if isinstance(folder, (str, Path)) else "").strip().rstrip("/")
    if _share(text):
        return "refused", SHARE
    if not text or not Path(text).is_absolute():
        return "refused", "짝지은 폴더가 절대 경로가 아니다"
    why = refusal(text, root, others=others, places=places)
    if why:                                          # 짝지은 뒤에 못 여는 곳이 됐다(그 자리에 프로젝트가 생겼다 · 짝 파일을 손으로 고쳤다)
        return "refused", why
    if not Path(text).is_dir():                      # 드라이브가 빠졌다 · 폴더를 옮겼다 — 문에는 남고 OS 의 쓰기 폴더에서는 빠져 엔진마다 다르게 군다
        return "missing", MISSING
    if _key(scope._canon(text, scope._root(root))) != _key(text):       # 사람이 고른 것은 그때의 진짜 자리다
        return "moved", MOVED
    return "", ""


def pair_all(root, mapping, *, others=()) -> dict[str, str]:
    """이름 여럿의 폴더를 한 번에 고른다 → 그 프로젝트의 짝 전부. 다 가려 본 뒤 한 번에 적는다 — 하나라도 틀리면 아무것도 바꾸지 않는다."""
    if not isinstance(mapping, dict) or not mapping:
        raise OutsideError("짝지을 이름과 폴더를 적는다")
    places = Places(root, others)
    checked: dict[str, str] = {}
    for name, folder in mapping.items():
        name, real = check_pair(root, name, folder, places=places)
        if any(name_key(have) == name_key(name) for have in checked):
            raise OutsideError(f"같은 이름을 두 번 적었다: {name}")
        checked[name] = real
    return _pair_change(root, put=checked)


def pair(root, name, folder, *, others=()) -> dict[str, str]:
    """사람이 그 이름으로 쓸 이 PC 의 폴더를 고른다 → 그 프로젝트의 짝 전부. 그 폴더를 그 이름으로 여는 것의 승인이다(다시 고르면 바뀐다)."""
    return pair_all(root, {name: folder}, others=others)


def from_text(text) -> dict | None:
    """만들 때 적는 줄임 꼴 `@이름/글롭` → 이름 참조. `@` 로 시작하는 글이 아니면 None · 잘못 적었으면 OutsideError.

    역할을 만드는 길(총괄의 도구 · 화면 · 명령줄)에서만 받는다 — 화면에 보이는 꼴 그대로 적게. 역할 글에는 객체로 적힌다: 역할 글 안의
    글 `@…` 은 전처럼 프로젝트 안의 폴더고, 만들 때 프로젝트 안의 `@폴더` 를 적으려면 `./@폴더/**` 로 적는다."""
    if not isinstance(text, str) or not text.startswith("@"):
        return None
    name, _, glob = text[1:].partition("/")
    ref = {"folder": name, "glob": glob} if glob else {"folder": name}
    ref_parts(ref)
    return ref


def unpair(root, names) -> dict[str, str]:
    """짝을 푼다 → 남은 짝. 짝이 없는 이름이 하나라도 있으면 아무것도 풀지 않는다(OutsideError)."""
    names = list(names)
    if not names or not all(isinstance(name, str) and name.strip() for name in names):
        raise OutsideError("풀 이름을 적는다")
    return _pair_change(root, drop=names)
