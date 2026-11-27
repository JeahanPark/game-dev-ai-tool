"""분석 보고(역분석가가 쓴 `…/분석/<대상>/보고.md`)의 형식 검사 — 단 증거 번호가 내보낸 REA 증거 묶음에 있는지만 본다(망 없음).

보지 않는 것: 주장이 그 증거에서 따라오는지 · 증거 묶음이 REA 가 낸 그대로인지 · 무엇을 옮겨 적었는지. 그래서 결과 줄에 늘
「내용이 옳은지는 검토하지 않았다」를 적는다. 틀: 서랍의 templates/teardown-brief.md.

보고와 증거 묶음은 세션이 쓸 수 있는 파일이다 — 조사 보고의 검사(gaf/research/check.py)와 같이 믿지 않고 읽는다: 경로는 풀어 보기
전에 글로 먼저 거르고(같은 `_inside` 를 쓴다) · 크기에 상한을 두고 · 정규식은 줄 안에서만 돌고 · lines() 는 죽지 않는다.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath

from ..research.check import _CODE, _inside, _read_small

REQUIRED = (("한 줄 요약", "한 줄 요약"), ("구조", "구조"), ("배울 것", "배울 것"), ("못 본 것", "모르는 것 · 못 본 것"))
UNSEEN = ("못 봄", "보지 못했다")            # 증거 번호가 하나도 없어도 되는 보고(못 본 것을 밝혔다)
BRIEF_MAX = 1024 * 1024                    # 보고 글의 크기 상한(바이트)
EXACT_MAX = 64 * 1024 * 1024               # 이 크기까지는 통째로 풀어 본다(JSON 인지 · 맨 위 records 의 번호만)
BUNDLE_MAX = 4 * 1024 * 1024 * 1024        # 이 크기까지는 나눠 읽어 번호만 모은다 — 352KB 짜리 어셈블리 하나의 증거가 14.5MB 였다
SCAN_CHUNK = 8 * 1024 * 1024               #   (6.10.1 · 2026-11-27) · REA 가 쥐는 증거의 한도는 1.05GB 다 — 큰 게임의 묶음은 64MB 를 넘는다
_ID_IN_FILE = re.compile(rb'(?<!\\)"evidence_id"\s*:\s*"(ev_[0-9a-f]{8,128})"')      # 글 값 안의 것(`\"evidence_id\"`)은 세지 않는다
HEAD_BYTES = 4096                          # 다른 자리의 글을 보고로 알아보려고 읽는 앞머리
WORK = (".gaf", "rea")                     # REA 작업 폴더의 자리(프로젝트 폴더 기준) — 증거 묶음은 이 아래의 것만 본다
_HEAD = re.compile(r"^-[ \t]*(대상|증거 묶음)[ \t]*:[ \t]*([^\n]*)$", re.MULTILINE)          # 줄 안에서만
_EV = r"ev_[0-9A-Fa-f]{8,64}"
_REFS = re.compile(r"\[(" + _EV + r"(?:[ \t]*[,·][ \t]*" + _EV + r"){0,20})\]")        # [ev_1a2b3c4d] · [ev_…, ev_…] · [ev_… · ev_…]
_LOOSE = re.compile(r"\[ev_[^\]\n]{0,120}\]")                                          # 번호처럼 쓴 것 — 꼴이 틀린 것을 잡는다
_ID = re.compile(r"ev_[0-9a-f]{8,128}")
_SPAN = re.compile(r"`([^`\n]{1,400})`")


def is_brief(path) -> bool:
    """분석 보고의 자리인가 — `…/분석/<폴더>/보고.md`."""
    parts = PurePosixPath(str(path).replace("\\", "/")).parts
    return len(parts) >= 3 and parts[-1] == "보고.md" and parts[-3] == "분석"


def _declares(path: Path) -> bool:
    """앞머리(4KB 안)에 보고의 머리 두 줄 — 「- 대상:」 과 「- 증거 묶음:」 — 이 다 있는가. 한 줄만 적은 글(메모 · 인계)은 보고가 아니다."""
    try:
        with path.open("rb") as handle:
            head = handle.read(HEAD_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return False
    found = _HEAD.findall(head)
    return all(any(name == wanted and value.strip() for name, value in found) for wanted in ("대상", "증거 묶음"))


def _path_value(value: str) -> str:
    """머리 줄의 값에서 경로만 — 코드 표시(`…`)가 있으면 그 안을, 없으면 줄 전체를."""
    found = _SPAN.search(value)
    return (found.group(1) if found else value).strip()


def _keep(obj: dict) -> dict:
    """증거 묶음을 풀면서 버린다 — 기록마다 `evidence_id` 와 맨 위의 `records` 만 남긴다. 64MB 를 통째로 쥐지 않는다(한 번에 쥐는 것은
    파일의 글과 가장 큰 기록 하나 몫이다)."""
    out = {}
    if isinstance(obj.get("evidence_id"), str):
        out["evidence_id"] = obj["evidence_id"]
    if isinstance(obj.get("records"), list):
        out["records"] = obj["records"]
    return out


def export_key(path) -> str:
    """내보낸 묶음을 적어 두는 이름 — 풀어 본 온전한 경로를 견줄 꼴로(`/` · 대소문자를 안 가리는 OS 에서는 소문자)."""
    try:
        full = Path(path).resolve()
    except (OSError, RuntimeError):
        full = Path(os.path.abspath(path))
    return os.path.normcase(str(full)).replace("\\", "/")


def fingerprint(path) -> str:
    """파일의 지문(SHA-256) — 나눠 읽는다. 지휘자가 `export_evidence_bundle` 이 돌아올 때 적고, 검사가 그것과 견준다."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scan(target: Path) -> set[str]:
    """큰 묶음에서 증거 번호만 — 조각으로 읽고, 조각의 경계에 걸린 번호를 놓치지 않게 앞 조각의 끝을 겹쳐 본다."""
    ids: set[str] = set()
    tail = b""
    with target.open("rb") as handle:
        while True:
            chunk = handle.read(max(1, int(SCAN_CHUNK)))
            if not chunk:
                break
            data = tail + chunk
            ids.update(found.decode("ascii") for found in _ID_IN_FILE.findall(data))
            tail = data[-256:]                           # 번호 하나의 길이(이름 · 따옴표 · 128자리)보다 길다
    return ids


