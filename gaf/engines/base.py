"""엔진 규약 — Claude Agent SDK 와 Codex SDK 를 한 모양으로 쓰는 층(2026-11-12 사용자 「하나의 SDK로 … 인풋 함수를 하나로」).

지휘자는 이 파일의 형식만 안다. Claude(Agent SDK) · Codex · 시험용 가짜 엔진이 같은 모양으로 붙는다.
엔진이 내는 이벤트는 모두 EngineEvent 하나로 바뀌어 나오므로 뷰어는 엔진을 몰라도 된다.

세션에 글을 넣는 길은 둘뿐이고 엔진마다 같다:
  turn(글)   쉬는 세션에 글을 넣어 일 한 덩이를 시작한다 → 그 덩이의 이벤트(끝은 result 또는 error)
  send(글)   도는 덩이에 글을 더 넣는다 → 세션은 다음 도구 차례에 그 글을 읽는다(덩이는 이어지고 result 는 하나다)
안에서는 다르다 — Claude 는 같은 입력 흐름에 글을 하나 더 쓰고(CLI 가 다음 도구 차례에 붙인다), Codex 는 `turn/steer` 를 부른다(11-12 실측).
  compact(남길 것)   쉬는 세션의 대화를 압축한다 → 압축 전후 토큰 수(못 하는 엔진 · 압축할 것이 없으면 None)

이벤트 종류(kind)와 data:
  session      {"session_id", "model", "engine", "permission", "resumed"}
  text         {"text", "sub"}                    sub=True 면 서브에이전트의 글
  tool         {"id", "name", "target", "sub"}    target = 파일 경로 · 명령 앞부분 같은 짧은 설명
                                                  파일 도구(Write · Edit …)면 "path"(자르지 않은 경로)도 싣는다
  tool_result  {"id", "ok", "excerpt"}
  limit        {"status", "window", "resets_at", "pct", "windows"}   status: allowed · allowed_warning · rejected
                                                  windows = {창 이름: {"pct", "resets_at", "status"}}
  compact      {"trigger", "pre_tokens", "post_tokens"}
  peer_hold    {"from_name", "cause"}             밖의 세션이 보낸 글이 보류됨
  note         {"what", ...}                      그 밖에 적어 둘 것(예: 밖에서 온 턴)
  result       {"ok", "text", "turns", "cost_usd", "usage", "duration_ms", "stop", "denials"}   한 턴의 끝
  error        {"message"}                        턴이 끝까지 못 감(이 뒤에 result 는 오지 않는다)
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Sequence

# 도구 하나를 써도 되는지 지휘자에게 묻는 문: (도구 이름, 도구 입력) → (허용?, 거부 이유)
Gate = Callable[[str, dict[str, Any]], Awaitable[tuple[bool, str]]]
# 세션이 사용자에게 물을 때: 질문 목록 → {질문 글: 답} · 물을 수 없으면 None(세션에는 「묻지 못한다」가 돌아간다)
Asker = Callable[[list[dict[str, Any]]], Awaitable["dict[str, str] | None"]]

class NotRunning(Exception):
    """도는 턴이 없어(또는 이 엔진이 못 해서) 글을 넣지 못했다 — 부른 쪽은 그 글을 다음 턴으로 돌린다."""


EVENT_KINDS = ("session", "text", "tool", "tool_result", "limit", "compact", "peer_hold", "note", "result", "error")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# 우리 명령을 세션에 도구로 붙일 때(11-13 사용자 「메시지는 MCP로」 — 따로 도는 서버가 없는 프로세스 안 도구):
TOOL_SERVER = "gaf"                             # 도구 묶음의 이름 — 세션이 보는 전체 이름은 mcp__gaf__<이름>
TOOL_LABELS = {"send": "SendMessage"}           # 세션이 보는 도구 이름(글 보내기는 SendMessage — 11-12 사용자 「겉으로는 똑같이 SendMessage」)
TOOL_HIDDEN = ("tell",)                         # 도구로는 붙이지 않는 것(send 의 옛 이름 — 사람의 명령으로만 남는다)


def tool_label(name: str) -> str:
    """명령 이름 → 세션이 보는 도구 이름."""
    return TOOL_LABELS.get(name, name)


def tool_full_name(name: str) -> str:
    """명령 이름 → 세션이 부르는 도구의 전체 이름(mcp__gaf__…)."""
    return f"mcp__{TOOL_SERVER}__{tool_label(name)}"


def session_tools(tools: Sequence["ToolDef"]) -> list["ToolDef"]:
    """세션에 도구로 붙이는 것만(TOOL_HIDDEN 을 뺀다)."""
    return [tool for tool in tools if tool.name not in TOOL_HIDDEN]


@dataclass
class ToolDef:
    """총괄 명령 하나의 알맹이(이름 · 설명 · 입력 모양 · handler). handler 는 글을 돌려준다.

    두 길로 불린다: 셸 명령 `gaf <이름> …` 이 지휘자의 command() 를 거쳐 부르거나(사람 · 명령을 치는 엔진의 세션),
    엔진이 세션에 프로세스 안 도구로 붙여 모델이 바로 부른다(Engine.commands == "tools" — 11-13).
    """

    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[str]]


@dataclass
class SessionSpec:
    """세션 하나를 여는 데 필요한 값. 엔진이 모르는 값은 무시한다."""

    role: str                                   # 역할 이름
    cwd: Path                                   # 작업 폴더(프로젝트 뿌리)
    session_name: str = ""                      # 세션 이름(다른 세션의 목록에 보이는 이름)
    instructions: str = ""                      # 지시문(머리말 + team/공통.md + 역할 글) — 엔진의 기본 지시문 뒤에 덧붙인다.
                                                # 엔진 전용 파일(CLAUDE.md · AGENTS.md)은 싣지 않는다
    model: str | None = None
    effort: str | None = None                   # 그 엔진의 efforts 가운데 하나
    resume: str | None = None                   # 이어 갈 세션 id(None 이면 새 세션)
    permission: str = "default"
    gate: Gate | None = None
    asker: Asker | None = None
    # 두 엔진 모두 우리 명령을 도구로 붙인다. 남의 MCP 서버 · 스킬 · 플러그인은 싣지 않는다.
    tools: list[ToolDef] = field(default_factory=list)
    skills: list[str] | None = None
    plugins: list[str] = field(default_factory=list)
    mcp_config: str | None = None
    add_dirs: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)       # 자식에 덧씌울 값(GAF_ROLE · GAF_SESSION · GAF_PORT · GAF_PYTHON · GAF_HOOK · PATH)
    accept_peers: bool = False                  # 밖의 세션이 보낸 글을 받는다
    deny_tools: tuple[str, ...] = ()            # 세션의 도구 목록에서 뺄 도구 이름(예약 · 클라우드 실행 — scope.TOOLS_DENIED · 문도 같은 이름을 막는다)
    extra: dict[str, Any] = field(default_factory=dict)     # 엔진별 값
    write_dirs: tuple[Path, ...] = ()            # OS 가 쓰기를 허락할 폴더들(절대 경로)이다. 첫 폴더에서 셸을 시작한다. 비면 엔진 기본이다(작업 폴더 전체).
    instructions_file: Path | None = None      # Claude 는 본문 대신 이 파일을 CLI 에 넘긴다.


@dataclass
class EngineEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"모르는 이벤트 종류: {self.kind}")


class EngineSession(abc.ABC):
    """세션 하나. open → turn(여러 번 · 도는 동안 send) → close."""

    session_id: str | None = None

    @abc.abstractmethod
    async def open(self) -> None:
        """세션 프로세스에 붙는다. 실패하면 예외."""

    @abc.abstractmethod
    def turn(self, text: str, images: Sequence[Path] = ()) -> AsyncIterator[EngineEvent]:
        """글 하나를 보내고 그 턴의 이벤트를 차례로 낸다. 마지막은 result 또는 error 다."""

    async def send(self, text: str, images: Sequence[Path] = ()) -> None:
        """도는 턴에 글을 더 넣는다 — 세션은 다음 도구 차례에 읽는다. 이벤트와 결과는 도는 turn() 이 낸다.

        돌아오면 글이 세션에 닿은 것이다(읽었다는 뜻은 아니다). 도는 턴이 없거나 못 넣는 엔진이면 NotRunning.
        """
        raise NotRunning("이 엔진은 도는 턴에 글을 넣지 못한다")

    @abc.abstractmethod
    async def interrupt(self) -> None:
        """도는 턴을 멈춘다."""

    async def context(self) -> dict[str, Any] | None:
        """지금 문맥 크기 {"tokens", "max", "pct"} · 모르면 None."""
        return None

    async def account(self) -> dict[str, Any] | None:
        """열린 세션의 구독 · API 종류만 읽는다. 모르면 None."""
        return None

    async def compact(self, keep: str = "") -> dict[str, Any] | None:
        """쉬는 세션(도는 턴이 없다)의 대화를 지금 압축한다.

        압축했으면 {"pre_tokens": …, "post_tokens": …}(모르는 값은 None), 이 엔진이 못 하거나 압축할 것이 없으면 None,
        하다가 실패하면 예외. keep = 요약에 꼭 남길 것(엔진이 받을 수 있을 때만 쓴다).
        """
        return None

    @abc.abstractmethod
    async def close(self) -> None:
        """세션 프로세스를 닫는다(대화 기록은 남는다 — resume 으로 잇는다)."""


class Engine(abc.ABC):
    name: str = ""
    efforts: tuple[str, ...] = EFFORTS
    compacts: bool = False                      # 압축을 요청할 수 있는 엔진인가(결과가 없을 때의 까닭을 가른다).
    narrows: bool = False                       # write_dirs 로 OS 샌드박스를 좁히는 엔진인가.
    commands: str = "shell"                     # 세션이 지휘자에 말을 거는 길: "shell" = 셸 명령(`gaf …` · `SendMessage …`) · "tools" = 붙여 준 도구
    tool_prefix: str = f"mcp__{TOOL_SERVER}__"   # 세션이 보는 우리 도구 이름의 앞머리(Claude 는 mcp__gaf__ · Codex 는 없다)

    def tool_name(self, name: str) -> str:
        """명령 이름 → 이 엔진의 세션이 부르는 도구 이름."""
        return self.tool_prefix + tool_label(name)
    shell: str = "bash"                         # 이 엔진의 세션이 명령을 치는 셸("bash" · "powershell") — 총괄의 「명령 쓰는 법」을 그 꼴로 붙인다

    @abc.abstractmethod
    def session(self, spec: SessionSpec) -> EngineSession:
        """spec 으로 세션을 하나 만든다(아직 열지 않는다)."""

    def available(self) -> tuple[bool, str]:
        """이 PC에서 쓸 수 있는지 · 못 쓰면 이유."""
        return True, ""

    async def read_account(self, spec: SessionSpec) -> dict[str, Any] | None:
        """세션 없이 계정 한도 · 구독을 읽는다. 지원하지 않으면 None."""
        return None
