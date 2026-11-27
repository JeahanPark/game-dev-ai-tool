"""Reddit — 공개 아카이브 Arctic Shift 의 API 에서 글과 댓글을 받는다(reddit.com 을 직접 부르지 않는다).

2026-11-27 이 PC 에서 reddit.com 의 화면용 검색 주소는 403 이었고, 이 아카이브는 정직한 이름으로 받아졌다(실측 17.60).
아카이브의 안내: 키워드 검색은 서브레딧이 있어야 한다 · 받는 차례는 날짜순뿐이다 · 「free service, be considerate」.
그날 잰 것: 낱말 검색은 때에 따라 됐다 안 됐다 했다(작은 서브레딧에서 6 ~ 10초에 되기도, 422 「Timeout. Maybe slow down a bit」로
거절되기도 · 큰 서브레딧은 줄곧 거절) · 기간(`after`)을 붙인 낱말 검색은 다섯 번 모두 422 였다 · 낱말 없는 목록은 1 ~ 5초에 왔다.
그래서 낱말 검색은 기간 없이 조금만 청하고(기간 밖은 묶을 때 세어서 뺀다), 거절된 서브레딧은 글 목록을 기간에 펼쳐 받아 낱말이
가까이 든 글을 고른다 — 그때는 「일부만」으로 적는다. 작성자의 이름 · 번호는 청하지도 싣지도 않는다.
"""
from __future__ import annotations

import datetime
import math
import re
import urllib.parse

from .. import score, text
from ..evidence import Item, QueryResult, outside
from ..net import NetError

HOST = "arctic-shift.photon-reddit.com"
API = f"https://{HOST}/api"
POST_FIELDS = "id,title,selftext,score,num_comments,created_utc,subreddit,over_18"
PACE = 3.0                                               # 요청 사이(초) — 무료 아카이브다
SEARCHES = {"quick": 4, "default": 8, "deep": 12}        # 한 갈래가 쓰는 검색 · 목록 요청 수(서브레딧끼리 고르게 나눠 쓴다)
SEARCH_LIMIT = 25                                        # 낱말 검색 하나가 받는 글 수(새것부터 — 점수순으로는 못 받는다)
LIST_LIMIT = 100                                         # 낱말 없는 목록 한 쪽
LIST_PAGES = {"quick": 2, "default": 4, "deep": 6}       # 낱말 검색이 거절된 서브레딧에서 받는 가장 많은 쪽 수
NEAR = 160                                               # 목록에서 고를 때: 검색 낱말들이 이 글자 수 안에 모여 있어야 한다
FALLBACK = ("rate-limited", "timeout")                   # 「검색이 무겁다」일 때만 목록으로 물러난다(막힘 · 오류는 아니다)
TREES = {"quick": 3, "default": 6, "deep": 10}           # 댓글을 받을 글 수
TREE_LIMIT = 50
COMMENTS_KEPT = 5
GONE = {"[deleted]", "[removed]", ""}
_SEVERITY = ("error", "blocked", "rate-limited", "timeout", "schema-drift", "skipped-time")
_POST_ID = re.compile(r"[0-9a-z]{1,12}")
_SUBREDDIT = re.compile(r"[A-Za-z0-9_]{2,21}")
_STOP = frozenset("a an the and or of to in on for with from by at is are was be it its this that as not no vs how why what when "
                  "which who do does did can".split())


def describe() -> dict:
    return {"name": "reddit", "label": "Reddit", "hosts": [HOST],
            "sends": "서브레딧 이름과 검색 낱말",
            "how": "Reddit 의 글 · 댓글을 공개 아카이브(Arctic Shift)에서 받는다 — reddit.com 을 직접 부르지 않는다",
            "terms": "Reddit 의 약관은 서면 동의 없는 수집을 금지한다. 아카이브는 연구자 · 누구나 쓰라고 연 무료 서비스다"
                     "(「be considerate」) — 천천히, 적게 부른다",
            "needs": ""}


def _number(value):
    """수이고 끝이 있는 값만(NaN · Infinity · 터무니없이 큰 값은 버린다 — 셈과 날짜 바꾸기를 깨뜨린다)."""
    return value if type(value) in (int, float) and math.isfinite(value) and abs(value) < 1e15 else None