def _bundle(target: Path, shown: str, errors: list, notes: list) -> list[str] | None:
    """증거 묶음의 증거 번호(정렬) — 읽지 못하면 errors 에 적고 None."""
    try:
        if not target.is_file():
            errors.append(f"증거 묶음 파일이 없다: {shown}")
            return None
        size = target.stat().st_size
        if size > BUNDLE_MAX:
            errors.append(f"증거 묶음이 너무 크다({BUNDLE_MAX // (1024 * 1024)}MB 까지 본다)")
            return None
        if size > EXACT_MAX:
            # 통째로 풀지 않는다(풀면 그 몇 배의 메모리를 쥔다) — 나눠 읽어 증거 번호만 모은다. JSON 인지는 보지 않는다(이 자리에는
            # 지휘자가 띄운 REA 만 쓴다). 맨 위 기록의 번호와 그 안에 딸린 번호를 가리지 않고 센다.
            ids = _scan(target)
            notes.append(f"증거 묶음이 커서({size // (1024 * 1024)}MB) 번호만 훑어봤다")
            if not ids:
                notes.append("증거 묶음에 기록이 없다")
            return sorted(ids)
        raw = _read_small(target, EXACT_MAX)
    except OSError as error:
        errors.append(f"증거 묶음을 읽지 못했다({type(error).__name__}): {shown}")
        return None
    if raw is None:
        errors.append(f"증거 묶음이 너무 크다({BUNDLE_MAX // (1024 * 1024)}MB 까지 본다)")
        return None
    try:
        # json.loads 는 한 번에 푼다 — 64MB 상한 안에서는 받아들인다(object_hook 이 기록을 풀자마자 줄여 쥐는 몫을 작게 한다).
        data = json.loads(raw, object_hook=_keep)
    except (ValueError, RecursionError):
        errors.append(f"증거 묶음이 JSON 이 아니다(REA 가 내보낸 그대로가 아니다): {shown}")
        return None
    finally:
        del raw
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        errors.append(f"증거 묶음의 꼴이 아니다(맨 위에 records 목록이 없다): {shown}")
        return None
    ids = {record["evidence_id"].lower() for record in records if isinstance(record, dict)
           and isinstance(record.get("evidence_id"), str) and _ID.fullmatch(record["evidence_id"].lower())}
    bad = sum(1 for record in records if not (isinstance(record, dict) and isinstance(record.get("evidence_id"), str)
                                              and _ID.fullmatch(record["evidence_id"].lower())))
    if not records:
        notes.append("증거 묶음에 기록이 없다")
    elif bad:
        notes.append(f"증거 묶음의 기록 {bad}개는 증거 번호의 꼴이 아니어서 세지 않았다")
    return sorted(ids)


