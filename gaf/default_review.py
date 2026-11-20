"""기본 검토 — 규칙 고르기 · 바뀐 정도 재기 · 기준 그림 두기(사용자 11-20 「음 이해했어 그럼 적용해」).

검토를 붙일지 총괄(AI)이 정하지 않는다: `gaf.json` 의 `reviews` 규칙(사람이 승인)에 맞는 파일이 바뀌면 지휘자가 늘 돌린다.
얼마나 바뀌었나는 프로그램이 잰다(토큰을 쓰지 않는다) — 그림은 앞 그림과 점끼리 견준 바뀐 넓이(%), 글은 바뀐 줄 · 글자 수.
재지 못하면(앞 그림이 없다 · 크기가 바뀌었다 · git 저장소가 아니다) 「큼」으로 본다. 숫자는 검토를 뺄지가 아니라 얼마나
무겁게 할지만 정한다 — 작아도 가벼운 검토(첫 검토자 하나)는 늘 돈다.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from . import measure, scope

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
TEXT_LIMIT = 2 * 1024 * 1024          # 이보다 큰 글 파일은 재지 않는다(큼)
CHANGED_KEPT = 200                    # 일 기록에 남기는 바뀐 파일 수


def matched(rules: list[dict], changed) -> list[dict]:
    """바뀐 파일이 when 에 맞는 규칙들 — 설정에 적힌 차례대로."""
    paths = [path for path in changed if path]
    return [rule for rule in rules if any(scope.glob_match(path, glob) for path in paths for glob in rule["when"])]


def baseline_dir(state_dir: Path, name: str) -> Path:
    """규칙마다 지난 채택본의 그림을 두는 곳(.gaf/review-baseline/<규칙>)."""
    safe = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", name).strip("._") or "rule"
    return Path(state_dir) / "review-baseline" / safe


def _git(root: Path, *args) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "--no-optional-locks", "-c", "core.quotepath=off", "-c", "core.fsmonitor=false", *args],
                              cwd=str(root), capture_output=True, timeout=20,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None


def head_bytes(root: Path, path: str) -> bytes | None:
    """HEAD 에 있던 그 파일의 바이트. HEAD 에 없던 파일(새 파일 · 첫 커밋 전)은 b'' · git 저장소가 아니거나 못 부르면 None."""
    root = Path(root)
    if not (root / ".git").exists():
        return None
    shown = _git(root, "cat-file", "blob", f"HEAD:{path}")
    if shown is None:
        return None
    if shown.returncode == 0:
        return shown.stdout
    head = _git(root, "rev-parse", "--verify", "-q", "HEAD")
    if head is None:
        return None
    return b""                       # HEAD 가 없거나(첫 커밋 전) HEAD 에 없는 파일 — 새로 생긴 것이다


def text_measure(root: Path, rule: dict, changed) -> tuple[int | None, int | None, str]:
    """when 에 맞는 바뀐 글 파일의 (바뀐 줄 수, 바뀐 글자 수, 못 잰 까닭). 앞 글은 git HEAD 의 것이다. 그림 같은 바이너리는 건너뛴다(넓이로 잰다)."""
    root = Path(root)
    lines = chars = 0
    for rel in sorted(set(changed)):
        if not any(scope.glob_match(rel, glob) for glob in rule["when"]):
            continue
        path = root / rel
        if path.is_dir():
            continue
        if path.is_file():
            try:
                if path.stat().st_size > TEXT_LIMIT:
                    return None, None, f"{rel}: 글이 커서 재지 않았다"
                data = path.read_bytes()
            except OSError:
                return None, None, f"{rel}: 읽지 못했다"
            try:
                after = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
        else:
            after = ""                  # 지운 파일
        before = head_bytes(root, rel)
        if before is None:
            return None, None, "git 저장소가 아니라 앞 글을 모른다"
        if len(before) > TEXT_LIMIT:
            return None, None, f"{rel}: 앞 글이 커서 재지 않았다"
        added, changed_chars = measure.text_change(before.decode("utf-8", "replace"), after)
        lines += added
        chars += changed_chars
    return lines, chars, ""


def area_measure(root: Path, rule: dict, snapshot_dir: Path | None, files, baseline: Path) -> tuple[float | None, str]:
    """사본의 그림(규칙의 files 에 맞는 것)을 앞 그림과 견준 바뀐 넓이 % · 못 잰 까닭.

    앞 그림은 규칙의 기준 그림(지난 채택본) · 없으면 git HEAD 의 것이다. 앞과 바이트가 같은 그림(그대로인 레퍼런스)은 넓이에 넣지 않는다 —
    바뀐 그림들만 합친 넓이 가운데 바뀐 점의 비율이다.
    """
    if snapshot_dir is None:
        return None, "볼 그림의 사본이 없다"
    images = [rel for rel in files if rel.lower().endswith(IMAGE_SUFFIXES)
              and any(scope.glob_match(rel, glob) for glob in rule["files"])]
    if not images:
        return None, "볼 그림이 없다"
    changed_px = total_px = 0
    for rel in images:
        try:
            after = (Path(snapshot_dir) / rel).read_bytes()
            saved = Path(baseline) / rel
            before = saved.read_bytes() if saved.is_file() else head_bytes(root, rel)
        except OSError:
            return None, f"{rel}: 읽지 못했다"
        if not before:
            return None, f"{rel}: 견줄 앞 그림이 없다(처음 보는 그림)"
        if before == after:
            continue
        counted = measure.changed_pixels(before, after)
        if counted is None:
            return None, f"{rel}: 그림을 읽지 못했거나 크기가 바뀌었다"
        changed_px += counted[0]
        total_px += counted[1]
    return (round(changed_px * 100 / total_px, 2) if total_px else 0.0), ""


def judge(rule: dict, area: float | None, area_why: str, lines: int | None, chars: int | None, text_why: str) -> dict:
    """잰 값을 규칙의 작음 기준(small)에 대 본다. 적힌 기준을 모두 넘지 않아야 작음 · 못 잰 기준이 하나라도 있으면 큼."""
    small = rule.get("small") or {}
    why = []
    ok = bool(small)
    if not small:
        why.append("작음 기준이 없다")
    if "area" in small:
        if area is None:
            ok = False
            why.append(area_why or "넓이를 재지 못했다")
        elif area >= small["area"]:
            ok = False
    for part, value in (("lines", lines), ("chars", chars)):
        if part in small:
            if value is None:
                ok = False
                why.append(text_why or "글을 재지 못했다")
            elif value >= small[part]:
                ok = False
    return {"area": area, "lines": lines, "chars": chars, "small": ok, "why": " · ".join(dict.fromkeys(w for w in why if w))}


def measure_text(measured: dict) -> str:
    """잰 값을 사람이 읽는 한 줄로(넓이 · 글자 · 줄 — 잰 것만)."""
    parts = []
    if measured.get("area") is not None:
        parts.append(f"넓이 {measured['area']:g}%")
    if measured.get("chars") is not None:
        parts.append(f"글자 {measured['chars']}")
    if measured.get("lines") is not None:
        parts.append(f"줄 {measured['lines']}")
    if not measured.get("small") and measured.get("why"):
        parts.append(measured["why"])
    return " · ".join(parts)


def save_baseline(state_dir: Path, rule: dict, snapshot_dir: Path, files) -> int:
    """채택본의 그림을 그 규칙의 새 기준으로 둔다(덮어쓴다). 둔 그림 수."""
    target = baseline_dir(state_dir, rule["name"])
    count = 0
    for rel in files:
        if not rel.lower().endswith(IMAGE_SUFFIXES) or not any(scope.glob_match(rel, glob) for glob in rule["files"]):
            continue
        source = Path(snapshot_dir) / rel
        if not source.is_file():
            continue
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        count += 1
    return count
