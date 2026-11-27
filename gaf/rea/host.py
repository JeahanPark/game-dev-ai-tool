"""지휘자가 REA 를 쥐고 전용 역할에게 내어 주는 쪽(1.17.0) — `Conductor` 가 이 섞음 클래스(`ReaHost`)를 물려받는다.

REA(`rea-agents` · MIT)는 도구가 200개가 넘는 MCP 서버다. 세션에 그대로 붙이지 않는다 — 도구 목록만 3MB 라 모델의 문맥을 채우고,
어느 호출이 대상을 돌리거나 고치는지 엔진의 문에서는 가릴 수 없다(효과 표시가 목록에 없다 · 11-27 이 PC 에서 돌려 봄). 그래서
지휘자가 REA 를 자식 프로세스로 띄워 직접 말하고, 역할의 세션에는 우리 도구 둘만 붙인다:

- `rea_tools` — 목록에서 찾기 · 도구 하나의 받는 값 보기
- `rea_call`  — 이름으로 부르기(다 부를 수 있다 — 사용자 11-27 「다넣는게 좋을것같은데」)

부를 때 지휘자가 본다: 켜져 있나(PC 마다 한 번 · 사람) → 경로(접근 값 폴더 · 쓰는 자리) → 효과(대상을 돌리거나 고치는 것 · 망 · 기기 · 돈 ·
약관 동의는 사람의 승인) → 부른다 → 결과를 줄여 준다(온 것은 작업 폴더에 둔다). 사람에게 묻는 것은 도구 안에서 기다리지 않는다:
도구는 「사람에게 묻겠다 — 답을 마쳐라」로 바로 돌아오고, 그 턴이 끝난 뒤 지휘자가 카드를 올려 답을 받고 같은 일을 이어서 부른다
(레퍼런스 조사와 같은 꼴 — 엔진의 도구 시간 제한에 걸리지 않고 승인이 오래 걸려도 된다).

게임마다는 묻지 않는다(사용자 11-27 「그렇게 하자」) — REA 를 그 PC 에서 처음 켤 때 한 번 법의 선을 보이고 승인을 받는다.

내리기(11-27 다른 눈의 검수로 고침): REA 는 취소 알림(`notifications/cancelled`)을 무시한다(6.10.1 실측 — 취소해도 결과가 온다). 도는
분석을 진짜로 멈추는 길은 자식을 나무째 끄는 것뿐이다. 그래서 일이 끝날 때(`_finish_task`) · 역할을 멈출 때(`_halt` · 끌 때)
그 역할에 도는 호출이 있으면 REA 를 내리고(다음 부름이 다시 띄운다), 세션이 닫힐 때는 늘 내린다. 내릴 때는 먼저 그 역할의
`ReaSession` 을 새 것으로 바꿔 끼우고(`_rea_detach` — 기다림 없이) 옛 것을 배경에서 내린다(`_rea_shutdown`) — 늦게 끝난 닫기가 다음
일이 띄운 REA 를 건드리지 않는다.

자식의 임시 폴더(1.17.1): 띄울 때마다 따로 하나다(`<밑자리>/<순번>-<난수>` — TEMP · TMP · TMPDIR). 밑자리는 Windows 면 PC 의 임시 폴더
아래 `gaf-rea/<작업 폴더의 지문>/`(gaf/paths.py 의 rea_temp_home), 옮길 자리가 없거나 다른 OS 면 작업 폴더 안 `tmp/` 다. 1.17.0 은 늘
작업 폴더 안에 두었는데, 경로에 점으로 시작하는 마디(`.gaf`)가 있으면 REA 가 Ghidra 실행 폴더를 그 마디가 없는 윗폴더 — 프로젝트 뿌리 —
로 옮겨, 뿌리에 대상의 사본까지 든 폴더가 생겼다(11-28 이 PC 에서 잼). 띄우기 전에 밑자리에 남은 폴더(이 지휘자가 쥔 것이 아닌 것 —
지난번 지휘자가 나무째 죽으며 남긴 것)를 치우고, 내릴 때는 제 것을 지운 뒤 빈 윗폴더도 지운다.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import functools
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import paths as gaf_paths

COMMANDS = ("rea_tools", "rea_call")
CALL_WAIT = 75.0                 # 도구 한 번의 마감(초) — 들어올 때 잡는다(띄우기 + 기다리기). Codex 의 도구 시간 제한(90초) 안쪽. 넘으면 「도는 중」으로 돌려주고 이어서 받게 한다
WAIT_FLOOR = 1.0                 # 마감이 거의 다 갔어도 기다리는 바닥(초) — 띄우기는 이만큼, 호출은 이것과 CALL_WAIT 의 2% 가운데 짧은 쪽만큼(시간을 줄인 시험에서도 비례해 준다). 진짜 값에서 도구 한 번은 77초를 넘지 않는다
CALL_MAX = 3600.0                # REA 호출 하나의 상한(초) — 큰 실행 파일의 첫 분석은 몇 분씩 걸린다
OPEN_MAX = 180.0                 # 띄우기 전체(initialize · 목록 · 효과 표)의 상한(초) — 넘으면 띄우던 것을 끄고 실패로 돌린다
CLOSE_MAX = 15.0                 # REA 하나를 닫는 시간의 상한(초) — 못 닫아도 이어 간다(나무째 끄기는 손이 한다)
SESSION_WAIT = 60.0              # 띄운 뒤 효과 표(binary_session)를 기다리는 시간(초) — 6.10.1 은 첫 호출이 4초쯤 걸린다(나머지 코드를 그때 싣는다)
FOUND_CHARS = 1500               # 찾기의 답에서 도구 하나에 쓰는 글자 수(여덟 개까지 준다 — 다 보려면 name 으로 본다)
ASKS_PER_TASK = 6                # 한 일에서 사람에게 묻는 횟수(켜기 · 도구 승인)의 상한
RUNNING_MAX = 4                  # 한 역할이 같이 돌릴 수 있는 호출의 수
ISSUED_KEEP = 256                # 내준 호출 번호 → 부른 일의 번호를 쥐는 수(앞 일의 번호를 알아보려고 — 오래된 것부터 잊는다)
ARGS_SHOWN = 1500                # 승인 카드에 싣는 받는 값의 글자 수 — 이보다 긴 값은 사람에게 다 보일 수 없어 청하지 않는다(카드의 글은 1,900자까지다)
EXPORTS_FILE = "rea-exports.json"  # 내보낸 증거 묶음의 지문을 적어 두는 곳(.gaf 바로 아래 — 세션의 Write · Edit · 셸 · REA 모두에게 닫힌 자리)
EXPORTS_KEEP = 200               # 역할마다 쥐는 내보낸 묶음의 수(오래된 것부터 잊는다)
TEMP_KEYS = ("TEMP", "TMP", "TMPDIR")
_TEMP_HOME = re.compile(r"[0-9a-f]{16}")         # 옮긴 밑자리의 이름(작업 폴더의 지문 — paths.rea_temp_home)
# 임시 폴더의 치우기와 만들기를 한 덩이로 한다 — 같은 역할의 두 띄우기(떼어 낸 옛 것의 늦은 띄우기와 새 것)가 서로 막 만든 폴더를 「남은 것」
# 으로 치우지 않게. 쥔 것의 목록도 이 잠금 안에서 읽고, 새 폴더도 이 잠금 안에서 ReaSession.temps 에 넣는다.
_TEMP_LOCK = threading.Lock()
HEAD = "[지휘자 알림 — 사람의 말이 아니다] "
TAIL = ("\n이 일을 이어서 한다: 그 도구 없이 할 수 있는 데까지 하고 마지막 답을 다시 쓴다(한 일 · 바뀐 파일 · 확인한 것). 못 본 것은 "
        "「못 봄」과 그 까닭으로 적는다. 다른 길(셸 명령 · 게임을 직접 띄우기)로 돌아가 하지 않는다.")
CLOSED = "그 세션의 REA 를 내렸다(세션이 닫혔거나 일이 끝났거나 멈췄다) — 필요하면 다시 부른다"
OPENING = {"ok": True, "text": "REA 를 띄우는 중이다(처음 한 번은 몇 초 걸린다) — 같은 부름을 다시 한다"}
STOPPED = {"ok": False, "error": "그 호출은 멈췄다(REA 를 내렸다) — 필요하면 다시 부른다"}


@dataclass
class ReaSession:
    """역할 하나가 쥔 REA — 자식 프로세스 하나와 그 역할의 승인 상태.

    내릴 때는 이 것을 통째로 떼어 내고(`_rea_detach` — 새 것으로 바꿔 끼운다) 옛 것을 내린다(`_rea_shutdown`). 떼어 낸 것은
    `closed` 다 — 늦게 끝난 띄우기는 제 손을 닫고, 새 호출을 받지 않는다."""

    name: str = ""                                       # 역할 이름(사건에 싣는다)
    client: Any = None                                   # gaf.rea.client.ReaClient
    tools: list = field(default_factory=list)            # tools/list 의 줄(이름 · 설명 · 받는 값)
    table: dict = field(default_factory=dict)            # 도구 → 효과(effects.table)
    work: Path | None = None                             # .gaf/rea/<역할>/ — 결과 · 증거 묶음의 자리(자식의 TEMP 는 Windows 면 PC 의 임시 폴더의 gaf-rea/<지문>/<순번>-<난수>, 아니면 이 아래 tmp/<순번>-<난수>)
    opening: Any = None                                  # 띄우는 중인 작업 — 그 작업이 끝날 때 비운다(done 콜백 · 기다리던 쪽이 끊겨도 같이 부른 쪽이 둘을 띄우지 않게)
    grants: set = field(default_factory=set)             # 이 일에서 사람이 승인한 도구
    denied: set = field(default_factory=set)             # 이 일에서 사람이 거절한 도구
    pending: dict | None = None                          # 턴이 끝나면 사람에게 물을 것 — {"kind": "enable"} 또는 {"kind": "tool", "name", "words", "arguments"}
    asked: int = 0                                       # 이 일에서 사람에게 물은 횟수
    calls: dict = field(default_factory=dict)            # 끝을 아직 받지 않은 호출 번호 → 작업
    issued: dict = field(default_factory=dict)           # 내준 호출 번호 → 부른 일의 번호(떼어 내도 옮긴다 — 번호를 다시 쓰지 않는다)
    call_n: int = 0
    task: str = ""                                       # 승인 상태가 속한 일의 번호(일이 바뀌면 비운다)
    temps: list = field(default_factory=list)            # 띄울 때마다 만든 자식의 임시 폴더 — 내릴 때 제 것만 치운다
    closed: bool = False                                 # 떼어 냈다(내리는 중이거나 내렸다)
    down: Any = None                                     # 내리는 작업 — 여러 번 불러도 한 번만 내린다


class ReaHost:
    """Conductor 에 섞는 REA 쪽 메서드. 지휘자의 것을 쓴다: paths · roles · _workers · _closing · store · _talk · _touch ·
    _request_action · _research_answers · _research_pause · _review_resume · _running · _poke · _turn · _close_session · _brief.

    지휘자가 부르는 자리: `_follow_ups` → `_rea_round`(턴 뒤 승인) · `_finish_task` 와 `_halt` · `stop` → `_rea_reap`(도는 호출이 있으면
    내린다) · `_close_session` → `_rea_detach` 뒤 `_rea_shutdown` · `stop` 끝 → `_rea_close_all`."""

    # ------------------------------------------------------------------ 누구에게 붙나
    def rea_role(self, name) -> bool:
        """그 역할의 세션에 REA 도구(`rea_tools` · `rea_call`)를 붙이나 — 역할 글 머리의 `rea: true` · 총괄은 아니다."""
        role = self.roles.get(name) if isinstance(name, str) else None
        return bool(role is not None and not role.lead and getattr(role, "rea", False))

    def _rea_session(self, name: str) -> ReaSession:
        sessions = self.__dict__.setdefault("_rea_sessions", {})
        if name not in sessions:
            sessions[name] = ReaSession(name=name)
        return sessions[name]

    def _rea_task(self, w) -> ReaSession:
        """그 역할의 REA 상태 — 일이 바뀌었으면 승인 · 거절 · 물은 횟수를 비운다(도는 프로세스는 그대로 둔다)."""
        state = self._rea_session(w.name)
        task = str(getattr(w, "task_id", "") or "")
        if state.task != task:
            state.task, state.grants, state.denied, state.pending, state.asked = task, set(), set(), None, 0
        return state

    def _rea_work(self, name: str) -> Path:
        return self.paths.state_dir / "rea" / name

    # ------------------------------------------------------------------ 도구 둘의 알맹이
    def _rea_refused(self, sender) -> tuple[Any, str]:
        w = self._workers.get(sender) if isinstance(sender, str) else None
        if w is None or not self.rea_role(sender):
            return None, "REA 도구는 역할 글에 `rea: true` 를 둔 역할의 세션만 쓴다"
        if self._closing:
            return None, "지휘자가 꺼지는 중이다"
        if w.busy and w.stop_requested:
            return None, "멈춘 턴이다 — 부르지 않았다"
        return w, ""

    async def _rea_off(self, w, state: ReaSession) -> dict | None:
        """REA 가 이 PC 에서 꺼져 있거나 안 깔렸으면 역할에게 돌려줄 답(사람에게 묻는 것을 걸어 둔다). 켜져 있으면 None."""
        from . import setup as rea_setup
        from . import state as rea_state

        ready = await asyncio.to_thread(lambda: rea_state.enabled() and rea_setup.server_argv() is not None)
        if ready:
            return None
        return self._rea_ask(w, state, {"kind": "enable"}, "REA 가 이 PC 에서 꺼져 있다(또는 아직 깔리지 않았다)")

    def _rea_ask(self, w, state: ReaSession, pending: dict, why: str) -> dict:
        """사람에게 물을 것을 걸어 두고 역할에게 돌려줄 답을 만든다 — 묻는 것은 이 턴이 끝난 뒤 지휘자가 한다.

        한 턴에 물을 것은 하나다. 이미 걸린 것이 있으면 덮지 않고 알린다 — 덮으면 카드는 하나만 뜨는데 둘 다 「묻겠다」를 받았다
        (11-27 다른 눈의 검수). 같은 것을 또 청하면 앞의 답을 그대로 준다. 어느 쪽도 묻는 횟수를 쓰지 않는다."""
        if not (w.busy and w.task_id and getattr(w, "research_open", False)):
            return {"ok": False, "error": f"{why} — 사람의 승인이 있어야 하는데, 지금 턴에서는 지휘자가 사람에게 물어 줄 수 없다"
                                          "(일을 하는 턴이 아니다). 일로 맡겨졌을 때 다시 부른다"}
        held = state.pending
        if held is not None:
            if (held.get("kind"), held.get("name"), held.get("key")) == (pending.get("kind"), pending.get("name"), pending.get("key")):
                return _waiting(why)
            what = held.get("name") if held.get("kind") == "tool" else "REA 켜기"
            return {"ok": False, "error": f"{why} — 사람에게 물을 것이 이미 하나 걸려 있다({what}) — 그 답이 온 뒤에 다시 청한다"}
        if state.asked >= ASKS_PER_TASK:
            return {"ok": False, "error": f"{why} — 이 일에서 사람에게 이미 {ASKS_PER_TASK}번 물었다. 더 묻지 않는다 — 못 본 것으로 적고 "
                                          "마무리한다"}
        state.pending = pending
        return _waiting(why)

    async def rea_tools_request(self, query, name, *, sender) -> dict:
        """REA 의 도구 목록에서 찾거나(`query`) 도구 하나의 받는 값을 본다(`name`)."""
        from . import gateway

        deadline = _now() + CALL_WAIT                 # 도구 한 번의 마감 — 띄우기가 길어도 이 안에서 돌아온다(rea_call 과 같다)
        w, error = self._rea_refused(sender)
        if error:
            return {"ok": False, "error": error}
        state = self._rea_task(w)
        off = await self._rea_off(w, state)
        if off is not None:
            return off
        try:
            if not await self._rea_open(w, state, deadline):
                return dict(OPENING)
        except Exception as exc:  # noqa: BLE001 — 못 띄운 까닭을 역할에게 그대로 준다
            return {"ok": False, "error": f"REA 를 띄우지 못했다: {_brief(str(exc), 400)}"}
        wanted = str(name or "").strip()
        if wanted:
            tool = next((row for row in state.tools if row.get("name") == wanted), None)
            if tool is None:
                near = ", ".join(row["name"] for row in gateway.search(state.tools, wanted, 6)) or "없음"
                return {"ok": False, "error": f"없는 도구다: {wanted} — 비슷한 이름: {near}"}
            return {"ok": True, "text": gateway.describe(tool, full=True) + self._rea_effect_line(wanted, state)}
        found = gateway.search(state.tools, str(query or ""), 8)
        if not found:
            return {"ok": True, "text": f"찾은 도구가 없다(전체 {len(state.tools)}개) — 낱말을 바꿔 본다(영문 · 도구 이름의 낱말)"}
        parts = [gateway.describe(row, limit=FOUND_CHARS) + self._rea_effect_line(row["name"], state) for row in found]
        return {"ok": True, "text": f"REA 도구 {len(state.tools)}개 가운데 {len(found)}개:\n\n" + "\n\n".join(parts)
                + "\n\n(받는 값 전체는 name 에 도구 이름을 넣어 다시 본다)"}

    def _rea_effect_line(self, name: str, state: ReaSession) -> str:
        from . import effects

        row = state.table.get(name)
        words = effects.ask_words(name, row)
        notes = []
        if row is not None and not row.get("available", True):
            notes.append(f"이 PC 에서는 지금 못 쓴다({row.get('reason') or '까닭 모름'})")
        if words:
            notes.append("부르면 사람의 승인을 거친다 — " + " · ".join(words))
        return ("\n  " + " / ".join(notes)) if notes else ""

    async def rea_call_request(self, name, arguments, *, sender, wait=None) -> dict:
        """REA 도구 하나를 부른다. 오래 걸리면 「도는 중」으로 돌아오고, `wait` 에 그 번호를 넣어 이어서 받는다.

        마감은 들어올 때 잡는다: 처음 부를 때 띄우는 시간까지 한 도구 안에서 다 쓰면 Codex 의 도구 시간(90초)을 넘어, 모델은 시간
        넘음만 받고 번호를 모른 채 호출은 돌았다 — 다시 부르면 같은 분석이 둘 돌았다(11-27 다른 눈의 검수). 그래서 띄우기가 마감을
        넘으면 부르지 않고 「띄우는 중」으로 돌려주고(띄우기는 배경에서 이어진다), 띄운 뒤에는 남은 시간만 기다린다."""
        from . import effects, gateway
        from .. import paths as gaf_paths

        deadline = _now() + CALL_WAIT
        w, error = self._rea_refused(sender)
        if error:
            return {"ok": False, "error": error}
        state = self._rea_task(w)
        if wait:
            return await self._rea_wait(state, str(wait), deadline)
        tool = str(name or "").strip()
        if not tool:
            return {"ok": False, "error": "name 에 도구 이름을 넣는다(찾기는 rea_tools)"}
        if not isinstance(arguments, dict):
            if arguments not in (None, ""):
                return {"ok": False, "error": "arguments 는 JSON 객체다"}
            arguments = {}
        off = await self._rea_off(w, state)
        if off is not None:
            return off
        try:
            if not await self._rea_open(w, state, deadline):
                return dict(OPENING)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"REA 를 띄우지 못했다: {_brief(str(exc), 400)}"}
        if not any(row.get("name") == tool for row in state.tools):
            near = ", ".join(row["name"] for row in gateway.search(state.tools, tool, 6)) or "없음"
            return {"ok": False, "error": f"없는 도구다: {tool} — 비슷한 이름: {near}(찾기는 rea_tools)"}
        row = state.table.get(tool)
        flags = frozenset((row or {}).get("effects") or ())
        # 임시 자리(Windows — PC 의 임시 폴더의 gaf-rea)는 띄울 때와 같은 셈으로 넘긴다(_make_temp) — 제 자리만 받는다
        refusal = gateway.check_paths(tool, arguments, flags, work=state.work, secrets=gaf_paths.run_dir(), root=self.paths.root,
                                      temp_root=gaf_paths.rea_temp_root(),
                                      temp_home=gaf_paths.rea_temp_home(Path(state.work).resolve()))
        if refusal:
            return {"ok": False, "error": refusal}
        if tool == "import_evidence_bundle":
            # 들이는 것은 지휘자가 내보낸 것으로 적어 둔 묶음만 — 지어 쓴 묶음을 들였다가 다시 내보내면 「REA 가 내보낸 묶음」이 된다
            # (11-27 다른 눈의 검수). 제가 내보낸 묶음을 다음 일에서 들여 이어 보는 것은 된다.
            refusal = await asyncio.to_thread(self._rea_import_refusal, w.name, arguments.get("path"))
            if refusal:
                return {"ok": False, "error": refusal}
        words = effects.ask_words(tool, row)
        key = _grant_key(tool, arguments)
        if words and key not in state.grants:
            if tool in state.denied:
                return {"ok": False, "error": f"사람이 이 일에서 {tool} 을 쓰지 않겠다고 했다 — 다시 청하지 않는다. 못 본 것으로 적는다"}
            # 승인은 **그 받는 값 그대로의 부름**에 묶는다 — 도구 이름에 묶으면, 한 번 승인받은 실행 도구를 다른 명령 · 스크립트 · 실행
            # 파일로 묻지 않고 부를 수 있었다(REA 는 샌드박스 밖에서 돈다 · 11-27 다른 눈의 검수). 그래서 사람이 승인하는 값을 카드에
            # 끝까지 싣는다 — 다 실을 수 없게 긴 값은 청하지 않는다(앞 600자만 실려 뒤의 값이 숨었다).
            shown = _plain(json.dumps(arguments, ensure_ascii=False, sort_keys=True))
            if len(shown) > ARGS_SHOWN:
                return {"ok": False, "error": f"{tool} 은(는) {' · '.join(words)} — 사람의 승인이 있어야 하는데 받는 값이 길어({len(shown):,}자) "
                                              f"사람에게 다 보일 수 없다. {ARGS_SHOWN:,}자 안으로 줄여서 부른다"}
            return self._rea_ask(w, state, {"kind": "tool", "name": tool, "words": words, "arguments": shown, "key": key},
                                 f"{tool} 은(는) {' · '.join(words)}")
        running = [number for number, job in state.calls.items() if not job.done()]
        if len(running) >= RUNNING_MAX:
            return {"ok": False, "error": f"도는 호출이 이미 {len(running)}개다({', '.join(running)}) — wait 로 끝을 받은 뒤에 부른다"}
        # 쓰는 자리의 윗폴더를 만들어 준다 — REA 는 만들어 주지 않고(「Evidence output directory does not exist」 · 11-27 진짜 6.10.1) 역할은
        # 작업 폴더에 폴더를 만들 수 없다. 그 자리 자체는 만들지 않는다(꺼낼 자리가 이미 있으면 REA 가 거절한다).
        await asyncio.to_thread(_make_parents, gateway.output_paths(tool, arguments, flags), state.work)
        # 띄우는 사이(기다림이 있었다) 멈췄거나 · 꺼지기 시작했거나 · 그 REA 를 내렸으면 부르지 않는다 — 주인 없는 호출이 남지 않게.
        w, error = self._rea_refused(sender)
        if error:
            return {"ok": False, "error": error}
        if state.closed or state.client is None:
            return {"ok": False, "error": CLOSED}
        state.call_n += 1
        number = f"c-{state.call_n}"
        client, work = state.client, state.work

        async def run() -> str:
            result = await client.call(tool, arguments, timeout=CALL_MAX)
            if tool == "export_evidence_bundle" and result.get("ok") and not result.get("is_error"):
                # 내보낸 묶음의 지문을 적어 둔다 — 형식 확인과 들이기가 그것과 견준다(세션이 셸로 고친 묶음이 통과하지 못하게)
                await asyncio.to_thread(self._rea_note_export, w.name, arguments.get("path"))
            return await asyncio.to_thread(gateway.shape, tool, result, work=work)
        job = asyncio.get_running_loop().create_task(run(), name=f"gaf-rea:{w.name}:{number}")
        job.add_done_callback(_consume)
        state.calls[number] = job
        state.issued[number] = state.task
        while len(state.issued) > ISSUED_KEEP:
            state.issued.pop(next(iter(state.issued)))
        self.store.emit("rea.called", role=w.name, task=w.task_id or None, data={"tool": tool, "call": number})
        return await self._rea_wait(state, number, deadline)

    async def _rea_wait(self, state: ReaSession, number: str, deadline: float | None = None) -> dict:
        """그 호출의 끝을 마감까지 기다린다. 지금 일의 호출만 받는다 — 앞 일의 호출은 그 일이 끝날 때 거뒀다(_rea_reap)."""
        job = state.calls.get(number)
        owner = state.issued.get(number)
        if owner is not None and owner != state.task:
            return {"ok": False, "error": f"없는 호출 번호다: {number} — 앞 일의 호출은 그 일이 끝날 때 거뒀다(이 일의 호출이 아니다). "
                                          "필요하면 다시 부른다"}
        if job is None:
            # 번호는 지휘자가 사는 동안만 이어진다(c-N) — 지휘자를 다시 켜면 1부터다. 옛 번호로 다른 호출의 결과를 받지 않게 알린다.
            why = ("그 끝은 이미 받았거나, 그 사이 REA 를 내려 멈췄다" if owner is not None else
                   "지휘자가 다시 떴거나 그 일이 끝나 그 호출은 사라졌다")
            return {"ok": False, "error": f"없는 호출 번호다: {number} — {why}. 필요하면 다시 부른다"}
        limit = CALL_WAIT if deadline is None else max(min(WAIT_FLOOR, CALL_WAIT * 0.02), deadline - _now())
        try:
            text = await asyncio.wait_for(asyncio.shield(job), limit)
        except asyncio.TimeoutError:
            return {"ok": True, "text": f"아직 도는 중이다(호출 {number} · {CALL_WAIT:g}초 넘음) — rea_call 을 "
                                        f'{{"wait": "{number}"}} 로 다시 불러 결과를 받는다(같은 도구를 새로 부르지 않는다).'}
        except asyncio.CancelledError:
            # 부른 쪽이 끊긴 것이면 그대로 올린다(호출은 돈다 — 다음 턴이 wait 로 받는다). 호출이 멈춘 것이면(REA 를 내렸다) 도구의
            # 답으로 돌려준다 — 올리면 그 턴이 「턴을 끊었다」로 끝났다(11-27 다른 눈의 검수).
            if _cancelling() or not job.cancelled():
                raise
            state.calls.pop(number, None)
            return dict(STOPPED)
        except Exception as exc:  # noqa: BLE001
            state.calls.pop(number, None)
            return {"ok": False, "error": f"REA 호출이 실패했다: {_brief(str(exc), 400)}"}
        state.calls.pop(number, None)
        return {"ok": True, "text": text}

    # ------------------------------------------------------------------ 내보낸 묶음의 지문
    def _rea_exports_file(self) -> Path:
        return self.paths.state_dir / EXPORTS_FILE

    def _rea_exports(self, name: str) -> dict[str, str]:
        """그 역할이 내보낸 묶음들 — {check.export_key(경로): 내보낼 때의 지문}. 형식 확인(`check.lines` 의 exports)에 넘긴다."""
        from ..paths import _read_json

        data = _read_json(self._rea_exports_file())
        rows = data.get(name) if isinstance(data, dict) else None
        return {key: row["sha256"] for key, row in rows.items()
                if isinstance(key, str) and isinstance(row, dict) and isinstance(row.get("sha256"), str)} if isinstance(rows, dict) else {}

    def _rea_note_export(self, name: str, path) -> None:
        """`export_evidence_bundle` 이 돌아온 그 파일의 지문을 `.gaf/rea-exports.json` 에 적는다(스레드에서 부른다 · 못 적어도 넘어간다 —
        그때는 형식 확인이 「적어 둔 파일이 아니다」로 알린다). 그 파일은 세션의 Write · Edit · 셸 · REA 모두에게 닫힌 자리다."""
        from . import check as rea_check
        from ..paths import _atomic_json, _read_json, file_lock

        try:
            if not isinstance(path, str) or not os.path.isfile(path):
                return
            row = {"sha256": rea_check.fingerprint(path), "bytes": os.path.getsize(path),
                   "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
            target = self._rea_exports_file()
            with file_lock(target):
                data = _read_json(target)
                data = data if isinstance(data, dict) else {}
                rows = data.get(name) if isinstance(data.get(name), dict) else {}
                rows.pop(rea_check.export_key(path), None)
                rows[rea_check.export_key(path)] = row                # 다시 내보낸 것은 맨 뒤로(가장 새것)
                data[name] = dict(list(rows.items())[-EXPORTS_KEEP:])
                _atomic_json(target, data)
        except Exception:  # noqa: BLE001
            pass

    def _rea_import_refusal(self, name: str, path) -> str:
        """`import_evidence_bundle` 을 받을지 — "" 면 받는다. 이 역할이 내보낸 것으로 적어 둔 묶음이고 지문이 그대로일 때만."""
        from . import check as rea_check

        if not isinstance(path, str) or not path.strip():
            return ""                                             # 꼴이 틀린 값은 REA 가 까닭을 알려 준다
        try:
            wanted = self._rea_exports(name).get(rea_check.export_key(path))
            if not wanted:
                return ("들이는 것은 이 역할이 `export_evidence_bundle` 로 내보낸 것으로 지휘자가 적어 둔 묶음만이다 — 그 경로를 준다"
                        "(다른 데서 만든 묶음은 들이지 않는다)")
            if not os.path.isfile(path) or rea_check.fingerprint(path) != wanted:
                return "그 묶음이 내보낸 뒤에 바뀌었다(지휘자가 적어 둔 지문과 다르다 · 또는 파일이 없다) — 들이지 않는다"
        except OSError as exc:
            return f"그 묶음을 읽지 못했다({type(exc).__name__}) — 들이지 않는다"
        return ""

    def _rea_used(self, name: str, task: str) -> bool:
        """그 역할이 그 일에서 REA 도구를 불렀나 — 뜯어본 일인데 알아볼 보고가 없을 때 알리려고 본다."""
        state = (self.__dict__.get("_rea_sessions") or {}).get(name)
        return bool(state is not None and task and any(owner == task for owner in state.issued.values()))

    # ------------------------------------------------------------------ 자식 프로세스
    async def _rea_open(self, w, state: ReaSession, deadline: float | None = None) -> bool:
        """그 역할의 REA 를 띄운다(이미 떠 있으면 그대로). 띄웠으면 True · 마감(`deadline`)까지 다 뜨지 않았으면 False(띄우기는 배경에서
        이어진다 — 다음 부름이 그 끝을 받는다). 그 REA 를 내렸으면 ReaClosed."""
        if state.closed:
            raise ReaClosed(CLOSED)
        if state.client is not None and state.client.alive:
            return True
        opening = state.opening
        if opening is None:
            opening = state.opening = asyncio.get_running_loop().create_task(self._rea_start(w.name, state),
                                                                             name=f"gaf-rea-open:{w.name}")
            opening.add_done_callback(functools.partial(_opened, state))
        try:
            if deadline is None:
                await asyncio.shield(opening)
            else:
                await asyncio.wait_for(asyncio.shield(opening), max(WAIT_FLOOR, deadline - _now()))
        except asyncio.TimeoutError:
            if not opening.done():
                return False
            if opening.cancelled():                   # 마감과 끝이 겹쳤다 — 그 끝을 따른다
                raise ReaClosed(CLOSED) from None
            if opening.exception() is not None:
                raise opening.exception() from None
            return True
        except asyncio.CancelledError:
            if _cancelling() or not opening.cancelled():
                raise
            raise ReaClosed(CLOSED) from None         # 내리는 쪽이 띄우기를 멈췄다 — 부른 쪽은 끊기지 않았다
        return True

    async def _rea_start(self, name: str, state: ReaSession) -> None:
        """띄우는 작업 하나(state.opening) — 스스로 끝난 옛 손이 있으면 먼저 닫고, 띄우기 전체에 상한을 둔다."""
        old, state.client = state.client, None
        if old is not None:
            # 스스로 끝난 REA(메모리 · 사람이 끔 · 도는 중에 죽음) — 새로 띄우기 전에 옛 손을 닫는다. 닫아야 남은 손자(JVM · 도우미)를
            # 나무째 치운다(11-27 다른 눈의 검수: 닫지 않고 새로 띄워 손자가 남았다). 못 닫아도 이어 간다.
            await _close_client(old)
            with contextlib.suppress(Exception):
                self.store.emit("rea.closed", role=name, data={"why": "끝나 있어 다시 띄운다"})
        try:
            await asyncio.wait_for(self._rea_launch(name, state), OPEN_MAX)
        except asyncio.TimeoutError:
            raise RuntimeError(f"REA 가 {OPEN_MAX:g}초 안에 뜨지 않았다(목록 · 효과 표까지) — 띄우던 것은 껐다") from None

    async def _rea_launch(self, name: str, state: ReaSession) -> None:
        from . import client as rea_client
        from . import effects
        from . import setup as rea_setup

        argv = await asyncio.to_thread(rea_setup.server_argv)
        if argv is None:
            raise RuntimeError("REA 가 깔려 있지 않다")
        work = self._rea_work(name)
        env = await asyncio.to_thread(rea_setup.server_env, work)
        # 자식의 임시 폴더는 띄울 때마다 따로 둔다(<밑자리>/<순번>-<난수>) — 같은 폴더를 쓰면 늦게 끝난 옛 것의 내리기가 새 자식의
        # 임시 파일을 지운다(11-27 다른 눈의 검수의 고침과 함께). 밑자리는 Windows 면 PC 의 임시 폴더의 gaf-rea/<지문> 이다 — TEMP 에 점으로
        # 시작하는 마디(`.gaf`)가 있으면 REA 가 Ghidra 실행 폴더를 프로젝트 뿌리로 옮겼다(11-28 잼 · gaf/paths.py). server_env 는 TEMP 를
        # work/tmp 로 주므로 여기서 덮는다. 내릴 때는 제 것만 치우고 빈 윗폴더도 지운다.
        spawned = self.__dict__["_rea_spawned"] = self.__dict__.get("_rea_spawned", 0) + 1
        temp = await asyncio.to_thread(_make_temp, work, spawned, self._rea_held, state.temps)      # state.temps 에는 잠금 안에서 넣는다
        env = {key: value for key, value in env.items() if key.upper() not in TEMP_KEYS}
        env.update({key: str(temp) for key in TEMP_KEYS})
        (work / ".gitignore").write_text("# REA 가 꺼낸 것 · 결과 · 증거 묶음 — 남의 게임에서 나온 것이다. git 에 올리지 않는다.\n*\n",
                                         encoding="utf-8", newline="\n")
        # 낱말 찾기(ripgrep — Grep 도구 · rg)는 윗폴더 `.gaf/.ignore` 의 `*` 를 따라 이 폴더를 가리켜도 건너뛴다. 이 폴더만 다시 연다 —
        # 뿌리에서 뒤질 때는 여전히 `.gaf` 를 건너뛴다(11-27 ripgrep 으로 잼: 가리키면 찾고 · 뿌리에서는 안 나온다).
        (work / ".ignore").write_text("!*\n", encoding="utf-8", newline="\n")
        client = rea_client.ReaClient(argv, env, work)
        try:
            await client.start()
            tools = await client.tools()
            table: dict = {}
            # 효과 표: 6.10.1 은 `detail: "complete"` 라야 무리마다 effects 를 준다(기본은 요약 — 효과가 없다). 옛 판(6.7.0)은 빈 값에
            # 다 준다 — complete 로 표가 비면 빈 값으로 한 번 더 읽는다(11-27 두 판을 이 PC 에서 돌려 봄). 끝내 비면 모든 도구가
            # 「효과를 모르는 도구」로 사람의 승인을 거친다(effects.ask_words).
            for arguments in ({"detail": "complete"}, {}):
                session = await client.call("binary_session", arguments, timeout=SESSION_WAIT)
                if session.get("ok") and not session.get("is_error"):
                    table = effects.table(session.get("structured"))
                if table:
                    break
        except BaseException:
            await _close_client(client)               # 멈췄거나(내리기 · 상한) 실패했다 — 띄우던 것을 끈다
            with contextlib.suppress(ValueError):
                state.temps.remove(temp)
            await asyncio.to_thread(_drop_temps, [temp])
            raise
        if state.closed:
            # 띄우는 사이에 그 세션이 닫혔다(떼어 냈다) — 쥘 주인이 없다. 제 손을 닫고 「닫혔다」로 끝낸다(11-27 다른 눈의 검수: 세션이
            # 닫힌 뒤 · 끈 뒤에 떠서 아무도 닫지 않았다). 임시 폴더는 내리는 쪽이 치운다.
            await _close_client(client)
            raise ReaClosed(CLOSED)
        state.client, state.tools, state.table, state.work = client, tools, table, work
        self.store.emit("rea.opened", role=name, data={"tools": len(tools), "effects": len(table)})

    # ------------------------------------------------------------------ 내리기
    def _rea_detach(self, name: str) -> ReaSession | None:
        """그 역할의 REA 를 떼어 낸다(기다림 없이) — 새 ReaSession 으로 바꿔 끼우고 옛 것(closed)을 돌려준다. 승인 상태 · 일 · 호출 번호는
        새 것으로 옮기고(같은 일이 이어질 수 있다) 사람에게 물을 것은 비운다. 쥔 것이 없으면(REA 를 쓰지 않은 역할) None 이고 아무것도
        바꾸지 않는다. 예외를 올리지 않는다(세션 닫기 · 멈춤의 길이 이어지게)."""
        try:
            sessions = self.__dict__.get("_rea_sessions") or {}
            old = sessions.get(name)
            if old is None or not (old.client is not None or old.opening is not None or old.calls or old.temps):
                return None
            sessions[name] = ReaSession(name=name, work=old.work, grants=old.grants, denied=old.denied, asked=old.asked,
                                        task=old.task, issued=dict(old.issued), call_n=old.call_n)
            old.closed = True
            # 내리기 전까지 쥐어 둔다 — 떼어 낸 쪽(세션 닫기)이 닫기를 기다리다 끊겨도 끌 때 `_rea_close_all` 이 내린다
            self.__dict__.setdefault("_rea_detached", {})[id(old)] = old
            return old
        except Exception:  # noqa: BLE001
            return None

    def _rea_down(self, state: ReaSession) -> asyncio.Task:
        """떼어 낸 것을 내리는 작업을 건다(한 번만) — 끌 때 `_rea_close_all` 이 남은 것까지 기다린다."""
        if state.down is None:
            downs = self.__dict__.setdefault("_rea_downs", set())
            detached = self.__dict__.setdefault("_rea_detached", {})
            state.down = asyncio.get_running_loop().create_task(self._rea_teardown(state), name=f"gaf-rea-down:{state.name}")
            downs.add(state.down)
            state.down.add_done_callback(downs.discard)
            state.down.add_done_callback(lambda _task, key=id(state): detached.pop(key, None))
        return state.down

    async def _rea_shutdown(self, state: ReaSession) -> None:
        """떼어 낸 것을 내리고 끝을 기다린다. 기다리던 쪽이 끊겨도 내리기는 끝까지 한다."""
        await asyncio.shield(self._rea_down(state))

    async def _rea_teardown(self, state: ReaSession) -> None:
        """도는 호출을 멈추고 · 띄우던 것을 멈추고(그 끝을 기다린다 · 상한) · 자식을 닫고(15초 상한) · 제 임시 폴더를 치운다(증거 묶음 ·
        결과는 둔다). 한 칸이 실패해도 다음 칸은 한다."""
        jobs = list(state.calls.values())
        for job in jobs:
            job.cancel()
        state.calls.clear()
        opening = state.opening
        if opening is not None and not opening.done():
            # 띄우던 것을 멈춘다 — 그 작업이 제 손을 닫는다(_rea_launch). 띄우기의 상한(180초)을 다 기다리지 않는다.
            opening.cancel()
            with contextlib.suppress(Exception):
                await asyncio.wait({opening}, timeout=CLOSE_MAX + 5)
        client, state.client = state.client, None
        if client is not None:
            await _close_client(client)
            with contextlib.suppress(Exception):
                self.store.emit("rea.closed", role=state.name)
        if jobs:
            with contextlib.suppress(Exception):
                await asyncio.wait(jobs, timeout=5)
        temps, state.temps = list(state.temps), []
        if temps:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(_drop_temps, temps)   # 윗폴더는 비었을 때만 지워진다 — 다음에 띄운 REA 의 것이 있으면 남는다

    def _rea_held(self) -> set[str]:
        """이 지휘자가 쥔 자식의 임시 폴더들(견줄 꼴 — _temp_key) — 지금 세션들과 떼어 내 내리는 중인 것들의 temps. 띄우기의 스레드가
        잠금 안에서 부른다(목록은 통째로 베껴 읽는다 — 루프 쪽이 그사이 고쳐도 깨지지 않게)."""
        states = [*list((self.__dict__.get("_rea_sessions") or {}).values()),
                  *list((self.__dict__.get("_rea_detached") or {}).values())]
        return {_temp_key(temp) for state in states for temp in list(state.temps)}

    def _rea_reap(self, name: str) -> None:
        """일이 끝날 때(_finish_task — 끝 · 멈춤 · 문제 모두) · 역할을 멈출 때(_halt · 끌 때) — 끝난 호출은 비우고, 아직 도는 호출이
        있으면 그 역할의 REA 를 내린다(나무째 · 다음 부름이 다시 띄운다). 기다림이 없다(내리기는 배경에서 · 끌 때 기다린다).

        REA 는 취소 알림을 무시해(6.10.1 실측) 호출을 버려도 분석은 돈다. 멈춘 일 · 끝난 일의 호출이 3600초까지 돌며(승인받은 효과
        있는 호출까지) 다음 일의 자리(RUNNING_MAX)를 막았다(11-27 다른 눈의 검수). 검사 · 검토 · 승인으로 같은 일이 이어지는 동안에는
        부르지 않는다 — 그 일의 다음 턴이 wait 로 받는다. 예외를 올리지 않는다."""
        try:
            state = (self.__dict__.get("_rea_sessions") or {}).get(name)
            if state is None or not state.calls:
                return
            for number, job in list(state.calls.items()):
                if job.done():
                    del state.calls[number]
            if state.calls:
                old = self._rea_detach(name)
                if old is not None:
                    self._rea_down(old)
        except Exception:  # noqa: BLE001
            pass

    async def _rea_close(self, name: str) -> None:
        """그 역할의 REA 를 내리고 끝을 기다린다 — 도는 호출은 버리고, 꺼낸 임시 폴더는 치운다(증거 묶음 · 결과는 둔다)."""
        old = self._rea_detach(name)
        if old is not None:
            await self._rea_shutdown(old)

    async def _rea_close_all(self) -> None:
        """끌 때(세션들을 닫은 뒤) — 남은 REA 를 모두 떼어 내리고, 앞서 떼어 둔 것 · 걸어 둔 내리기까지 다 끝나기를 기다린다(상한이 있다)."""
        for name in list(self.__dict__.get("_rea_sessions") or {}):
            self._rea_detach(name)
        for state in list((self.__dict__.get("_rea_detached") or {}).values()):
            self._rea_down(state)
        downs = list(self.__dict__.get("_rea_downs") or ())
        if downs:
            await asyncio.wait(downs, timeout=CLOSE_MAX * 2 + 10)

    # ------------------------------------------------------------------ 턴 뒤 — 사람에게 묻고 이어 부른다
    def _rea_card(self, w, task: str, pending: dict, need: list[str]) -> dict:
        if pending["kind"] == "enable":
            from . import setup as rea_setup

            lines = [f"역할 「{w.name}」이 맡은 일 {task} 에서 REA(게임 빌드를 뜯어보는 분석 도구)를 쓰려 한다.",
                     *(["깔 것:", *("- " + line for line in need)] if need else ["깔 것: 없음(이미 깔려 있다)"]),
                     "승인하면 이 PC 에 깔고 켠다 — 다음부터는 묻지 않는다(게임마다 묻지 않는다 · 끄기: gaf rea off).", *rea_setup.NOTICE]
            title = "REA 켜기 청"
        else:
            lines = [f"역할 「{w.name}」이 맡은 일 {task} 에서 REA 도구 {pending['name']} 을 부르려 한다.",
                     "이 도구는: " + " · ".join(pending["words"]), "받는 값: " + pending["arguments"],
                     "승인하면 이 받는 값 그대로의 부름을 이 일이 끝날 때까지 받는다 — 값이 달라지면 다시 묻는다."]
            title = f"REA 도구 청 — {pending['name']}"
        question = _clip("\n".join(lines), 1900)
        return self._request_action(w.name, question, {"kind": "rea" if pending["kind"] == "enable" else "rea_tool", "count": 1,
                                                       "title": title, "lines": lines, "task": task,
                                                       **({"tool": pending["name"]} if pending["kind"] == "tool" else {})})

    async def _rea_round(self, w, letter, record: dict, out):
        """역할이 그 턴에서 사람의 승인이 있어야 하는 것을 청했으면(REA 켜기 · 효과가 있는 도구) 카드를 올려 답을 받고 같은 일을 이어 부른다."""
        state = self._rea_task(w)
        pending, state.pending = state.pending, None
        if pending is None or w.stop_requested or self._closing:
            return out
        if not out.got_result or not out.ok or out.error:
            return out
        state.asked += 1
        task = record["id"]
        w.now = "REA 승인을 기다린다"
        w.wait_reason = "REA — 사람의 승인을 기다린다"      # 화면의 그 역할 줄에 보이는 칸(검토를 기다릴 때와 같다 — now · live 글은 턴 중에만 실린다)
        self._begin_live(w, "check", "REA — 사람의 승인을 기다린다")
        self._talk(w, "system", text="지휘자: REA 를 쓰려면 사람의 승인이 있어야 한다 — 승인을 기다린다")
        self._touch()
        self._running.discard(w.name)            # 기다리는 동안 자리를 내놓는다(조사 · 검토를 기다릴 때와 같다)
        self._poke()
        note: str | None = None
        try:
            need: list[str] = []
            if pending["kind"] == "enable":       # 무엇을 받게 되는지(Node · REA) — 디스크와 `node --version` 을 보니 다른 스레드에서
                from . import setup as rea_setup
                try:
                    need = list(await asyncio.to_thread(rea_setup.install_plan))
                except Exception:  # noqa: BLE001 — 못 알아내도 카드는 올린다
                    need = ["(무엇을 받을지 알아내지 못했다 — 승인하면 필요한 것을 받는다)"]
            asked = self._rea_card(w, task, pending, need)
            if not asked.get("ok"):
                note = f"{HEAD}REA 의 승인을 사람에게 청하지 못했다({asked.get('error')}).{TAIL}"
            else:
                waiting = self._research_answers[asked["id"]] = {"done": False}
                try:
                    if not await self._research_pause(w, lambda: waiting["done"]):
                        if self._decisions.pop(asked["id"], None) is not None:      # 멈췄다 — 기다리던 카드를 거둔다
                            self.store.emit("decision.answered", role=w.name, id=asked["id"], question="(멈춰서 거둠) REA 청")
                            self._touch()
                            self._flush_now()
                    else:
                        note = self._rea_note(state, pending, waiting)
                finally:
                    self._research_answers.pop(asked["id"], None)
        finally:
            await self._review_resume(w)
            w.now, w.wait_reason = "", ""
            self._end_live(w)
        if self._closing:
            w.stop_requested = True
        if note is None or w.stop_requested:
            return out
        self._talk(w, "system", text="지휘자: REA 승인의 답을 주고 같은 일을 이어서 하라고 했다")
        w.research_open = True                   # 이어 부른 턴에서 다시 청할 수 있다
        second = await self._turn(w, letter, note, [], joinable=True)
        second.absorb(out)
        second.wrote = {**out.wrote, **second.wrote}
        if second.error and not second.got_result and not w.stop_requested:
            await self._close_session(w, "error")
        return second

    def _rea_note(self, state: ReaSession, pending: dict, waiting: dict) -> str:
        if waiting.get("failed"):
            return f"{HEAD}사람은 승인했으나 지휘자가 해내지 못했다({_brief(str(waiting['failed']), 300)}).{TAIL}"
        if not waiting.get("approved"):
            said = _brief(str(waiting.get("answer") or "거절"), 300)
            if pending["kind"] == "tool":
                state.denied.add(pending["name"])
                return f"{HEAD}사람이 REA 도구 {pending['name']} 을 쓰지 않겠다고 했다(사람의 답: 「{said}」).{TAIL}"
            return f"{HEAD}사람이 REA 를 이 PC 에서 켜지 않겠다고 했다(사람의 답: 「{said}」) — 뜯어보지 못한다.{TAIL}"
        if pending["kind"] == "tool":
            state.grants.add(pending.get("key") or pending["name"])
            return (f"{HEAD}사람이 REA 도구 {pending['name']} 의 그 부름(청한 받는 값 그대로)을 승인했다 — 같은 값으로 다시 부르고 일을 "
                    "이어서 한다(이 일이 끝날 때까지 같은 값의 부름은 묻지 않는다 · 값이 달라지면 사람에게 다시 묻는다). 다 하면 마지막 답을 "
                    "다시 쓴다(한 일 · 바뀐 파일 · 확인한 것).")
        return (f"{HEAD}사람이 REA 를 이 PC 에서 켰다(깔기까지 마쳤다) — 이제 rea_tools · rea_call 을 쓸 수 있다. 하려던 일을 이어서 한다. "
                "다 하면 마지막 답을 다시 쓴다(한 일 · 바뀐 파일 · 확인한 것).")

    async def _rea_enable(self) -> str:
        """승인 카드의 「승인」이 하는 일 — 깔고(없을 때) 켠다. 한 일을 글로 돌려준다(실패는 예외)."""
        from . import setup as rea_setup
        from . import state as rea_state

        result = await asyncio.to_thread(rea_setup.install)
        if not result.get("ok"):
            # 까닭의 글(npm 의 끝 출력)에는 집 폴더의 경로가 들어 있을 수 있다 — 계정 이름이 대화 · 기록에 남지 않게 가린다.
            raise ReaSetupError("REA 를 깔지 못했다: " + _brief(_no_home(str(result.get("error") or "까닭 모름")), 400))
        await asyncio.to_thread(rea_state.enable)
        return "이 PC 에 REA 를 깔고 켰다(다음부터는 묻지 않는다 · 끄기: gaf rea off)."


class ReaSetupError(Exception):
    """REA 를 깔거나 켜지 못했다 — 글은 사람과 역할에게 그대로 보인다."""


class ReaClosed(Exception):
    """그 REA 를 내렸다(세션이 닫혔거나 일이 끝났거나 멈췄다) — 띄우기 · 부르기를 그만둔다. 글은 역할에게 그대로 보인다."""


def _grant_key(tool: str, arguments: dict) -> str:
    """승인을 묶는 이름 — 도구와 받는 값(키의 차례는 가리지 않는다)의 지문. 값이 한 글자라도 다르면 다른 부름이다."""
    try:
        body = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        body = repr(arguments)
    return tool + ":" + hashlib.sha256(body.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _plain(text: str) -> str:
    """사람에게 보일 글(승인 카드 · 기록) — 한 줄로 펴고, 집 폴더의 경로는 `~` 로 가리고(계정 이름이 대화 · 기록에 남지 않게), 눈에 안
    보이는 제어 · 방향 글자(글의 방향을 뒤집어 파일 이름을 속이는 U+202E 등)는 `\\uXXXX` 로 드러낸다(11-27 다른 눈의 검수)."""
    flat = " ".join(_no_home(str(text)).split())
    return "".join(ch if unicodedata.category(ch) not in ("Cf", "Cc", "Co", "Cs") else "\\u%04x" % ord(ch) for ch in flat)


def _waiting(why: str) -> dict:
    return {"ok": True, "waiting": True,
            "text": f"{why} — 사람의 승인이 있어야 한다. 이 답을 마치면 지휘자가 사람에게 묻고, 답이 오는 대로 이 일을 이어서 부른다. "
                    "지금은 무엇을 하려 했는지 한 줄로 답을 마친다(기다리거나 다른 길로 돌아가지 않는다)."}


def _now() -> float:
    return asyncio.get_running_loop().time()


def _cancelling() -> int:
    """지금 작업에 걸린 취소 청의 수 — 부른 쪽이 끊긴 것(>0)과 기다리던 것이 멈춘 것(0)을 가른다."""
    task = asyncio.current_task()
    probe = getattr(task, "cancelling", None)
    return int(probe()) if callable(probe) else 0


def _opened(state: ReaSession, task: asyncio.Task) -> None:
    """띄우는 작업이 끝났다 — 그 자리를 비운다(기다리던 쪽의 finally 가 아니다: 기다리던 쪽이 끊겨도 띄우기는 이어지고, 그사이 다시
    부른 쪽이 그것을 기다려야 둘을 띄우지 않는다 · 11-27 다른 눈의 검수). 받지 않은 예외로 남지 않게 꺼내 둔다."""
    if state.opening is task:
        state.opening = None
    if not task.cancelled():
        task.exception()


def _consume(task: asyncio.Task) -> None:
    """끝을 받지 않고 거둔 호출의 예외가 「받지 않은 예외」로 남지 않게 한다."""
    if not task.cancelled():
        task.exception()


async def _close_client(client) -> None:
    try:
        await asyncio.wait_for(client.close(), CLOSE_MAX)
    except Exception:  # noqa: BLE001 — 못 닫아도 이어 간다
        pass


def _make_temp(work: Path, number: int, held=None, into: list | None = None) -> Path:
    """그 띄우기만의 임시 폴더 <밑자리>/<순번>-<난수> 를 만든다(스레드에서 부른다). 밑자리는 paths.rea_temp_home(work)(Windows — PC 의
    임시 폴더의 gaf-rea/<지문>), 없으면 work/tmp. 옛 것을 치우는 쪽이 그사이 밑자리를 지웠으면 다시 만든다.

    만들기 전에 밑자리에 남은 폴더 — 이 지휘자가 쥔 것(held() · _temp_key 의 꼴)이 아닌 것, 곧 지난번 지휘자가 죽으며 남긴 것 — 를
    지운다(한 프로젝트에 지휘자는 하나다). 옮긴 자리를 쓰면 작업 폴더 안 tmp 도 같은 규칙으로 치우고, 비면 지운다(server_env 가 만들어
    둔 빈 폴더 · 1.17.0 이 남긴 것). 새 폴더는 into(그 ReaSession.temps)에 잠금 안에서 넣는다."""
    legacy = (Path(work) / "tmp").resolve()
    home = gaf_paths.rea_temp_home(Path(work).resolve())
    base = home if home is not None else legacy
    with _TEMP_LOCK:
        keep = set(held()) if held is not None else set()
        _sweep(base, keep)
        if home is not None:
            _sweep(legacy, keep)
            with contextlib.suppress(OSError):
                os.rmdir(legacy)                          # 비었을 때만 지워진다
        for _ in range(5):
            temp = base / f"{number}-{secrets.token_hex(3)}"
            try:
                temp.mkdir(parents=True)
            except (FileExistsError, FileNotFoundError):
                continue
            if into is not None:
                into.append(temp)
            return temp
    raise OSError("REA 의 임시 폴더를 만들지 못했다")


def _temp_key(path) -> str:
    """임시 폴더를 견줄 꼴 — 온전한 경로 · (Windows) 대소문자를 접는다."""
    return os.path.normcase(os.path.abspath(path))


def _sweep(base: Path, keep: set[str]) -> None:
    """밑자리 바로 아래의 폴더 가운데 keep 에 없는 것을 지운다 — 파일 · 링크는 두고, 못 지운 것(잠긴 파일)은 넘어간다."""
    try:
        with os.scandir(base) as found:
            entries = list(found)
    except OSError:
        return
    for entry in entries:
        try:
            plain = (entry.is_dir(follow_symlinks=False) and not entry.is_symlink()
                     and not getattr(entry, "is_junction", lambda: False)())     # is_junction 은 3.12 부터다
        except OSError:
            continue
        path = base / entry.name
        if plain and _temp_key(path) not in keep:
            shutil.rmtree(path, ignore_errors=True)


def _drop_temps(temps) -> None:
    """내린(또는 못 띄운) 임시 폴더들을 지우고, 빈 윗폴더(work/tmp · 지문 폴더)와 — 옮긴 자리면 — 빈 gaf-rea 도 지운다(os.rmdir — 비었을
    때만 · 같은 역할 · 다른 역할의 다음 REA 것이 있으면 남는다). 못 지워도 넘어간다(스레드에서 부른다)."""
    for temp in temps:
        shutil.rmtree(temp, ignore_errors=True)
    for folder in {Path(temp).parent for temp in temps}:
        with contextlib.suppress(OSError):
            os.rmdir(folder)
        if folder.parent.name == gaf_paths.REA_TEMP_FOLDER and _TEMP_HOME.fullmatch(folder.name):
            with contextlib.suppress(OSError):
                os.rmdir(folder.parent)


def _make_parents(paths: list[str], work: Path) -> None:
    """쓰는 자리들의 윗폴더를 만든다 — 작업 폴더 안일 때만(check_paths 를 통과한 값이지만 글로 한 번 더 본다). 못 만들어도 넘어간다
    (그때는 REA 가 까닭을 알려 준다)."""
    base = os.path.normcase(os.path.abspath(work))
    for raw in paths:
        if not os.path.isabs(raw) or ":" in os.path.splitdrive(raw)[1]:      # 흐름 이름(`x::$DATA`)이 든 꼴은 만들지 않는다
            continue
        parent = os.path.abspath(os.path.dirname(raw))
        if os.path.normcase(parent) == base or os.path.normcase(parent).startswith(base + os.sep):
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError:
                pass


def _no_home(text: str) -> str:
    """집 폴더의 경로를 `~` 로 — 적힌 꼴(`\\` · `/` · JSON 안의 겹친 `\\\\`)과 대소문자(Windows)를 가리지 않는다."""
    home = str(Path.home())
    back, double = chr(92), chr(92) * 2
    spellings = {home, home.replace(back, "/"), home.replace("/", back), home.replace(back, double)}
    for spelling in sorted((item for item in spellings if len(item) > 3), key=len, reverse=True):
        text = re.sub(re.escape(spelling), lambda _found: "~", text, flags=re.IGNORECASE if os.name == "nt" else 0)
    return text


def _brief(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"