def _known(ids: list[str], ref: str) -> bool:
    """번호(앞 8자리 이상)가 묶음의 어느 증거 번호의 앞부분인가."""
    at = bisect.bisect_left(ids, ref)
    return at < len(ids) and ids[at].startswith(ref)


def _refs(text: str) -> set[str]:
    return {ref.lower() for group in _REFS.findall(text) for ref in re.findall(_EV, group)}


def check(root, brief_path, *, exports: dict | None = None) -> dict:
    """exports 는 지휘자가 적어 둔 것 — {export_key(묶음 경로): 내보낼 때의 지문}. 주면 묶음이 거기 적혀 있고 지금의 지문이 같아야
    한다(지휘자는 그 역할이 내보낸 것만 준다). 안 주면(사람이 따로 볼 때) 자리와 번호만 본다."""
    root, brief_path = Path(root), Path(brief_path)
    result = {"path": brief_path.as_posix(), "ok": False, "errors": [], "notes": [], "refs": 0}
    errors, notes = result["errors"], result["notes"]
    try:
        raw = _read_small(brief_path, BRIEF_MAX)
    except OSError as error:
        errors.append(f"보고를 읽지 못했다({type(error).__name__})")
        return result
    if raw is None:
        errors.append(f"보고가 너무 크다({BRIEF_MAX // 1024:,}KB 까지 본다)")
        return result
    body = raw.decode("utf-8", errors="replace")
    head: dict[str, str] = {}
    top = body.split("\n## ", 1)[0]                     # 머리 줄은 첫 절 앞에서만 — 본문의 「- 대상: …」 목록 줄을 머리로 읽지 않는다
    for name, value in _HEAD.findall(top):
        head.setdefault(name, value.strip())            # 같은 줄이 둘이면 처음 것
    if not head.get("대상"):
        errors.append("머리에 「- 대상: <게임 이름과 판 · 폴더>」 줄이 없다")
    # 절을 가른다: 「쓰는 규칙」은 틀의 안내(보기로 적은 `[ev_1a2b3c4d]` 이 있다) — 꼭 있어야 할 절로 세지 않고, 그 절에서만 코드 표시
    # 안을 빼고 본다(통째로 건너뛰면 그 이름의 절 아래에 지어낸 번호를 숨길 수 있다). 틀 맨 위의 「쓰는 법」 칸은 본문이 아니다.
    main, rules, headings, summary = [], [], [], []
    bucket, in_summary = main, False
    for text in body.splitlines():
        if text.startswith("## "):
            name = text[3:].strip()
            bucket = rules if "쓰는 규칙" in name else main
            in_summary = bucket is main and REQUIRED[0][0] in name
            if bucket is main:
                headings.append(name)
        elif in_summary:
            summary.append(text)
        if text.startswith("> **쓰는 법**"):
            continue
        bucket.append(text)
    for key, name in REQUIRED:
        if not any(key in heading for heading in headings):
            errors.append(f"「{name}」 절이 없다")
    main_text = "\n".join(main)
    rules_text = "\n".join(_CODE.sub("", text) for text in rules)
    used = _refs(main_text)
    result["refs"] = len(used)
    loose = sorted({found for found in _LOOSE.findall(main_text) + _LOOSE.findall(rules_text) if not _REFS.fullmatch(found)})
    if loose:
        errors.append(f"증거 번호의 꼴이 아니다: {' · '.join(item[:40] for item in loose[:5])} — `[ev_` 와 앞 8자리 이상과 `]` 로 쓴다")
    # 번호가 하나도 없어도 되는 것은 못 봤다고 **한 줄 요약에서** 밝힌 보고뿐이다 — 「못 본 것」 절에는 늘 「보지 못했다」가 적혀서(틀의 말)
    # 본문 어디서든 받아 주면 이 줄이 아무것도 잡지 못한다.
    if not used and not any(word in "\n".join(summary) for word in UNSEEN):
        errors.append("증거 번호(`[ev_1a2b3c4d]` 꼴)가 하나도 없다 — 본 것 옆에 단다(못 봤으면 한 줄 요약에 「못 봄」과 그 까닭을 쓴다)")
    if not head.get("증거 묶음"):
        errors.append("머리에 「- 증거 묶음: <내보낸 증거 묶음 파일의 경로>」 줄이 없다")
        return result
    value = _path_value(head["증거 묶음"])
    target = _inside(root, value)
    if target is None:
        errors.append("증거 묶음의 경로가 프로젝트 밖이다(프로젝트 폴더 기준이나 프로젝트 안의 온전한 경로로 적는다)")
        return result
    # REA 작업 폴더(.gaf/rea/<역할>/)의 것만 본다 — 세션은 거기에 쓰지 못하니(문) 그 자리의 묶음은 지휘자가 띄운 REA 가 내보낸 것이다.
    # 다른 자리의 파일은 세션이 지어 쓸 수 있다(풀어 본 자리로 견준다 — 연결을 타고 나가도 걸린다).
    try:
        target.relative_to(root.resolve() / WORK[0] / WORK[1])
    except (ValueError, OSError):
        errors.append("증거 묶음이 REA 작업 폴더(.gaf/rea/<역할>/) 밖이다 — REA 가 그 폴더에 내보낸 묶음의 경로를 적는다"
                      "(다른 자리의 파일은 REA 가 낸 것인지 알 수 없다)")
        return result
    if exports is not None:
        # 그 자리에는 Write · Edit 로 못 쓰지만 셸로 쓰는 것까지 문이 막지는 못한다 — 지휘자가 내보낼 때 적어 둔 지문과 견준다.
        wanted = exports.get(export_key(target))
        if not isinstance(wanted, str) or not wanted:
            errors.append("증거 묶음이 이 역할이 `export_evidence_bundle` 로 내보낸 것으로 적어 둔 파일이 아니다 — 내보낸 그 경로를 적는다")
            return result
        try:
            same = target.is_file() and fingerprint(target) == wanted
        except OSError:
            same = False
        if not same:
            errors.append("증거 묶음이 내보낸 뒤에 바뀌었다(지휘자가 적어 둔 지문과 다르다 · 또는 파일이 없다) — 다시 내보낸다")
            return result
    ids = _bundle(target, value[:200], errors, notes)
    if ids is None:
        return result
    missing = sorted(ref for ref in used | _refs(rules_text) if not _known(ids, ref))
    if missing:
        errors.append(f"증거 묶음에 없는 번호다: {' · '.join(missing[:10])}" + (f" 외 {len(missing) - 10}개" if len(missing) > 10 else ""))
    result["ok"] = not errors
    return result


