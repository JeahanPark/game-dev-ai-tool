"""꾸러미의 서랍을 읽고 필요한 글만 프로젝트로 꺼낸다(표준 라이브러리만)."""
from __future__ import annotations

import difflib
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).with_suffix("")
KINDS = {"roles": "역할", "procedures": "절차", "templates": "문서 틀", "rules": "규칙",
         "genres": "장르 시트", "engines": "엔진 참조"}
_KEYS = ("title", "summary", "group", "game_engine", "scope", "source")
# 목록에서 묶음(group)이 나오는 차례 — 사람이 일하는 흐름대로. 여기 없는 묶음은 뒤에 이름 차례로 온다.
GROUP_ORDER = ("방향", "시작", "기획", "구조", "구조와 계획", "프로그램", "아트", "아트와 소리", "소리", "글", "서사", "UX",
               "검증", "버그와 출시", "출시", "분야별 제작", "코드", "문서", "공통", "검토 질문", "장르 공통", "장르",
               "엔진 Unity", "엔진 Unreal", "엔진 Godot", "엔진 웹")
# 찾는 낱말을 바꿔 읽는다: 한글로 적은 엔진 이름(글의 제목 · id 는 영어 이름을 쓴다)과, 한 가족인데 끝 글자만 다른 장르 이름
# — 「로그라이크」로 찾으면 「액션 로그라이트」도 나와야 한다(11-16 진짜 총괄이 그렇게 찾고 못 찾았다).
FIND_ALIASES = {"유니티": "unity", "언리얼": "unreal", "고도": "godot", "고닷": "godot", "웹": "web",
                "로그라이크": "로그라이", "로그라이트": "로그라이", "roguelike": "rogueli", "roguelite": "rogueli"}
_ENGINE_WORDS = ("unity", "unreal", "godot", "web")
LIST_MAX = 80               # 목록 한 번에 보이는 줄 수 — 넘으면 자르고 몇 개가 더 있는지 알린다
_ID_PART = re.compile(r"[a-z0-9][a-z0-9._-]*")
_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?", re.I)


class LibraryError(ValueError):
    def __init__(self, message):
        super().__init__(" ".join(str(message).splitlines()))


def _parts(value, *, item=False) -> list[str]:
    if not isinstance(value, str) or not value or any(c in value for c in '\\:*?"<>|'):
        raise LibraryError("서랍 경로는 드라이브와 역슬래시 없는 상대 경로로 적는다")
    parts = value.split("/")
    if any(not p or p in (".", "..") or p.endswith((".", " ")) or _DEVICE.fullmatch(p)
           or any(ord(c) < 32 for c in p) or (item and not _ID_PART.fullmatch(p)) for p in parts):
        raise LibraryError("서랍 경로의 조각이 잘못됐다: 빈 조각 · .. · 끝 점/공백 · 예약 이름은 쓰지 않는다")
    return parts


def _inside(path: Path, root: Path) -> Path:
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError, RuntimeError) as exc:
        raise LibraryError("서랍 경로가 허용한 폴더 밖이다") from exc
    return resolved


def _kind(kind) -> Path:
    if not isinstance(kind, str) or kind not in KINDS:
        raise LibraryError("없는 서랍 칸이다: " + str(kind))
    return _inside(ROOT / kind, ROOT)


def _source(kind, item_id) -> Path:
    folder = _kind(kind)
    _parts(item_id, item=True)
    return _inside(folder / (item_id + ".md"), folder)


def role_id(value):
    """목록은 역할을 `roles/<id>` 로 찍는다 — 역할을 꺼낼 때 그 꼴 그대로 넣어도 받는다(11-16 진짜 총괄이 넷을 그렇게 넣어 거절됐다)."""
    return value[len("roles/"):] if isinstance(value, str) and value.startswith("roles/") else value


def split_item(item) -> tuple[str, str]:
    if not isinstance(item, str) or "/" not in item:
        raise LibraryError("서랍 글은 <칸>/<id> 로 적는다")
    kind, item_id = item.split("/", 1)
    _source(kind, item_id)
    return kind, item_id


def _parse(raw: bytes) -> tuple[dict, str]:
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise LibraryError("머리말의 첫 줄 --- 가 없다")
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "---"), None)
    if end is None:
        raise LibraryError("머리말을 닫는 --- 가 없다")
    meta = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            raise LibraryError("머리말은 키: 값 줄이어야 한다")
        meta[key.strip()] = value.strip()
    for key in ("title", "summary"):
        if not meta.get(key):
            raise LibraryError(f"머리말의 {key} 가 없다")
    return meta, "".join(lines[end + 1:])


def _paths(kind):
    folder = _kind(kind)
    if not folder.is_dir():
        return
    for path in sorted(folder.rglob("*.md")):
        if path.is_file() and not path.name.startswith("_"):
            yield path, path.relative_to(folder).as_posix()[:-3]


def _read(kind, item_id) -> tuple[bytes, dict, str]:
    path = _source(kind, item_id)
    try:
        raw = path.read_bytes()
        meta, body = _parse(raw)
    except FileNotFoundError as exc:
        matches = difflib.get_close_matches(item_id, [i for _, i in _paths(kind)], n=3)
        hint = " · 비슷한 id: " + ", ".join(matches) if matches else ""
        raise LibraryError(f"없는 서랍 글이다: {kind}/{item_id}{hint}") from exc
    except (OSError, UnicodeError, LibraryError) as exc:
        raise LibraryError(f"{kind}/{item_id}.md: {exc}") from exc
    return raw, meta, body


def load(kind, item_id) -> tuple[dict, str]:
    _, meta, body = _read(kind, item_id)
    return meta, body


