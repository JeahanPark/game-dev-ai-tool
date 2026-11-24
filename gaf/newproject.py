"""프로그램 안에서 프로젝트를 더한다 — 고른 폴더를 검사하고, 첫 파일을 만들고, 이 PC의 목록에 적는다(1.8.0).

사용자 11-24 「안에 기능을 만들면안돼나?」 — 그 앞에는 설치 파일(설치.cmd)을 다시 돌려야 프로젝트가 늘었다. 검사는 처음 준비
(firstrun.setup)와 같이 쓴다: 전체 경로만 받고(지휘자의 작업 폴더에 기대지 않게), 드라이브 뿌리 · 사용자 집 폴더(와 그것을 품은
폴더)를 받지 않고, 프로젝트끼리 겹치게 만들지 않는다 — 폴더 선택 창에서 한 칸 위 · 아래를 잘못 고르면 프로젝트 둘이 같은 파일을
고친다. 지우는 것은 하나뿐이다: 첫 파일을 만들다 멈췄을 때 이번에 만든 것(다음에 「이미 준비된 폴더」로 잘못 보지 않게).
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
from pathlib import Path

from . import projects
from .paths import Paths

NAME_LIMIT = 60
PROGRAM = Path(__file__).resolve().parent.parent          # 이 프로그램을 깐 폴더(gaf 꾸러미의 윗폴더)
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", re.IGNORECASE)      # Windows 가 장치 이름으로 쓴다
_LONG, _LONG_UNC = "\\\\?\\", "\\\\?\\UNC\\"          # Windows 의 긴 경로 머리 — 떼고 본다(같은 폴더가 다른 글이 되지 않게)


class ProjectError(ValueError):
    """사람이 고칠 수 있는 까닭 — 글을 그대로 화면에 보인다."""


def _same_place(first: Path, second: Path) -> bool:
    """글은 달라도 운영체제가 같은 곳이라고 하나(공유 이름 · 긴 경로 머리로 가리킨 같은 폴더)."""
    try:
        return os.path.samefile(first, second)
    except (OSError, ValueError):
        return False


def folder_error(project: Path) -> str:
    """프로젝트로 쓸 수 없는 폴더면 까닭을, 되면 빈 글을 준다."""
    if project == Path(project.anchor):
        return "드라이브 뿌리는 프로젝트로 쓰지 않는다 — 게임 폴더를 고른다"
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None
    if home is not None:
        if project == home or _same_place(project, home):
            return "사용자 집 폴더는 프로젝트로 쓰지 않는다 — 그 안의 게임 폴더를 고른다"
        if project in home.parents:
            return "사용자 집 폴더를 품은 폴더는 프로젝트로 쓰지 않는다 — 게임 폴더를 고른다"
    if not project.is_dir():
        return "없는 폴더이거나 폴더가 아니다 — 이미 있는 게임 폴더를 고른다"
    return ""


def outer_project(folder: Path) -> Path | None:
    """그 폴더의 윗폴더 가운데 이미 프로젝트인 곳(gaf.json 이 있는 곳)."""
    for parent in folder.parents:
        if (parent / "gaf.json").is_file():
            return parent
    return None


def inner_project(folder: Path, listed=None) -> Path | None:
    """그 폴더 안의 프로젝트 — 목록에 적어 둔 것(몇 칸 아래든)과 바로 아래 칸의 것. listed 를 주면 목록 대신 그 폴더들을 본다."""
    roots = [entry["root"] for entry in projects.entries()] if listed is None else list(listed)
    for root in roots:
        try:
            resolved = Path(root).resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if folder in resolved.parents and (resolved / "gaf.json").is_file():
            return resolved
    try:
        with os.scandir(folder) as items:
            for item in items:
                if item.is_dir() and os.path.isfile(os.path.join(item.path, "gaf.json")):
                    return Path(item.path)
    except OSError:
        pass
    return None


def overlap_error(project: Path, listed=None) -> str:
    """새 프로젝트의 파일을 만들면 다른 프로젝트 · 이 프로그램과 겹치는 자리면 까닭을, 아니면 빈 글을 준다."""
    outer = outer_project(project)
    if outer is not None:
        return f"이 폴더는 이미 있는 프로젝트({outer})의 안쪽이다 — 그 프로젝트를 연다"
    # 프로그램을 깐 폴더(와 그 안)에 게임 프로젝트의 파일을 만들지 않는다 — 새 판을 덮어 풀 때 섞인다. 그 폴더가 이미 프로젝트면
    # (이 프레임워크를 만드는 저장소처럼 gaf.json 을 둔 곳) 위의 「안쪽」 검사가 본다.
    if (project == PROGRAM or PROGRAM in project.parents) and not (PROGRAM / "gaf.json").is_file():
        return "프로그램을 깐 폴더 안은 프로젝트로 쓰지 않는다 — 게임 폴더를 고른다"
    if project in PROGRAM.parents:
        return "프로그램을 깐 폴더가 이 안에 있다 — 그 폴더 말고 게임 폴더를 고른다"
    inner = inner_project(project, listed)
    if inner is not None:
        return f"이 폴더 안에 이미 프로젝트({inner})가 있다 — 그 프로젝트를 열거나 게임 폴더를 고른다"
    return ""


def place_error(project: Path, listed=None) -> str:
    """고른 폴더를 프로젝트로 쓸 수 없는 까닭(없으면 빈 글) — 처음 준비(설치)와 화면이 같이 쓴다.

    이미 준비된 폴더(gaf.json 이 있다)는 만드는 것이 없으니 겹침을 따지지 않는다."""
    problem = folder_error(project)
    if not problem and not (project / "gaf.json").exists():
        problem = overlap_error(project, listed)
    return problem


def _absolute(text: str, empty: str) -> Path:
    text = text.strip().strip('"')
    if not text:
        raise ProjectError(empty)
    if text.startswith(_LONG_UNC):
        text = "\\\\" + text[len(_LONG_UNC):]
    elif text.startswith(_LONG):
        text = text[len(_LONG):]
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ProjectError("폴더의 전체 경로를 적는다(예: C:\\Games\\내게임)")
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        raise ProjectError("폴더 경로를 읽지 못했다 — 다시 고른다") from None


def _name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ProjectError("프로젝트 이름을 적는다")
    if _BAD.search(name):
        raise ProjectError('이름에 쓸 수 없는 글자가 있다(\\ / : * ? " < > |)')
    if name.endswith((".", " ")) or set(name) <= {"."}:
        raise ProjectError("이름은 점이나 빈칸으로 끝날 수 없다")
    if len(name) > NAME_LIMIT:
        raise ProjectError(f"이름은 {NAME_LIMIT}자까지다")
    if _RESERVED.fullmatch(name):
        raise ProjectError(f"Windows 가 따로 쓰는 이름이다: {name}")
    return name


def base_folder(root) -> str:
    """새 폴더를 만들 기본 위치 — 지금 프로젝트의 윗폴더(그곳이 다른 프로젝트의 안쪽이면 빈 글: 사람이 고른다)."""
    try:
        parent = Path(root).resolve().parent
    except (OSError, RuntimeError, ValueError):
        return ""
    if (parent / "gaf.json").is_file() or outer_project(parent) is not None:
        return ""
    return str(parent)


def add(folder: str = "", parent: str = "", name: str = "", *, plan=None, init=None, listed=None) -> tuple[Path, bool]:
    """프로젝트를 더한다 → (프로젝트 뿌리, 이번에 첫 파일을 만들었나). 안 되는 값은 ProjectError.

    folder = 이미 있는 게임 폴더. parent + name = 그 위치에 새 폴더를 만들어 시작한다. 이미 준비된 폴더(gaf.json 이 있다 —
    팀이 git 으로 같이 쓰는 프로젝트)는 그대로 두고 목록에만 적는다. plan 은 Claude 구독 이름(총괄의 기본 모델을 고른다).
    listed 는 겹침을 볼 프로젝트 폴더들(안 주면 이 PC 의 목록).
    """
    fresh = bool(parent.strip() or name.strip())
    if fresh:
        place = _absolute(parent, "만들 위치를 고르거나 적는다")
        name = _name(name)
        if not place.is_dir():
            raise ProjectError("만들 위치가 없는 폴더다 — 이미 있는 폴더를 고른다")
        project = place / name
        if project.exists():
            raise ProjectError(f"이미 있는 폴더다: {project} — 「이미 있는 게임 폴더」로 고른다")
        problem = overlap_error(project, listed)
    else:
        project = _absolute(folder, "게임 폴더를 고르거나 적는다")
        problem = place_error(project, listed)
    if problem:
        raise ProjectError(problem)
    if fresh:
        try:
            project.mkdir()
        except OSError:
            raise ProjectError("폴더를 만들지 못했다 — 그 위치에 쓸 수 있는지 확인한다") from None
    paths = Paths(project)
    made = not paths.config.exists()
    if made:
        from .firstrun import lead_defaults          # 구독이 Max 아래면 총괄을 계정의 기본 모델로(설치 때와 같은 규칙)

        if init is None:
            from .cli import _init as init
        had_team, had_wiki = paths.team.exists(), (project / "wiki").exists()
        try:
            with contextlib.redirect_stdout(io.StringIO()):          # 첫 파일을 만들며 찍는 글은 터미널용이다
                code = init(paths, **lead_defaults(plan))
        except (OSError, ValueError):
            code = 1
        if code != 0:
            # 만들다 멈춘 것을 남기지 않는다 — gaf.json 만 남으면 다음에 「이미 준비된 폴더」로 보고 총괄 없는 프로젝트를 켠다.
            if fresh:
                shutil.rmtree(project, ignore_errors=True)          # 방금 이 손으로 만든 폴더다
            else:
                with contextlib.suppress(OSError):
                    paths.config.unlink()
                for created, there in ((paths.team, had_team), (project / "wiki", had_wiki)):
                    if not there:
                        shutil.rmtree(created, ignore_errors=True)
            raise ProjectError("프로젝트의 첫 파일을 만들지 못했다 — 폴더에 쓸 수 있는지 확인한다")
    try:
        from .config import load_config

        title = load_config(paths).project
    except (OSError, ValueError):
        raise ProjectError("gaf.json 을 읽지 못했다 — 파일을 고친 뒤 다시 한다") from None
    projects.remember(project, title)
    return project, made
