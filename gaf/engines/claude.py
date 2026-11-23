"""Claude 엔진 — Claude Agent SDK(claude_agent_sdk)로 세션을 연다.

근거(돌려 본 것): docs/호환표.md §2 · §3, 실측 코드 probes/sdk_probe.py · probes/peer2_probe.py.
이 파일만 SDK를 안다. SDK는 open() 안에서 불러온다(SDK가 없는 파이썬에서도 gaf 의 나머지는 돌게).

세션에는 우리 명령만 프로세스 안 MCP 도구로 붙인다. 남의 MCP 서버 · 스킬 · 플러그인은 싣지 않는다.
설정 출처를 비워 CLAUDE.md 를 싣지 않고, 지휘자가 만든 글(team/공통.md + 역할.md)만 넣는다. 셸 명령은 되돌릴 길로 남긴다.
SDK의 훅(PreToolUse = 문) · 권한 콜백(묻기) · 이어 가기 · 환경변수 · 도구 · 문맥 압축을 쓴다.

한 덩이(turn 하나)와 도는 턴에 글 넣기(send) — 11-12 실측(SDK 0.2.180 · CLI 2.1.321 · docs/노하우.md):
  - 도는 턴에 글을 더 쓰면(client.query) CLI 는 다음 도구 차례에 그 글을 붙인다 — 결과(ResultMessage)는 하나다.
  - 마지막 도구 차례 뒤에 쓴 글은 도는 턴이 끝난 뒤 곧바로 새 턴이 된다 — 결과가 둘이고, 그 사이에 쉼 알림은 없다.
  - 환경변수 CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1 이면 `session_state_changed`(running · requires_action · idle)가 온다.
    idle 은 밀린 글까지 다 끝난 뒤 한 번 온다.
그래서 turn() 은 결과 하나가 아니라 「CLI 가 쉴 때까지」를 한 덩이로 보고, 그 사이의 결과들을 하나로 모아 낸다
(지휘자가 「쉰다」고 본 세션이 뒤에서 넣은 글의 턴을 돌고 있지 않게).
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import mimetypes
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

from .base import (TOOL_SERVER, Engine, EngineEvent, EngineSession, NotRunning, SessionSpec, ToolDef,
                   session_tools, tool_label)

# 지시문 파일이 없는 직접 호출만 이 한도를 쓴다(Windows 명령줄 한도 약 32KB).
MAX_APPEND_CHARS = 12000
# CLI 가 내는 메시지 한 줄을 SDK 가 받아 주는 크기(ClaudeAgentOptions.max_buffer_size). SDK 기본값은 1MB 다 — 그림을 Read 하면
# 도구 결과 한 줄에 그림이 base64 로 실려 그 한도를 넘고, SDK 가 읽기를 끝내 턴이 오류로 죽고 세션이 닫힌다(11-17 한 프로젝트의 총괄이
# 699KB jpg 를 Read 하다 겪음 · probes/bigread_live.py 로 재현: 700KB JPEG 한 장이면 넘는다). 한도는 미리 잡는 메모리가 아니라
# 「한 줄이 이보다 길면 그만 읽는다」는 선이다 — 그림 여러 장 · PDF 쪽을 한 번에 읽어도 남게 넉넉히 둔다.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
ACCOUNT_TIMEOUT = 30.0          # 연결과 계정 읽기의 제한 시간(초)
ACCOUNT_CLOSE_TIMEOUT = 10.0    # 읽기가 끝난 뒤 닫기에 따로 주는 시간(초)
# 사람이 보낸 것으로 치는 턴의 출처(ResultMessage.origin.kind)
OWN_ORIGINS = (None, "human")
_EOF = object()
_DONE = object()
# CLI 가 쉰다는 상태 값(session_state_changed.state). None 은 그 알림을 한 번도 못 받은 것이다(알림이 없는 CLI)
IDLE = "idle"


@dataclass
class ClaudeEngineOptions:
    setting_sources: list[str] = field(default_factory=list)   # 비움 = 설정 파일 · CLAUDE.md 를 싣지 않는다
    system_prompt: str = "preset"          # preset = Claude Code 기본 지시문 + 역할 글 · bare = 역할 글만
    connect_timeout: float = 120.0
    cli_path: str | None = None            # None 이면 SDK에 같이 오는 실행 파일
    tool_search: bool | None = None        # False 면 ENABLE_TOOL_SEARCH=false(도구를 처음부터 다 싣는다)
    send_grace: float = 2.0                # 넣은 글을 CLI 가 집어 가기를 기다리는 시간(초) — 그 안에 새 턴이 시작되면 같은 덩이로 본다


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def tool_target(tool_input: dict[str, Any]) -> str:
    """도구 입력에서 화면에 보일 짧은 대상(파일 경로 · 명령 앞부분)을 고른다."""
    for key in ("file_path", "notebook_path", "path", "command", "pattern", "url", "query", "skill", "description", "to"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return _short(value, 120)
    return ""


def limit_windows(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """RateLimitEvent raw 의 unifiedWindows → {창 이름: {"pct", "resets_at", "status"}}."""
    out: dict[str, dict[str, Any]] = {}
    for name, window in (raw.get("unifiedWindows") or {}).items():
        if not isinstance(window, dict):
            continue
        used = window.get("utilization")
        out[str(name)] = {"pct": None if used is None else round(float(used) * 100, 1),
                          "resets_at": window.get("resetsAt"), "status": window.get("status")}
    return out


def account_data(info: Any) -> dict[str, Any]:
    """계정에서 구독 · 제공자의 글 값만 고른다."""
    account = info.get("account") if isinstance(info, dict) else None
    account = account if isinstance(account, dict) else {}
    return {key: value if isinstance(value, str) else None
            for key, value in (("subscription", account.get("subscriptionType")), ("provider", account.get("apiProvider")))}


def models_data(info: Any) -> list[dict[str, Any]]:
    """SDK 초기 정보의 모델 목록 → [{id · name · note · efforts}] — 이 로그인이 고를 수 있는 것과 모델마다 되는 세기다.

    `default`(계정의 기본 모델)는 빈 글로 준다 — 역할 글의 `model:` 을 비운 것과 같다(11-24 사용자: 화면에서 모델 · 세기 바꾸기).
    """
    rows = info.get("models") if isinstance(info, dict) else None
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("value"), str) or not row["value"]:
            continue
        efforts = row.get("supportedEffortLevels") if row.get("supportsEffort") else []
        out.append({"id": "" if row["value"] == "default" else row["value"],
                    "name": str(row.get("displayName") or row["value"]), "note": str(row.get("description") or ""),
                    "efforts": [level for level in efforts if isinstance(level, str)] if isinstance(efforts, list) else []})
    return out


@dataclass
class _Unit:
    """turn() 하나가 모으는 것 — 내 글로 돈 턴들의 결과와 끼어든 턴의 결과."""

    own: list[dict[str, Any]] = field(default_factory=list)        # 내 글(첫 글 · 넣은 글)로 돈 턴의 결과
    foreign: list[dict[str, Any]] = field(default_factory=list)    # 끼어든 턴(배경 작업 알림 · 밖의 세션 글)의 결과
    pending: bool = False       # 결과 뒤에 새 턴이 시작된 표시가 왔다 — 그 턴의 결과를 기다린다
    sent: bool = False          # 마지막 결과 뒤로 글을 넣은 적이 있다(쉼 알림이 없는 CLI 가 결과 뒤를 잠깐 더 보는 까닭)
    quiet_until: float = 0.0    # 이때까지는 끝내지 않고 새 턴의 표시를 기다린다


def merge_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """한 덩이 안의 결과들 → 결과 하나(성공은 모두 성공일 때 · 글은 이어 붙이고 · 값은 더하고 · 끝난 까닭은 마지막 것).

    결과가 둘 이상이면 턴마다의 답을 `parts` 에 따로 싣는다 — 그 답들은 턴이 돌 때 이미 글(text)로 나갔으니, 받는 쪽(지휘자)이
    이어 붙인 글을 대화에 한 번 더 적지 않게 한다.
    """
    if len(rows) == 1:
        return dict(rows[0])
    usage: dict[str, Any] = {}
    for row in rows:
        for key, value in (row.get("usage") or {}).items():
            usage[key] = usage.get(key, 0) + (value or 0)
    texts = [text for text in (str(row.get("text") or "").strip() for row in rows) if text]
    return {
        "ok": all(bool(row.get("ok")) for row in rows),
        "text": "\n\n".join(texts),
        "parts": texts,
        "turns": sum(int(row.get("turns") or 0) for row in rows),
        "cost_usd": round(sum(float(row.get("cost_usd") or 0.0) for row in rows), 6),
        "usage": usage,
        "duration_ms": sum(int(row.get("duration_ms") or 0) for row in rows),
        "stop": rows[-1].get("stop"),
        "denials": sum(int(row.get("denials") or 0) for row in rows),
    }


def result_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for item in content:
        if isinstance(item, dict):
            parts.append(str(item.get("text", "")) if item.get("type") == "text" else f"[{item.get('type', '?')}]")
        else:
            parts.append(str(item))
    return "\n".join(p for p in parts if p)


class ClaudeSession(EngineSession):
    def __init__(self, spec: SessionSpec, options: ClaudeEngineOptions) -> None:
        self.spec = spec
        self.options = options
        self.session_id: str | None = spec.resume
        self.model: str | None = None
        self._client: Any = None
        self._sdk: Any = None
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._cost_seen = 0.0
        self._stderr: list[str] = []
        self._resumed = bool(spec.resume)
        self._state: str | None = None         # CLI 의 상태(running · requires_action · idle) · 알림을 못 받았으면 None
        self._unit: _Unit | None = None        # 도는 덩이(없으면 글을 못 넣는다)
        self._sending = 0                      # 지금 쓰고 있는 send() 수
        self._last_send = 0.0                  # 마지막으로 글을 쓴 때(첫 글 포함 · time.monotonic)

    # ------------------------------------------------------------------ 열기
    async def open(self) -> None:
        import claude_agent_sdk as sdk

        self._sdk = sdk
        spec = self.spec
        kwargs: dict[str, Any] = {
            "cwd": str(spec.cwd),
            "setting_sources": list(self.options.setting_sources),
            "permission_mode": spec.permission,
            "can_use_tool": self._can_use_tool,
            "strict_mcp_config": True,
            "stderr": self._on_stderr,
            "max_buffer_size": MAX_MESSAGE_BYTES,       # 그림 Read 결과처럼 1MB 를 넘는 한 줄도 받는다(위 상수의 설명)
            # 글을 있는 그대로 넣는다 — 이 값이 없으면 Claude Code 가 글 속의 `@경로` 를 그 파일의 내용으로 바꿔 붙인다(도구 호출이
            # 아니라 문을 거치지 않는다 · 11-12 실측: 다른 세션이 보낸 글의 `@secret.txt` 로 파일 내용이 그대로 붙었다)
            "verbatim_prompts": True,
        }
        if spec.model:
            kwargs["model"] = spec.model
        if spec.effort:
            if spec.effort not in ClaudeEngine.efforts:
                raise ValueError(f"모르는 세기: {spec.effort}")
            kwargs["effort"] = spec.effort
        if spec.resume:
            kwargs["resume"] = spec.resume
        else:
            self.session_id = str(uuid.uuid4())
            kwargs["session_id"] = self.session_id
        kwargs["system_prompt"] = self._system_prompt()
        if spec.gate is not None:
            # on_failure="block": 문이 예외를 내면 그 도구를 막는다(열린 채로 지나가지 않게)
            kwargs["hooks"] = {"PreToolUse": [sdk.HookMatcher(hooks=[self._pre_tool_use], on_failure="block")]}
        # 남의 MCP 서버 · 플러그인은 싣지 않는다(strict_mcp_config=True 라 전역 MCP 도 안 들어온다).
        # skills=[] 는 SDK 문서의 「스킬을 목록에서 다 뺀다」다. spec.skills · spec.plugins · spec.mcp_config 는 보지 않는다.
        kwargs["skills"] = []
        tools = session_tools(spec.tools)
        if tools:
            # 우리 명령(글 보내기 · 총괄의 맡기기 …)만 프로세스 안 도구로 붙인다 — 따로 도는 서버가 없고 명령마다 프로세스를 띄우지
            # 않는다(11-13 사용자 「메시지는 MCP로」 · 11-12 11:01 전의 꼴). 모델은 mcp__gaf__<이름> 으로 부른다.
            kwargs["mcp_servers"] = {TOOL_SERVER: sdk.create_sdk_mcp_server(
                name=TOOL_SERVER, version="1", tools=[self._sdk_tool(sdk, tool) for tool in tools])}
        if spec.deny_tools:
            # 세션이 쓰지 않는 도구(예약 · 클라우드 실행)를 목록에서 뺀다 — CLI 의 `--disallowedTools`. 문(PreToolUse)도 같은 이름을 막는다
            kwargs["disallowed_tools"] = list(spec.deny_tools)
        if spec.add_dirs:
            kwargs["add_dirs"] = [str(p) for p in spec.add_dirs]
        env = dict(spec.env)
        if self.options.tool_search is False:
            env.setdefault("ENABLE_TOOL_SEARCH", "false")
        # 쉼 알림(session_state_changed)을 받는다 — 한 덩이의 끝을 안다(머리말)
        env["CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"] = "1"
        # Bash · Agent 의 배경 실행 칸(run_in_background)을 없앤다 — 턴이 끝난 뒤에도 도는 작업을 만들지 않게(11-12 QA 결함-27 ·
        # 실측: 이 값이면 모델이 그 칸을 못 본다). 문도 그 칸이 참이면 막는다(scope.BACKGROUND_REASON)
        env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"
        kwargs["env"] = env
        if spec.session_name:
            kwargs["extra_args"] = {"name": spec.session_name}
        if spec.instructions_file is not None and self.options.system_prompt != "bare":
            kwargs.setdefault("extra_args", {})["append-system-prompt-file"] = str(spec.instructions_file)
        if spec.accept_peers:
            kwargs["settings"] = json.dumps({"crossSessionInbound": "accept"})
        if self.options.cli_path:
            kwargs["cli_path"] = self.options.cli_path
        self._client = sdk.ClaudeSDKClient(sdk.ClaudeAgentOptions(**kwargs))
        try:
            await asyncio.wait_for(self._client.connect(), self.options.connect_timeout)
        except BaseException:
            await self._drop_client()
            raise
        self._reader = asyncio.create_task(self._read_loop(), name=f"gaf-claude-reader:{spec.role}")

    def _system_prompt(self) -> Any:
        if self.spec.instructions_file is not None:
            return ({"type": "file", "path": str(self.spec.instructions_file)} if self.options.system_prompt == "bare"
                    else {"type": "preset", "preset": "claude_code"})
        text = self.spec.instructions.strip()
        if len(text) > MAX_APPEND_CHARS:      # 파일 없는 호출은 명령줄 한도를 넘기지 않는다.
            text = (f"너는 「{self.spec.role}」 역할이다. 지시문이 길어 여기에 싣지 않았다. "
                    f"일을 시작하기 전에 team/공통.md(있으면)와 team/{self.spec.role}/역할.md 를 읽고 따른다.")
        if self.options.system_prompt == "bare":
            return text
        if text:
            return {"type": "preset", "preset": "claude_code", "append": text}
        return {"type": "preset", "preset": "claude_code"}

    @staticmethod
    def _sdk_tool(sdk: Any, definition: ToolDef) -> Any:
        """ToolDef 하나 → SDK 의 프로세스 안 도구. handler 의 글을 그대로 돌려주고, 「오류:」로 시작하면 오류로 표시한다."""
        async def run(args: Any) -> dict[str, Any]:
            text = await definition.handler(dict(args) if isinstance(args, dict) else {})
            result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
            if text.startswith("오류:"):
                result["is_error"] = True
            return result

        return sdk.tool(tool_label(definition.name), definition.description, definition.schema)(run)

    def _on_stderr(self, line: str) -> None:
        self._stderr.append(line[:400])
        del self._stderr[:-20]

    # ------------------------------------------------------------------ 문
    async def _pre_tool_use(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        gate = self.spec.gate
        if gate is None:
            return {}
        ok, reason = await gate(str(input_data.get("tool_name", "")), dict(input_data.get("tool_input") or {}))
        if ok:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": reason or "지휘자가 막았다"}}

    async def _can_use_tool(self, tool_name: str, tool_input: dict[str, Any], context: Any) -> Any:
        sdk = self._sdk
        if tool_name == "AskUserQuestion":
            questions = list(tool_input.get("questions") or [])
            answers = await self.spec.asker(questions) if self.spec.asker else None
            if answers is None:
                return sdk.PermissionResultDeny(
                    message="이 세션은 사용자에게 물을 수 없다. 가장 그럴듯한 쪽으로 정하고 보고에 「스스로 정한 것」으로 적어라. "
                            "정말 못 정하면 「막힘: <이유>」로 끝내라.")
            return sdk.PermissionResultAllow(updated_input={"questions": questions, "answers": answers})
        # 그 밖의 「물어볼 도구」: 문(PreToolUse)을 이미 지났으므로 허용한다
        return sdk.PermissionResultAllow()

    # ------------------------------------------------------------------ 읽기
    async def _read_loop(self) -> None:
        try:
            async for message in self._client.receive_messages():
                await self._queue.put(message)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            await self._queue.put(exc)
        finally:
            await self._queue.put(_EOF)

    def _convert(self, message: Any, unsolicited: bool = False) -> list[EngineEvent]:
        sdk = self._sdk
        out: list[EngineEvent] = []
        if isinstance(message, sdk.HookEventMessage):
            return out
        if isinstance(message, sdk.SystemMessage):
            data = message.data or {}
            if message.subtype == "init":
                self.session_id = data.get("session_id") or self.session_id
                self.model = data.get("model") or self.model
                out.append(EngineEvent("session", {"session_id": self.session_id, "model": self.model, "engine": "claude",
                                                   "permission": data.get("permissionMode"), "resumed": self._resumed}))
            elif message.subtype == "compact_boundary":
                meta = data.get("compact_metadata") or data.get("compactMetadata") or {}
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except ValueError:
                        meta = {}
                out.append(EngineEvent("compact", {"trigger": meta.get("trigger"), "pre_tokens": meta.get("pre_tokens"),
                                                   "post_tokens": meta.get("post_tokens")}))
            elif message.subtype == "peer_message_hold":
                out.append(EngineEvent("peer_hold", {"from_name": data.get("from_name"), "cause": data.get("cause")}))
            elif message.subtype == "session_state_changed":
                state = data.get("state")
                if isinstance(state, str) and state:
                    self._state = state                 # 이벤트로 내지 않는다 — 덩이의 끝을 가리는 데만 쓴다
            return out
        if isinstance(message, sdk.RateLimitEvent):
            info = message.rate_limit_info
            windows = limit_windows(info.raw or {})
            pct = None if info.utilization is None else round(float(info.utilization) * 100, 1)
            if pct is None:     # 사용률은 맨 위 칸이 비어 와도 unifiedWindows 안에 있다(11-12 실측)
                pct = (windows.get(info.rate_limit_type or "") or {}).get("pct")
            out.append(EngineEvent("limit", {"status": info.status, "window": info.rate_limit_type, "resets_at": info.resets_at,
                                             "pct": pct, "windows": windows}))
            return out
        if isinstance(message, sdk.AssistantMessage):
            sub = message.parent_tool_use_id is not None
            for block in message.content:
                if isinstance(block, sdk.TextBlock):
                    if block.text.strip():
                        out.append(EngineEvent("text", {"text": block.text, "sub": sub}))
                elif isinstance(block, sdk.ToolUseBlock):
                    tool_input = block.input or {}
                    tool = {"id": block.id, "name": block.name, "target": tool_target(tool_input), "sub": sub}
                    path = tool_input.get("file_path") or tool_input.get("notebook_path")
                    if isinstance(path, str) and path:
                        tool["path"] = path         # 파일 도구는 자르지 않은 경로도 싣는다(결과물 찾기)
                    out.append(EngineEvent("tool", tool))
            return out
        if isinstance(message, sdk.UserMessage):
            if isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, sdk.ToolResultBlock):
                        out.append(EngineEvent("tool_result", {"id": block.tool_use_id, "ok": not block.is_error,
                                                               "excerpt": _short(result_text(block.content), 300)}))
            return out
        if isinstance(message, sdk.ResultMessage):
            total = float(message.total_cost_usd or 0.0)      # 한 클라이언트 안에서는 누적값이다
            cost = max(0.0, total - self._cost_seen)
            self._cost_seen = max(self._cost_seen, total)
            usage = message.usage or {}
            out.append(EngineEvent("result", {
                "ok": (not message.is_error) and message.subtype == "success",
                "text": message.result or "",
                "turns": message.num_turns,
                "cost_usd": round(cost, 6),
                "usage": {"input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0),
                          "cache_read": usage.get("cache_read_input_tokens", 0),
                          "cache_write": usage.get("cache_creation_input_tokens", 0)},
                "duration_ms": message.duration_ms,
                "stop": message.terminal_reason or message.subtype,
                "denials": len(message.permission_denials or []),
            }))
            return out
        return out

    @staticmethod
    def _origin_kind(message: Any) -> Any:
        origin = getattr(message, "origin", None)
        return origin.get("kind") if isinstance(origin, dict) else None

    # ------------------------------------------------------------------ 한 턴
    def _prompt(self, text: str, images: Sequence[Path]) -> Any:
        if not images:
            return text
        blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for path in images:
            media = mimetypes.guess_type(str(path))[0] or "image/png"
            data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})

        async def stream() -> AsyncIterator[dict[str, Any]]:
            yield {"type": "user", "message": {"role": "user", "content": blocks}, "parent_tool_use_id": None}

        return stream()

    def _settled(self, unit: _Unit) -> bool:
        """이 덩이에 더 기다릴 턴이 없는가. 쉼 알림을 받는 CLI 면 쉰다(idle)는 알림이 와야 하고, 못 받는 CLI 면 내 결과가 오면 된다."""
        if unit.pending:
            return False
        if self._state is None:
            return bool(unit.own)
        # 쉰다 = 입력 줄에 남은 글이 없다. 내 결과가 없어도(내 글이 밖에서 일으킨 턴에 섞여 들어갔다) 그 턴의 결과로 끝낸다
        return self._state == IDLE and bool(unit.own or unit.foreign)

    async def _next(self, unit: _Unit) -> Any:
        """다음 메시지 · 덩이가 끝났으면 _DONE. 끝낼 때는 먼저 덩이를 닫는다(결과를 내는 사이에 글이 들어오지 않게)."""
        while True:
            if not self._queue.empty():
                return self._queue.get_nowait()
            if not self._settled(unit):
                return await self._queue.get()
            if self._sending:
                wait = self.options.send_grace          # 글을 쓰는 중이다 — 다 쓰고 CLI 가 집어 갈 틈을 준다
            else:
                # 방금 쓴 글은 CLI 가 아직 못 읽었을 수 있다(쉼 알림이 먼저 나온다) — 그 글로 새 턴이 시작되는지 잠깐 본다
                wait = max(self._last_send + self.options.send_grace, unit.quiet_until) - time.monotonic()
                if wait <= 0:
                    self._unit = None
                    return _DONE
            try:
                return await asyncio.wait_for(self._queue.get(), wait)
            except asyncio.TimeoutError:
                continue

    def _mark(self, unit: _Unit, message: Any) -> None:
        """메시지 하나를 보고 「결과 뒤에 새 턴이 도는가」를 적는다."""
        sdk = self._sdk
        if isinstance(message, sdk.ResultMessage):
            unit.pending = False
            if self._state is None and unit.sent:
                # 쉼 알림이 없는 CLI: 넣은 글이 도구 차례에 못 들어갔으면 이 결과 바로 뒤에 그 글의 턴이 시작된다
                unit.quiet_until = time.monotonic() + self.options.send_grace
            unit.sent = False
            return
        if not (unit.own or unit.foreign):
            return                                      # 첫 결과 전 — 도는 턴의 메시지다
        if isinstance(message, sdk.SystemMessage) and message.subtype == "session_state_changed":
            unit.pending = self._state != IDLE          # 쉰다 = 도는 턴이 없다 · 다시 돈다 = 새 턴이 시작됐다
        elif isinstance(message, (sdk.AssistantMessage, sdk.UserMessage)) or (
                isinstance(message, sdk.SystemMessage) and message.subtype == "init"):
            unit.pending = True

    async def turn(self, text: str, images: Sequence[Path] = ()) -> AsyncIterator[EngineEvent]:
        """글 하나로 한 덩이를 돈다 — CLI 가 쉴 때까지다(도는 동안 send() 로 넣은 글의 턴까지). 결과는 끝에 하나로 모아 낸다."""
        async with contextlib.aclosing(self._run(text, images)) as events:
            async for event in events:
                yield event

    async def _run(self, text: str, images: Sequence[Path], *, verbatim: bool = True) -> AsyncIterator[EngineEvent]:
        if self._client is None:
            raise RuntimeError("세션이 열려 있지 않다")
        async with self._lock:
            # 우리가 보내기 전에 와 있던 것(밖의 세션이 일으킨 턴 · 보류 알림)을 먼저 비운다. CLI 가 아직 도는 중이면(쉼 알림으로 안다)
            # 쉴 때까지 본다 — 도는 턴에 새 덩이의 첫 글을 넣으면 그 턴의 결과와 섞인다.
            while True:
                if not self._queue.empty():
                    stale = self._queue.get_nowait()
                elif self._state in (None, IDLE):
                    break
                else:
                    stale = await self._queue.get()
                if stale is _EOF or isinstance(stale, BaseException):
                    yield EngineEvent("error", {"message": self._error_text(stale)})
                    return
                for event in self._convert(stale, unsolicited=True):
                    if event.kind == "result":
                        yield EngineEvent("note", {"what": "outside_turn", "text": _short(event.data.get("text", ""), 300),
                                                   "cost_usd": event.data.get("cost_usd")})
                    elif event.kind in ("peer_hold", "limit", "compact", "session"):
                        # /compact 를 아직 보내지 않았다. 앞 턴의 압축을 이번 명령의 성공으로 치지 않는다.
                        if verbatim or event.kind != "compact":
                            yield event
            started = self._last_send = time.monotonic()
            client = self._client
            try:
                try:
                    if not verbatim:
                        # SDK 비공개 칸이다(0.2.180 · CLI 2.1.321 · 11-16 실측). True 면 client_composed 표시가 붙어
                        # /compact 도 보통 글로 간다. 이 명령 한 글을 쓰는 동안만 표시를 빼고, 실패해도 곧바로 되돌린다.
                        client._verbatim_prompts = False
                    await client.query(self._prompt(text, images))
                finally:
                    if not verbatim:
                        client._verbatim_prompts = True
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001
                yield EngineEvent("error", {"message": self._error_text(exc)})
                return
            self._last_send = time.monotonic()
            unit = self._unit = _Unit()
            try:
                while True:
                    message = await self._next(unit)
                    if message is _DONE:
                        break
                    if message is _EOF or isinstance(message, BaseException):
                        self._unit = None
                        yield EngineEvent("error", {"message": self._error_text(message)})
                        return
                    own = self._origin_kind(message) in OWN_ORIGINS
                    for event in self._convert(message):
                        if event.kind != "result":
                            yield event
                        elif own:
                            unit.own.append(event.data)
                        else:
                            # 끼어든 턴(배경 작업 알림 · 밖의 세션 글)의 끝 — 우리 턴은 아직이다
                            unit.foreign.append(event.data)
                            yield EngineEvent("note", {"what": "injected_turn", "origin": self._origin_kind(message),
                                                       "text": _short(event.data.get("text", ""), 300)})
                    self._mark(unit, message)
                data = merge_results(unit.own or unit.foreign)
                data["wall_ms"] = int((time.monotonic() - started) * 1000)
                yield EngineEvent("result", data)
            finally:
                self._unit = None

    async def compact(self, keep: str = "") -> dict[str, Any] | None:
        text = "/compact" + (" " + " ".join(keep.splitlines()) if keep else "")
        result = None
        async with contextlib.aclosing(self._run(text, (), verbatim=False)) as events:
            async for event in events:
                if event.kind == "compact":
                    result = event.data
                elif event.kind == "error":
                    raise RuntimeError(event.data.get("message") or "대화를 압축하지 못했다")
        return result

    async def send(self, text: str, images: Sequence[Path] = ()) -> None:
        """도는 덩이에 글을 더 넣는다 — 같은 입력 줄에 글을 하나 더 쓴다. CLI 는 다음 도구 차례에 그 글을 붙이고, 도구 차례가 더 없으면
        도는 턴 뒤에 그 글로 턴을 하나 더 돈다(둘 다 이 덩이 안이다 — 결과는 turn() 이 하나로 낸다). 도는 덩이가 없으면 NotRunning.
        """
        unit = self._unit
        if self._client is None or unit is None:
            raise NotRunning("도는 턴이 없다")
        unit.sent = True
        self._sending += 1
        self._last_send = time.monotonic()
        try:
            await self._client.query(self._prompt(text, images))
        finally:
            self._sending -= 1
            self._last_send = time.monotonic()

    def _error_text(self, problem: Any) -> str:
        tail = " | ".join(self._stderr[-3:])
        if problem is _EOF:
            return "세션 프로세스가 끝났다" + (f" — {tail}" if tail else "")
        return f"{type(problem).__name__}: {problem}" + (f" — {tail}" if tail else "")

    # ------------------------------------------------------------------ 그 밖
    async def interrupt(self) -> None:
        if self._client is not None:
            await self._client.interrupt()

    async def context(self) -> dict[str, Any] | None:
        if self._client is None:
            return None
        try:
            usage = await asyncio.wait_for(self._client.get_context_usage(), 30)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(usage, dict):
            return None
        return {"tokens": usage.get("totalTokens"), "max": usage.get("maxTokens"), "pct": usage.get("percentage")}

    async def account(self) -> dict[str, Any] | None:
        """계정 종류(구독 · API)만 — 값이 비밀인 것은 돌려주지 않는다."""
        if self._client is None:
            return None
        try:
            info = await asyncio.wait_for(self._client.get_server_info(), 30)
        except Exception:  # noqa: BLE001
            return None
        return account_data(info)

    async def _drop_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), 30)
            except BaseException:  # noqa: BLE001
                pass

    async def close(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.cancel()
            try:
                await reader
            except BaseException:  # noqa: BLE001
                pass
        await self._drop_client()


class ClaudeEngine(Engine):
    compacts = True
    name = "claude"
    commands = "tools"          # 우리 명령을 프로세스 안 도구로 붙인다(ClaudeSession.open — 11-13)

    def __init__(self, options: ClaudeEngineOptions | None = None) -> None:
        self.options = options or ClaudeEngineOptions()
        self.models: list[dict[str, Any]] | None = None      # read_account 가 읽어 둔 모델 목록(아직 안 읽었으면 None)

    def session(self, spec: SessionSpec) -> ClaudeSession:
        return ClaudeSession(spec, self.options)

    async def list_models(self, spec: SessionSpec) -> list[dict[str, Any]]:
        """이 로그인이 고를 수 있는 모델(글을 보내지 않는다 — 사용량 없음). 못 읽으면 빈 목록."""
        if self.models is None:
            await self.read_account(spec)
        return list(self.models or [])

    async def read_account(self, spec: SessionSpec) -> dict[str, Any] | None:
        """글을 보내지 않고 SDK 초기 정보의 구독만 읽고 닫는다."""
        try:
            import claude_agent_sdk as sdk

            kwargs = {"cwd": str(spec.cwd), "env": dict(spec.env), "setting_sources": [],
                      "strict_mcp_config": True, "mcp_servers": {}, "tools": [], "skills": [],
                      "verbatim_prompts": True}
            if self.options.cli_path:
                kwargs["cli_path"] = self.options.cli_path
            client = sdk.ClaudeSDKClient(sdk.ClaudeAgentOptions(**kwargs))

            async def read():
                await client.connect()
                info = await client.get_server_info()
                self.models = models_data(info) or self.models       # 같은 답에 모델 목록이 같이 온다 — 화면의 모델 고르기가 쓴다
                return account_data(info)

            result = None
            try:
                result = await asyncio.wait_for(read(), ACCOUNT_TIMEOUT)
            finally:
                try:
                    await asyncio.wait_for(client.disconnect(), ACCOUNT_CLOSE_TIMEOUT)
                except Exception:  # noqa: BLE001 — 취소는 삼키지 않는다.
                    result = None
            return result
        except Exception:  # noqa: BLE001 — 조회 실패로 켜기를 막지 않는다.
            return None

    def available(self) -> tuple[bool, str]:
        try:
            import claude_agent_sdk  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            return False, f"claude_agent_sdk 를 불러오지 못했다: {exc}"
        return True, ""
