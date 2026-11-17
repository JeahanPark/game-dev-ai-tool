"""Codex 엔진 — OpenAI Codex SDK(openai_codex)로 세션을 연다.

근거(돌려 본 것): probes/codex_probe.py · probes/out/codex_probe_*.json(2026-11-12 · Codex CLI 0.163.0 · SDK 0.167.2 · Windows).
이 파일만 SDK를 안다. SDK는 open() 안에서 불러온다(SDK가 없는 파이썬에서도 gaf 의 나머지는 돌게).
SDK는 `codex app-server`(JSON-RPC)를 띄운다 — 세션 하나에 프로세스 하나다.

우리 명령은 클라이언트 도구로 붙이고, 남의 MCP · 스킬 · 엔진 전용 md(AGENTS.md)는 싣지 않는다.
그래서 이 엔진은
  - AGENTS.md 를 싣지 않는다(`project_doc_max_bytes=0`). 그래도 실린 지시 파일이 있으면(Codex 전역 AGENTS.md) 세션을 열지 않는다
  - 앱(MCP) · 플러그인 · 스킬을 끈다(같이 오는 스킬은 설정 하나로, 찾은 스킬 · MCP 서버는 하나씩)
  - 문은 PreToolUse 훅 하나다: Codex → `.gaf/bin/gaf_hook.py`(gaf/hook.py) → 지휘자 `/api/gate`.
    그 훅만 이 실행에 한해 신뢰하고(`hooks.state` 를 실행 인자로 준다 — `~/.codex` 에 남기지 않는다) 다른 훅은 끈다
  - 샌드박스 workspace-write(역할의 쓰기 폴더 밖 쓰기 · 네트워크는 OS 가 막는다. 읽기는 못 막는다 — 11-15 실측) · 승인은 never(묻지 않고 거부)
지시문은 지휘자가 만든 글(team/공통.md + 역할.md)이 developer_instructions 로 들어간다. 셸 명령은 되돌릴 길로 남긴다.

훅은 실패하면 열린다(훅 명령이 종료 코드 ≠ 0 으로 끝나면 Codex 는 도구를 그냥 돌린다 — 11-12 실측). 그래서 세 겹으로 막는다:
  ① 훅 프로그램(gaf/hook.py)은 지휘자에 닿지 못해도 · 입력이 깨져도 거부를 내고 0 으로 끝난다
  ② 훅 명령(hook_command)은 그 프로그램이 못 뜨거나 죽으면 스스로 거부를 낸다(표시 GATE_DOWN) — 도구가 돌지 않는다
  ③ 이 엔진은 ②의 표시가 든 거부나 훅이 「실패」로 끝났다는 알림을 보면 턴을 멈추고 오류로 끝낸다(문이 고장 난 채로 일하지 않게)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

from .base import EFFORTS, Engine, EngineEvent, EngineSession, NotRunning, SessionSpec, ToolDef, session_tools, tool_label

HOOK_TIMEOUT = 30               # 훅 하나의 제한 시간(초) — gaf/hook.py 의 기다림(20초)보다 길게
ARGS_LIMIT = 24000              # 실행 인자(설정 덮어쓰기)의 글자 수 한도 — Windows 명령줄은 약 32KB 다
STEER_TIMEOUT = 30              # 도는 턴에 글을 넣는 요청(turn/steer)의 답을 기다리는 시간(초)
TOOL_TIMEOUT = 90.0             # 우리 도구 하나(지휘자의 handler)의 답을 기다리는 시간(초) — 셸 명령 길(gaf/cli.py)과 같다
ACCOUNT_TIMEOUT = 30.0          # 세션 없는 계정 조회의 읽기 제한 시간(초)
ACCOUNT_CLOSE_TIMEOUT = 10.0    # 그 뒤 app-server 를 닫는 데 적어도 주는 시간(초) — 읽기가 시간을 다 써도 프로세스를 남기지 않게(30분마다 돈다)
CANNOT_ASK = ("이 세션은 사용자에게 물을 수 없다. 가장 그럴듯한 쪽으로 정하고 보고에 「스스로 정한 것」으로 적어라. "
              "정말 못 정하면 마지막 답의 첫 줄을 「막힘: <이유>」로 써라.")
WINDOWS = {10080: "seven_day", 300: "five_hour"}        # 한도 창 길이(분) → 지휘자가 쓰는 이름
# Windows 에서 Codex 의 셸은 Windows PowerShell 5.1 이다. BOM 없는 UTF-8 파일을 ANSI 로 읽고, Set-Content 는 ANSI 로 쓴다 —
# 11-12 진짜 세션에서 역할이 인계 파일을 「글자가 깨져 보인다」며 통째로 갈아 썼다. 그래서 지시문 끝에 이 글을 붙인다.
# 파일을 apply_patch 로 쓰게 하면 쓰기 범위 확인(문)도 거친다 — 셸로 쓴 것은 문이 경로를 보지 못한다.
WINDOWS_NOTE = """[이 PC의 셸 — 엔진이 붙인 글]
셸은 Windows PowerShell 5.1 이다. 이 프로젝트의 글 파일은 UTF-8 이다.
- 파일을 만들거나 고칠 때는 apply_patch 를 쓴다. 셸의 Set-Content · Out-File · `>` 로 쓰지 않는다(한글이 깨지거나 BOM 이 붙고, 쓰기 범위 확인을 거치지 않는다).
- 파일을 읽을 때는 `Get-Content -Encoding UTF8 <파일>` 로 읽는다(-Encoding 을 빼면 한글이 깨져 보인다 — 파일이 깨진 것이 아니다)."""
FILE_TOOLS = {"add": "Write", "update": "Edit", "delete": "Delete"}
# 훅 명령이 스스로 내는 거부의 표시(훅 프로그램이 못 떴거나 죽었을 때). 엔진이 이 표시를 보면 턴을 멈춘다.
# 거부 글은 ASCII 로만 쓴다(PowerShell 을 거쳐 나간다).
GATE_DOWN = "GAF-GATE-DOWN"
GATE_DOWN_REASON = GATE_DOWN + ": gate hook program did not run"


@dataclass
class CodexEngineOptions:
    codex_bin: str | None = None           # None · 빈 글이면 PATH 의 codex 에서 진짜 실행 파일을 찾는다
    sandbox: str = "workspace-write"       # read-only · workspace-write · danger-full-access
    windows_sandbox: str = "unelevated"    # Windows 샌드박스 방식 — 이 PC에서 돌려 본 값(없으면 명령이 모두 막힌다)
    connect_timeout: float = 60.0


class _Raw:
    """SDK 의 request() 에 넘기는 「답 모양」 — 검사하지 않고 받은 JSON 그대로 돌려받는다.

    SDK(0.167.2)와 PC에 깔린 실행 파일(0.163.0)의 판이 달라 답의 칸이 조금씩 다를 수 있다. 필요한 칸만 꺼내 쓴다.
    """

    @classmethod
    def model_validate(cls, value: Any) -> Any:
        return value


@dataclass
class _Turn:
    """턴 하나를 도는 동안 모으는 것."""

    texts: list[str] = field(default_factory=list)
    final: str | None = None
    usage_first: dict | None = None        # 이 턴의 첫 사용량 알림(총합 − 마지막 = 턴 전의 총합)
    usage_last: dict | None = None
    calls: int = 0
    denials: int = 0
    status: str | None = None
    error: str = ""
    duration_ms: int | None = None
    gate_broken: str = ""
    interrupt_sent: bool = False
    compacted: bool = False
    open_tools: set = field(default_factory=set)


# ---------------------------------------------------------------- 순수 함수(SDK 없이 시험한다)
def find_codex(which=shutil.which) -> str | None:
    """PATH 의 codex 에서 진짜 실행 파일을 찾는다.

    Windows 의 npm 판은 PATH 에 .cmd 껍데기가 있고 진짜 codex.exe 는 그 옆 node_modules 안에 있다.
    껍데기로 띄우면 cmd.exe 가 인자의 `&` · `<` · `>` 를 제 뜻으로 읽어 설정 값이 깨진다 — 그래서 .exe 를 찾는다.
    """
    shim = which("codex")
    if not shim:
        return None
    path = Path(shim).resolve()
    if os.name != "nt" or path.suffix.lower() == ".exe":
        return str(path)
    base = path.parent / "node_modules" / "@openai" / "codex"
    found = sorted(base.glob("node_modules/@openai/codex-*/vendor/*/bin/codex.exe")) + sorted(base.glob("vendor/*/bin/codex.exe"))
    return str(found[0]) if found else None


def _ps(value: str) -> str:
    """PowerShell 의 글자 그대로 문자열(작은따옴표 · 안의 ' 는 둘로)."""
    return "'" + str(value).replace("'", "''") + "'"


def hook_command(python: str, script: str, windows: bool | None = None) -> str:
    """훅 명령 글. Windows 에서 Codex 는 훅 명령을 PowerShell 로 돌린다 — 따옴표로 시작하면 안 돌고 `&` 로 불러야 돈다(11-12 실측).

    훅 프로그램이 못 뜨거나(파일 · 파이썬이 없음) 죽으면(종료 코드 ≠ 0) 명령이 스스로 거부 JSON 을 내고 0 으로 끝난다 —
    그냥 두면 Codex 는 훅이 「실패」한 것으로 보고 도구를 돌린다(열린 채 실패). 프로그램이 낸 글(허용이면 없음 · 거부면 거부 JSON)은 그대로 낸다.
    Windows 쪽 명령에는 큰따옴표를 쓰지 않는다(Codex 가 PowerShell 에 넘길 때 따옴표가 어떻게 되는지에 기대지 않으려고).
    11-12 `powershell -Command` 로 흉내 내 봄: 허용 · 거부 · 죽음 · 예외 · 파일 없음 · 파이썬 없음 여섯 가지가 뜻대로 나왔다.
    """
    python, script = Path(python).as_posix(), Path(script).as_posix()
    if os.name == "nt" if windows is None else windows:
        deny = ("@{hookSpecificOutput=@{hookEventName='PreToolUse';permissionDecision='deny';"
                f"permissionDecisionReason={_ps(GATE_DOWN_REASON)}}}}} | ConvertTo-Json -Compress")
        return (f"$o = & {_ps(python)} -X utf8 {_ps(script)}; "
                f"if (-not $? -or $LASTEXITCODE -ne 0) {{ {deny} }} else {{ $o }}; exit 0")
    deny = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": GATE_DOWN_REASON}}, ensure_ascii=True, separators=(",", ":"))
    return f"{shlex.quote(python)} -X utf8 {shlex.quote(script)} || printf '%s' {shlex.quote(deny)}"


def instructions_text(text: str, windows: bool | None = None) -> str:
    """developer_instructions 로 보낼 글: 지휘자가 만든 지시문 + (Windows 면) 셸 알림."""
    parts = [str(text or "").strip()]
    if os.name == "nt" if windows is None else windows:
        parts.append(WINDOWS_NOTE)
    return "\n\n".join(part for part in parts if part)


def base_overrides(options: CodexEngineOptions, hook: str | None, windows: bool | None = None) -> tuple[str, ...]:
    """모든 세션에 주는 설정 덮어쓰기(`--config 키=값` · 값은 TOML)."""
    out = ["project_doc_max_bytes=0", "features.apps=false", "features.plugins=false", "skills.bundled.enabled=false",
           # 잠자기 도구를 끈다: 총괄이 일을 맡긴 뒤 이 도구로 몇 분씩 턴 안에서 기다렸다(11-12 실측 156 · 190 · 346초) —
           # 그동안 역할의 결과 글이 총괄에게 들어가지 못한다. 긴 명령은 셸이 끝날 때까지 돌면 된다.
           "features.sleep_tool=false"]
    if os.name == "nt" if windows is None else windows:
        out.append("windows.sandbox=" + json.dumps(options.windows_sandbox))
    if hook:
        out.append('hooks.PreToolUse=[{matcher="*", hooks=[{type="command", command=' + json.dumps(hook, ensure_ascii=False)
                   + f", timeout={HOOK_TIMEOUT}}}]}}]")
    return tuple(out)


def sandbox_overrides(write_dirs) -> tuple[str, ...]:
    """첫 폴더는 작업 폴더로 열고 나머지 폴더만 샌드박스에 더한다(값은 TOML 배열이다)."""
    if len(write_dirs) <= 1:
        return ()
    folders = [path.as_posix() for path in write_dirs[1:]]
    return ("sandbox_workspace_write.writable_roots=" + json.dumps(folders, ensure_ascii=False),)


def hook_rows(listed: Any) -> list[dict]:
    """hooks/list 의 답 → 훅 목록(납작하게)."""
    data = listed.get("data") if isinstance(listed, dict) else None
    return [hook for entry in data or [] if isinstance(entry, dict) for hook in entry.get("hooks") or [] if isinstance(hook, dict)]


def is_gate(hook: dict, command: str | None) -> bool:
    return bool(command) and hook.get("source") == "sessionFlags" and hook.get("command") == command


def extra_overrides(hooks: Any, skills: Any, config: Any, hook: str | None) -> tuple[str, ...]:
    """한 번 띄워 본 것(훅 · 스킬 · MCP 서버)으로 만드는 덮어쓰기: 우리 훅만 신뢰 · 나머지 훅 · 스킬 · MCP 서버는 끈다."""
    out: list[str] = []
    state = []
    for row in hook_rows(hooks):
        key, digest = row.get("key"), row.get("currentHash")
        if not isinstance(key, str) or not key:
            continue
        if is_gate(row, hook) and isinstance(digest, str) and digest:
            state.append(json.dumps(key, ensure_ascii=False) + "={trusted_hash=" + json.dumps(digest) + "}")
        else:
            state.append(json.dumps(key, ensure_ascii=False) + "={enabled=false}")
    if state:
        out.append("hooks.state={" + ", ".join(state) + "}")
    off = []
    for entry in (skills.get("data") if isinstance(skills, dict) else None) or []:
        for skill in (entry.get("skills") if isinstance(entry, dict) else None) or []:
            path = skill.get("path") if isinstance(skill, dict) else None
            if isinstance(path, str) and path:
                off.append("{path=" + json.dumps(path, ensure_ascii=False) + ", enabled=false}")
    if off:
        out.append("skills.config=[" + ", ".join(off) + "]")
    servers = ((config.get("config") if isinstance(config, dict) else None) or {}).get("mcp_servers") or {}
    for name in sorted(servers) if isinstance(servers, dict) else []:
        out.append("mcp_servers." + json.dumps(str(name), ensure_ascii=False) + ".enabled=false")
    return tuple(out)


def gate_problem(hooks: Any, hook: str | None) -> str:
    """진짜 실행에서 다시 본 훅 목록이 「우리 훅 하나만 · 신뢰됨」인가. 문제가 없으면 빈 글."""
    rows = hook_rows(hooks)
    ours = [row for row in rows if is_gate(row, hook)]
    others = [row for row in rows if not is_gate(row, hook) and row.get("enabled") is not False]
    if hook:
        if len(ours) != 1:
            return f"문(PreToolUse 훅)을 걸지 못했다 — 훅 목록에 우리 훅이 {len(ours)}개다"
        if ours[0].get("enabled") is False or ours[0].get("trustStatus") not in ("trusted", "managed"):
            return f"문(PreToolUse 훅)이 신뢰되지 않았다(상태 {ours[0].get('trustStatus')}) — Codex CLI 판이 달라졌는지 본다"
    if others:
        names = ", ".join(sorted({str(row.get("sourcePath") or row.get("source")) for row in others})[:3])
        return f"끄지 못한 다른 훅이 있다: {names}"
    return ""


def limit_data(response: Any) -> dict | None:
    """account/rateLimits/read 의 답 → limit 이벤트의 data. 창이 없으면 None."""
    snapshot = response.get("rateLimits") if isinstance(response, dict) else None
    if not isinstance(snapshot, dict):
        return None
    windows: dict[str, dict[str, Any]] = {}
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if not isinstance(window, dict) or type(window.get("usedPercent")) not in (int, float):
            continue
        minutes = window.get("windowDurationMins")
        name = WINDOWS.get(minutes, f"{minutes}m" if minutes else key)
        windows[name] = {"pct": float(window["usedPercent"]), "resets_at": window.get("resetsAt"), "status": None}
    if not windows:
        return None
    reached = bool(snapshot.get("rateLimitReachedType")) or response.get("ordinaryUsageAllowed") is False
    fullest = max(windows, key=lambda name: windows[name]["pct"])
    main = fullest if reached or "seven_day" not in windows else "seven_day"
    status = "rejected" if reached else "allowed"
    windows[main]["status"] = status
    return {"status": status, "window": main, "resets_at": windows[main]["resets_at"], "pct": windows[main]["pct"],
            "windows": windows}


def account_data(response: Any) -> dict[str, str | None]:
    """구독 이름 · 계정 종류만 남긴다. 이메일 · 계정 번호는 싣지 않는다."""
    account = response.get("account") if isinstance(response, dict) else None
    account = account if isinstance(account, dict) else {}
    return {key: value if isinstance(value, str) else None
            for key, value in (("type", account.get("type")), ("plan", account.get("planType")))}


def usage_data(first: dict | None, last: dict | None) -> dict[str, int]:
    """이 턴에 쓴 토큰. 알림의 total 은 스레드 전체 누적이라, 턴 전의 누적(첫 알림의 total − last)을 뺀다."""
    out = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    if not isinstance(first, dict) or not isinstance(last, dict):
        return out

    def number(usage: Any, part: str, key: str) -> int:
        value = ((usage.get(part) if isinstance(usage, dict) else None) or {}).get(key)
        return int(value) if type(value) in (int, float) else 0

    def spent(key: str) -> int:
        before = number(first, "total", key) - number(first, "last", key)
        return max(0, number(last, "total", key) - before)

    cached = spent("cachedInputTokens")
    out.update(input=max(0, spent("inputTokens") - cached), output=spent("outputTokens"), cache_read=cached,
               cache_write=spent("cacheWriteInputTokens"))
    return out


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _relative(path: str, root: str) -> str:
    """화면에 보일 경로: 작업 폴더 안이면 그 아래 경로로."""
    full, base = str(path).replace("\\", "/"), str(root).replace("\\", "/").rstrip("/")
    same = full.casefold().startswith(base.casefold() + "/") if os.name == "nt" else full.startswith(base + "/")
    return full[len(base) + 1:] if base and same else full


def item_events(item: Any, done: bool, root: str, turn: _Turn) -> list[EngineEvent]:
    """item/started · item/completed 의 item 하나 → 엔진 이벤트들."""
    out: list[EngineEvent] = []
    if not isinstance(item, dict):
        return out
    kind, item_id = item.get("type"), str(item.get("id") or "")
    status = item.get("status")

    def tool(tool_id: str, name: str, target: str, path: str | None = None, ok: bool | None = None, excerpt: str = "") -> None:
        if tool_id not in turn.open_tools:
            turn.open_tools.add(tool_id)
            data = {"id": tool_id, "name": name, "target": _short(target, 120), "sub": False}
            if path:
                data["path"] = path              # 파일 도구는 자르지 않은 경로도 싣는다(결과물 · 인계 고침 찾기)
            out.append(EngineEvent("tool", data))
        if done:
            turn.open_tools.discard(tool_id)
            out.append(EngineEvent("tool_result", {"id": tool_id, "ok": bool(ok), "excerpt": _short(excerpt, 300)}))

    if kind == "agentMessage":
        text = item.get("text")
        if done and isinstance(text, str) and text.strip():
            turn.texts.append(text)
            if item.get("phase") == "final_answer":
                turn.final = text
            out.append(EngineEvent("text", {"text": text, "sub": False}))
    elif kind == "commandExecution":
        actions = [a.get("command") for a in item.get("commandActions") or [] if isinstance(a, dict) and a.get("command")]
        target = " ; ".join(str(a) for a in actions) or str(item.get("command") or "")
        tool(item_id, "Shell", target, ok=status == "completed" and item.get("exitCode") in (0, None),
             excerpt=str(item.get("aggregatedOutput") or ""))
    elif kind == "fileChange":
        for index, change in enumerate(item.get("changes") or []):
            if not isinstance(change, dict) or not change.get("path"):
                continue
            how = change.get("kind") if isinstance(change.get("kind"), dict) else {}
            path = str(change["path"])
            tool(f"{item_id}#{index}", FILE_TOOLS.get(str(how.get("type")), "Edit"), _relative(path, root), path=path,
                 ok=status == "completed")
            moved = how.get("movePath") or how.get("move_path")
            if moved:
                tool(f"{item_id}#{index}m", "Write", _relative(str(moved), root), path=str(moved), ok=status == "completed")
    elif kind == "webSearch":
        tool(item_id, "WebSearch", str(item.get("query") or ""), ok=True)
    elif kind == "dynamicToolCall":                 # 우리가 붙인 도구(11-13) — 이름은 접두어 없이 온다
        tool(item_id, str(item.get("tool") or "tool"), _short(item.get("arguments") or "", 100),
             ok=status == "completed" and item.get("success") is not False)
    elif kind == "mcpToolCall":
        tool(item_id, f"mcp:{item.get('server')}.{item.get('tool')}", _short(item.get("arguments") or "", 100),
             ok=status == "completed")
    elif kind == "contextCompaction":
        if done and not turn.compacted:
            turn.compacted = True
            out.append(EngineEvent("compact", {"trigger": "auto", "pre_tokens": None, "post_tokens": None}))
    return out


class CodexSession(EngineSession):
    _tools: dict[str, ToolDef] = {}         # 붙인 우리 도구(세션이 보는 이름 → 알맹이) — open() 이 채운다

    def __init__(self, spec: SessionSpec, options: CodexEngineOptions) -> None:
        self.spec = spec
        self.options = options
        self.session_id: str | None = spec.resume
        self.model: str | None = None
        self._sdk: Any = None
        self._codex: Any = None
        self._thread: Any = None
        self._handle: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()
        self._resumed = bool(spec.resume)
        self._hook: str | None = None
        self._limit: dict | None = None            # 열 때 읽은 한도 — 첫 턴 머리에 낸다
        self._usage: dict | None = None            # 마지막 사용량 알림(문맥 크기)

    # ------------------------------------------------------------------ 열기
    async def open(self) -> None:
        import openai_codex as sdk

        self._sdk = sdk
        self._loop = asyncio.get_running_loop()
        spec = self.spec
        if spec.effort and spec.effort not in CodexEngine.efforts:
            raise ValueError(f"모르는 세기: {spec.effort}")
        codex_bin = self.options.codex_bin or find_codex()
        if not codex_bin:
            raise RuntimeError("Codex CLI 실행 파일을 찾지 못했다 — gaf.json 의 codex.bin 에 경로를 적는다")
        self._hook = self._hook_command()
        write_dirs = spec.write_dirs if self.options.sandbox == "workspace-write" else ()
        cwd = write_dirs[0] if write_dirs else spec.cwd
        base = base_overrides(self.options, self._hook) + sandbox_overrides(write_dirs)
        env = dict(spec.env)
        config = dict(codex_bin=str(codex_bin), cwd=str(cwd), client_name="gaf", client_title="게임 AI 프레임워크")
        # 1) 한 번 띄워 훅 · 스킬 · MCP 서버를 본다(모델을 부르지 않는다 · 약 0.3초) — 우리 훅의 신뢰 값은 Codex 가 셈한 것을 받아야 한다
        probe = sdk.AsyncCodex(sdk.CodexConfig(config_overrides=base, env=env or None, **config))
        try:
            await asyncio.wait_for(probe.__aenter__(), self.options.connect_timeout)
            await self._check_ready(probe)
            cwds = {"cwds": [str(cwd)]}
            extra = extra_overrides(await self._request(probe, "hooks/list", cwds),
                                    await self._request(probe, "skills/list", {**cwds, "forceReload": True}),
                                    await self._request(probe, "config/read", {"cwd": str(cwd), "includeLayers": False}),
                                    self._hook)
        finally:
            await self._quiet_close(probe)
        overrides = base + extra
        if sum(len(item) for item in overrides) > ARGS_LIMIT:
            raise RuntimeError("꺼야 할 스킬 · 훅 · MCP 서버가 너무 많아 실행 인자에 다 싣지 못했다 — Codex 쪽 스킬을 줄인다")
        # 2) 진짜 실행
        self._codex = sdk.AsyncCodex(sdk.CodexConfig(config_overrides=overrides, env=env or None, **config))
        try:
            await asyncio.wait_for(self._codex.__aenter__(), self.options.connect_timeout)
            self._codex._client._sync._approval_handler = self._on_server_request     # SDK 안쪽 값 — 공개된 길이 없다(0.167.2)
            problem = gate_problem(await self._request(self._codex, "hooks/list", {"cwds": [str(cwd)]}), self._hook)
            if problem:
                raise RuntimeError(problem)
            params: dict[str, Any] = {"cwd": str(cwd), "sandbox": self.options.sandbox, "approvalPolicy": "never"}
            instructions = instructions_text(spec.instructions)
            if instructions:
                params["developerInstructions"] = instructions
            if spec.model:
                params["model"] = spec.model
            # 우리 명령을 「클라이언트가 주는 도구」로 붙인다(11-13 사용자 「메시지는 MCP로」 · 진짜 CLI 로 본 것 —
            # probes/codex_tools_probe.py: thread/start · thread/resume 가 dynamicTools 를 받고, 모델이 부르면 item/tool/call 이
            # 온다). 따로 도는 서버가 없고 명령마다 프로세스를 띄우지 않는다. 이어 연 스레드에도 다시 준다(옛 스레드는 도구가 없다).
            self._tools = {tool_label(tool.name): tool for tool in session_tools(spec.tools)}
            if self._tools:
                params["dynamicTools"] = [{"type": "function", "name": name, "description": tool.description,
                                           "inputSchema": tool.schema} for name, tool in self._tools.items()]
            if spec.resume:
                started = await self._request(self._codex, "thread/resume", {**params, "threadId": spec.resume})
            else:
                started = await self._request(self._codex, "thread/start", params)
            sources = [str(path) for path in started.get("instructionSources") or []]
            if sources:
                raise RuntimeError("엔진 전용 지시 파일이 실렸다(" + ", ".join(sources[:3]) + ") — 이 프레임워크는 team/공통.md · "
                                   "역할.md 만 싣는다. 그 파일의 이름을 바꾸거나 옮긴다")
            self.session_id = str((started.get("thread") or {}).get("id") or spec.resume or "") or None
            if not self.session_id:
                raise RuntimeError("Codex 가 스레드 번호를 주지 않았다")
            self.model = started.get("model") or spec.model
            self._thread = sdk.AsyncThread(self._codex, self.session_id)
            if spec.session_name and not spec.resume:
                with contextlib.suppress(Exception):      # 이름은 `codex resume` 목록에 보이는 것뿐이다 — 못 붙여도 된다
                    await self._request(self._codex, "thread/name/set", {"threadId": self.session_id, "name": spec.session_name})
            self._limit = await self._read_limit()
        except BaseException:
            await self._drop()
            raise

    def _hook_command(self) -> str | None:
        """문을 걸 훅 명령. 문(gate)이 없으면 None. 문이 있는데 걸 재료가 없으면 열지 않는다(열린 채로 돌지 않게)."""
        if self.spec.gate is None:
            return None
        env = self.spec.env
        python, script = env.get("GAF_PYTHON") or "", env.get("GAF_HOOK") or ""
        if not python or not script or not env.get("GAF_PORT") or not env.get("GAF_SESSION"):
            raise RuntimeError("문(PreToolUse 훅)을 걸 수 없다 — 지휘자를 `python -m gaf serve` 로 띄워야 한다"
                               "(GAF_PYTHON · GAF_HOOK · GAF_PORT · GAF_SESSION 이 있어야 한다)")
        return hook_command(python, script)

    async def _request(self, codex: Any, method: str, params: dict | None) -> Any:
        """공개 클래스에 없는 요청을 SDK 의 요청 길로 보낸다(답은 JSON 그대로)."""
        return await asyncio.wait_for(codex._client.request(method, params, response_model=_Raw), self.options.connect_timeout)

    async def _check_ready(self, codex: Any) -> None:
        account = await self._request(codex, "account/read", {"refreshToken": False})
        if isinstance(account, dict) and account.get("account") is None and account.get("requiresOpenaiAuth"):
            raise RuntimeError("Codex 로그인이 없다 — 터미널에서 `codex login` 을 한 번 한다")
        if os.name == "nt" and self.options.sandbox != "danger-full-access":
            ready = await self._request(codex, "windowsSandbox/readiness", None)
            status = ready.get("status") if isinstance(ready, dict) else None
            if status not in (None, "ready"):
                raise RuntimeError(f"Codex 의 Windows 샌드박스가 준비되지 않았다({status}) — 터미널에서 `codex` 를 한 번 띄워 설정한다")

    async def _read_limit(self) -> dict | None:
        """남은 한도를 읽는다(모델을 부르지 않는다). 못 읽으면 None."""
        if self._codex is None:
            return None
        try:
            return limit_data(await asyncio.wait_for(
                self._codex._client.request("account/rateLimits/read", None, response_model=_Raw), 20))
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ Codex 가 물어 오는 것(SDK 의 읽기 스레드에서 불린다)
    def _on_server_request(self, method: str, params: dict | None) -> dict:
        if method == "item/tool/requestUserInput":
            return self._ask(params or {})
        if method == "item/tool/call":
            return self._tool_call(params or {})
        if method in ("applyPatchApproval", "execCommandApproval"):       # 옛 규약의 승인
            return {"decision": "denied"}
        if method.endswith("/requestApproval"):
            # 승인은 never 라 오지 않는다. 와도 받아 주지 않는다 — 문은 훅이고, 샌드박스 밖으로 나가는 것은 허락하지 않는다.
            return {"decision": "decline"}
        return {}

    def _tool_call(self, params: dict) -> dict:
        """모델이 우리 도구를 불렀다 → handler 를 지휘자의 루프에서 돌리고 그 글을 돌려준다(이 스레드는 그동안 기다린다).

        모양은 11-13 진짜 CLI 로 본 것이다: 물음 {tool, arguments, namespace, callId, …} → 답 {contentItems, success}.
        """
        name = str(params.get("tool") or "")
        tool, loop = (None if params.get("namespace") else self._tools.get(name)), self._loop
        if tool is None or loop is None:
            text = f"오류: 없는 도구다: {name}"
        else:
            args = params.get("arguments")
            try:
                text = asyncio.run_coroutine_threadsafe(tool.handler(args if isinstance(args, dict) else {}), loop).result(TOOL_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 — 도구 오류는 모델에게 글로 돌려준다
                text = f"오류: {type(exc).__name__}: {exc}"
        return {"contentItems": [{"type": "inputText", "text": text}], "success": not text.startswith("오류:")}

    def _ask(self, params: dict) -> dict:
        """Codex 의 「사용자에게 묻기」 → 지휘자의 「정할 것」. 답이 올 때까지 이 스레드가 기다린다."""
        asked = [q for q in params.get("questions") or [] if isinstance(q, dict)]
        questions = [{"question": str(q.get("question") or ""), "header": str(q.get("header") or ""),
                      "options": [{"label": str(o.get("label") or ""), "description": str(o.get("description") or "")}
                                  for o in q.get("options") or [] if isinstance(o, dict)],
                      "multiSelect": False} for q in asked]
        answers: dict[str, str] | None = None
        asker, loop = self.spec.asker, self._loop
        if asker is not None and loop is not None and questions:
            try:
                answers = asyncio.run_coroutine_threadsafe(asker(questions), loop).result()
            except Exception:  # noqa: BLE001
                answers = None
        out = {}
        for q in asked:
            answer = (answers or {}).get(str(q.get("question") or ""))
            out[str(q.get("id") or "")] = {"answers": [answer if isinstance(answer, str) and answer else CANNOT_ASK]}
        return {"answers": out}

    # ------------------------------------------------------------------ 한 턴
    def _input(self, text: str, images: Sequence[Path]) -> Any:
        if not images:
            return text
        sdk = self._sdk
        return [sdk.TextInput(text), *(sdk.LocalImageInput(str(path)) for path in images)]

    def _convert(self, method: str, payload: Any, turn: _Turn) -> list[EngineEvent]:
        """알림 하나 → 엔진 이벤트들(턴에 모을 것은 turn 에 적는다)."""
        out: list[EngineEvent] = []
        if not isinstance(payload, dict):
            return out
        if method in ("item/started", "item/completed"):
            return item_events(payload.get("item"), method == "item/completed", str(self.spec.cwd), turn)
        if method == "thread/tokenUsage/updated":
            usage = payload.get("tokenUsage")
            if isinstance(usage, dict):
                turn.usage_first = turn.usage_first or usage
                turn.usage_last = self._usage = usage
                turn.calls += 1
        elif method == "hook/completed":
            run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
            if run.get("eventName") == "preToolUse" and run.get("source") == "sessionFlags":
                entries = "; ".join(str(e.get("text") or "") for e in run.get("entries") or [] if isinstance(e, dict))
                if run.get("status") == "blocked":
                    if GATE_DOWN in entries:
                        # 훅 명령이 스스로 낸 거부 = 훅 프로그램이 못 떴거나 죽었다. 그 도구는 막혔지만 문이 고장 난 채로 일하게 두지 않는다
                        turn.gate_broken = turn.gate_broken or "훅 프로그램이 돌지 않았다(못 떴거나 죽었다 — .gaf/bin/gaf_hook.py 와 파이썬을 본다)"
                    else:
                        turn.denials += 1
                elif run.get("status") not in ("completed", "running"):
                    # 훅 명령 자체가 죽으면 Codex 는 도구를 그냥 돌린다 — 문 없이 돌게 두지 않는다
                    turn.gate_broken = turn.gate_broken or f"훅 상태 {run.get('status')}" + (f" · {entries}" if entries else "")
        elif method == "error":
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            if not payload.get("willRetry"):
                turn.error = str(error.get("message") or turn.error)
        elif method == "thread/compacted":
            if not turn.compacted:
                turn.compacted = True
                out.append(EngineEvent("compact", {"trigger": "auto", "pre_tokens": None, "post_tokens": None}))
        elif method == "turn/completed":
            done = payload.get("turn") if isinstance(payload.get("turn"), dict) else {}
            turn.status = str(done.get("status") or "")
            turn.duration_ms = done.get("durationMs")
            error = done.get("error") if isinstance(done.get("error"), dict) else {}
            turn.error = str(error.get("message") or turn.error)
        return out

    @staticmethod
    def _payload(payload: Any) -> Any:
        if hasattr(payload, "model_dump"):
            return payload.model_dump(by_alias=True, exclude_none=True, mode="json")
        return getattr(payload, "params", None)

    async def turn(self, text: str, images: Sequence[Path] = ()) -> AsyncIterator[EngineEvent]:
        if self._codex is None or self._thread is None:
            raise RuntimeError("세션이 열려 있지 않다")
        wanted = ("item/started", "item/completed", "thread/tokenUsage/updated", "hook/completed", "error",
                  "thread/compacted", "turn/completed")
        async with self._lock:
            started = time.monotonic()
            yield EngineEvent("session", {"session_id": self.session_id, "model": self.model, "engine": "codex",
                                          "permission": self.options.sandbox, "resumed": self._resumed})
            if self._limit is not None:
                limit, self._limit = self._limit, None
                yield EngineEvent("limit", limit)
            turn = _Turn()
            try:
                options = {"effort": self.spec.effort} if self.spec.effort else {}
                handle = await self._thread.turn(self._input(text, images), **options)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001
                yield EngineEvent("error", {"message": self._error_text(exc)})
                return
            self._handle = handle
            stream = handle.stream()
            try:
                async for note in stream:
                    if note.method not in wanted:
                        continue                              # 글자 단위 알림 등 — 바꾸지 않고 넘긴다
                    for event in self._convert(note.method, self._payload(note.payload), turn):
                        yield event
                    if turn.gate_broken and not turn.interrupt_sent:
                        turn.interrupt_sent = True
                        with contextlib.suppress(Exception):
                            await asyncio.wait_for(handle.interrupt(), 10)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001
                yield EngineEvent("error", {"message": self._error_text(exc)})
                return
            finally:
                self._handle = None
                with contextlib.suppress(Exception):
                    await stream.aclose()
            limit = await self._read_limit()
            if limit is not None:
                yield EngineEvent("limit", limit)
            if turn.gate_broken:
                yield EngineEvent("error", {"message": "문(PreToolUse 훅)이 돌지 않아 턴을 멈췄다 — " + turn.gate_broken})
                return
            if turn.status is None:
                yield EngineEvent("error", {"message": "턴이 끝났다는 알림이 오지 않았다"})
                return
            answer = turn.final if turn.final is not None else (turn.texts[-1] if turn.texts else "")
            if turn.status != "completed" and turn.error:
                answer = (answer + "\n" if answer else "") + f"(엔진 오류: {turn.error})"
            yield EngineEvent("result", {
                "ok": turn.status == "completed",
                "text": answer,
                "turns": max(1, turn.calls),
                "cost_usd": 0.0,                              # 구독이라 값을 주지 않는다 — 지어내지 않는다
                "usage": usage_data(turn.usage_first, turn.usage_last),
                "duration_ms": turn.duration_ms,
                "stop": turn.status,
                "denials": turn.denials,
                "wall_ms": int((time.monotonic() - started) * 1000),
            })

    async def send(self, text: str, images: Sequence[Path] = ()) -> None:
        """도는 턴에 글을 더 넣는다 — 그 턴의 핸들에 `turn/steer` 를 부른다(11-12 실측: 바로 돌아오고, 모델은 다음 차례에 그 글을 읽으며,
        `turn/completed` 는 하나다). 턴이 없거나 막 끝났으면 NotRunning — Codex 는 그때 「no active turn to steer」(-32600)로 거절한다.
        """
        handle = self._handle
        if handle is None:
            raise NotRunning("도는 턴이 없다")
        try:
            await asyncio.wait_for(handle.steer(self._input(text, images)), STEER_TIMEOUT)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            raise
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "InvalidRequestError" or "no active turn" in str(exc).lower():
                raise NotRunning(f"도는 턴에 넣지 못했다: {str(exc)[:200]}") from None
            raise

    def _error_text(self, problem: Any) -> str:
        tail = ""
        with contextlib.suppress(Exception):
            lines = [line for line in self._codex._client._sync._stderr_tail(3).splitlines() if line.strip()]
            tail = " | ".join(line[:300] for line in lines[-3:])
        return f"{type(problem).__name__}: {str(problem)[:600]}" + (f" — {tail}" if tail else "")

    # ------------------------------------------------------------------ 그 밖
    async def interrupt(self) -> None:
        handle = self._handle
        if handle is not None:
            await handle.interrupt()

    async def context(self) -> dict[str, Any] | None:
        usage = self._usage
        if not isinstance(usage, dict):
            return None
        tokens = (usage.get("last") or {}).get("totalTokens")
        limit = usage.get("modelContextWindow")
        if type(tokens) not in (int, float):
            return None
        percent = round(tokens / limit * 100, 1) if type(limit) in (int, float) and limit > 0 else None
        return {"tokens": tokens, "max": limit, "pct": percent}

    async def compact(self, keep: str = "") -> dict[str, Any] | None:
        async with self._lock:
            if self._codex is None or self._thread is None:
                raise RuntimeError("세션이 열려 있지 않다")
            # SDK 0.167.2 의 AsyncThread.compact → thread/compact/start 는 빈 답만 주고 턴 번호를 주지 않는다.
            # ContextCompactedNotification 에는 turnId 가 있어, notification_registry · _message_router 는 이를
            # 턴별 큐로만 보낸다(next_notification 으로는 못 받는다). 구독하지 않은 턴의 알림은 바로 버린다.
            # 시작 전에 그 턴을 구독할 길이 없어 완료를 믿고 기다릴 수 없다 — 요청하지 않고 새 세션으로 잇게 한다.
            # keep 도 이 요청이 받는 칸이 아니므로 쓰지 않는다.
            return None

    async def account(self) -> dict[str, Any] | None:
        """계정 종류(구독 · API)와 요금제 이름만 — 값이 비밀인 것은 돌려주지 않는다."""
        if self._codex is None:
            return None
        try:
            info = await self._request(self._codex, "account/read", {"refreshToken": False})
        except Exception:  # noqa: BLE001
            return None
        return account_data(info)

    @staticmethod
    async def _quiet_close(codex: Any) -> None:
        try:
            await asyncio.wait_for(codex.close(), 30)
        except BaseException:  # noqa: BLE001
            pass

    async def _drop(self) -> None:
        codex, self._codex, self._thread, self._handle = self._codex, None, None, None
        if codex is not None:
            await self._quiet_close(codex)

    async def close(self) -> None:
        await self._drop()


class CodexEngine(Engine):
    efforts = EFFORTS + ("ultra",)
    compacts = False
    name = "codex"
    narrows = True
    shell = "powershell" if os.name == "nt" else "bash"      # Windows 의 Codex 는 명령을 PowerShell 로 친다(11-12 실측)
    commands = "tools"          # 우리 명령을 「클라이언트가 주는 도구」로 붙인다(CodexSession.open — 11-13)
    tool_prefix = ""            # Codex 의 세션은 붙인 도구를 접두어 없이 본다(SendMessage · team …)

    def __init__(self, options: CodexEngineOptions | None = None) -> None:
        self.options = options or CodexEngineOptions()

    def session(self, spec: SessionSpec) -> CodexSession:
        return CodexSession(spec, self.options)

    async def read_account(self, spec: SessionSpec) -> dict[str, Any] | None:
        """모델 · 세션 없이 app-server 에서 한도와 구독만 읽고 닫는다."""
        try:
            import openai_codex as sdk

            codex_bin = self.options.codex_bin or find_codex()
            if not codex_bin:
                return None
            client = sdk.AsyncCodex(sdk.CodexConfig(
                codex_bin=str(codex_bin), cwd=str(spec.cwd), env=dict(spec.env) or None,
                client_name="gaf", client_title="게임 AI 프레임워크",
                config_overrides=base_overrides(self.options, None)))

            async def read():
                await client.__aenter__()
                limit = await client._client.request("account/rateLimits/read", None, response_model=_Raw)
                info = await client._client.request("account/read", {"refreshToken": False}, response_model=_Raw)
                return {"limit": limit_data(limit), **account_data(info)}

            loop = asyncio.get_running_loop()
            deadline = loop.time() + ACCOUNT_TIMEOUT
            result = None
            try:
                result = await asyncio.wait_for(read(), ACCOUNT_TIMEOUT)
            finally:
                # 읽기가 시간을 다 써도 close 를 한 번 시작하고, 닫는 데는 따로 시간을 준다 — 남은 마감(거의 0)만 주면 닫기가 곧바로
                # 끊겨 app-server 가 남을 수 있다(30분마다 도는 일이라 쌓인다 · 11-17 총괄 검토).
                try:
                    await asyncio.wait_for(client.close(), max(ACCOUNT_CLOSE_TIMEOUT, deadline - loop.time()))
                except Exception:  # noqa: BLE001 — 닫기 실패로 끄는 쪽의 취소를 삼키지 않는다.
                    result = None
            return result
        except Exception:  # noqa: BLE001 — 한도를 못 읽어도 지휘자는 켜진다.
            return None

    def available(self) -> tuple[bool, str]:
        try:
            import openai_codex  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            return False, f"openai_codex(Codex SDK)를 불러오지 못했다: {exc}"
        if not (self.options.codex_bin or find_codex()):
            return False, "Codex CLI 실행 파일을 찾지 못했다 — Codex CLI 를 깔거나 gaf.json 의 codex.bin 에 경로를 적는다"
        if self.options.codex_bin and not Path(self.options.codex_bin).is_file():
            return False, f"codex.bin 의 실행 파일이 없다: {self.options.codex_bin}"
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        for name in ("AGENTS.override.md", "AGENTS.md"):
            if (home / name).is_file():
                # project_doc_max_bytes=0 은 프로젝트의 AGENTS.md 만 뺀다 — 전역 것은 그래도 실린다(11-12 실측)
                return False, (f"Codex 전역 지시 파일이 있다({(home / name).as_posix()}) — 이 프레임워크는 엔진 전용 지시 파일을 "
                               "싣지 않는다. 그 파일의 이름을 바꾸면 쓸 수 있다")
        return True, ""
