"""모델 호출과 셸 실행 없이 지시어를 처리하는 시험용 엔진."""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import AsyncIterator, Sequence

from .base import Engine, EngineEvent, EngineSession, NotRunning, SessionSpec


class FakeEngine(Engine):
    name = "fake"

    @property
    def compacts(self):
        return self.compact_to is not None

    def __init__(self, context_tokens: int = 1000):
        self.sessions: list[FakeSession] = []
        self.context_tokens = context_tokens
        self.turn_log: list[dict] = []
        self.send_log: list[dict] = []      # 도는 턴에 넣은 글(send)
        self.compact_to: int | None = 400   # 압축 뒤 문맥 토큰 · None 이면 압축을 못 한다
        self.compact_fail: str = ""
        self.compact_wait: float = 0.0
        self.compact_blind: bool = False    # 시험용: 압축 뒤의 문맥 크기를 그 세션의 다음 턴까지 모른다(Codex 처럼 — context() 가 None)
        self.compact_says: int | None = None  # 시험용: 압축이 알려 주는 크기(post_tokens) — Codex 는 지시문 · 도구가 빠진 「줄인 대화」의
        #                                       크기를 준다(압축 뒤의 문맥 compact_to 보다 작다). None 이면 compact_to 와 같다
        self.compact_log: list[dict] = []
        self.fail_open = 0          # 시험용: 남은 횟수만큼 open() 이 실패한다
        self.fail_send = 0          # 시험용: 남은 횟수만큼 send() 가 NotRunning 을 낸다(턴이 막 끝난 것처럼)
        self._number = 0

    def session(self, spec: SessionSpec) -> FakeSession:
        session = FakeSession(spec, self)
        self.sessions.append(session)
        return session


