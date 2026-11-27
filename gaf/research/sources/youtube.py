"""YouTube — yt-dlp 를 자식 프로세스로 불러 검색 · 상세 · 댓글 · 자막을 받는다(로그인 · 쿠키 · 프록시 없이).

2026-11-27 이 PC 에서 yt-dlp 2026.11.23 으로 받아지는 것을 봤다(실측 17.60): 검색 5건 2초 · 상세와 댓글 20개 3초 · 원본 언어의
자막. 번역 자막을 청하면 429 가 왔다 — 원본 언어의 것만 청한다. 막히면(봇 확인 · 429) 돌아가지 않고 그대로 알린다.
채널 이름은 싣고, 댓글을 쓴 계정은 싣지 않는다.
"""
from __future__ import annotations

import contextlib
import datetime
import html
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .. import ENV_KEEP, score, text
from ..evidence import Item, QueryResult

# 설정 파일 · 브라우저 쿠키 · 캐시를 쓰지 않는다. 프록시는 빈 값으로 못 박는다(주지 않으면 yt-dlp 가 Windows 의 시스템 프록시를
# 읽는다) · 플러그인 폴더(집 폴더 · `C:/etc/yt-dlp/plugins` 등)의 코드를 읽어 돌리지 않는다(11-27 다른 눈의 검수).
BASE = ["--ignore-config", "--no-cookies-from-browser", "--no-cache-dir", "--no-warnings", "--proxy", "", "--no-plugin-dirs"]
RESULTS = {"quick": 8, "default": 15, "deep": 25}        # 검색 하나가 받는 영상 수
DETAILS = {"quick": 3, "default": 6, "deep": 10}         # 상세(날짜 · 좋아요 · 댓글)를 받을 영상 수
COMMENTS = {"quick": 0, "default": 10, "deep": 20}       # 상세 하나가 받는 댓글 수
SUBTITLES = {"quick": 0, "default": 2, "deep": 4}        # 자막을 받을 영상 수
SEARCH_SECONDS, DETAIL_SECONDS, SUBS_SECONDS = 60.0, 45.0, 30.0
LEAST_SECONDS = 15.0                     # 이보다 적게 남으면 부르지 않는다
COMMENTS_KEPT, HIGHLIGHTS_KEPT, HIGHLIGHT_CHARS = 5, 3, 400
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{6,20}")
_TAG = re.compile(r"<[^>]*>")


def describe() -> dict:
    return {"name": "youtube", "label": "YouTube", "hosts": ["youtube.com", "googlevideo.com"],
            "sends": "검색 낱말(고른 영상의 번호는 상세 · 자막 · 댓글을 받을 때)",
            "how": "yt-dlp 로 영상 검색 · 상세 · 댓글 · 자막을 받는다(로그인 · 쿠키 없이)",
            "terms": "YouTube 의 약관은 자동 접근을 금지한다(검색 엔진 · 서면 허락 말고) — 그 걸림을 알고 켜는 출처다",
            "needs": "yt-dlp(영상 정보를 받는 공개 도구 · Unlicense) — 켤 때 이 프로그램에 깐다(인터넷에서 받는다)"}


def available(find=importlib.util.find_spec) -> bool:
    try:
        return find("yt_dlp") is not None
    except (ImportError, ValueError):
        return False


REQUIREMENTS = Path(__file__).resolve().parents[3] / "requirements-research.txt"      # 판을 못 박아 둔 목록(프로그램과 같이 온다)
INSTALL_SECONDS = 180                   # 까는 데 기다리는 가장 긴 시간 — 그사이 지휘자를 끄면 이만큼 늦어질 수 있다


def install(run=subprocess.run) -> bool:
    """yt-dlp 를 이 프로그램의 파이썬에 깐다 — 사람이 YouTube 출처를 켠 때만 부른다(승인 카드 · `gaf research allow youtube`).
    됐으면 True. 실패한 명령의 출력에는 계정 · 경로가 섞일 수 있어 돌려주지 않는다."""
    try:
        done = run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=INSTALL_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return False
    importlib.invalidate_caches()
    return done.returncode == 0 and available()