def _item(row, index: int) -> Item | None:
    if not isinstance(row, dict) or row.get("over_18") is True:
        return None
    post_id, title, sub = row.get("id"), row.get("title"), row.get("subreddit")
    # 글 번호와 서브레딧 이름은 주소와 증거의 머리 줄에 그대로 들어간다 — 꼴이 맞는 것만 받는다(아카이브가 준 값도 바깥 글이다).
    if not (isinstance(post_id, str) and _POST_ID.fullmatch(post_id) and isinstance(title, str) and title.strip()
            and isinstance(sub, str) and _SUBREDDIT.fullmatch(sub)):
        return None
    body = row.get("selftext") if isinstance(row.get("selftext"), str) else ""
    counts = {key: value for key, value in (("score", _number(row.get("score"))), ("comments", _number(row.get("num_comments"))))
              if value is not None}
    created = _number(row.get("created_utc"))
    try:
        date = datetime.datetime.fromtimestamp(created, datetime.timezone.utc).date().isoformat() if created and created > 0 else None
    except (OverflowError, OSError, ValueError):
        date = None
    return Item(source="reddit", native_id=f"t3_{post_id}", query=index, title=text.clean(title)[:300],
                text="" if body.strip() in GONE else text.clean(body)[:2000],
                url=f"https://www.reddit.com/r/{sub}/comments/{post_id}/", date=date, counts=counts, container=f"r/{sub}")


def _flatten(rows, out: list) -> list:
    """댓글 나무를 깊이와 상관없이 편다 — 지워진 것과 「더 보기」 줄은 뺀다."""
    for row in rows if isinstance(rows, list) else []:
        data = row.get("data") if isinstance(row, dict) else None
        if not isinstance(data, dict) or row.get("kind") == "more":
            continue
        body = data.get("body")
        if isinstance(body, str) and body.strip() not in GONE:
            out.append(data)
        replies = data.get("replies")
        if isinstance(replies, dict):
            _flatten((replies.get("data") or {}).get("children") if isinstance(replies.get("data"), dict) else None, out)
    return out


def _comments(door, item: Item) -> list[dict]:
    url = f"{API}/comments/tree?" + urllib.parse.urlencode({"link_id": item.native_id, "limit": TREE_LIMIT})
    data = door.get_json(url)
    rows = _flatten(data.get("data") if isinstance(data, dict) else None, [])
    rows.sort(key=lambda row: -(_number(row.get("score")) or 0))
    out = []
    for row in rows[:COMMENTS_KEPT]:
        link = row.get("permalink")
        out.append({"text": text.clean(row["body"])[:600], "count": _number(row.get("score")),
                    "url": "https://www.reddit.com" + link if isinstance(link, str) and link.startswith("/r/") else item.url})
    return out


def _rows(door, params: dict) -> list:
    data = door.get_json(f"{API}/posts/search?" + urllib.parse.urlencode({**params, "sort": "desc", "fields": POST_FIELDS}))
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise NetError("schema-drift", "응답의 꼴이 바뀌었다(data 목록이 없다)")
    return rows


def _spots(word: str, body: str) -> list[int]:
    """낱말이 글의 어디어디에 있는가. 영문 낱말은 앞머리로 맞춘다(reward 는 rewards 에도 맞는다) — 여기서는 넓게 받고,
    고르는 일은 뒤에서 rank 낱말로 한다."""
    word = text.norm(word)
    if not word:
        return []
    if word.isascii():
        return [found.start() for found in re.finditer("(?<![0-9a-z])" + re.escape(word), body)]
    out, at = [], body.find(word)
    while at >= 0:
        out.append(at)
        at = body.find(word, at + 1)
    return out


def _strong(words: str) -> list[str]:
    """묶음에서 뜻을 싣는 낱말 — 영문은 세 글자 이상이고 흔한 낱말(or · the …)이 아닌 것, 한글 등은 두 글자 이상. 없으면 첫 낱말."""
    parts = [text.norm(part) for part in words.split()]
    strong = [part for part in parts if (len(part) >= 3 and part not in _STOP if part.isascii() else len(part) >= 2)]
    return strong or parts[:1]


