"""조사 보고의 형식 검사 — 보고에 단 증거 번호 · 주소 · 따온 말이 증거 묶음에 있는지만 본다(망 없음).

보지 않는 것: 주장이 그 증거에서 따라오는지 · 「보충 출처」의 주소가 진짜인지 · 숫자의 뜻. 그래서 결과 줄에 늘
「내용은 검토하지 않았다」를 적는다 — 형식이 맞다는 것이 사실 확인은 아니다(11-27 GPT 검수).

보고와 증거는 세션이 쓸 수 있는 파일이다 — 믿지 않고 읽는다(11-27 다른 눈의 검수): 경로는 풀어 보기 전에 글로 먼저 거르고
(`//호스트/공유` 를 열면 Windows 가 그 호스트에 붙는다) · 크기에 상한을 두고 · 정규식은 줄 안에서만 돌고 · 꼴이 틀린 줄은 건너뛴다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import text

REQUIRED = (("결론", "한 줄 결론"), ("발견", "발견"), ("못 본 것", "약한 곳 · 못 본 것"))
QUOTES_MAX = 6
BRIEF_MAX = 1024 * 1024                 # 보고 글의 크기 상한(바이트)
EVIDENCE_MAX = 8 * 1024 * 1024          # 증거.json 의 크기 상한
_RUN_ID = re.compile(r"r-\d{8}-\d{6}-[0-9a-f]{4}")
_REFS = re.compile(r"\[(E\d{1,4}(?:[ \t]*[,·][ \t]*E\d{1,4}){0,20})\]")         # [E3] · [E1, E2] · [E1 · E2]
_URL = re.compile(r"https?://[^\s<>\"'）)\]]+")
_HEAD = re.compile(r"^-[ \t]*(조사 번호|증거)[ \t]*:[ \t]*([^\n]*)$", re.MULTILINE)      # 줄 안에서만 — 빈칸 · 빈 줄이 길어도 한 번에 지나간다
_MARK = re.compile(r"^\((?:추천|좋아요|조회)[^)\n]{0,20}\)[ \t]*")                 # 증거 글의 댓글 줄 머리 「(추천 13) 」


def is_brief(path) -> bool:
    """조사 보고인가 — `…/조사/<폴더>/보고.md`."""
    parts = PurePosixPath(str(path).replace("\\", "/")).parts
    return bool(parts) and parts[-1] == "보고.md" and "조사" in parts[:-1]


def _inside(root: Path, value) -> Path | None:
    """프로젝트 안의 경로면 그 자리를, 아니면 None — **풀어 보기 전에** 글로 거른다. 온전한 경로(드라이브 · `/` · `//호스트/공유`)는
    뿌리 아래일 때만 받는다. 그 뒤 풀어 본 자리(연결 포함)가 뿌리 안인지 다시 본다."""
    raw = str(value or "").strip().strip("`").strip()
    if not raw or any(ord(ch) < 32 for ch in raw):
        return None
    windows = PureWindowsPath(raw)
    if raw.startswith(("//", "\\\\")) or windows.drive.startswith("\\\\"):
        return None                                            # `//호스트/공유` — 열어 보지 않는다(여는 것만으로 그 호스트에 붙는다)
    if windows.drive or raw.startswith(("/", "\\")):
        if os.name == "nt" and not windows.drive:
            return None                                        # 드라이브 없는 뿌리(`/etc/…`)는 어느 드라이브인지 모른다
        try:
            relative = Path(os.path.abspath(raw)).relative_to(Path(os.path.abspath(root)))     # 글로만 견준다(디스크를 안 본다)
        except ValueError:
            return None
    else:
        relative = Path(raw)
    try:
        base = Path(root).resolve()
        full = (base / relative).resolve()
        full.relative_to(base)
    except (ValueError, OSError):
        return None
    return full


def _read_small(path: Path, limit: int) -> bytes | None:
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    return None if len(raw) > limit else raw


def _urls(body: str) -> list[str]:
    return [url.rstrip(".,;:!?」』") for url in _URL.findall(body)]


def _same(url: str) -> str:
    return url.rstrip("/").casefold()


def _quote(line: str) -> str:
    """따온 말 한 줄을 견줄 꼴로 — 인용 표시 · 따옴표 · 증거 글이 붙인 표시(「(추천 13) 」 · 잘린 쪽의 「…」)를 걷는다."""
    body = _MARK.sub("", line.lstrip("> ").strip())
    return text.norm(body.strip("\"'“”‘’「」『』 …").strip())


def _rows(data) -> dict:
    """증거의 줄을 번호로 — 꼴이 맞는 줄만(번호 · 주소가 글인 것). 세션이 고친 파일일 수 있다."""
    out = {}
    for row in data.get("items") if isinstance(data.get("items"), list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("eid"), str) or not isinstance(row.get("url"), str):
            continue
        comments = [item for item in row.get("comments") or [] if isinstance(item, dict)] if isinstance(row.get("comments"), list) else []
        highlights = [item for item in row.get("highlights") or [] if isinstance(item, str)] if isinstance(row.get("highlights"), list) else []
        out[row["eid"]] = {"url": row["url"], "title": row.get("title") if isinstance(row.get("title"), str) else "",
                           "text": row.get("text") if isinstance(row.get("text"), str) else "", "highlights": highlights,
                           "comments": [{"url": item.get("url") if isinstance(item.get("url"), str) else "",
                                         "text": item.get("text") if isinstance(item.get("text"), str) else ""} for item in comments]}
    return out


def check(root, brief_path) -> dict:
    root, brief_path = Path(root), Path(brief_path)
    result = {"path": brief_path.as_posix(), "ok": False, "errors": [], "notes": [], "refs": 0, "extra": 0}
    errors, notes = result["errors"], result["notes"]
    raw_brief = _read_small(brief_path, BRIEF_MAX)
    if raw_brief is None:
        errors.append(f"보고가 너무 크다({BRIEF_MAX // 1024:,}KB 까지 본다)")
        return result
    body = raw_brief.decode("utf-8")
    head = dict(_HEAD.findall(body))
    if not head.get("증거", "").strip():
        errors.append("머리에 「- 증거: <증거 파일의 경로>」 줄이 없다")
        return result
    target = _inside(root, head["증거"])
    if target is None:
        errors.append("증거 파일의 경로가 프로젝트 밖이다(프로젝트 폴더 기준의 경로로 적는다)")
        return result
    data_path = (target if target.is_dir() else target.parent) / "증거.json"
    try:
        raw = _read_small(data_path, EVIDENCE_MAX)
        data = json.loads(raw.decode("utf-8")) if raw is not None else None
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        errors.append(f"증거 파일을 읽지 못했다: {head['증거'].strip()[:200]}")
        return result
    items = _rows(data)
    if len(items) != len(data["items"]):
        errors.append("증거 파일의 줄 가운데 꼴이 틀린 것이 있다(프로그램이 받은 그대로가 아니다)")
    run_id = data.get("id")
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        errors.append("증거의 조사 번호가 꼴이 아니다(프로그램이 받은 그대로가 아니다)")
        return result
    if head.get("조사 번호", "").strip().strip("`") != run_id:
        errors.append(f"조사 번호가 증거의 것({run_id})과 다르다")
    record = _read(root / ".gaf" / "research" / f"{run_id}.json")
    if record is None:
        notes.append("이 PC 에 받은 기록이 없다 — 증거가 프로그램이 받은 그대로인지는 확인하지 못했다")
    elif record.get("sha256") != hashlib.sha256(raw).hexdigest():
        errors.append("증거 파일이 받은 뒤 바뀌었다(받은 때의 값과 다르다)")
    # 절을 가른다: 「보충 출처」는 역할이 직접 본 주소를 적는 곳 · 「쓰는 규칙」은 틀의 안내(보기로 적은 `[E3]` · 주소가 있다).
    # 그 둘은 꼭 있어야 할 절로 세지 않는다 — 「발견과 보충 출처」 같은 이름으로 두 몫을 하게 두지 않는다.
    main, extra, headings = [], [], []
    bucket = main
    for line in body.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            bucket = extra if "보충 출처" in name else [] if "쓰는 규칙" in name else main
            if bucket is main:
                headings.append(name)
        if line.startswith("> **쓰는 법**"):
            continue                              # 틀 맨 위의 안내 칸 — 따온 말이 아니다
        bucket.append(line)
    for key, name in REQUIRED:
        if not any(key in heading for heading in headings):
            errors.append(f"「{name}」 절이 없다")
    main_text, extra_text = "\n".join(main), "\n".join(extra)

    def refs(body_text: str) -> set[str]:
        return {name for group in _REFS.findall(body_text) for name in re.findall(r"E\d{1,4}", group)}
    used = sorted(refs(main_text), key=lambda name: int(name[1:]))
    result["refs"] = len(used)
    missing = sorted({name for name in refs(main_text) | refs(extra_text) if name not in items}, key=lambda name: int(name[1:]))
    if missing:
        errors.append(f"증거에 없는 번호다: {' · '.join(missing[:10])}")
    if data.get("state") == "nothing-solid":
        if "찾지 못함" not in body:
            errors.append("증거가 「찾지 못함」이다 — 보고에도 「찾지 못함」이라고 쓴다")
    elif not used:
        errors.append("증거 번호(`[E3]` 꼴)가 하나도 없다 — 근거가 있는 주장 옆에 단다")
    known = {_same(row["url"]) for row in items.values()}
    known |= {_same(comment["url"]) for row in items.values() for comment in row["comments"] if comment["url"]}
    extra_urls = {_same(url) for url in _urls(extra_text)}
    result["extra"] = len(extra_urls)
    strangers = sorted({url for url in _urls(main_text) if _same(url) not in known | extra_urls})
    if strangers:
        errors.append("증거에도 「보충 출처」에도 없는 주소다: " + " · ".join(strangers[:5]))
    corpus = text.norm("\n".join(part for row in items.values() for part in (
        row["title"], row["text"], *row["highlights"], *(comment["text"] for comment in row["comments"]))))
    quotes = [quote for quote in (_quote(line) for line in main if line.startswith(">")) if quote]
    loose = [quote for quote in quotes if quote not in corpus]
    if loose:
        errors.append(f"따온 말이 증거에 그대로 없다: 「{loose[0][:60]}」" + (f" 외 {len(loose) - 1}개" if len(loose) > 1 else ""))
    if len(quotes) > QUOTES_MAX:
        notes.append(f"따온 말이 {len(quotes)}개다(여섯 개까지가 좋다)")
    result["ok"] = not errors
    return result


def _read(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def line(result: dict) -> str:
    """일 결과의 머리 · 명령줄에 찍는 한 줄. 무엇을 보지 않았는지를 늘 밝힌다."""
    tail = "".join(f" · {note}" for note in result["notes"])
    if result["ok"]:
        return (f"조사 보고 형식: 맞음 — 증거 번호 {result['refs']}개 · 보충 출처 {result['extra']}개(확인 안 함){tail} · "
                f"주장이 증거에서 따라오는지 같은 내용은 검토하지 않았다({result['path']})")
    return f"조사 보고 형식: 맞지 않음 — {' / '.join(result['errors'][:5])}{tail}({result['path']})"


def lines(root, paths) -> list[str]:
    """일이 쓴 파일 가운데 조사 보고만 골라 본다 — 줄마다 보고 하나. 프로젝트 밖을 가리키는 경로는 열어 보지 않고, 검사가 죽어도
    일은 이어지게 한 줄로 알린다(세션이 쓴 글을 읽는 자리다)."""
    out, seen = [], set()
    for path in paths:
        shown = str(path).replace("\\", "/")
        if not is_brief(shown):
            continue
        full = _inside(Path(root), path)
        if full is None or full in seen:
            continue
        seen.add(full)
        try:
            shown = full.relative_to(Path(root).resolve()).as_posix()
            out.append(line({**check(root, full), "path": shown}))
        except Exception as error:  # noqa: BLE001 — 형식 검사는 알리기만 한다. 여기서 죽으면 그 일의 결과 글이 사라진다.
            out.append(f"조사 보고 형식: 못 읽었다 — {type(error).__name__}({shown})")
    return out