def line(result: dict) -> str:
    """일 결과의 머리 · 명령줄에 찍는 한 줄. 무엇을 보지 않았는지를 늘 밝힌다."""
    tail = "".join(f" · {note}" for note in result["notes"])
    if result["ok"]:
        return f"분석 보고 형식: 맞음 — 증거 번호 {result['refs']}개{tail} · 내용이 옳은지는 검토하지 않았다({result['path']})"
    return f"분석 보고 형식: 맞지 않음 — {' / '.join(result['errors'][:5])}{tail}({result['path']})"


def lines(root, paths, *, exports: dict | None = None) -> list[str]:
    """일이 쓴 파일 가운데 분석 보고만 골라 본다 — 줄마다 보고 하나. 자리(…/분석/<폴더>/보고.md)나 앞머리의 두 줄로 알아본다.
    프로젝트 밖을 가리키는 경로는 열어 보지 않고, 검사가 죽어도 일은 이어지게 한 줄로 알린다. 64MB 묶음을 푸는 데 1초 남짓 걸릴 수
    있다 — 지휘자는 스레드에서 부른다."""
    out, seen = [], set()
    for path in paths or []:
        try:
            shown = str(path).replace("\\", "/")
            if not shown.lower().endswith(".md"):
                continue
            full = _inside(Path(root), path)
            if full is None or full in seen:
                continue
            seen.add(full)
            if not is_brief(shown) and not _declares(full):
                continue
            shown = full.relative_to(Path(root).resolve()).as_posix()
            out.append(line({**check(root, full, exports=exports), "path": shown}))
        except Exception as error:  # noqa: BLE001 — 형식 검사는 알리기만 한다. 여기서 죽으면 그 일의 결과 글이 사라진다.
            out.append(f"분석 보고 형식: 못 읽었다 — {type(error).__name__}({str(path)[:200]})")
    return out
