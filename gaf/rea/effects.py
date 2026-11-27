"""REA 도구의 효과 — 어느 부름에 사람의 승인이 있어야 하나(순수 함수).

효과 표시는 도구 목록(`tools/list`)에 없다. MCP 의 네 힌트는 너무 거칠다(readOnly 가 다섯 개뿐이다). 가는 표시는 `binary_session`
의 `result.tool_availability.groups[]` 에 있다 — 무리마다 `{names, surface, effects, annotations, available, reason, remediation}`,
효과 이름은 camelCase(`executesForeignCode` · `mutatesTarget` · `accessesNetwork` …). 6.10.1 은 `{detail: "complete"}` 로 불러야 무리에
effects 가 온다(기본은 요약 — 효과가 없다) · 6.7.0 은 빈 값에 다 준다(2026-11-27 두 판을 이 PC 에서 잼 — 부르는 차례는 host._rea_open).
"""
from __future__ import annotations

from typing import Any

ASK = {"executesForeignCode": "대상의 코드를 돌린다", "mutatesTarget": "대상을 고친다", "mutatesDevice": "기기를 바꾼다",
       "accessesNetwork": "망을 쓴다", "changesUiState": "화면을 조작한다"}
# 대상을 열고 닫는 세션 도구에 붙은 `accessesNetwork` 는 분석 제공자(Ghidra)와 말하는 이 PC 안의 연결이다(6.10.1 에서 봄 · 2026-11-27 —
# 설명 원문 「Open a validated local artifact … and bind its analysis provider」). 지휘자는 자식에게 바깥 제공자의 설정(REA_IDA_MCP_CONFIG ·
# REA_KUNA_COMMAND)을 주지 않는다(setup.SERVER_ENV_KEEP). 이것을 물으면 네이티브 게임을 볼 때마다 승인 카드가 뜬다 — 사용자가 게임마다는
# 묻지 않기로 했다(11-27 「그렇게 하자」). 그 도구에 묻는 효과가 「망」 하나뿐일 때만 넘어가고, 다른 효과가 같이 붙으면 다 묻는다.
PROVIDER_LINK = frozenset({"open_binary", "close_binary", "open_bundle_resources", "reset_investigation"})
UNKNOWN = "효과를 모르는 도구다"
CONSENT = "약관 동의를 적는다"
MONEY = "돈이 들 수 있다"
_MONEY_WORDS = ("build_computer", "budget", "purchase")
_NAME_MAX = 200


def table(session_result: Any) -> dict[str, dict]:
    """`binary_session` 의 structured 결과에서 도구 → {"effects": frozenset, "available": bool, "reason": str}. 꼴을 모르면 {}.

    받는 것: call() 의 `structured`(`{"result": {...}}`) — 그 안의 `result` 나 call() 의 돌려준 값 통째도 받는다. 무리의 `names` ·
    `effects` 가 목록이 아니면 그 무리를 건너뛴다. `available` · `reason` 이 없거나 꼴이 틀리면 못 씀 · "" 로 둔다. 한 도구가 두 무리에
    있으면 효과는 합치고, 둘 다 쓸 수 있을 때만 쓸 수 있다고 본다."""
    try:
        groups = _groups(session_result)
        out: dict[str, dict] = {}
        for group in groups:
            if not isinstance(group, dict):
                continue
            names, flags = group.get("names"), group.get("effects")
            if not isinstance(names, list) or not isinstance(flags, list):
                continue
            effects = frozenset(flag for flag in flags if isinstance(flag, str) and flag)
            available = group.get("available") is True
            reason = group.get("reason") if isinstance(group.get("reason"), str) else ""
            for name in names:
                if not isinstance(name, str) or not name or len(name) > _NAME_MAX:
                    continue
                row = out.get(name)
                if row is None:
                    out[name] = {"effects": effects, "available": available, "reason": reason}
                else:
                    row["effects"] = row["effects"] | effects
                    if row["available"] and not available:
                        row["available"], row["reason"] = False, reason
        return out
    except Exception:  # noqa: BLE001 — REA 가 준 꼴이 어떻든 지휘자는 죽지 않는다(그때는 모든 도구가 「효과를 모르는 도구」가 된다)
        return {}


def _groups(value: Any) -> list:
    for _ in range(3):                                   # call() 의 값 → structured → result 를 차례로 벗긴다
        if not isinstance(value, dict):
            return []
        availability = value.get("tool_availability")
        if isinstance(availability, dict):
            groups = availability.get("groups")
            return groups if isinstance(groups, list) else []
        value = value.get("structured") if "structured" in value else value.get("result")
    return []


def ask_words(name: str, row: dict | None) -> list[str]:
    """사람의 승인이 있어야 하는 까닭 — [] 면 묻지 않는다. 효과(ASK 의 차례) · 이름(약관 동의 · 돈) · 표에 없는 도구."""
    words: list[str] = []
    if row is None:
        words.append(UNKNOWN)
    else:
        effects = row.get("effects") if isinstance(row, dict) else None
        flags = {flag for flag in effects if isinstance(flag, str)} if isinstance(effects, (set, frozenset, list, tuple)) else None
        if flags is None:
            words.append(UNKNOWN)                         # 표의 줄이 꼴이 틀렸다 — 모르는 것으로 본다
        else:
            asked = [flag for flag in ASK if flag in flags]
            if not (asked == ["accessesNetwork"] and name in PROVIDER_LINK):
                words.extend(ASK[flag] for flag in asked)
    lowered = str(name or "").casefold()
    if "consent" in lowered:
        words.append(CONSENT)
    if any(part in lowered for part in _MONEY_WORDS):
        words.append(MONEY)
    return words
