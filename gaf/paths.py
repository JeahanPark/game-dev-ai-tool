"""프로젝트 경로와 작은 파일 입출력 도우미."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def team(self) -> Path:
        return self.root / "team"

    @property
    def state_dir(self) -> Path:
        return self.root / ".gaf"

    @property
    def state(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def events(self) -> Path:
        return self.state_dir / "events.jsonl"

    @property
    def chat(self) -> Path:
        return self.state_dir / "chat.jsonl"

    @property
    def post(self) -> Path:
        return self.state_dir / "post.jsonl"

    @property
    def tasks_dir(self) -> Path:
        return self.state_dir / "tasks"

    @property
    def jobs_dir(self) -> Path:
        return self.state_dir / "jobs"

    @property
    def uploads(self) -> Path:
        return self.state_dir / "uploads"

    @property
    def server(self) -> Path:
        return self.state_dir / "server.json"

    @property
    def config(self) -> Path:
        return self.root / "gaf.json"

    def role_dir(self, name: str) -> Path:
        return self.team / name

    def ensure(self) -> None:
        for path in (self.state_dir, self.tasks_dir, self.uploads):
            path.mkdir(parents=True, exist_ok=True)
        # 낱말 찾기(ripgrep — Grep 도구 · rg)가 지휘자 폴더를 건너뛰게 한다. git 저장소가 아닌 뿌리에서는 .gitignore 가 듣지 않아,
        # 뿌리를 뒤지기만 해도 이 폴더의 기록(대화 · 일 · 이름표)이 모델의 문맥에 들어간다(11-12 QA 결함-21 · 24).
        # ripgrep 은 git 과 상관없이 `.ignore` 를 따른다(11-12 확인). 이미 있으면 두고, 못 써도 넘어간다.
        marker = self.state_dir / ".ignore"
        if not marker.exists():
            try:
                marker.write_text("*\n", encoding="utf-8", newline="\n")
            except OSError:
                pass


def user_dir() -> Path:
    """이 PC 사용자의 공통 설정 폴더. 접근 값을 두는 run_dir 과 별개다."""
    custom = os.environ.get("GAF_USER_DIR")
    return Path(custom) if custom else Path.home() / ".gaf"


def run_dir() -> Path:
    """떠 있는 지휘자의 접근 값 파일을 두는 폴더 — 프로젝트 폴더 밖이다(11-12 QA 결함-21).

    프로젝트 안(.gaf/)에 두면, 뿌리에서 낱말을 찾기만 해도(Grep · rg) 그 값이 모델의 문맥과 대화 기록에 들어간다 —
    git 저장소가 아닌 폴더에서는 .gitignore 도 막아 주지 않는다. 환경변수 GAF_RUN_DIR 로 바꿀 수 있다(시험이 쓴다).
    """
    custom = os.environ.get("GAF_RUN_DIR")
    if custom:
        return Path(custom)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "gaf" / "run"
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "gaf"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "gaf" / "run"


# ---- 자식 REA 의 임시 자리(1.17.1)
# 2026-11-28 이 PC 에서 잼(REA 6.10.1 + Ghidra 12.2 + JDK 25 · Windows): Ghidra 는 프로젝트 경로의 마디가 `.` 으로 시작하면 거절한다. 그래서
# REA(`dist/ghidra/GhidraSessionRoot.js`)는 os.tmpdir() 에 그런 마디가 있으면 Ghidra 실행 폴더의 자리를 옮긴다 — Windows 가 아니면 /tmp 로,
# Windows 에서는 그 마디가 없는 가장 가까운 윗폴더로. 1.17.0 은 자식의 TEMP 를 `<프로젝트>/.gaf/rea/<역할>/tmp/…` 로 주어 그 윗폴더가
# 프로젝트 뿌리였다 — 대상을 읽는 동안 뿌리에 `rea-ghidra-<32자 hex>/`(Ghidra 프로젝트 · 캐시 · 대상의 사본 · JVM 의 집과 임시 폴더 — 작은
# exe 하나에 53.8MB)가 생겼고, 나무째 죽이면 남을 수 있었다(꺼낸 것은 `.gaf/rea/` 에만이라는 약속이 깨졌다). TEMP 에 점 마디가 없으면 그
# 폴더는 TEMP 안에 생겼다(잼). REA 에는 이 자리를 따로 정하는 환경변수가 없다(`SESSION_ROOT = ghidraSessionRoot()` — TEMP 뿐).
# 그래서 Windows 에서는 자식의 TEMP 를 PC 의 임시 폴더 아래 `gaf-rea/<작업 폴더의 지문>/` 에 둔다(gaf/rea/host.py · 문은 gaf/scope.py).
REA_TEMP_FOLDER = "gaf-rea"
_WINDOWS = os.name == "nt"           # 시험이 바꾼다(os.name 자체를 바꾸면 pathlib 이 깨진다)


def rea_temp_root() -> Path | None:
    """Windows 에서 자식 REA 의 임시 폴더들을 두는 윗자리 `<PC 의 임시 폴더>/gaf-rea` — 경로만 셈한다(만들지 않는다).

    후보는 차례로 tempfile.gettempdir() → 환경변수 LOCALAPPDATA(온전한 경로일 때)의 `Temp`. 정리한(resolve) 경로에 `.` 으로 시작하는
    마디가 없는 첫 후보를 쓴다 — 있으면 REA 가 Ghidra 실행 폴더를 다시 그 윗폴더로 옮긴다. 둘 다 안 되면 None. Windows 가 아니면 None —
    거기서는 REA 가 스스로 /tmp 로 옮기므로 작업 폴더 안 tmp 를 그대로 쓴다."""
    if not _WINDOWS:
        return None
    candidates = []
    with contextlib.suppress(OSError):                # 쓸 수 있는 임시 폴더가 하나도 없으면 gettempdir 이 오류다
        candidates.append(tempfile.gettempdir())
    local = os.environ.get("LOCALAPPDATA")
    if local and Path(local).is_absolute():
        candidates.append(str(Path(local) / "Temp"))
    for candidate in candidates:
        try:
            folder = Path(candidate).resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if not any(part.startswith(".") for part in folder.parts[1:]):
            return folder / REA_TEMP_FOLDER
    return None


def rea_temp_home(work) -> Path | None:
    """그 REA 작업 폴더(`<뿌리>/.gaf/rea/<역할>`)의 자식이 쓰는 임시 자리 `rea_temp_root()/<지문>` — 경로만 셈한다(만들지 않는다).

    지문은 os.path.normcase(os.path.abspath(work)) 의 SHA-256 앞 16자 — 역할 이름에 한글 · 빈칸이 있어도 경로가 안전하고, 프로젝트 ·
    역할마다 따로다(Windows 에서는 대소문자 · 빗금만 다른 적기도 같은 자리). 윗자리가 없으면(None) None."""
    root = rea_temp_root()
    if root is None:
        return None
    key = os.path.normcase(os.path.abspath(work))
    return root / hashlib.sha256(key.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def find_root(start: Path | None = None) -> Path:
    start = Path(start if start is not None else Path.cwd()).resolve()
    folders = (start, *start.parents)
    for marker in ("gaf.json", "team", ".git"):
        for folder in folders:
            candidate = folder / marker
            if (candidate.is_file() if marker == "gaf.json" else
                    candidate.is_dir() if marker == "team" else candidate.exists()):
                return folder
    return start


APPEND_WAITS = (0.005, 0.01, 0.02, 0.04)      # 잠긴 파일을 다시 읽거나 덧붙여 보기 전에 쉬는 시간(초) — 다 합쳐 0.075초


def _replace(source: Path, target: Path, *, patient: bool = True) -> None:
    """바꿔치기. 누가 대상 파일을 쥐고 있으면(PermissionError) 조금씩 쉬며 6번까지 해 본다(다 합쳐 0.15초) — patient 가 False 면 한 번만."""
    last = 5 if patient else 0
    for attempt in range(last + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == last:
                raise
            time.sleep(0.01 * (attempt + 1))


def _atomic_text(path: Path, text: str, *, patient: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        _replace(temporary, path, patient=patient)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, data: dict, *, patient: bool = True) -> None:
    _atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", patient=patient)


def _os_lock(handle, take: bool) -> None:
    """열린 파일의 첫 바이트를 OS 잠금으로 쥐거나(take) 놓는다 — 못 쥐면 바로 OSError."""
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), (fcntl.LOCK_EX | fcntl.LOCK_NB) if take else fcntl.LOCK_UN)


@contextlib.contextmanager
def file_lock(path: Path, *, wait: float = 3.0):
    """파일 하나를 읽고-고치고-쓰는 동안 다른 프로세스 · 스레드가 끼어들지 못하게 한다 — 옆의 `<이름>.lock` 을 OS 잠금으로 쥔다.

    두 프로세스가 같은 목록을 같이 고치면 한쪽이 적은 것이 사라졌다(11-13 QA 결함-31). 쥔 프로세스가 죽으면 OS 가 잠금을 풀어
    주므로 남은 .lock 파일이 길을 막지 않는다. wait 초 안에 못 쥐면 TimeoutError(= OSError)다.
    """
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    handle = None
    try:
        while True:
            try:
                handle = lock.open("a+b")
                _os_lock(handle, True)
                break
            except OSError:
                if handle is not None:
                    handle.close()
                    handle = None
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"다른 프로그램이 쥐고 있다: {lock.name}") from None
                time.sleep(0.01)
        yield
    finally:
        if handle is not None:
            with contextlib.suppress(OSError):
                _os_lock(handle, False)
            handle.close()


def _no_constant(name: str) -> None:
    """JSON 에 없는 수(NaN · Infinity)는 「값 없음」으로 읽는다 — 그대로 두면 화면에 보낼 때 JSON 이 되지 않는다(11-12 QA 결함-15)."""
    return None


def _read_json(path: Path) -> dict | None:
    """JSON 객체 하나를 읽는다 → 없거나 깨졌으면 None.

    누가 파일을 쥐고 있거나 바꿔치기하는 순간이면(Windows 의 PermissionError) 조금 쉬었다 다시 읽는다(다 합쳐 0.075초) —
    한 번 못 열었다고 「없다」로 읽지 않게(11-12 QA 결함-14).
    """
    for attempt in range(len(APPEND_WAITS) + 1):
        try:
            data = json.loads(path.read_text(encoding="utf-8"), parse_constant=_no_constant)
            return data if isinstance(data, dict) else None
        except PermissionError:
            if attempt == len(APPEND_WAITS):
                return None
            time.sleep(APPEND_WAITS[attempt])
        except (OSError, ValueError, RecursionError):
            return None
    return None


def _json_lines(path: Path) -> Iterator[dict]:
    try:
        with path.open("rb") as stream:
            for line in stream:
                try:
                    value = json.loads(line, parse_constant=_no_constant)
                except (ValueError, UnicodeError, RecursionError):
                    continue
                if isinstance(value, dict):
                    yield value
    except FileNotFoundError:
        return


def _reverse_lines(path: Path) -> Iterator[bytes]:
    with path.open("rb") as stream:
        position = stream.seek(0, os.SEEK_END)
        tail = b""
        while position:
            size = min(position, 8192)
            position -= size
            stream.seek(position)
            lines = (stream.read(size) + tail).split(b"\n")
            tail = lines[0]
            yield from reversed(lines[1:])
        yield tail


def _last_record(path: Path, key: str) -> dict | None:
    try:
        for line in _reverse_lines(path):
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(record, dict) and type(record.get(key)) is int:
                return record
    except FileNotFoundError:
        pass
    return None


def _append_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 끝 줄이 끊겼으면 버린다. 완성된 줄에 줄바꿈만 없으면 이어 붙인다.
    if path.exists() and path.stat().st_size:
        with path.open("r+b") as stream:
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) != b"\n":
                tail = next(_reverse_lines(path))
                try:
                    json.loads(tail)
                except (ValueError, UnicodeError):
                    stream.truncate(path.stat().st_size - len(tail))
                else:
                    stream.seek(0, os.SEEK_END)
                    stream.write(b"\n")
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(line + "\n" for line in lines))


def _append_jsonl(path: Path, *records: dict[str, Any], patient: bool = True) -> None:
    """기록을 한 줄씩 덧붙인다(여러 개면 한 번에 쓴다).

    다른 프로그램이 파일을 쥐고 있으면(Windows 의 공유 위반 = PermissionError) 조금 쉬었다 다시 한다 — patient 가 False 면
    한 번만 해 본다. 그래도 안 되면 그 오류를 올린다. 올리면 안 되는 곳(이벤트 · 대화 · 우편)은 JsonlLog 로 쓴다.
    """
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    if not lines:
        return
    waits = APPEND_WAITS if patient else ()
    for attempt in range(len(waits) + 1):
        try:
            _append_lines(path, lines)
            return
        except PermissionError:
            if attempt == len(waits):
                raise
            time.sleep(waits[attempt])


class JsonlLog:
    """줄 단위 기록 파일 하나에 덧붙이는 손. 못 쓴 줄은 쥐고 있다가 다음에 쓸 때 먼저 쓴다(순서가 지켜진다).

    기록 파일이 잠겨도(백신 · 백업 · 다른 프로그램이 공유 없이 엶) 부르는 쪽이 죽지 않게 한다 — 11-12 QA 결함-13 · 04:
    이벤트 한 줄을 못 써서 일꾼이 죽고 일을 잃었다. 쥐고 있는 줄은 프로세스가 죽으면 사라지므로, 쓸 수 있게 되면 바로 쓴다.
    """
    KEEP = 5000                     # 쥐고 있는 줄의 한도(넘으면 오래된 것부터 버리고 센다)

    def __init__(self, path: Path) -> None:
        self.path = path
        self.pending: list[dict[str, Any]] = []
        self.dropped = 0            # 한도를 넘어 버린 줄 수(다시 쓰게 되면 0 으로)
        self.error = ""             # 지금 못 쓰는 까닭(다 쓰면 비운다)
        self.recovered: tuple[int, int] | None = None    # 막혔다 풀렸을 때 (한꺼번에 쓴 줄 수, 버린 줄 수) — 부르는 쪽이 읽고 비운다

    def append(self, record: dict[str, Any]) -> bool:
        json.dumps(record, ensure_ascii=False)       # 못 적을 값이면 여기서 바로 오류 — 쥔 줄에 섞여 뒤의 줄을 막지 않게
        self.pending.append(record)
        return self.flush()

    def flush(self, patient: bool | None = None) -> bool:
        """쥐고 있는 줄을 다 쓴다 → 다 썼으면 True. 이미 막혀 있던 파일은 한 번만 해 본다(쓸 때마다 쉬지 않는다).

        patient 를 True 로 주면 막혀 있던 파일도 쉬어 가며 다시 해 본다(끄기 전의 마지막 쓰기).
        """
        if not self.pending:
            return True
        held = len(self.pending)
        try:
            _append_jsonl(self.path, *self.pending, patient=(not self.error) if patient is None else patient)
        except OSError as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            extra = held - self.KEEP
            if extra > 0:
                del self.pending[:extra]
                self.dropped += extra
            return False
        self.pending.clear()
        if self.error:
            self.recovered = (held, self.dropped)
            self.error, self.dropped = "", 0
        return True
