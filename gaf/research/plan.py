"""조사 계획 — 총괄이 주는 것을 엄격히 본다(프로그램은 스스로 계획하지 않는다).

바깥으로 나가는 것은 `targets`(서브레딧 이름)와 `search`(검색 낱말)뿐이다. 주제 · 쓸 곳 · 갈래 이름 · 고르는 낱말(`rank`)은
이 PC 안에서만 쓴다 — 그래서 나가는 칸은 꼴과 길이를 좁게 받는다.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

SOURCES = ("reddit", "youtube")
DEPTHS = ("quick", "default", "deep")
WINDOW_DEFAULT, WINDOW_MAX = 365, 1825
QUERIES_MAX, TARGETS_MAX, SEARCH_MAX, RANK_MAX = 4, 4, 3, 20
REDDIT_WORDS_MAX = 4                    # Reddit 갈래의 검색 낱말 묶음 하나에 넣는 낱말 수
_QUERY_KEYS = ("label", "source", "targets", "search", "rank")
_PLAN_KEYS = ("topic", "purpose", "queries", "window_days", "depth")
_SUBREDDIT = re.compile(r"[A-Za-z0-9_]{2,21}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}


class PlanError(ValueError):
    """계획이 틀렸다 — 글에 어느 칸을 어떻게 고칠지 적는다."""


@dataclass(frozen=True)
class Query:
    label: str
    source: str
    targets: tuple[str, ...]
    search: tuple[str, ...]
    rank: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    topic: str
    purpose: str
    queries: tuple[Query, ...]
    window_days: int = WINDOW_DEFAULT
    depth: str = "default"


def _line(value, where: str, low: int, high: int) -> str:
    """한 줄 글 — 줄바꿈 · 겹친 빈칸은 한 칸으로. 제어 문자는 받지 않는다."""
    if not isinstance(value, str):
        raise PlanError(f"{where}: 글로 적는다")
    if _CONTROL.search(value):
        raise PlanError(f"{where}: 제어 문자를 빼고 적는다")
    text = " ".join(unicodedata.normalize("NFC", value).split())
    if not low <= len(text) <= high:
        raise PlanError(f"{where}: {low}~{high}자로 적는다(지금 {len(text)}자)")
    return text


def _lines(value, where: str, count_low: int, count_high: int, low: int, high: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise PlanError(f"{where}: 글의 목록으로 적는다")
    out, seen = [], set()
    for index, item in enumerate(value):
        text = _line(item, f"{where}[{index}]", low, high)
        if text.casefold() not in seen:              # 대소문자만 다른 것은 하나로
            seen.add(text.casefold())
            out.append(text)
    if not count_low <= len(out) <= count_high:
        raise PlanError(f"{where}: {count_low}~{count_high}개를 적는다(지금 {len(out)}개)")
    return tuple(out)


def _query(raw, where: str) -> Query:
    if not isinstance(raw, dict):
        raise PlanError(f"{where}: 객체로 적는다(label · source · targets · search · rank)")
    unknown = sorted(set(raw) - set(_QUERY_KEYS))
    if unknown:
        raise PlanError(f"{where}: 모르는 칸이다 — {', '.join(map(str, unknown))}(쓸 수 있는 칸: {' · '.join(_QUERY_KEYS)})")
    label = _line(raw.get("label"), f"{where}.label", 1, 60)
    source = raw.get("source")
    if source not in SOURCES:
        raise PlanError(f"{where}.source: {' · '.join(SOURCES)} 가운데 하나로 적는다")
    targets_raw = raw.get("targets") or []
    if source == "reddit":
        if not isinstance(targets_raw, (list, tuple)):
            raise PlanError(f"{where}.targets: 서브레딧 이름의 목록으로 적는다")
        names = []
        for index, item in enumerate(targets_raw):
            name = re.sub(r"^/?r/", "", item.strip()) if isinstance(item, str) else ""
            if not _SUBREDDIT.fullmatch(name):
                raise PlanError(f"{where}.targets[{index}]: 서브레딧 이름만 적는다(영문 · 숫자 · _ 2~21자 · 예: gamedesign)")
            if name.casefold() not in {seen.casefold() for seen in names}:
                names.append(name)
        if not 1 <= len(names) <= TARGETS_MAX:
            raise PlanError(f"{where}.targets: 서브레딧 이름을 1~{TARGETS_MAX}개 적는다 — Reddit 은 서브레딧 안에서만 낱말로 찾는다")
        targets = tuple(names)
    else:
        if targets_raw:
            raise PlanError(f"{where}.targets: YouTube 갈래에는 적지 않는다(search 의 낱말로 찾는다)")
        targets = ()
    search = _lines(raw.get("search"), f"{where}.search", 1, SEARCH_MAX, 2, 80)
    for index, words in enumerate(search):
        # 승인 카드에 보이는 글과 실제로 나가는 글이 같아야 한다 — 보이지 않는 글자(너비 없는 글자 · 방향 바꿈 · 태그 문자)를 받지 않는다.
        if any(unicodedata.category(ch) in ("Cf", "Co", "Cn", "Cs") for ch in words):
            raise PlanError(f"{where}.search[{index}]: 보이지 않는 글자가 들어 있다 — 보이는 글자로만 적는다")
    if source == "reddit":
        # 아카이브의 검색도, 물러난 목록의 고르기도 낱말이 모두(또는 가까이) 든 글을 잡는다 — 길수록 0건이 된다(실측 17.60).
        for index, words in enumerate(search):
            if len(words.split()) > REDDIT_WORDS_MAX:
                raise PlanError(f"{where}.search[{index}]: Reddit 의 검색 낱말 묶음은 낱말 넷까지다(지금 {len(words.split())}개) — "
                                "낱말이 모두 든 글만 잡히니 「card reward」처럼 짧게 적고, 다른 낱말은 묶음을 나눠 적는다")
    rank = _lines(raw.get("rank"), f"{where}.rank", 2, RANK_MAX, 1, 40)
    return Query(label, source, targets, search, rank)


def validate(topic, purpose, queries, *, window_days=None, depth=None) -> Plan:
    topic = _line(topic, "topic", 1, 200)
    purpose = _line(purpose, "purpose", 1, 300)
    if not isinstance(queries, (list, tuple)) or not 1 <= len(queries) <= QUERIES_MAX:
        raise PlanError(f"queries: 갈래를 1~{QUERIES_MAX}개 적는다(갈래 하나 = 보고의 한 갈래)")
    built = tuple(_query(raw, f"queries[{index}]") for index, raw in enumerate(queries))
    if window_days is None:
        window_days = WINDOW_DEFAULT
    if type(window_days) is not int or not 1 <= window_days <= WINDOW_MAX:
        raise PlanError(f"window_days: 1~{WINDOW_MAX} 의 정수로 적는다(기본 {WINDOW_DEFAULT})")
    depth = "default" if depth is None else depth
    if depth not in DEPTHS:
        raise PlanError(f"depth: {' · '.join(DEPTHS)} 가운데 하나로 적는다")
    return Plan(topic, purpose, built, window_days, depth)


def to_dict(plan: Plan) -> dict:
    return {"topic": plan.topic, "purpose": plan.purpose, "window_days": plan.window_days, "depth": plan.depth,
            "queries": [{"label": q.label, "source": q.source, "targets": list(q.targets), "search": list(q.search),
                         "rank": list(q.rank)} for q in plan.queries]}


def from_dict(raw) -> Plan:
    if not isinstance(raw, dict):
        raise PlanError("계획은 객체로 적는다")
    unknown = sorted(set(raw) - set(_PLAN_KEYS))
    if unknown:
        raise PlanError(f"모르는 칸이다 — {', '.join(map(str, unknown))}")
    return validate(raw.get("topic"), raw.get("purpose"), raw.get("queries"),
                    window_days=raw.get("window_days"), depth=raw.get("depth"))


def sources(plan: Plan) -> tuple[str, ...]:
    """이 계획이 쓰는 출처(정해진 차례로)."""
    used = {query.source for query in plan.queries}
    return tuple(name for name in SOURCES if name in used)


def sent_words(plan: Plan) -> dict[str, list[str]]:
    """출처마다 실제로 바깥에 나가는 낱말 — 승인 카드와 증거 파일의 머리에 그대로 보인다."""
    out: dict[str, list[str]] = {}
    for query in plan.queries:
        words = out.setdefault(query.source, [])
        for word in (*(f"r/{name}" for name in query.targets), *query.search):
            if word.casefold() not in {seen.casefold() for seen in words}:
                words.append(word)
    return {name: out[name] for name in SOURCES if name in out}


def fingerprint(plan: Plan) -> str:
    """뜻이 같은 계획이면 같은 값(정리한 뒤의 글로 셈한다)."""
    text = json.dumps(to_dict(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def slug(topic) -> str:
    """주제를 폴더 이름에 쓸 줄임으로 — 한글 · 영문 · 숫자만 남기고 `-` 로 잇는다(40자). 경로는 프로그램이 만든다."""
    text = unicodedata.normalize("NFC", str(topic or ""))
    parts = re.findall(r"[0-9A-Za-z가-힣]+", text)
    name = "-".join(parts)[:40].strip("-")
    return "조사" if not name or name.casefold() in _RESERVED else name
