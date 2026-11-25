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
"""
from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path

from . import scope
from .paths import _atomic_json, _read_json, file_lock, run_dir, user_dir

FILE = "outside.json"
UNAPPROVED = "이 PC 에서 승인하지 않은 프로젝트 밖 폴더다"
SHARE = "공유 · 장치 경로는 프로젝트 밖 쓰기 폴더로 열 수 없다"
GLOBBY = "이름에 글롭 글자(* ? [)가 든 폴더는 열 수 없다"
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


def _split(pattern) -> tuple[str, str] | None:
    """절대 경로 글롭 → (글자 폴더, 나머지 글롭). 절대 글롭이 아니면 None(문이 절대 글롭으로 보는 기준 — Path.is_absolute 와 같다)."""
    if not isinstance(pattern, str) or not pattern.strip():
        return None
    text = _posix(pattern)
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


def covered(folder, allowed) -> bool:
    """그 폴더가 승인한 폴더와 같은 곳이거나 그 아래인가(글자로 — 둘 다 진짜 자리다)."""
    key = _key(folder)
    return any(key == item or key.startswith(item + "/")
               for item in (_key(value) for value in allowed if isinstance(value, str) and value.strip()))


def apply(write, root, allowed, *, others=()) -> tuple[list[str], list[dict]]:
    """역할의 write → (적용할 write, 닫은 것들). 밖 글롭은 승인한 폴더 안일 때만 남기고, 글자 부분을 진짜 자리로 바꿔 적는다.

    닫은 것 = {"pattern": 적은 글롭, "folder": 실제로 열릴 폴더, "why": 까닭, "refused": 승인해도 못 여는 곳인가}."""
    applied, closed, _ = resolve(write, root, allowed, others=others)
    return applied, closed


def resolve(write, root, allowed, *, others=(), places: Places | None = None) -> tuple[list[str], list[dict], list[str]]:
    """apply 와 같은 판정 + 열린 밖 글롭의 폴더들 → (적용할 write, 닫은 것들, 열린 폴더들). 지휘자가 화면에 줄 표를 한 번에 만든다."""
    base = scope._root(root)
    applied: list[str] = []
    closed: list[dict] = []
    opened: list[str] = []
    for pattern in write:
        found = _split(pattern)
        folder = _folder(found, base) if found else None
        if folder is None:
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


def approve(root, folders, *, others=()) -> list[str]:
    """사람이 승인한 폴더를 적는다 → 그 프로젝트의 승인 목록. 못 여는 곳 · 절대 경로가 아닌 것 · 파일이 하나라도 있으면 아무것도 적지 않는다."""
    base = scope._root(root)
    places = None
    clean = []
    for folder in folders:
        text = _posix(folder).strip() if isinstance(folder, (str, Path)) else ""
        if not text or not Path(text).is_absolute():
            raise OutsideError("승인할 폴더는 절대 경로로 적는다")
        text = os.path.normpath(text).replace("\\", "/")
        for bad, why in ((_share(text), SHARE), (any(char in text for char in _GLOB), GLOBBY)):       # 디스크를 보기 전에 가린다
            if bad:
                raise OutsideError(f"{why}: {text}")
        real = scope._canon(text, base)
        if scope._under(real, base)[0]:
            raise OutsideError(f"프로젝트 안의 폴더는 승인할 것이 없다: {real}")
        places = places or Places(root, others)
        why = refusal(real, root, places=places)
        if why:
            raise OutsideError(f"{why}: {real}")
        if Path(real).is_file():
            raise OutsideError(f"파일은 승인할 폴더가 아니다(그 윗폴더를 승인한다): {real}")
        clean.append(real)
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