def _mentions(groups, row) -> bool:
    """그 글의 제목 · 본문에 낱말 묶음 하나의 낱말 **둘**이 가까이(NEAR 글자 안에) 있는가 — 차례는 보지 않는다(뜻 있는 낱말이
    하나뿐인 묶음은 그 낱말이 있으면 된다). 글 몇백 개에서 고르는 자리라 아카이브의 검색(낱말이 모두 든 글)보다 넓게 받는다 — 그대로
    하면 긴 묶음은 0건이 된다(11-27 진짜 총괄의 조사: 200개에서 0건). 그래도 긴 글 여기저기에 흩어진 낱말은 그 주제의 표시가 못
    되니 가까이 있어야 한다(같은 날: 흩어진 낱말로 고른 글이 증거의 윗자리를 채웠다). 거르는 일은 뒤에서 rank 낱말로 한다."""
    if not isinstance(row, dict):
        return False
    body = text.norm(f"{row.get('title') or ''} {row.get('selftext') or ''}")
    for words in groups:
        strong = _strong(words)
        spots = [found for found in (_spots(word, body) for word in strong) if found]
        if len(strong) == 1:
            if spots:
                return True
            continue
        if any(abs(one - two) <= NEAR for place, first in enumerate(spots) for second in spots[place + 1:]
               for one in first for two in second):
            return True
    return False


def _recent(door, sub: str, groups, pages: int, today: datetime.date, window_days: int):
    """낱말 없는 글 목록을 받아 낱말 묶음이 가까이 든 글만 고른다 → (쓴 요청 수, 본 글 수, 고른 줄, 못 받은 까닭).

    첫 쪽은 가장 새것부터 받고, 다음 쪽들은 기간을 쪽 수로 나눈 자리에서 받는다 — 글이 많은 곳에서 이어서만 받으면 최근 며칠만
    보게 된다. 나눈 자리가 이미 본 데보다 새것이면(글이 적은 곳) 본 데서 잇고, 건너뛴 자리에 글이 모자라면(기간보다 젊은 곳) 남은
    쪽을 새것 쪽에서 잇는다. 이어 받던 쪽이 덜 차거나 기간 밖에 닿으면 더 받지 않는다."""
    end = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time(), datetime.timezone.utc).timestamp()
    cutoff = end - (window_days + 1) * 86400
    step = window_days * 86400 / max(1, pages)
    used, seen, picked, error = 0, 0, [], None
    front = lowest = None                 # front = 새것 쪽에서 이어 받은 데의 끝 · lowest = 지금까지 본 가장 옛 글
    spread = True
    while used < pages:
        params, jumped, chained = {"subreddit": sub, "limit": LIST_LIMIT}, False, used == 0
        if used:
            mark = end - used * step
            jumped = spread and mark < lowest
            chained = not jumped and (not spread or lowest == front)
            params["before"] = int(mark if jumped else lowest if spread else front)
        used += 1
        try:
            rows = _rows(door, params)
        except NetError as exc:
            error = exc
            break
        seen += len(rows)
        picked += [row for row in rows if _mentions(groups, row)]
        stamps = [stamp for stamp in (_number(row.get("created_utc")) for row in rows if isinstance(row, dict)) if stamp]
        if stamps:
            lowest = min(stamps) if lowest is None else min(lowest, min(stamps))
            if chained:
                front = min(stamps) if front is None else min(front, min(stamps))
        if len(rows) < LIST_LIMIT or not stamps:
            if jumped and front is not None and front >= cutoff:
                spread = False            # 건너뛴 자리에 글이 모자랐다 — 남은 쪽은 새것 쪽에서 잇는다
                continue
            break
        if (front if not spread else lowest) < cutoff:
            break
    return used, seen, picked, error


