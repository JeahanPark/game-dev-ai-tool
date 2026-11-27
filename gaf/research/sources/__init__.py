"""출처 — 이름마다 「무엇을 어디로 보내고 무엇을 받는지」(describe)와 받아 오는 길(collect)을 가진다.

넣은 까닭과 약관의 걸림은 docs/조사/레퍼런스-조사/ 에 있다(2026-11-27 사용자 「넣어줘」 — Reddit · YouTube).
"""
from __future__ import annotations

from . import reddit, youtube

SOURCES = {"reddit": reddit, "youtube": youtube}


def describe_all() -> list[dict]:
    return [module.describe() for module in SOURCES.values()]