def items(kind=None, *, find="", engine="") -> list[dict]:
    kinds = list(KINDS) if kind is None else [kind]
    if not isinstance(find, str) or not isinstance(engine, str):
        raise LibraryError("find 와 engine 은 글이어야 한다")
    engine = engine.strip().lower()
    engine = FIND_ALIASES.get(engine, engine)
    if engine and engine not in _ENGINE_WORDS:
        raise LibraryError("engine: " + " · ".join(_ENGINE_WORDS) + " 가운데 하나를 쓴다")
    result = []
    for section in kinds:
        rows = []
        for _, item_id in _paths(section):
            try:
                meta, _ = load(section, item_id)
            except LibraryError:
                continue
            row = {"kind": section, "id": item_id, **{key: meta.get(key, "") for key in _KEYS}}
            if engine and row["game_engine"] != engine:
                continue
            if not _found(row, find):
                continue
            rows.append(row)
        result.extend(sorted(rows, key=lambda r: (_group_rank(r["group"]), r["group"], r["title"], r["id"])))
    return result


def _group_rank(group: str) -> int:
    return GROUP_ORDER.index(group) if group in GROUP_ORDER else len(GROUP_ORDER)


def _found(row: dict, find: str) -> bool:
    """빈칸으로 나눈 낱말이 모두(id · title · summary · group 어디든) 들어 있는가. 대소문자는 가리지 않는다."""
    text = "\n".join(row[key] for key in ("id", "title", "summary", "group")).casefold()
    for word in find.casefold().split():
        word = FIND_ALIASES.get(word, word)
        if word in _ENGINE_WORDS:
            # 엔진 이름은 낱말의 머리에서만 맞춘다 — `unity` 가 `community` 에 걸리지 않게.
            if not re.search(r"(?<![a-z0-9])" + re.escape(word), text):
                return False
        elif word not in text:
            return False
    return True


def problems() -> list[str]:
    result = []
    for kind in KINDS:
        try:
            for path, item_id in _paths(kind):
                try:
                    load(kind, item_id)
                except LibraryError as exc:
                    result.append(f"{path}: {exc}")
        except (LibraryError, OSError) as exc:
            result.append(f"{ROOT / kind}: {exc}")
    return result


def default_take_folder(root) -> str:
    """서랍 글의 기본 자리. 위키 목록이 있을 때만 원본 폴더에 둔다."""
    return "wiki/raw/서랍" if (Path(root) / "wiki/index.md").is_file() else "docs/서랍"


def destination(root, kind, item_id, *, to=None) -> Path:
    """쓰기 전에 문이 검사할 경로. 파일이나 폴더를 만들지 않는다."""
    _source(kind, item_id)
    if kind == "roles":
        raise LibraryError("역할은 `role new --from` 으로 꺼낸다")
    if to is None:
        to = default_take_folder(root)
    parts = _parts(to)
    return _inside(Path(root).joinpath(*parts, kind, item_id + ".md"), Path(root))


def take(root, kind, item_id, *, to=None) -> Path:
    target = destination(root, kind, item_id, to=to)
    raw, _, _ = _read(kind, item_id)
    if target.exists():
        raise LibraryError(f"이미 있다: {target}")
    tail = f"\n<!-- 서랍 {kind}/{item_id} 에서 꺼냄 · 글 속의 「원문」은 머리말 source 의 글이다 · 출처와 라이선스: 프레임워크의 gaf/library/NOTICE.md -->\n"
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _inside(target, Path(root))
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".library-", delete=False) as out:
            temporary = Path(out.name)
            out.write(raw + tail.encode("utf-8"))
            out.flush()
            os.fsync(out.fileno())
        # 완성된 임시 파일을 원자적으로 게시한다. replace 와 달리 동시에 생긴 목적지도 덮지 않는다.
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise
        except OSError:
            # 하드링크가 없는 디스크(exFAT · 일부 네트워크 폴더): 「없을 때만 만든다」로 연다 — 있는 파일은 여전히 덮지 않는다.
            with open(target, "xb") as out:
                try:
                    out.write(raw + tail.encode("utf-8"))
                except OSError:
                    out.close()
                    target.unlink(missing_ok=True)      # 반쯤 쓴 글을 남기지 않는다
                    raise
    except FileExistsError as exc:
        raise LibraryError(f"이미 있다: {target}") from exc
    except OSError as exc:
        raise LibraryError(f"서랍 글을 꺼내지 못했다: {target} · {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def list_text(kind=None, *, find="", engine="", tools=False) -> str:
    rows = items(kind, find=find, engine=engine)
    counts = " · ".join(f"{k}({KINDS[k]}) {sum(r['kind'] == k for r in rows)}개"
                        for k in KINDS if kind is None or kind == k)
    lines = [counts] + [f"{r['kind']}/{r['id']}  {r['title']} [{r['group']}] — {r['summary']}" for r in rows[:LIST_MAX]]
    if len(rows) > LIST_MAX:
        fields = "library_list 의 kind · find" if tools else "--kind · --find"
        lines.append(f"… {len(rows) - LIST_MAX}개가 더 있다 — {fields} 로 좁힌다")
    if not rows:
        field = "library_list 의 kind" if tools else "--kind"
        lines.append(f"맞는 글이 없다 — 낱말을 줄이거나 {field} 만 주고 목록을 본다")
    return "\n".join(lines)


def show_text(item) -> str:
    meta, body = load(*split_item(item))
    if len(body) > 40000:
        body = body[:40000] + "\n(잘림 — 프로젝트에 꺼내서 나눠 읽는다)"
    head = [f"{key}: {meta[key]}" for key in ("title", "group", "summary", "scope", "game_engine", "source") if meta.get(key)]
    return "\n".join(head) + "\n\n" + body