def run_ytdlp(args, timeout: float, cwd):
    """(종료 코드 · 표준 출력 · 표준 오류). 시간 안에 안 끝나면 죽이고 코드 None."""
    env = {key: os.environ[key] for key in ENV_KEEP if key in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run([sys.executable, "-X", "utf8", "-I", "-m", "yt_dlp", *args], cwd=str(cwd), env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired:
        return None, "", "시간 초과"
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


def _failure(code, stderr: str) -> tuple[str, str]:
    lowered = (stderr or "").casefold()
    last = " ".join(((stderr or "").strip().splitlines() or [""])[-1].split())[:200]
    if code is None:
        return "timeout", "yt-dlp 가 정한 시간 안에 답하지 않았다"
    if "no module named yt_dlp" in lowered:
        return "unavailable", "yt-dlp 가 깔려 있지 않다 — 사람이 `python -m gaf research allow youtube` 를 치면 깐다"
    if "429" in lowered or "too many requests" in lowered:
        return "rate-limited", f"YouTube 가 한도를 걸었다(yt-dlp 의 말 「{last}」)"
    if "sign in to confirm" in lowered or "not a bot" in lowered:
        return "blocked", f"YouTube 가 이 PC 의 요청을 막았다(yt-dlp 의 말 「{last}」)"
    return "error", f"yt-dlp 의 말 「{last}」" if last else f"yt-dlp 종료 코드 {code}"


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and abs(value) < 1e15 else None


def vtt_text(vtt: str) -> str:
    """자막(VTT)을 글로: 시간 줄 · 표시를 걷고, 굴러 올라가며 되풀이되는 줄은 한 번만."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", (vtt or "").replace("\r\n", "\n")):
        lines = block.split("\n")
        at = next((n for n, line in enumerate(lines) if "-->" in line), None)
        if at is None:
            continue
        for line in lines[at + 1:]:
            clean = " ".join(html.unescape(_TAG.sub("", line)).split())
            if clean and (not out or out[-1] != clean):
                out.append(clean)
    return " ".join(out)


def _highlights(transcript: str, rank) -> list[str]:
    """자막에서 고르는 낱말 둘레를 자른 글(서로 겹치지 않게 몇 개 · 자막의 차례대로). 딱 맞는 자리를 먼저 자르고 남는 칸을 어미가
    붙은 자리로 채운다 — 점수와 같은 맞춤이다."""
    if not transcript or not text.hits(rank, transcript):
        return []
    if len(transcript) <= HIGHLIGHT_CHARS:
        return [transcript]
    lowered = transcript.casefold()

    def spots(exact: bool) -> list[int]:
        out: list[int] = []
        for term in rank:
            start = 0
            while len(out) < 12:
                at = text.find(term, lowered, start, exact=exact)
                if at < 0:
                    break
                out.append(at)
                start = at + 1
        return sorted(out)
    cuts: list[tuple[int, int]] = []
    for at in spots(True) + spots(False):
        if len(cuts) >= HIGHLIGHTS_KEPT:
            break
        begin = max(0, at - HIGHLIGHT_CHARS // 3)
        end = begin + HIGHLIGHT_CHARS
        if not any(begin < other_end and other_begin < end for other_begin, other_end in cuts):
            cuts.append((begin, end))
    return [("…" if begin > 0 else "") + transcript[begin:end].strip() + ("…" if end < len(transcript) else "")
            for begin, end in sorted(cuts)]


def _track(info: dict) -> str | None:
    """받을 자막 하나: 영상의 언어로 올린 자막 → 자동 자막의 원본 언어 → 한국어 · 영어로 올린 자막 → 영상 언어의 자동 자막.
    남이 올린 다른 언어의 번역 자막과 자동 번역 자막은 청하지 않는다(진짜 호출에서 아랍어 번역 자막을 집었다 — 실측 17.60)."""
    manual = info.get("subtitles") if isinstance(info.get("subtitles"), dict) else {}
    manual = sorted(name for name in manual if name != "live_chat")
    language = info.get("language") if isinstance(info.get("language"), str) else ""

    def uploaded(code: str):
        return next((name for name in manual if code and (name == code or name.startswith(code + "-"))), None)
    if uploaded(language):
        return uploaded(language)
    auto = info.get("automatic_captions") if isinstance(info.get("automatic_captions"), dict) else {}
    original = sorted(name for name in auto if name.endswith("-orig"))
    if original:
        return original[0]
    return uploaded("ko") or uploaded("en") or (language if language and language in auto else None)


def collect(plan, index: int, runner, today: datetime.date, *, deadline: float, work_dir, clock=time.monotonic) -> QueryResult:
    query = plan.queries[index]
    work = Path(work_dir)
    calls = 0
    found: dict[str, Item] = {}
    failures: list[tuple[str, str]] = []
    notes: list[str] = []
    succeeded = 0

    def left() -> float:
        return deadline - clock()

    for phrase in query.search:
        remaining = left()
        if remaining < LEAST_SECONDS:
            failures.append(("skipped-time", f"「{phrase}」: 남은 시간이 모자라 찾지 않았다"))
            continue
        calls += 1
        code, out, err = runner([*BASE, "--flat-playlist", "--dump-json", f"ytsearch{RESULTS[plan.depth]}:{phrase}"],
                                min(SEARCH_SECONDS, remaining), work)
        if code != 0 and not out.strip():
            status, detail = _failure(code, err)
            failures.append((status, f"「{phrase}」: {detail}"))
            continue
        rows, broken = [], 0
        for line in out.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                broken += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
        if broken and not rows:
            failures.append(("schema-drift", f"「{phrase}」: yt-dlp 출력의 꼴이 바뀌었다"))
            continue
        succeeded += 1
        if code != 0:                    # 받은 것은 쓰되 「됨」으로 적지 않는다
            notes.append(f"「{phrase}」: 검색이 끝까지 돌지 못했다(받은 데까지 쓴다 — {_failure(code, err)[1]})")
        for row in rows:
            video_id, title = row.get("id"), row.get("title")
            if (not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id) or not isinstance(title, str)
                    or not title.strip() or row.get("live_status") in ("is_live", "is_upcoming")):
                continue
            views = _number(row.get("view_count"))
            found.setdefault(video_id, Item(
                source="youtube", native_id=video_id, query=index, title=text.clean(title)[:300],
                text=text.clean(row.get("description") if isinstance(row.get("description"), str) else "")[:2000],
                url=f"https://www.youtube.com/watch?v={video_id}", counts={"views": views} if views is not None else {},
                container=text.clean(row.get("channel") if isinstance(row.get("channel"), str) else "")[:100]))
    items = list(found.values())

    # 상세는 고르는 낱말이 맞은 것부터, 그다음은 YouTube 가 준 차례대로 받는다 — 제목에 낱말이 없다고 빼지 않는다(검색이 이미 주제로
    # 골랐고, 그 관점은 설명 · 댓글 · 자막에 있을 수 있다). 남길지는 다 받은 뒤 묶을 때 정한다(11-27 진짜 조사: 15건 가운데 14건이
    # 상세도 못 받고 빠졌다).
    order = {video_id: place for place, video_id in enumerate(found)}
    wanted = sorted(items, key=lambda item: (-score.relevance(query.rank, item.title, item.text), order[item.native_id]))
    tracks: dict[str, str] = {}
    detail_failed = skipped = subs_failed = 0
    comment_count = COMMENTS[plan.depth]
    for item in wanted[:DETAILS[plan.depth]]:
        remaining = left()
        if remaining < LEAST_SECONDS:
            skipped += 1
            break
        calls += 1
        extra = (["--write-comments", "--extractor-args", f"youtube:comment_sort=top;max_comments={comment_count},{comment_count},0,0"]
                 if comment_count else [])
        code, out, err = runner([*BASE, "--skip-download", "--dump-single-json", *extra, item.url], min(DETAIL_SECONDS, remaining), work)
        try:
            info = json.loads(out) if code == 0 else None
        except ValueError:
            info = None
        if not isinstance(info, dict):
            detail_failed += 1
            continue
        stamp = info.get("upload_date")
        if isinstance(stamp, str) and re.fullmatch(r"\d{8}", stamp):
            item.date = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"
        counts = {"views": _number(info.get("view_count")), "likes": _number(info.get("like_count")),
                  "comments": None if comment_count else _number(info.get("comment_count"))}   # 댓글을 받으면 그 수는 받은 수라 싣지 않는다
        item.counts = {**item.counts, **{key: value for key, value in counts.items() if value is not None}}
        if isinstance(info.get("description"), str) and info["description"].strip():
            item.text = text.clean(info["description"])[:2000]
        roots = [row for row in info.get("comments") or [] if isinstance(row, dict) and row.get("parent") in (None, "root")
                 and isinstance(row.get("text"), str) and row["text"].strip()]
        roots.sort(key=lambda row: -(_number(row.get("like_count")) or 0))
        item.comments_list = [{"text": text.clean(row["text"])[:600], "count": _number(row.get("like_count")),
                               "url": f"{item.url}&lc={row['id']}" if isinstance(row.get("id"), str) else item.url}
                              for row in roots[:COMMENTS_KEPT]]
        track = _track(info)
        if track:
            tracks[item.native_id] = track
    for item in [item for item in wanted if item.native_id in tracks][:SUBTITLES[plan.depth]]:
        remaining = left()
        if remaining < LEAST_SECONDS:
            skipped += 1
            break
        calls += 1
        lang = tracks[item.native_id]
        code, _, _ = runner([*BASE, "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", lang,
                             "--sub-format", "vtt", "-o", "%(id)s", item.url], min(SUBS_SECONDS, remaining), work)
        got = False
        for path in work.glob(f"{item.native_id}.*"):
            if path.suffix == ".vtt" and not got:
                with contextlib.suppress(OSError, UnicodeDecodeError):
                    item.highlights = _highlights(vtt_text(path.read_text(encoding="utf-8")), query.rank)
                    got = True
            with contextlib.suppress(OSError):
                path.unlink()
        subs_failed += not got
    if detail_failed:
        notes.append(f"상세를 받지 못한 영상 {detail_failed}개")
    if subs_failed:
        notes.append(f"자막을 받지 못한 영상 {subs_failed}개")
    if skipped:
        notes.append("남은 시간이 모자라 상세 · 자막의 일부를 건너뛰었다")
    details = [detail for _, detail in failures] + notes
    order = ("error", "blocked", "rate-limited", "timeout", "schema-drift", "unavailable", "skipped-time")
    if not succeeded and failures:
        status = min((status for status, _ in failures), key=order.index)
    elif failures or notes:
        status = "partial"
    else:
        status = "ok" if items else "no-results"
    return QueryResult(index, status, items, " · ".join(details)[:600], calls)
