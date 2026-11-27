"""REA 와 말하는 손 — 지휘자가 띄운 REA 자식 프로세스 하나와 줄마다 JSON-RPC 2.0(MCP stdio)을 주고받는다(asyncio).

이 PC 에서 잰 것(REA 6.7.0 · 2026-11-27 · docs 의 런타임 탐침): 응답 한 줄이 10MB 를 넘기도 한다 · 도구 목록이 한 줄 3MB 다 ·
틀린 `REA_*` 설정 값이면 initialize 전에 끝난다(끝난 값 1 · stderr 한 줄) · stdin 을 닫으면 30~77ms 안에 끝난다 · Ghidra 를 쓰면
JVM 과 도우미가 자식으로 뜬다. 그래서
- stdout 은 스스로 잘라 읽는다 — 줄 길이 상한(LINE_MAX)을 크게 두고, 넘는 줄은 버리되 그 부름 하나만 실패로 돌린다(읽는 쪽은 산다).
- 읽는 쪽은 하나다 — 온 응답을 번호(id)로 기다리는 부름에 나눠 준다. 서버의 알림은 쓰지 않는다(마지막 몇 개만 진단용으로 쥔다).
- stderr 는 쉬지 않고 비운다(파이프가 차서 자식이 멈추지 않게) — 끝 4KB 만 쥔다.
- 끌 때는 stdin 을 닫고 3초 기다린 뒤 프로세스 나무째 끈다.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal
import subprocess
from collections import deque
from pathlib import Path
from typing import Any

import gaf

PROTOCOL = "2025-06-18"
LINE_MAX = 256 * 1024 * 1024          # 응답 한 줄의 상한(바이트) — 넘으면 그 줄은 버리고 그 부름만 실패로 돌린다
CHUNK = 256 * 1024                    # stdout 을 한 번에 읽는 크기
PARSE_IN_THREAD = 1024 * 1024         # 이보다 긴 줄은 스레드에서 푼다(10MB 를 푸는 동안 지휘자의 고리를 막지 않는다)
STDERR_TAIL = 4096                    # 쥐어 두는 stderr 의 끝(바이트)
NOTES_KEEP = 8                        # 쥐어 두는 서버 알림 · 글이 아닌 줄의 수
CLOSE_WAIT = 3.0                      # stdin 을 닫고 스스로 끝나기를 기다리는 시간(초)
KILL_WAIT = 5.0                       # 나무째 끈 뒤 끝나기를 기다리는 시간(초)
WRITE_WAIT = 30.0                     # 한 메시지를 보내는 시간의 상한(초) — 넘으면 자식이 입력을 안 읽는 것이다
LIST_TIMEOUT = 120.0                  # tools/list 한 쪽을 기다리는 시간(초) — 6.7.0 전체 목록이 2초 남짓
LIST_PAGES = 200                      # tools/list 의 쪽 수 상한(같은 cursor 를 되풀이하는 서버에 갇히지 않게)
WATCH_EVERY = 1.0                     # 자식이 끝났는지 보는 간격(초) — 손자가 파이프를 쥐면 EOF 가 안 오기 때문
_TAIL_ID = re.compile(rb'"id"\s*:\s*(\d{1,15})\s*\}\s*$')          # 버린 줄의 끝 「"id":5}」 — TS SDK 는 id 를 맨 끝에 쓴다
_HEAD_ID = re.compile(rb'^\s*\{\s*"jsonrpc"\s*:\s*"2\.0"\s*,\s*"id"\s*:\s*(\d{1,15})\s*,')
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class ReaError(Exception):
    """REA 를 띄우지 못했거나(initialize 까지) 목록을 받지 못한 까닭 — 부름 하나의 실패는 call() 이 돌려주는 값에 적는다."""


class _Unsendable(Exception):
    """JSON 으로 바꿀 수 없는 메시지다."""


class ReaClient:
    """REA 자식 하나. `spawn` 은 시험용으로 `asyncio.create_subprocess_exec` 와 같은 꼴의 함수를 받는다."""

    def __init__(self, argv: list[str], env: dict[str, str], cwd: Path, *, spawn=None):
        self._argv = [str(part) for part in argv]
        self._env = {str(key): str(value) for key, value in dict(env).items()}
        self._cwd = Path(cwd)
        self._spawn = spawn or asyncio.create_subprocess_exec
        self._proc = None
        self._stderr_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._pending: dict[int, asyncio.Future] = {}
        self._next = 0
        self._write_lock = asyncio.Lock()
        self._tools_lock = asyncio.Lock()
        self._tools: list[dict] | None = None
        self._stderr = bytearray()
        self._notes: deque = deque(maxlen=NOTES_KEEP)
        self._dead: str | None = None          # 자식이 끝났거나 쓸 수 없게 된 까닭
        self._started = False
        self._closed = False
        self._close_task: asyncio.Future | None = None
        self.server: dict = {}                 # initialize 의 결과(서버 정보 · 능력 · instructions)
        self.dropped_env: list[str] = []       # 첫 실행이 initialize 전에 끝나 빼고 다시 띄운 `REA_*` 이름

    # ------------------------------------------------------------------ 겉
    @property
    def alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.returncode is None and self._dead is None and not self._closed

    @property
    def stderr_tail(self) -> str:
        return _CONTROL.sub("", bytes(self._stderr).decode("utf-8", errors="replace")).strip()

    @property
    def notes(self) -> list[dict]:
        """마지막 몇 개의 서버 알림 · 글이 아닌 줄(진단용 — 내용은 쥐지 않고 이름과 크기만)."""
        return list(self._notes)

    async def start(self, timeout: float = 40.0) -> dict:
        """띄우고 initialize → notifications/initialized. 자식이 답하기 전에 끝났고 환경에 `REA_*` 가 있으면 그것을 빼고 한 번 더 띄운다
        (틀린 REA 설정 값이면 진짜 서버가 그렇게 끝난다). 시간은 띄우기 한 번마다 `timeout` 이다."""
        if self._started:
            raise ReaError("이미 띄운 REA 다")
        self._started = True
        env = dict(self._env)
        try:
            for attempt in (0, 1):
                await self._launch(env)
                outcome = await self._request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                             "clientInfo": {"name": "gaf", "version": gaf.__version__}}, timeout)
                if outcome[0] == "ok":
                    break
                if outcome[0] == "dead":
                    await self._settle()
                tail = self.stderr_tail
                ended = self._proc.returncode if self._proc is not None else None
                await self._shutdown(graceful=False)
                dropped = sorted(key for key in env if key.upper().startswith("REA_"))
                if outcome[0] == "dead" and attempt == 0 and dropped:
                    self.dropped_env = dropped
                    env = {key: value for key, value in env.items() if key not in dropped}
                    self._reset()
                    continue
                if outcome[0] == "timeout":
                    why = f"REA 가 {timeout:g}초 안에 initialize 에 답하지 않았다"
                else:
                    why = f"REA 가 initialize 에 답하기 전에 끝났다(끝난 값 {ended})" if ended is not None else outcome[1]
                raise ReaError(why + _tail_note(tail))
            message = outcome[1]
            result = message.get("result")
            if "error" in message or not isinstance(result, dict):
                error = message.get("error") if isinstance(message.get("error"), dict) else {}
                tail = self.stderr_tail
                await self._shutdown(graceful=False)
                raise ReaError(f"REA 가 initialize 를 받지 않았다: {_cut(str(error.get('message') or message), 300)}" + _tail_note(tail))
            problem = await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            if problem:
                tail = self.stderr_tail
                await self._shutdown(graceful=False)
                raise ReaError(problem + _tail_note(tail))
            self.server = result
            return result
        except BaseException:
            if self._proc is not None and self._proc.returncode is None:
                await self._shutdown(graceful=False)
            raise

    async def tools(self) -> list[dict]:
        """tools/list 의 모든 쪽(nextCursor 를 따라간다) — 한 번 받으면 이 손이 사는 동안 그것을 쓴다. 줄마다 네 칸만 남긴다."""
        async with self._tools_lock:
            if self._tools is not None:
                return self._tools
            rows, cursor, seen = [], None, set()
            for _ in range(LIST_PAGES):
                outcome = await self._request("tools/list", {} if cursor is None else {"cursor": cursor}, LIST_TIMEOUT)
                if outcome[0] != "ok":
                    raise ReaError(_failure_text(outcome, LIST_TIMEOUT, self.stderr_tail))
                message = outcome[1]
                result = message.get("result")
                if "error" in message or not isinstance(result, dict):
                    error = message.get("error") if isinstance(message.get("error"), dict) else {}
                    raise ReaError(f"REA 가 도구 목록을 주지 않았다: {_cut(str(error.get('message') or '꼴이 틀린 응답'), 300)}")
                for tool in result.get("tools") if isinstance(result.get("tools"), list) else []:
                    if isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool["name"]:
                        rows.append({"name": tool["name"],
                                     "description": tool["description"] if isinstance(tool.get("description"), str) else "",
                                     "inputSchema": tool["inputSchema"] if isinstance(tool.get("inputSchema"), dict) else {},
                                     "annotations": tool["annotations"] if isinstance(tool.get("annotations"), dict) else {}})
                cursor = result.get("nextCursor")
                if not isinstance(cursor, str) or not cursor or cursor in seen:
                    break
                seen.add(cursor)
            self._tools = rows
            return rows

    async def call(self, name: str, arguments: dict, timeout: float = 600.0) -> dict:
        """tools/call 하나. ok=False 는 나르는 길의 실패뿐이다(시간 넘음 · 자식이 끝남 · JSON-RPC 오류 · 너무 큰 응답) — 그때 text 가
        까닭을 적는다. 도구가 낸 오류는 ok=True · is_error=True 이고, 그 글이 REA 의 오류 봉투(JSON)면 error 에 푼다."""
        if not isinstance(name, str) or not name:
            return _failed("도구 이름이 비었다")
        if not isinstance(arguments, dict):
            return _failed("arguments 는 JSON 객체로 준다")
        outcome = await self._request("tools/call", {"name": name, "arguments": arguments}, timeout)
        if outcome[0] == "ok":
            return _result(outcome[1], outcome[2])
        if outcome[0] == "big":
            return _failed(f"REA 의 응답이 너무 크다({outcome[1]:,} 바이트 — {LINE_MAX:,} 바이트까지 받는다). "
                           "detail:\"summary\" 로 줄이거나 inspect_analysis_view 로 쪽을 나눠 받는다", outcome[1])
        if outcome[0] == "dead" and not self._closed:
            await self._settle()
            outcome = ("dead", self._dead or outcome[1])
        return _failed(_failure_text(outcome, timeout, "" if self._closed else self.stderr_tail))

    async def close(self) -> None:
        """stdin 을 닫고 3초 기다린 뒤, 아직 살아 있으면 프로세스 나무째 끈다. 여러 번 불러도 한 번만 한다."""
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.ensure_future(self._shutdown(graceful=True))
        await asyncio.shield(self._close_task)

    # ------------------------------------------------------------------ 띄우기 · 끄기
    def _reset(self) -> None:
        self._proc = None
        self._stderr_task = None
        self._dead = None
        self._stderr = bytearray()
        self._notes.clear()

    async def _launch(self, env: dict[str, str]) -> None:
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            options["start_new_session"] = True            # 자기 프로세스 무리 — 끌 때 무리째 끈다
        try:
            proc = await self._spawn(*self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     cwd=str(self._cwd), env=env, limit=CHUNK * 4, **options)
        except (OSError, ValueError) as error:
            raise ReaError(f"REA 를 띄우지 못했다: {type(error).__name__}: {_cut(str(error), 300)}") from None
        self._proc = proc
        reader = self._task(self._read_stdout(proc.stdout))
        self._stderr_task = self._task(self._read_stderr(proc.stderr))
        self._task(self._watch(proc, reader))

    async def _settle(self) -> None:
        """자식이 끝났으면 끝난 값과 stderr 의 마지막 줄이 들어올 틈을 준다(각 1초까지) — 실패를 알리는 글에 까닭을 싣는다."""
        proc, task = self._proc, self._stderr_task
        if proc is None:
            return
        await _ended(proc, 1.0)
        if task is not None and not task.done():
            await asyncio.wait({task}, timeout=1.0)

    def _task(self, coroutine) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coroutine, name="gaf-rea-client")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _shutdown(self, *, graceful: bool) -> None:
        proc, tasks = self._proc, list(self._tasks)
        if proc is not None:
            if graceful and proc.returncode is None:
                with contextlib.suppress(Exception):
                    proc.stdin.close()
                await _ended(proc, CLOSE_WAIT)
            if proc.returncode is None:
                await _kill_tree(proc)
                await _ended(proc, KILL_WAIT)
            else:
                _sweep(proc)                                  # 자식은 끝났어도 손자(JVM · 도우미)가 남았을 수 있다
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if proc is not None:
            transport = getattr(proc, "_transport", None)
            if transport is not None:
                with contextlib.suppress(Exception):
                    transport.close()
        self._died("REA 를 닫았다" if self._closed else "REA 를 내렸다")

    def _died(self, reason: str) -> None:
        """자식을 더 쓸 수 없다 — 기다리는 부름을 모두 실패로 끝낸다."""
        if self._dead is None:
            self._dead = reason
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_result(("dead", self._dead))

    def _ended_text(self, proc) -> str:
        code = proc.returncode
        return f"REA 가 끝났다(끝난 값 {code})" if code is not None else "REA 가 출력을 닫았다"

    # ------------------------------------------------------------------ 보내기 · 받기
    async def _request(self, method: str, params: dict, timeout: float) -> tuple:
        """("ok", 메시지, 바이트) · ("timeout",) · ("dead", 까닭) · ("big", 바이트) 가운데 하나를 돌려준다."""
        if self._closed:
            return ("dead", "REA 를 이미 닫았다")
        if self._proc is None or self._dead is not None:
            return ("dead", self._dead or "REA 가 떠 있지 않다")
        self._next += 1
        rid = self._next
        future = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        try:
            try:
                problem = await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            except _Unsendable as error:
                return ("send", str(error))
            if problem:
                return ("dead", problem)
            try:
                return await asyncio.wait_for(future, timeout)
            except TimeoutError:
                self._pending.pop(rid, None)
                await self._send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                  "params": {"requestId": rid, "reason": "timeout"}})
                return ("timeout",)
        except asyncio.CancelledError:
            if self._pending.pop(rid, None) is not None and self.alive:     # 부른 쪽이 멈췄다 — REA 에게도 멈추라고 한다
                self._task(self._send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                       "params": {"requestId": rid, "reason": "cancelled"}}))
            raise
        finally:
            self._pending.pop(rid, None)

    async def _send(self, message: dict) -> str:
        """메시지 하나를 한 줄로 보낸다 — 여럿이 같이 불러도 한 줄씩(자물쇠). 못 보냈으면 까닭을, 보냈으면 "" 를 돌려준다.
        JSON 으로 못 바꾸는 값이면 `_Unsendable` 을 던진다(자식의 탓이 아니다)."""
        try:
            data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        except (TypeError, ValueError, RecursionError) as error:
            raise _Unsendable(f"받는 값을 JSON 으로 바꾸지 못했다({type(error).__name__})") from None
        proc = self._proc
        if proc is None or proc.stdin is None:
            return "REA 가 떠 있지 않다"
        async with self._write_lock:
            if proc.returncode is not None or self._dead is not None:
                return self._dead or self._ended_text(proc)
            try:
                proc.stdin.write(data)
                await asyncio.wait_for(proc.stdin.drain(), WRITE_WAIT)
            except TimeoutError:
                self._died(f"REA 가 {WRITE_WAIT:g}초 동안 입력을 받지 않았다")
                return self._dead or ""
            except (OSError, RuntimeError, AttributeError) as error:      # 끊긴 파이프 · 닫힌 전송
                return f"REA 에 보내지 못했다({type(error).__name__})"
        return ""

    async def _read_stdout(self, stream) -> None:
        """stdout 을 덩어리로 읽어 스스로 줄을 자른다 — 아주 긴 줄에도 죽지 않는다(LINE_MAX 를 넘는 줄은 머리 · 끝만 남겨 버린다)."""
        parts: list[bytes] = []
        size, over, head, tail = 0, False, b"", b""
        try:
            while True:
                chunk = await stream.read(CHUNK)
                if not chunk:
                    break
                start = 0
                while True:
                    end = chunk.find(b"\n", start)
                    piece = chunk[start:] if end < 0 else chunk[start:end]
                    if piece:
                        if over:
                            tail = (tail + piece)[-256:]
                        elif size + len(piece) > LINE_MAX:
                            over = True
                            head = (parts[0] if parts else piece)[:256]
                            tail = (b"".join(parts[-2:]) + piece)[-256:]
                            parts = []
                        else:
                            parts.append(piece)
                        size += len(piece)
                    if end < 0:
                        break
                    start = end + 1
                    if over:
                        self._oversize(head, tail, size)
                    elif parts:
                        data = parts[0] if len(parts) == 1 else b"".join(parts)
                        parts = []
                        await self._dispatch(data)
                        del data
                    parts, size, over, head, tail = [], 0, False, b"", b""
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 — 읽는 쪽이 죽으면 기다리는 부름이 모두 멈춘다. 까닭을 남기고 실패로 끝낸다
            self._died(f"REA 의 출력을 읽다가 멈췄다({type(error).__name__})")
            return
        proc = self._proc
        for _ in range(20):                                        # 끝난 값을 적으려고 잠깐 기다린다
            if proc is None or proc.returncode is not None:
                break
            await asyncio.sleep(0.05)
        self._died(self._ended_text(proc) if proc is not None else "REA 가 출력을 닫았다")

    async def _dispatch(self, data: bytes) -> None:
        size = len(data)
        try:
            message = await asyncio.to_thread(json.loads, data) if size > PARSE_IN_THREAD else json.loads(data)
        except (ValueError, RecursionError):
            self._notes.append({"kind": "글이 아닌 줄", "bytes": size})
            return
        if not isinstance(message, dict):
            self._notes.append({"kind": "꼴이 틀린 줄", "bytes": size})
            return
        if "method" in message:
            method = message.get("method")
            if "id" in message:                                    # 서버가 묻는 것 — ping 에만 답하고 나머지는 모른다고 한다
                reply = ({"jsonrpc": "2.0", "id": message["id"], "result": {}} if method == "ping" else
                         {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "not supported by gaf"}})
                self._task(self._send(reply))
            self._notes.append({"kind": "알림", "method": _cut(str(method), 80), "bytes": size})
            return
        rid = message.get("id")
        if isinstance(rid, int) and not isinstance(rid, bool):
            future = self._pending.pop(rid, None)
            if future is not None and not future.done():
                future.set_result(("ok", message, size))

    def _oversize(self, head: bytes, tail: bytes, size: int) -> None:
        """LINE_MAX 를 넘어 버린 줄 — 머리나 끝에서 응답 번호를 찾아 그 부름만 실패로 끝낸다(못 찾으면 그 부름은 시간이 넘어 끝난다)."""
        found = _TAIL_ID.search(tail) or _HEAD_ID.search(head)
        self._notes.append({"kind": "너무 긴 줄", "bytes": size})
        if found:
            future = self._pending.pop(int(found.group(1)), None)
            if future is not None and not future.done():
                future.set_result(("big", size))

    async def _read_stderr(self, stream) -> None:
        with contextlib.suppress(Exception):
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return
                self._stderr += chunk
                if len(self._stderr) > STDERR_TAIL:
                    del self._stderr[:-STDERR_TAIL]

    async def _watch(self, proc, reader: asyncio.Task) -> None:
        """자식이 끝났는지 본다 — 손자가 stdout 을 물려받아 쥐고 있으면 EOF 가 오지 않으니, 끝난 값으로도 안다."""
        while proc.returncode is None:
            await asyncio.sleep(WATCH_EVERY)
        await asyncio.wait({reader}, timeout=2.0)                 # 끝나기 전에 쓴 응답을 다 읽을 틈
        self._died(self._ended_text(proc))


# ---------------------------------------------------------------------- 결과의 꼴
def _failed(text: str, size: int = 0) -> dict:
    return {"ok": False, "is_error": False, "structured": None, "text": text, "error": None, "bytes": size}


def _failure_text(outcome: tuple, timeout: float, tail: str) -> str:
    if outcome[0] == "timeout":
        return f"REA 가 {timeout:g}초 안에 끝내지 않아 그 부름을 멈췄다(취소를 보냈다)"
    if outcome[0] == "send":
        return str(outcome[1])
    return str(outcome[1] if len(outcome) > 1 else "REA 와 말하지 못했다") + _tail_note(tail, 600)


def _result(message: dict, size: int) -> dict:
    if "error" in message:
        error = message["error"] if isinstance(message["error"], dict) else {}
        code = error.get("code")
        text = _cut(str(error.get("message") or ""), 2000)
        return {"ok": False, "is_error": False, "structured": None, "text": f"REA 가 그 부름을 받지 않았다(JSON-RPC {code}): {text}",
                "error": {"code": code, "category": "jsonrpc", "message": text, "remediation": ""}, "bytes": size}
    result = message.get("result")
    if not isinstance(result, dict):
        return _failed("REA 의 응답 꼴이 틀렸다(result 가 없다)", size)
    content = result.get("content")
    texts = [part["text"] for part in content if isinstance(part, dict) and part.get("type") == "text"
             and isinstance(part.get("text"), str)] if isinstance(content, list) else []
    text = texts[0] if len(texts) == 1 else "\n".join(texts)
    is_error = result.get("isError") is True
    # 성공한 결과는 같은 것을 두 번 싣는다(content 의 JSON 글 · structuredContent) — 글을 다시 풀지 않는다(풀면 세 벌이 된다).
    return {"ok": True, "is_error": is_error, "structured": result.get("structuredContent"), "text": text,
            "error": _envelope(text) if is_error else None, "bytes": size}


def _envelope(text: str) -> dict | None:
    """REA 의 오류 봉투 `{"error": {code, category, message, retryable, remediation:{action}, details}}` 를 푼다 — 아니면 None."""
    if not text.lstrip().startswith("{") or len(text) > 1024 * 1024:
        return None
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict):
        return None
    remedy = error.get("remediation")
    if isinstance(remedy, dict):
        remedy = remedy.get("action") if isinstance(remedy.get("action"), str) else json.dumps(remedy, ensure_ascii=False)
    out = {"code": _cut(str(error.get("code") or ""), 100), "category": _cut(str(error.get("category") or ""), 100),
           "message": _cut(str(error.get("message") or ""), 2000), "remediation": _cut(str(remedy or ""), 1000)}
    if isinstance(error.get("details"), dict) and error["details"]:
        out["details"] = error["details"]         # 6.10.1: 틀린 값의 목록(issues) · 넘친 크기(reported_limits) — 줄이는 것은 gateway 가 한다
    return out


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _tail_note(tail: str, limit: int = 1500) -> str:
    return f" — stderr 끝: {_cut(tail[-limit:], limit + 1)}" if tail else ""


# ---------------------------------------------------------------------- 프로세스 나무
async def _ended(proc, wait: float) -> bool:
    """끝났나를 끝난 값으로 본다 — `proc.wait()` 는 파이프가 다 닫혀야 돌아온다(손자가 쥐면 멈춘다 · Python 3.12+)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    while proc.returncode is None and loop.time() < deadline:
        await asyncio.sleep(0.05)
    return proc.returncode is not None