def _budget(plan) -> tuple[int, int]:
    """(검색 · 목록 요청 수, 댓글을 받을 글 수) — reddit 갈래가 여럿이면 나눠 쓴다(조사 하나가 보내는 요청의 합이 갈래 수만큼
    불어나지 않게)."""
    lanes = sum(query.source == "reddit" for query in plan.queries) or 1
    return max(2, SEARCHES[plan.depth] // lanes), max(1, TREES[plan.depth] // lanes)


def collect(plan, index: int, door, today: datetime.date) -> QueryResult:
    query = plan.queries[index]
    before = door.requests
    searches, trees = _budget(plan)
    found: dict[str, Item] = {}
    failures: list[tuple[str, str]] = []
    notes: list[str] = []
    succeeded = 0

    def take(rows) -> None:
        for row in rows:
            item = _item(row, index)
            if item is not None:
                found.setdefault(item.native_id, item)

    base, extra = divmod(searches, len(query.targets))
    unasked = 0
    for order, sub in enumerate(query.targets):
        share = base + (1 if order < extra else 0)           # 서브레딧마다의 몫 — 고르게, 남는 것은 앞에서부터
        for place, words in enumerate(query.search):
            if share <= 0:
                unasked += len(query.search) - place        # 깊이의 요청 수가 모자라 묻지 않은 (서브레딧 · 낱말 묶음)
                break
            share -= 1
            try:
                rows = _rows(door, {"subreddit": sub, "query": words, "limit": SEARCH_LIMIT})
            except NetError as error:
                if error.status not in FALLBACK:
                    failures.append((error.status, f"r/{sub} 「{words}」: {error.detail}"))
                    continue
                # 이 서브레딧에는 낱말 검색을 더 보내지 않는다 — 남은 몫으로 글 목록을 받아 모든 낱말 묶음을 한꺼번에 본다.
                pages = min(LIST_PAGES[plan.depth], share)
                used, seen, picked, list_error = _recent(door, sub, query.search, pages, today, plan.window_days)
                if seen:
                    take(picked)
                    notes.append(f"r/{sub} 「{words}」: 낱말 검색이 거절돼({error.detail}) 글 목록에서 골랐다 — 본 글 {seen}개 가운데 낱말이 "
                                 f"가까이 든 {len(picked)}개"
                                 + (f"(목록의 뒤쪽은 받지 못했다: {list_error.detail})" if list_error is not None else ""))
                else:
                    why = (list_error.detail if list_error is not None else
                           "글 목록이 비어 있었다" if used else "남은 요청이 없다")
                    failures.append((error.status, f"r/{sub} 「{words}」: 낱말 검색이 거절됐고({error.detail}) 글 목록도 받지 못했다({why})"))
                break
            succeeded += 1
            take(rows)
    items = list(found.values())

    def promise(item: Item) -> float:
        rel = score.relevance(query.rank, item.title, item.text)
        return score.stream(rel, score.freshness(item.date, today, plan.window_days), score.engagement("reddit", item.counts))
    # 댓글은 고르는 낱말이 맞은 글부터 받되, 낱말이 글에 없다고 빼지 않는다 — 검색 낱말로 이미 주제를 골랐고 그 관점은 댓글에 있을 수
    # 있다(남길지는 묶을 때 정한다). 기간 밖의 글에는 청하지 않는다.
    wanted = [item for item in sorted(items, key=lambda item: (-promise(item), item.native_id))
              if not outside(item.date, today, plan.window_days)][:trees]
    missed = 0
    for item in wanted:
        try:
            item.comments_list = _comments(door, item)
        except NetError as error:
            missed += 1
            last = error.detail
    details = [detail for _, detail in failures] + notes
    if missed:
        details.append(f"댓글을 받지 못한 글 {missed}개({last})")
    if unasked:
        # 「보낸 낱말」에는 계획의 낱말이 다 적힌다 — 실제로 묻지 않은 것이 있으면 밝힌다. 정한 깊이대로 한 것이라 상태는 바꾸지 않는다.
        details.append(f"요청 수(깊이 {plan.depth})가 모자라 묻지 않은 서브레딧 · 낱말 묶음 {unasked}개")
    if not succeeded and not notes and failures:
        status = min((status for status, _ in failures), key=lambda name: _SEVERITY.index(name) if name in _SEVERITY else 0)
    elif failures or missed or notes:
        status = "partial"
    else:
        status = "ok" if items else "no-results"
    return QueryResult(index, status, items, " · ".join(details)[:600], door.requests - before)