class FakeSession(EngineSession):
    def __init__(self, spec: SessionSpec, engine: FakeEngine):
        self.spec, self.engine = spec, engine
        self.session_id: str | None = None
        self.resumed = False
        self._opened = False
        self._closed = False
        self._interrupted = asyncio.Event()
        self._lock = asyncio.Lock()
        self._lines: deque[str] | None = None       # 도는 턴의 남은 줄(턴 밖이면 None)
        self._arrived = asyncio.Event()             # 도는 턴에 글이 들어왔다(`!wait` 를 깨운다)
        self._context_hidden = False                # 압축 뒤 다음 턴까지 문맥 크기를 모른다(compact_blind)

    @property
    def running(self) -> bool:
        return self._lines is not None

    async def open(self) -> None:
        if self._closed:
            raise RuntimeError("이미 닫은 세션이다")
        if self._opened:
            return
        if self.engine.fail_open > 0:
            self.engine.fail_open -= 1
            raise RuntimeError("가짜 엔진: 세션을 열지 못했다")
        self.resumed = self.spec.resume is not None
        if self.resumed:
            self.session_id = self.spec.resume
        else:
            self.engine._number += 1
            self.session_id = f"fake-{self.engine._number}"
        self._opened = True

    async def _gate(self, name: str, data: dict) -> tuple[bool, str]:
        try:
            return await self.spec.gate(name, data) if self.spec.gate else (True, "")
        except Exception as exc:
            return False, f"문을 확인하지 못했다: {exc}"

    async def turn(self, text: str, images: Sequence[Path] = ()) -> AsyncIterator[EngineEvent]:
        if not self._opened or self._closed:
            raise RuntimeError("세션이 열려 있지 않다")
        async with self._lock:
            if self._closed:
                raise RuntimeError("이미 닫은 세션이다")
            self._interrupted.clear()
            self._arrived.clear()
            self._context_hidden = False             # 턴이 돌면 문맥 크기를 다시 안다
            self._lines = deque(text.splitlines())
            started = time.monotonic()
            self.engine.turn_log.append({"role": self.spec.role, "session_id": self.session_id,
                                         "text": text, "images": list(images)})
            yield EngineEvent("session", {"session_id": self.session_id, "model": self.spec.model or "fake",
                                         "engine": "fake", "permission": self.spec.permission, "resumed": self.resumed})
            answer = "가짜 답: " + (text.splitlines()[0] if text.splitlines() else "")
            replies = []
            denials = 0
            tool_number = 0
            while self._lines:
                line = self._lines.popleft()
                if self._interrupted.is_set():
                    break
                if not line.startswith("!"):
                    continue
                command, _, argument = line.partition(" ")
                argument = argument.strip()
                if command == "!fail":
                    self._lines = None
                    yield EngineEvent("error", {"message": argument or "가짜 엔진 오류"})
                    return
                if command == "!say":
                    answer = argument
                elif command == "!text":                  # 턴 중간에 하는 말(진짜 엔진의 text 이벤트)
                    yield EngineEvent("text", {"text": argument, "sub": False})
                elif command == "!sleep":
                    try:
                        await asyncio.wait_for(self._interrupted.wait(), timeout=max(0, float(argument)))
                    except asyncio.TimeoutError:
                        pass
                    except ValueError:
                        self._lines = None
                        yield EngineEvent("error", {"message": "!sleep의 초는 수여야 한다"})
                        return
                elif command == "!wait":
                    # 글이 들어올 때까지(send) 기다린다 — 진짜 세션의 「다음 도구 차례」 자리. 먼저 와 있던 글이 있으면 바로 지나간다.
                    waiters = [asyncio.ensure_future(self._arrived.wait()), asyncio.ensure_future(self._interrupted.wait())]
                    try:
                        await asyncio.wait(waiters, timeout=max(0.0, float(argument or 0)), return_when=asyncio.FIRST_COMPLETED)
                    except ValueError:
                        self._lines = None
                        yield EngineEvent("error", {"message": "!wait의 초는 수여야 한다"})
                        return
                    finally:
                        for waiter in waiters:
                            waiter.cancel()
                    self._arrived.clear()
                elif command == "!limit":
                    try:
                        pct, status = argument.split(maxsplit=1)
                        yield EngineEvent("limit", {"pct": float(pct), "status": status, "window": "seven_day",
                                                    "resets_at": None, "windows": {}})
                    except ValueError:
                        self._lines = None
                        yield EngineEvent("error", {"message": "!limit에는 사용률과 상태를 쓴다"})
                        return
                elif command in ("!write", "!bash", "!ask", "!call"):
                    tool_number += 1
                    tool_id = f"fake-tool-{tool_number}"
                    names = {"!write": "Write", "!bash": "Bash", "!ask": "AskUserQuestion"}
                    name = names.get(command, argument.partition(" ")[0])
                    tool = {"id": tool_id, "name": name, "target": argument, "sub": False}
                    if command == "!write":
                        tool["path"] = argument.partition(" ")[0]       # 파일 도구는 온전한 경로도 싣는다
                    yield EngineEvent("tool", tool)
                    ok, excerpt = True, ""
                    try:
                        if command == "!write":
                            filename, _, content = argument.partition(" ")
                            content = content.replace("⏎", "\n")          # 여러 줄 파일은 ⏎ 로 줄을 나눠 적는다(명령은 한 줄이라서)
                            if not filename:
                                raise ValueError("!write에 파일 경로가 없다")
                            ok, excerpt = await self._gate("Write", {"file_path": filename, "content": content})
                            if ok:
                                path = Path(filename.replace("\\", "/"))
                                if not path.is_absolute():
                                    path = self.spec.cwd / path
                                path.parent.mkdir(parents=True, exist_ok=True)
                                path.write_text(content, encoding="utf-8", newline="\n")
                                excerpt = "파일을 썼다"
                            else:
                                denials += 1
                        elif command == "!bash":
                            ok, excerpt = await self._gate("Bash", {"command": argument})
                            if ok:
                                excerpt = "명령을 허용했다"
                            else:
                                denials += 1
                        elif command == "!ask":
                            questions = [{"question": argument}]
                            response = await self.spec.asker(questions) if self.spec.asker else None
                            if response is None:
                                ok, excerpt = False, "묻지 못함"
                            else:
                                excerpt = "\n".join(str(value) for value in response.values())
                                replies.append(excerpt)
                        elif command == "!call":
                            tool_name, _, payload = argument.partition(" ")
                            definition = next((tool for tool in self.spec.tools if tool.name == tool_name), None)
                            if definition is None:
                                raise ValueError(f"없는 도구다: {tool_name}")
                            args = json.loads(payload or "{}")
                            if not isinstance(args, dict):
                                raise ValueError("도구 인자는 JSON 객체여야 한다")
                            excerpt = str(await definition.handler(args))
                    except Exception as exc:
                        ok, excerpt = False, f"도구를 쓰지 못했다: {exc}"
                    yield EngineEvent("tool_result", {"id": tool_id, "ok": ok, "excerpt": excerpt})
            self._lines = None                       # 여기부터는 글을 못 넣는다(턴이 끝나는 중)
            aborted = self._interrupted.is_set()
            if replies:
                answer += "\n" + "\n".join(replies)
            yield EngineEvent("result", {"ok": not aborted, "text": answer, "turns": 1, "cost_usd": 0.0,
                                         "usage": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                                         "duration_ms": int((time.monotonic() - started) * 1000),
                                         "stop": "aborted" if aborted else "success", "denials": denials})

    async def send(self, text: str, images: Sequence[Path] = ()) -> None:
        if self.engine.fail_send > 0:
            self.engine.fail_send -= 1
            raise NotRunning("가짜 엔진: 턴이 막 끝났다")
        if self._lines is None or self._closed:
            raise NotRunning("도는 턴이 없다")
        self.engine.send_log.append({"role": self.spec.role, "session_id": self.session_id,
                                     "text": text, "images": list(images)})
        self._lines.extend(text.splitlines())
        self._arrived.set()

    async def interrupt(self) -> None:
        self._interrupted.set()

    async def context(self) -> dict | None:
        if not self._opened or self._closed or self._context_hidden:
            return None
        return {"tokens": self.engine.context_tokens, "max": 200000,
                "pct": self.engine.context_tokens / 200000 * 100}

    async def compact(self, keep: str = "") -> dict | None:
        async with self._lock:
            if not self._opened or self._closed:
                raise RuntimeError("세션이 열려 있지 않다")
            engine = self.engine
            engine.compact_log.append({"role": self.spec.role, "session_id": self.session_id, "keep": keep})
            await asyncio.sleep(engine.compact_wait)
            if engine.compact_fail:
                raise RuntimeError(engine.compact_fail)
            if engine.compact_to is None:
                return None
            before, engine.context_tokens = engine.context_tokens, engine.compact_to
            self._context_hidden = engine.compact_blind
            says = engine.compact_to if engine.compact_says is None else engine.compact_says
            return {"pre_tokens": before, "post_tokens": says}

    async def close(self) -> None:
        self._closed = True
        self._lines = None
        self._interrupted.set()
