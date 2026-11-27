"""조사 프로세스 — 지휘자가 띄우는 고정 프로그램이다(모델 없음 · 세션의 권한 없음).

요청 파일(계획 · 허락된 출처 · 조사 번호)을 읽고, 출처마다 받아, 증거 묶음을 새 폴더에 쓴다. 표준 출력의 줄은 배경 일의
출력으로 보인다. 묶음을 옮기고 알리는 것은 지휘자가 한다. 명령 · 경로 · 주소를 입력으로 받지 않는다 — 받는 것은 검사한 계획뿐이다.

쓰는 법(지휘자가): python -X utf8 -I -c "<gaf 가 있는 폴더를 경로에 넣고 gaf.research.run.main() 을 부른다>" --request <파일> --out <새 폴더>
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import re
import shutil
import sys
import time
from pathlib import Path

from . import evidence
from . import plan as plan_mod
from .evidence import STATUS_WORDS, QueryResult
from .net import Door
from .sources import reddit, youtube

DEADLINES = {"quick": 90, "default": 180, "deep": 300}       # 조사 하나의 절대 마감(초)
REQUESTS = {"quick": 10, "default": 20, "deep": 30}          # 문으로 나가는 요청 수(아카이브)
SPARE = 10.0                                                # 묶음을 쓰는 데 남겨 두는 시간
COLLECTORS = {"reddit": reddit.collect, "youtube": youtube.collect}
_RUN_ID = re.compile(r"r-\d{8}-\d{6}-[0-9a-f]{4}")


class RequestError(ValueError):
    """요청이 틀렸다 — 아무것도 부르지 않는다."""


def _check(request):
    if not isinstance(request, dict):
        raise RequestError("요청은 객체여야 한다")
    run_id, created_at = request.get("id"), request.get("created_at")
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise RequestError("id: 조사 번호의 꼴이 아니다")
    if not isinstance(created_at, str) or not created_at.strip() or len(created_at) > 40:
        raise RequestError("created_at: 만든 때가 없다")
    try:
        plan = plan_mod.from_dict(request.get("plan"))
    except plan_mod.PlanError as error:
        raise RequestError(f"plan: {error}") from None
    allowed = request.get("sources")
    missing = [name for name in plan_mod.sources(plan) if not isinstance(allowed, list) or name not in allowed]
    if missing:
        raise RequestError(f"sources: 허락되지 않은 출처가 계획에 있다 — {' · '.join(missing)}")
    return plan, run_id, created_at


def _safe(index, call):
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 — 출처 하나가 죽어도 나머지는 쓴다.
        return QueryResult(index, "error", detail=f"{type(exc).__name__}: {' '.join(str(exc).split())[:200]}")


def run(request, out_dir, *, today=None, collectors=None, youtube_available=None, clock=time.monotonic, fetch=None) -> dict:
    plan, run_id, created_at = _check(request)
    collectors = collectors or COLLECTORS
    available = youtube_available or youtube.available
    out = Path(out_dir)
    if out.exists():
        raise FileExistsError(f"이미 있는 폴더다: {out.name}")
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    deadline = clock() + DEADLINES[plan.depth] - SPARE
    work = out.parent / (out.name + ".work")
    work.mkdir(parents=True, exist_ok=False)
    try:
        lanes = {name: [index for index, query in enumerate(plan.queries) if query.source == name]
                 for name in plan_mod.sources(plan)}

        def reddit_lane(indexes):
            door = Door({reddit.HOST}, deadline=deadline, pace=reddit.PACE, budget=REQUESTS[plan.depth], fetch=fetch, clock=clock)
            return [_safe(index, lambda index=index: collectors["reddit"](plan, index, door, today)) for index in indexes]

        def youtube_lane(indexes):
            if not available():
                return [QueryResult(index, "unavailable", detail="yt-dlp 가 깔려 있지 않다 — 사람이 `python -m gaf research allow youtube` "
                                    "를 치면 깐다") for index in indexes]
            return [_safe(index, lambda index=index: collectors["youtube"](
                plan, index, youtube.run_ytdlp, today, deadline=deadline, work_dir=work, clock=clock)) for index in indexes]

        runners = {"reddit": reddit_lane, "youtube": youtube_lane}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(lanes) or 1, thread_name_prefix="gaf-research") as pool:
            futures = [pool.submit(runners[name], indexes) for name, indexes in lanes.items()]
            results = [result for future in futures for result in future.result()]
        bundle = evidence.build(plan, results, today=today, run_id=run_id, created_at=created_at)
        written = evidence.write(bundle, out)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for row in bundle["sources"]:
        print(f"{row['source']}: {STATUS_WORDS[row['status']]} · 받은 {row['received']}건 → 남은 {row['kept']}건 · 요청 {row['requests']}번"
              + (f" · {row['detail']}" if row["detail"] else ""), flush=True)
    for line in bundle["warnings"]:
        print("경고: " + line, flush=True)
    print(f"남은 증거 {written['items']}건 · 묶음을 썼다", flush=True)
    return {"id": run_id, "state": bundle["state"], "items": written["items"], "sha256": written["sha256"],
            "sources": bundle["sources"], "warnings": bundle["warnings"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gaf.research.run", add_help=False)
    parser.add_argument("--request", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        run(request, args.out)
    except (RequestError, FileExistsError, ValueError, OSError) as error:
        print(f"오류: {error}", flush=True)
        return 2
    except Exception as error:  # noqa: BLE001 — 모르는 실패도 한 줄로 알리고 끝낸다.
        print(f"오류: 조사를 마치지 못했다({type(error).__name__})", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
