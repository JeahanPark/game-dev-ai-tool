"""세션 엔진의 PreToolUse 훅이 부르는 작은 프로그램 — 도구를 써도 되는지 지휘자에게 묻는다.

Codex 는 문(도구를 쓰기 전에 묻는 콜백)을 SDK 로 주지 않는다. 대신 훅 명령을 돌려 준다 — 그 명령이 이것이다:
  Codex(도구 쓰기 직전) → 이 프로그램(표준 입력 = 훅 입력 JSON) → 지휘자 `POST /api/gate` → 허용이면 조용히 끝, 거부면 거부 JSON.

- 표준 라이브러리만 쓴다(도구를 쓸 때마다 뜨므로 가볍게).
- 닿지 못하면 막는다. 이 프로그램이 죽으면(종료 코드 ≠ 0) Codex 는 도구를 그냥 돌린다(11-12 실측) — 그래서 어떤 오류든 잡아
  거부 JSON 을 내고 0 으로 끝난다. 프로그램이 아예 못 뜨거나 죽은 경우는 훅 명령이 스스로 거부를 내고(`engines/codex.py`
  `hook_command`), 엔진이 그 표시를 보고 턴을 멈춘다.
- 거부 JSON 은 ASCII 로만 쓴다(Windows 에서는 PowerShell 을 거쳐 나가 글자판이 바뀔 수 있다).
- 누가 물었는지는 환경변수 GAF_SESSION(지휘자가 세션을 열 때 준 표)으로 가린다. 값은 어디에도 찍지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

TIMEOUT = 20        # 지휘자의 답을 기다리는 시간(초) — 훅 제한 시간(엔진이 30초로 건다)보다 짧게


def deny(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                               "permissionDecisionReason": reason or "지휘자가 막았다"}}, ensure_ascii=True)


def ask(port: int, token: str, data: dict, timeout: float = TIMEOUT) -> dict:
    body = json.dumps({"tool_name": data.get("tool_name"), "tool_input": data.get("tool_input"), "cwd": data.get("cwd")},
                      ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/gate", data=body,
                                     headers={"Content-Type": "application/json", "X-GAF-Session": token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # PC의 프록시 설정을 물려주지 않는다
    with opener.open(request, timeout=timeout) as response:
        answer = json.loads(response.read().decode("utf-8"))
    return answer if isinstance(answer, dict) else {}


def decide(raw: bytes, environ) -> str:
    """훅 입력 → 낼 글(허용이면 빈 글 · 거부면 거부 JSON)."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        return deny("문: 훅 입력을 읽지 못했다 — 막는다")
    if not isinstance(data, dict):
        return deny("문: 훅 입력이 객체가 아니다 — 막는다")
    if data.get("hook_event_name") not in (None, "PreToolUse"):
        return ""
    token, port = environ.get("GAF_SESSION") or "", environ.get("GAF_PORT") or ""
    if not token or not port.isdigit():
        return deny("문: 지휘자 주소나 세션 표가 없다(GAF_PORT · GAF_SESSION) — 막는다")
    try:
        answer = ask(int(port), token, data)
    except Exception as exc:  # noqa: BLE001 — 닿지 못하면 막는다(열린 채로 지나가지 않게)
        return deny(f"문: 지휘자에 닿지 못했다({type(exc).__name__}) — 막는다. 지휘자가 떠 있는지 본다")
    if answer.get("allow") is True:
        return ""
    return deny(str(answer.get("reason") or answer.get("error") or "지휘자가 막았다"))


def main() -> int:
    try:
        text = decide(sys.stdin.buffer.read(), os.environ)
    except BaseException as exc:  # noqa: BLE001
        text = deny(f"문: 훅 안의 오류({type(exc).__name__}) — 막는다")
    if text:
        sys.stdout.write(text)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
