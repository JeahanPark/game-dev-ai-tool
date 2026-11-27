"""점수 — 모두 0 ~ 1. 숫자는 처음 값이다(진짜 주제로 재 보고 고친다 — 실측 17.60)."""
from __future__ import annotations

import datetime
import math

from . import text

TITLE_BONUS = 0.15
RELEVANCE_FULL = 3                      # 고르는 낱말이 이만큼 맞으면 관련도 가득
RELEVANCE_FLOOR = 0.15                  # 이보다 낮으면 주제 밖으로 본다
WEIGHTS = (0.65, 0.25, 0.10)            # 관련 · 신선 · 참여
# 출처마다 「아주 많은」 값 — 그 조사에서 제일 좋은 것이 늘 가득이 되지 않게 절대 기준으로 잰다.
ENGAGEMENT = {
    "reddit": (("score", 2000, 0.6), ("comments", 300, 0.4)),
    "youtube": (("views", 1_000_000, 0.5), ("likes", 50_000, 0.3), ("comments", 5_000, 0.2)),
}


def relevance(rank, title, body) -> float:
    """고르는 낱말 가운데 글에 있는 것의 수 ÷ 3(낱말이 그보다 적으면 그 수) · 제목에도 있으면 조금 더."""
    found = text.hits(rank, f"{title or ''}\n{body or ''}")
    if not found:
        return 0.0
    full = max(1, min(RELEVANCE_FULL, len({text.norm(term) for term in rank if text.norm(term)})))
    value = min(1.0, len(found) / full)
    if text.hits(rank, title or ""):
        value = min(1.0, value + TITLE_BONUS)
    return value


def freshness(date, today: datetime.date, window_days: int) -> float:
    """창 안에서 날짜에 따라 곧게 줄어든다. 날짜를 모르면 낡은 것이 아니라 가운데 값이다."""
    try:
        day = datetime.date.fromisoformat(str(date)[:10])
    except (TypeError, ValueError):
        return 0.5
    age = (today - day).days
    if age <= 0:
        return 1.0
    return max(0.0, 1.0 - age / max(1, window_days))


def _ratio(value, reference) -> float:
    return min(1.0, math.log1p(max(0.0, float(value))) / math.log1p(reference))


def engagement(source, counts) -> float | None:
    """반응 수를 출처의 절대 기준으로 0 ~ 1 로. 숫자가 하나도 없으면 0 이 아니라 None(모름)."""
    total = weight_sum = 0.0
    for key, reference, weight in ENGAGEMENT.get(source, ()):
        value = (counts or {}).get(key)
        if type(value) in (int, float) and math.isfinite(value):
            total += weight * _ratio(value, reference)
            weight_sum += weight
    return total / weight_sum if weight_sum else None


def stream(rel: float, fresh: float, eng: float | None) -> float:
    return WEIGHTS[0] * rel + WEIGHTS[1] * fresh + WEIGHTS[2] * (eng or 0.0)