def _taskkill() -> str:
    path = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "taskkill.exe")
    return path if os.path.isfile(path) else "taskkill"


async def _kill_tree(proc) -> None:
    """나무째 끈다 — Windows: taskkill /T /F(거부되면 자손을 찾아 끈다) · 그 밖: 띄울 때 만든 프로세스 무리."""
    if os.name == "nt":
        done = False
        killer = None
        try:
            killer = await asyncio.create_subprocess_exec(
                _taskkill(), "/PID", str(proc.pid), "/T", "/F", stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            done = await asyncio.wait_for(killer.wait(), 10) == 0
        except (OSError, TimeoutError):
            if killer is not None and killer.returncode is None:
                with contextlib.suppress(Exception):
                    killer.kill()
        if not done:
            _sweep(proc)
    else:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()


def _sweep(proc) -> None:
    """자식의 남은 자손을 끈다(자식이 스스로 끝난 뒤에도) — Windows: 부모 번호로 찾아 아래부터 · 그 밖: 프로세스 무리."""
    if os.name == "nt":
        from ..jobs import _kill_windows_tree

        class Root:                                   # 끝난 자식의 asyncio 손잡이는 kill() 에서 ProcessLookupError 를 낸다 — 번호만 넘긴다
            pid = proc.pid

            @staticmethod
            def kill():
                with contextlib.suppress(Exception):
                    proc.kill()
        with contextlib.suppress(Exception):
            _kill_windows_tree(Root)
    else:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
