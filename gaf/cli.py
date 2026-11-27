"""지휘자와 뷰어를 다루는 명령줄."""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import math
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__, cloudvoice, desktop, doctor, firstrun, hooks, launch, library, outside, projects, remote, remote_ops, tailnet
from .remote_ops import fixed_port as _fixed_port
from .app import build, install_commands, pid_alive, read_server
from .config import ConfigError, change_folders, load_config
from .dictation import Dictation
from .envclean import clean_env
from .paths import Paths, _atomic_json, _atomic_text, _read_json, file_lock, find_root, run_dir
from .roles import RoleError, _role_folders, create_role, has_roles, load_role
from .server import IMAGE_LIMIT, IMAGE_TYPES, Server

# 사람이 보는 표의 말(화면의 상태 알약과 같다). 총괄이 읽는 말은 lead_tools.STATUS_WORDS 다 — 더 길게 풀어 쓴 것.
STATUS_WORDS = {"idle": "쉼", "working": "일하는 중", "waiting": "기다림", "blocked": "막힘", "off": "꺼짐"}
TOOL_TIMEOUT = 90           # 총괄 명령을 기다리는 시간(초) — stop 은 도는 턴이 끝나기를 30초까지 기다린다
SHUTDOWN_WAIT_STEP = 2.0     # 비었는지 다시 청하는 간격(초)
SESSION_ONLY = "team · send(= SendMessage) · assign · tell · result · ask · show · commit · push · custom · stop <역할> · role new|off|on|reset · library list|show|take · check list|add|remove · job run|list|out|stop · research run"
PORT_IN_USE = (48, 98, 10048)   # 「주소를 이미 쓰고 있다」의 오류 번호(macOS · Linux · Windows)
START_LOCK_SECONDS = 30          # 같은 프로젝트의 다른 serve 가 뜨는 것을 기다리는 시간
WIKI_IGNORE = "wiki/.obsidian/workspace*.json"


class InputError(ValueError):
    """사람이나 세션이 고칠 수 있는 입력 오류 — 글을 그대로 보여 준다."""


class Parser(argparse.ArgumentParser):
    def format_usage(self):
        return super().format_usage().replace("usage: ", "사용법: ", 1)

    def format_help(self):
        return (super().format_help().replace("usage: ", "사용법: ", 1)
                .replace("positional arguments:", "인자:").replace("options:", "선택사항:")
                .replace("show this help message and exit", "사용법을 보고 끝낸다"))

    def error(self, message):
        detail = message if message.startswith(("포트는", "--duty")) else "필요한 값과 명령을 확인한다 — --help로 사용법을 본다"
        self.usage_error(detail)

    def usage_error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"사용법 오류: {message}\n")


def _parser():
    parser = Parser(prog="python -m gaf", description="Game Dev AI Tool")
    parser.add_argument("--version", action="version", version=f"Game Dev AI Tool {__version__}", help="버전을 보고 끝낸다")
    commands = parser.add_subparsers(dest="command", required=True, title="명령")

    def command(name, help_text, parent=commands):
        child = parent.add_parser(name, help=help_text, description=help_text)
        child.add_argument("--root", type=Path, metavar="폴더", help="프로젝트 폴더")
        return child

    serve = command("serve", "지휘자와 뷰어를 띄운다")
    serve.add_argument("--port", type=int, default=None, metavar="번호")
    command("status", "저장된 상태를 본다")
    command("init", "프로젝트의 첫 파일을 만든다")
    wiki = commands.add_parser("wiki", help="위키 · 옵시디언 볼트를 준비한다")
    actions = wiki.add_subparsers(dest="action", required=True)
    command("init", "위키 · 옵시디언 틀을 더한다(있는 파일은 그대로)", actions)
    say = command("say", "총괄에게 말한다")
    say.add_argument("text", metavar="글")
    say.add_argument("--image", type=Path, action="append", default=[], metavar="파일")
    command("team", "역할마다 상태 · 들고 있는 일 · 마지막 결과를 본다(총괄이 맡기기 전에 보는 것)")
    show = command("show", "파일을 화면의 대화에 올려 사람에게 보인다(총괄 세션)")
    show.add_argument("path", type=Path, metavar="파일")
    show.add_argument("--caption", default="", metavar="글")
    commit = command("commit", "고른 파일의 커밋을 화면에서 사람에게 청한다")
    commit.add_argument("paths", nargs="+", metavar="경로")
    message = commit.add_mutually_exclusive_group(required=True)
    message.add_argument("--message", metavar="글", help="`-` 면 표준 입력에서 읽는다")
    message.add_argument("--file", type=Path, metavar="글파일")
    command("push", "지금 가지의 푸시를 화면에서 사람에게 청한다")
    custom = command("custom", "Custom 변경을 화면에서 사람에게 청한다(총괄 세션)")
    custom.add_argument("scope", choices=("all", "project", "role"))
    custom.add_argument("text", nargs="?", default=None, metavar="글")
    custom.add_argument("--file", type=Path, metavar="파일")
    custom.add_argument("--role", default="", metavar="역할")
    custom.add_argument("--why", required=True, metavar="까닭")
    assign = command("assign", "역할에게 일을 맡긴다")
    assign.add_argument("role", metavar="역할")
    assign.add_argument("title", metavar="제목")
    assign.add_argument("brief", nargs="?", default=None, metavar="내용", help="`-` 면 표준 입력에서 읽는다")
    assign.add_argument("--file", type=Path, default=None, metavar="파일", help="내용을 이 파일에서 읽는다")
    assign.add_argument("--done-when", default="", metavar="끝기준")
    assign.add_argument("--check", default="", metavar="명령", help="역할이 끝낸 뒤 지휘자가 돌릴 검사 명령")
    assign.add_argument("--check-rounds", type=int, default=3, metavar="N")
    assign.add_argument("--check-timeout-min", type=float, default=None, metavar="분")
    assign.add_argument("--check-shell", choices=("bash", "powershell"), default="")
    assign.add_argument("--expect", default="", metavar="경로,…")
    assign.add_argument("--review", default="", metavar="역할,…")
    assign.add_argument("--review-files", default=None, metavar="글롭,…")
    assign.add_argument("--review-brief", default=None, metavar="글")
    assign.add_argument("--review-keep-going", action="store_true")
    assign.add_argument("--no-default-review", action="store_true", help="이 일에서 기본 검토를 뺀다(사람만 — 총괄은 못 뺀다)")
    tell = command("tell", "일을 새로 만들지 않고 역할의 줄에 글을 덧붙인다")
    tell.add_argument("role", metavar="역할")
    tell.add_argument("text", nargs="?", default=None, metavar="글", help="`-` 면 표준 입력에서 읽는다")
    tell.add_argument("--file", type=Path, default=None, metavar="파일", help="글을 이 파일에서 읽는다")
    send = command("send", "다른 세션(역할 · 총괄)에게 글을 보낸다 — 세션 안의 `SendMessage <받는 이> \"<글>\"` 과 같다")
    send.add_argument("to", metavar="받는이")
    send.add_argument("text", nargs="?", default=None, metavar="글", help="`-` 면 표준 입력에서 읽는다")
    send.add_argument("--file", type=Path, default=None, metavar="파일", help="글을 이 파일에서 읽는다")
    send.add_argument("--reply", action="store_true", help="답이 필요하다 — 받은 쪽의 마지막 답이 글로 돌아온다(세션이 보낼 때)")
    result = command("result", "일 하나의 결과 전문을 본다")
    result.add_argument("task", metavar="일번호")
    result.add_argument("--wait", nargs="?", const=1800.0, type=float, default=None, metavar="초",
                        help="일이 끝날 때까지 기다렸다가 찍는다(기다릴 초 · 안 주면 1800 · 제때 안 끝나면 종료 코드 2 — 사람의 셸에서만)")
    inbox = command("inbox", "밖의 총괄 우편함에서 새 글과 일 끝 알림을 받는다")
    inbox.add_argument("--wait", nargs="?", const=1800.0, type=float, default=None, metavar="초",
                       help="새 글이 올 때까지 기다린다(안 주면 1800초 · 제때 안 오면 종료 코드 2)")
    inbox.add_argument("--after", type=int, default=None, metavar="N", help="이 번호 뒤의 글을 보기만 한다(읽은 자리는 그대로다)")
    ask = command("ask", "사람에게 묻는다 — 화면의 「내가 정할 것」에 올린다(총괄 세션만 쓴다)")
    ask.add_argument("question", nargs="?", default=None, metavar="물음", help="`-` 면 표준 입력에서 읽는다")
    ask.add_argument("--file", type=Path, default=None, metavar="파일", help="물음을 이 파일에서 읽는다")
    ask.add_argument("--option", action="append", default=[], metavar="보기", help="고를 보기(여러 번 쓴다 · 4개까지)")
    stop = command("stop", "일을 멈춘다")
    stop.add_argument("role", nargs="?", default="", metavar="역할")
    shutdown = command("shutdown", "지휘자를 끈다")
    shutdown.add_argument("--wait", type=float, default=None, metavar="분",
                          help="도는 턴 · 배경 일이 비기를 기다렸다 끈다(양수 · 소수도 된다)")
    opener = command("open", "지휘자를 (꺼져 있으면) 배경에서 띄우고 화면을 연다 — 폴더를 안 주면 마지막에 연 프로젝트")
    opener.add_argument("--no-browser", action="store_true", help="화면을 열지 않고 주소만 찍는다")
    opener.add_argument("--app", action="store_true", help="주소창 없는 프로그램 창으로 연다")
    opener.add_argument("--last", action="store_true",
                        help="지금 폴더를 보지 않고 마지막에 쓰던 프로젝트를 연다(공용 아이콘이 쓴다)")
    shortcut = command("shortcut", "프로그램을 켜는 바로 가기를 만든다(사람만 쓴다)")
    shortcut.add_argument("--dir", type=Path, metavar="폴더", help="바로 가기를 둘 곳(기본: 바탕화면)")
    shortcut.add_argument("--name", metavar="이름", help="바로 가기 이름")
    shortcut.add_argument("--last", action="store_true",
                          help="프로젝트를 정하지 않는 공용 아이콘 — 마지막에 쓰던 프로젝트를 연다")
    setup = command("setup", "처음 한 번 하는 준비(사람만 쓴다)")
    setup.add_argument("--no-shortcut", action="store_true", help="바로 가기를 만들지 않는다")
    setup.add_argument("--no-open", action="store_true", help="준비 뒤 프로그램을 켜지 않는다")
    setup.add_argument("--codex", action="store_true", help="GPT 설치에 묻지 않고 예로 답한다")
    setup.add_argument("--ask", action="store_true", help="GPT · 옵시디언 설치를 다시 고른다")
    command("doctor", "이 PC가 준비됐는지 본다(엔진 · 로그인 · 프로젝트 파일)")
    voice = command("voice", "이 PC의 음성 인식을 확인하거나 준비한다(사람만 쓴다)")
    voice.add_argument("action", nargs="?", default="status", choices=["status", "setup"])
    work_hooks = command("hooks", "일 앞뒤 명령 · 기본 검사와 믿음을 본다 · trust 는 둘을 함께 믿는다")
    work_hooks.add_argument("action", nargs="?", choices=["trust"], metavar="trust")
    beyond = command("outside", "역할이 쓰려는 프로젝트 밖 폴더와 이름을 본다 · approve/revoke 로 폴더를 승인하고 거둔다 · declare/undeclare 로 이름을 "
                     "적고 지운다 · pair/unpair 로 그 이름의 이 PC 폴더를 고르고 푼다 · path 로 그 폴더를 찾는다(path 말고는 사람만 쓴다)")
    beyond.add_argument("action", nargs="?", choices=["approve", "revoke", "declare", "undeclare", "pair", "unpair", "path"],
                        metavar="approve|revoke|declare|undeclare|pair|unpair|path")
    beyond.add_argument("folders", nargs="*", metavar="값", help="approve · revoke: 폴더(절대 경로)… / declare: 이름 [설명] / undeclare · unpair: 이름… / "
                        "pair: 이름 폴더 / path: 이름")
    beyond.add_argument("--all", action="store_true", help="approve: 쓰려는 폴더 전부(못 여는 곳은 뺀다) · revoke: 승인한 폴더 전부")
    checks = command("check", "프로젝트 기본 검사를 본다 · 추가하거나 뺀다")
    actions = checks.add_subparsers(dest="action", required=True)
    command("list", "기본 검사와 믿음", actions)
    add = command("add", "기본 검사 추가(총괄은 사람 승인)", actions)
    add.add_argument("name")
    add.add_argument("check_command")
    add.add_argument("--when", default="")
    add.add_argument("--shell", choices=("bash", "powershell"), default="")
    add.add_argument("--timeout-min", type=float, default=10)
    add.add_argument("--why", required=True)
    remove = command("remove", "기본 검사 제거(총괄은 사람 승인)", actions)
    remove.add_argument("name")
    reviews = command("review", "프로젝트 기본 검토(맞는 파일이 바뀌면 지휘자가 늘 검토)를 본다 · 추가하거나 뺀다")
    review_actions = reviews.add_subparsers(dest="action", required=True)
    command("list", "기본 검토 규칙과 믿음", review_actions)
    review_add = command("add", "기본 검토 추가(총괄은 사람 승인)", review_actions)
    review_add.add_argument("name")
    review_add.add_argument("--when", required=True, metavar="글롭,…", help="이 글롭에 맞는 파일이 바뀌면 검토한다")
    review_add.add_argument("--reviewers", required=True, metavar="역할,…", help="검토 역할 1~3개(작은 바뀜은 첫 역할 하나만 본다)")
    review_add.add_argument("--files", default="", metavar="글롭,…", help="검토가 볼 그림 · 레퍼런스")
    review_add.add_argument("--brief", default="", metavar="글", help="검토 기준")
    review_add.add_argument("--small-area", type=float, default=None, metavar="%", help="바뀐 넓이가 이보다 작으면 가벼운 검토")
    review_add.add_argument("--small-lines", type=int, default=None, metavar="N", help="바뀐 줄 수가 이보다 작으면 가벼운 검토")
    review_add.add_argument("--small-chars", type=int, default=None, metavar="N", help="바뀐 글자 수가 이보다 작으면 가벼운 검토")
    review_add.add_argument("--no-keep-going", action="store_true", help="큰 바뀜에서도 나란히 견주기로 더 찾지 않는다")
    review_add.add_argument("--why", required=True)
    review_remove = command("remove", "기본 검토 제거(총괄은 사람 승인)", review_actions)
    review_remove.add_argument("name")
    listing = command("projects", "이 PC에서 연 프로젝트를 본다 · `projects forget <폴더>` 는 목록에서 뺀다")
    listing.add_argument("action", nargs="?", choices=["forget"], metavar="forget")
    listing.add_argument("folder", nargs="?", type=Path, metavar="폴더")
    far = command("remote", "폰 · 다른 PC에서 붙는 원격 접속(등록한 기기만): on · off · devices · forget <기기|all>")
    far.add_argument("action", nargs="?", default="status", choices=["status", "on", "off", "devices", "forget"],
                     metavar="on|off|devices|forget")
    far.add_argument("device", nargs="?", default="", metavar="기기")
    far.add_argument("--public", action="store_true", help="PC의 Tailscale Funnel 로 인터넷 주소를 연다(폰에는 설치할 것 없음)")
    far.add_argument("--funnel-port", type=int, default=None, metavar="번호", help="공개 포트: 443 · 8443 · 10000(--public 과만 쓴다)")
    far.add_argument("--https", action="store_true", help="사설망 앞단이 https 로 연다(Tailscale: 기기 이름이 공개 인증서 기록에 남는다)")
    far.add_argument("--serve-port", type=int, default=None, metavar="번호", help="사설망에서 여는 포트(기본: 지휘자 포트와 같다)")
    far.add_argument("--host", action="append", default=[], metavar="이름",
                     help="들일 Host 이름을 직접 적는다(여러 번) — Tailscale 을 부르지 않고, 앞단은 손으로 건다")
    far.add_argument("--user", action="append", default=[], metavar="사람", help="들일 사람(앞단이 붙이는 로그인 이름 · 여러 번)")
    far.add_argument("--identity-header", default=None, metavar="머리",
                     help="앞단이 「누가 보냈나」를 붙이는 머리 이름(기본 Tailscale-User-Login · 빈 글이면 사람을 가리지 않는다)")
    command("pair", "원격 기기를 등록할 코드를 받는다(떠 있는 지휘자에게)")
    role = commands.add_parser("role", help="역할을 다룬다")
    actions = role.add_subparsers(dest="action", required=True, title="역할 명령")
    command("list", "역할 목록을 본다", actions)
    new = command("new", "역할을 만든다", actions)
    new.add_argument("name", metavar="이름")
    new.add_argument("--duty", default=None, metavar="하는일")
    new.add_argument("--from", dest="template", metavar="서랍id")
    for option in ("engine", "model", "effort", "write"):
        new.add_argument("--" + option, metavar="값")
    for action in ("off", "on", "reset"):
        child = command(action, {"off": "역할을 끈다", "on": "역할을 켠다", "reset": "역할을 새로 시작한다"}[action], actions)
        child.add_argument("name", metavar="이름")
    shelf = commands.add_parser("library", help="서랍을 보고 필요한 글만 꺼낸다")
    actions = shelf.add_subparsers(dest="action", required=True)
    listing = command("list", "서랍 목록을 본다", actions)
    listing.add_argument("--kind", choices=list(library.KINDS))
    listing.add_argument("--engine", choices=["unity", "unreal", "godot", "web"], default="")
    listing.add_argument("--find", default="")
    show = command("show", "서랍 글을 읽는다", actions)
    show.add_argument("item", metavar="칸/id")
    take = command("take", "서랍 글을 프로젝트에 꺼낸다", actions)
    take.add_argument("items", nargs="+", metavar="칸/id")
    take.add_argument("--to", metavar="폴더", help="기본: 위키가 있으면 wiki/raw/서랍, 없으면 docs/서랍")
    job = commands.add_parser("job", help="배경 일을 다룬다")
    actions = job.add_subparsers(dest="action", required=True, title="배경 일 명령")
    run = command("run", "긴 명령을 배경 일로 띄운다(총괄 세션)", actions)
    run.add_argument("job_command", nargs="?", default=None, metavar="명령")
    run.add_argument("--file", type=Path, default=None, metavar="파일")
    for option in ("name", "cwd"):
        run.add_argument("--" + option, default="", metavar="값")
    run.add_argument("--shell", choices=("bash", "powershell"), default="")
    run.add_argument("--timeout-min", type=float, default=0, metavar="분")
    command("list", "배경 일 목록을 본다", actions)
    out = command("out", "배경 일 출력의 끝을 본다", actions)
    out.add_argument("job", metavar="번호")
    out.add_argument("--lines", type=int, default=40, metavar="N")
    stop = command("stop", "배경 일을 멈춘다", actions)
    stop.add_argument("job", metavar="번호")
    from .research.plan import SOURCES as research_sources
    research = commands.add_parser("research", help="레퍼런스 조사 — 출처를 보고 켜고 끈다 · 지난 조사와 보고의 형식을 본다")
    actions = research.add_subparsers(dest="action", required=True, title="레퍼런스 조사 명령")
    command("sources", "출처마다 어디로 무엇을 보내는지와 이 프로젝트에 켰는지를 본다", actions)
    for name, text in (("allow", "출처를 이 PC 에서 이 프로젝트에 켠다(사람만)"), ("deny", "출처를 끈다(사람만)")):
        change = command(name, text, actions)
        change.add_argument("sources", nargs="+", choices=list(research_sources), metavar="출처")
    command("list", "지난 조사를 새것부터 본다", actions)
    check = command("check", "조사 보고(…/조사/<폴더>/보고.md)의 형식을 본다 — 번호 · 주소 · 따온 말이 증거에 있는가만", actions)
    check.add_argument("brief", type=Path, metavar="보고.md")
    run = command("run", "레퍼런스 조사를 청한다(총괄 세션)", actions)
    run.add_argument("--file", type=Path, required=True, metavar="계획 JSON 파일")
    rea = commands.add_parser("rea", help="REA(게임 빌드 · 프로그램을 뜯어보는 분석 도구) — 이 PC 에 깔고 켜고 끈다 · 무엇이 있는지 본다(사람만)")
    actions = rea.add_subparsers(dest="action", required=True, title="REA 명령")
    command("status", "REA · Node · Ghidra(판까지) · Java 가 이 PC 에 있는지와 켰는지를 본다", actions)
    command("on", "REA 를 이 PC 에 (없으면) 깔고 켠다 — 맞는 Node 가 없으면 Node 도 받는다(인터넷 · 사람만)", actions)
    command("off", "REA 를 이 PC 에서 끈다 — 깐 것은 그대로 둔다(사람만)", actions)
    return parser


def _table(rows):
    print("역할\t상태\t일\t이유")
    for role in rows:
        task = role.get("task") or role.get("last") or {}
        print("\t".join(str(v or "—").replace("\n", " ") for v in (
            role.get("name"), STATUS_WORDS.get(role.get("status"), role.get("status")),
            task.get("title"), role.get("reason"))))


def _elapsed(seconds):
    if type(seconds) not in (int, float) or (isinstance(seconds, float) and not math.isfinite(seconds)):
        seconds = 0
    seconds = max(0, int(seconds))
    return f"{seconds // 60}분째" if seconds >= 60 else f"{seconds}초째"


def _running_for(job):
    """도는 배경 일이 지금까지 돈 시간(초). state.json 은 바뀔 때만 다시 쓰여, 거기 적힌 초는 일을 띄운 순간의 값으로 남는다 —
    시작 시각이 있으면 지금까지를 센다. 시각이 깨졌거나 앞날이면 적힌 초를 그대로 준다."""
    from datetime import datetime

    try:
        since = (datetime.now().astimezone() - datetime.fromisoformat(job.get("started_at"))).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return job.get("seconds")
    return since if since >= 0 else job.get("seconds")


def _status(paths):
    state = _read_json(paths.state)
    if state is None:
        print("아직 띄운 적이 없다")
        return 0
    record = _read_json(paths.server) or {}
    alive = pid_alive(record.get("pid"))
    print("지휘자: " + ("살아 있다" if alive else "꺼져 있다"))
    _table(state.get("roles", []))
    jobs = state.get("jobs")
    if alive and isinstance(jobs, list):
        for job in jobs:
            if (not isinstance(job, dict) or job.get("status") != "running"
                    or not isinstance(job.get("id"), str) or not isinstance(job.get("name"), str)):
                continue
            name = " ".join(job["name"].split())
            print(f"배경 일: {job['id']} {name} — 도는 중({_elapsed(_running_for(job))})")
    for decision in state.get("decisions") or []:    # 답은 화면(뷰어)에서 한다 — 여기는 있다는 것만 보인다
        if not isinstance(decision, dict):
            continue
        questions = decision.get("questions")
        first = questions[0].get("question") if isinstance(questions, list) and questions and isinstance(questions[0], dict) else ""
        print(f"정할 것: {decision.get('id')} {' '.join(str(first or '').split())[:120]} (답은 화면에서 한다)")
    for notice in state.get("notices", []):
        print("알림: " + str(notice.get("text", "")))
    return 0


def _hooks(paths, action):
    config = load_config(paths)
    commands = config.hooks
    fingerprint = hooks.digest(commands, config.checks, config.reviews)
    count = sum(len(commands.get(key, [])) for key in ("before_work", "after_work"))
    if count:
        for key, title in (("before_work", "일 전"), ("after_work", "일 뒤")):
            print(title)
            for command in commands.get(key, []):
                print(command)
    for check in config.checks:
        print(f"기본 검사: {check['name']}\n{check['command']}\nwhen: {', '.join(check['when']) or '(어떤 파일이든)'}")
    count += len(config.checks)
    for review in config.reviews:      # 기본 검토(명령은 없다 — 회차마다 검토 세션을 연다)
        print(f"기본 검토: {review['name']}\nwhen: {', '.join(review['when'])} · 검토 역할: {', '.join(review['reviewers'])}")
    count += len(config.reviews)
    if action == "trust":
        hooks.trust(paths.root, fingerprint)
        print(f"믿었다 — 명령 {count}개(이 PC · 이 프로젝트). 떠 있는 지휘자는 다음 점검 때 알아챈다" if count else "명령이 없다")
    elif not count:
        print("명령이 없다")
    else:
        print("믿음: 됨" if hooks.trusted(paths.root) == fingerprint else
              "믿음: 안 됨 — python -m gaf hooks trust 로 확인한다")
    return 0


def _checks(paths, args):
    from .config import validate_checks, change_check, ConfigError
    from . import scope
    from .lead_tools import checks_text
    config = load_config(paths)
    fingerprint = hooks.digest(config.hooks, config.checks, config.reviews)
    if args.action == "list":
        print(checks_text(config.checks, not fingerprint or hooks.trusted(paths.root) == fingerprint))
        return 0
    kind, values = _tool_request(args)
    if args.action == "add":
        check = validate_checks([{k: v for k, v in values.items() if k != "why"}])[0]
        if not args.why.strip() or len(args.why) > 4000:
            raise ConfigError("why: 어떤 실수를 막는지 1~4000자로 적는다")
        allowed, reason = scope.check_tool("PowerShell" if check["shell"] == "powershell" else "Bash",
            {"command": check["command"]}, write=(), root=paths.root, bash_deny=config.bash_deny)
        if not allowed:
            raise ConfigError("문이 막았다 — " + reason)
    else:
        check = {"name": args.name}
    _, trusted = change_check(paths, kind, check)
    print("기본 검사를 바꿨다." + (" 함께 믿었다." if trusted else " `gaf hooks trust` 로 전체를 확인하라."))
    return 0


def _research(paths, args):
    """레퍼런스 조사의 사람 쪽 명령 — 출처를 보고 켜고 끈다 · 지난 조사와 보고의 형식을 본다(지휘자가 떠 있지 않아도 된다)."""
    from .research import check, consent, sources
    if args.action == "run":
        print("오류: 조사는 지휘자가 연 세션(총괄 · 맡은 일을 하는 역할)이 청한다 — 화면에서 총괄에게 조사를 시킨다", file=sys.stderr)
        return 1
    if args.action in ("allow", "deny"):
        on = (consent.allow if args.action == "allow" else consent.deny)(paths.root, args.sources)
        print(f"{' · '.join(dict.fromkeys(args.sources))} 를 이 PC 에서 이 프로젝트에 {'켰다' if args.action == 'allow' else '껐다'}. "
              f"지금 켜진 출처: {' · '.join(on) or '없음'}")
        if args.action == "allow" and "youtube" in args.sources and not sources.youtube.available():
            print("YouTube 를 받는 도구 yt-dlp 를 이 프로그램에 깐다. 잠시 기다린다")
            if not sources.youtube.install():
                print("오류: yt-dlp 를 깔지 못했다 — 인터넷을 확인하고 같은 명령을 다시 친다(출처는 켜 둔 그대로다)", file=sys.stderr)
                return 1
            print("yt-dlp 를 깔았다.")
        return 0
    if args.action == "sources":
        on = consent.allowed(paths.root)
        for row in sources.describe_all():
            print(f"{row['name']}({row['label']}): {'켜짐' if row['name'] in on else '꺼짐'}")
            print(f"  받는 법: {row['how']}")
            print(f"  보내는 것: {row['sends']} → {' · '.join(row['hosts'])}")
            print(f"  약관: {row['terms']}")
            if row["needs"]:
                print(f"  필요한 것: {row['needs']}")
        print("켜기: gaf research allow <출처>… · 끄기: gaf research deny <출처>… (처음 조사할 때 화면의 승인으로도 켜진다 — 이 PC 의 이 프로젝트에만)")
        return 0
    if args.action == "list":
        rows = []
        for path in (paths.state_dir / "research").glob("r-*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(row, dict) and all(isinstance(row.get(key), str) for key in ("id", "topic", "dir")):
                rows.append(row)
        words = {"ok": "넉넉함", "thin": "얇음", "nothing-solid": "찾지 못함"}
        for row in sorted(rows, key=lambda row: row["id"], reverse=True)[:50]:
            print(f"{row['id']} 「{row['topic']}」 — {words.get(row.get('state'), row.get('state'))} · 증거 {row.get('items')}건 · {row['dir']}/증거.md")
        if not rows:
            print("(조사가 없다)")
        return 0
    brief = args.brief if args.brief.is_absolute() else paths.root / args.brief
    if brief.suffix.lower() != ".md" or not brief.is_file():
        print("오류: 조사 보고를 준다 — 있는 글 파일(.md · 흔히 `…/조사/<폴더>/보고.md`)", file=sys.stderr)
        return 1
    try:
        shown = brief.resolve().relative_to(paths.root.resolve()).as_posix()
    except ValueError:
        shown = brief.as_posix()
    try:
        result = {**check.check(paths.root, brief), "path": shown}
    except (OSError, UnicodeError) as exc:
        print(f"오류: 보고를 읽지 못했다({type(exc).__name__})", file=sys.stderr)
        return 1
    print(check.line(result))
    return 0 if result["ok"] else 1


def _rea(args):
    """REA 의 사람 쪽 명령 — 이 PC 에 무엇이 있는지 본다 · 깔고 켠다 · 끈다(지휘자가 떠 있지 않아도 된다 · PC 마다 한 번).

    켜기는 사람이 터미널에서 친 이 명령이 곧 승인이다(`gaf research allow` 와 같다 — 묻는 칸은 없다). 받을 것과 법의 선을 먼저 찍는다.
    """
    from .rea import setup as rea_setup, state as rea_state
    if args.action == "status":
        for line in rea_setup.status_lines():
            print(line)
        return 0
    if args.action == "off":
        rea_state.disable()
        print("REA 를 이 PC 에서 껐다 — 깐 것은 그대로 둔다(다시 켜기: python -m gaf rea on)")
        return 0
    need = rea_setup.install_plan()
    print("REA 를 이 PC 에서 켠다(이 PC 의 모든 프로젝트 · 게임마다 묻지 않는다).")
    print("받을 것:" if need else "받을 것: 없음(이미 깔려 있다)")
    for line in need:
        print(f"  · {line}")
    print("알아 둘 것:")
    for line in rea_setup.NOTICE:
        print(f"  {line}")
    if need:
        result = rea_setup.install(progress=lambda text: print(text, flush=True))
        if not result.get("ok"):
            print(f"오류: 깔지 못했다 — {result.get('error')}\n켜지 않았다. 같은 명령을 다시 치면 이어서 깐다", file=sys.stderr)
            return 1
    rea_state.enable()
    print("REA 를 켰다 — REA 를 맡은 역할이 지휘자를 거쳐 쓴다. 끄기: python -m gaf rea off")
    return 0


def _reviews(paths, args):
    from .config import validate_reviews, change_review, ConfigError
    from .lead_tools import reviews_text
    config = load_config(paths)
    fingerprint = hooks.digest(config.hooks, config.checks, config.reviews)
    if args.action == "list":
        print(reviews_text(config.reviews, not fingerprint or hooks.trusted(paths.root) == fingerprint))
        return 0
    kind, values = _tool_request(args)
    if args.action == "add":
        rule = validate_reviews([{k: v for k, v in values.items() if k != "why"}])[0]
        if not args.why.strip() or len(args.why) > 4000:
            raise ConfigError("why: 왜 늘 검토해야 하는지 1~4000자로 적는다")
    else:
        rule = {"name": args.name}
    _, trusted = change_review(paths, kind, rule)
    print("기본 검토를 바꿨다." + (" 함께 믿었다." if trusted else " `gaf hooks trust` 로 전체를 확인하라."))
    return 0


def _shortcut(paths, link_dir=None, name=None, *, last=False):
    """바탕화면 바로 가기. last = 공용 아이콘(1.8.0) — 프로젝트를 정하지 않고 마지막에 쓰던 것을 연다(프로젝트는 프로그램 안에서 고른다)."""
    if not last and not paths.config.is_file():
        print("먼저 python -m gaf init --root <프로젝트 폴더> 로 준비한다", file=sys.stderr)
        return 1
    # AppData(run_dir) 가 아니라 사람마다의 gaf 폴더(~/.gaf)에 둔다 — Microsoft Store 의 파이썬은 AppData 에 쓴 파일을 제 꾸러미 안
    # (Packages\…\LocalCache)으로 옮겨 써서, 탐색기가 바로 가기의 그림을 못 찾았다(11-18 사용자 「찾았는데 아이콘이 없어」 · 1.3.1).
    from .paths import user_dir

    icon = user_dir() / "gaf.ico"
    data = desktop.icon_bytes()
    icon.parent.mkdir(parents=True, exist_ok=True)
    if not icon.is_file() or icon.read_bytes() != data:
        icon.write_bytes(data)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    label = desktop.APP_NAME if last else desktop.shortcut_name(paths.root)
    ok, detail = desktop.make_shortcut(link_dir, name if name is not None else label,
        target=str(pythonw) if pythonw.is_file() else sys.executable,
        arguments="-X utf8 -m gaf open --app --last" if last else f'-X utf8 -m gaf open --app --root "{paths.root.resolve()}"',
        workdir=str(Path(__file__).resolve().parent.parent), icon=str(icon), description=label)
    print("바로 가기를 만들었다: " + detail if ok else detail, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def _init(paths, *, lead_model=None, lead_effort=None):
    """프로젝트의 첫 파일. lead_model · lead_effort 가 빈 글이면 총괄을 계정의 기본값으로 만든다(첫 설치가 구독을 보고 고른다 — 1.3.0)."""
    paths.root.mkdir(parents=True, exist_ok=True)
    if not paths.config.exists():
        _atomic_json(paths.config, {"project": paths.root.name})
        print("gaf.json 만듦")
    config = load_config(paths)
    if not paths.role_dir(config.lead_role).exists():
        create_role(paths, config, config.lead_role, lead=True, model=lead_model, effort=lead_effort)      # 하는 일은 총괄 틀의 본문이 말한다
        print(f"team/{config.lead_role}/역할.md · 인계.md 만듦")
    common = paths.team / "공통.md"          # 모든 역할이 따르는 규칙(엔진 전용 CLAUDE.md · AGENTS.md 대신 쓰는 파일)
    if not common.exists():
        template = Path(__file__).parent / "templates" / "공통.md"
        _atomic_text(common, template.read_text(encoding="utf-8"))
        print("team/공통.md 만듦")
    _ignore_lines(paths, (".gaf/", "team/*/세션.json"))
    return _wiki_init(paths, fill=False)


def _ignore_lines(paths, lines):
    ignore = paths.root / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    additions = [line for line in lines if line not in existing.splitlines()]
    if additions:
        with ignore.open("a", encoding="utf-8", newline="\n") as stream:
            if existing and not existing.endswith("\n"):
                stream.write("\n")
            stream.write("\n".join(additions) + "\n")
        for line in additions:
            print(f".gitignore에 {line} 더함")


def _wiki_init(paths, *, fill=True):
    """위키 틀 복사. init은 새 폴더만, wiki init은 빠진 파일도 더한다."""
    wiki = paths.root / "wiki"
    fresh = not wiki.exists()
    if fill or fresh:
        template = Path(__file__).parent / "templates/wiki"
        for source in sorted(template.rglob("*")):
            if not source.is_file():
                continue
            target = wiki / source.relative_to(template)
            if not target.resolve().is_relative_to(paths.root.resolve()):
                raise ValueError("위키 틀은 프로젝트 안에만 만든다")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open("xb") as stream:
                    stream.write(source.read_bytes())
            except FileExistsError:
                pass
        if fresh:
            print("wiki/ 만듦 — 옵시디언에서 이 폴더를 볼트로 연다")
    _ignore_lines(paths, (WIKI_IGNORE,))
    return 0


async def _serve(paths, port):
    # 「떠 있나 보기 → 포트 잡기 → 떠 있다고 적기」를 프로젝트마다 한 번에 하나만 한다. 잠금 없이는 serve 둘이 같이 와서 둘 다
    # 「없다」를 보고 떴고, 하나는 server.json 에 없는 숨은 지휘자가 되어 같은 .gaf 를 썼다(11-13 QA 결함-32).
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as starting:
        try:
            starting.enter_context(file_lock(paths.state_dir / "serve", wait=START_LOCK_SECONDS))
        except TimeoutError:
            print("다른 지휘자가 뜨는 중이다 — 잠시 뒤 python -m gaf open 으로 다시 본다", file=sys.stderr)
            return 1
        return await _serve_locked(paths, port, starting)


async def _serve_locked(paths, port, starting):
    record = read_server(paths)
    if record:
        print("이미 떠 있다: " + str(record.get("url") or f"pid {record.get('pid')} · 포트 {record.get('port')}"))
        return 1
    app = build(paths.root)
    # GPT 받아쓰기의 키(1.9.0): 아래의 환경 정리는 이름에 KEY 가 든 변수를 지운다 — 그 앞에서 읽어 받아쓰기에만 쓴다(세션에는 넘기지 않는다).
    voice_key = os.environ.get(cloudvoice.ENV, "")
    clean_env(pass_env=app.config.claude["pass_env"])
    # 포트: 명령의 --port → gaf.json 의 server.port → 이 PC에 정해 둔 포트(원격으로 여는 프로젝트) → 빈 번호
    # 공개 길의 문(앞문 · 뒷문)은 서버의 문지기가 연다 — PC 하나의 문이라 어느 프로젝트가 켰든 켜져 있는 지휘자가 같이 지킨다(1.12.0)
    server = Server(app, port=(app.config.server["port"] or projects.port_of(paths.root)) if port is None else port,
                    dictation=Dictation(env_key=voice_key, words=lambda: [app.config.project, *app.conductor.roles]))
    if not (paths.role_dir(app.config.lead_role) / "역할.md").is_file() and not has_roles(paths):
        # 총괄 없이 다른 역할만 둔 프로젝트는 그대로 돈다(일은 밖에서 `gaf assign` 으로 맡긴다) — 역할이 하나도 없을 때만 알린다
        print("먼저 python -m gaf init", file=sys.stderr, flush=True)
    try:
        # 포트를 먼저 잡는다 — 세션이 `gaf …` 명령으로 지휘자를 부를 주소(GAF_PORT)를 세션을 열기 전에 알아야 한다.
        try:
            await server.start()
        except OSError as error:
            if error.errno in PORT_IN_USE or getattr(error, "winerror", None) in PORT_IN_USE:
                raise InputError(f"오류: 포트 {server.port} 를 다른 프로그램이 쓰고 있다 — `--port <번호>` 로 다른 번호를 주거나 "
                                 "gaf.json 의 server.port 를 바꾼다(0 이면 빈 번호를 고른다)") from None
            raise
        starting.close()                       # 적었다 — 이제 다른 serve 는 「이미 떠 있다」를 본다
        install_commands(app, server.port)
        await app.conductor.start()
        projects.remember(paths.root, app.config.project)      # 이 PC에서 연 프로젝트 목록(프로젝트 밖)
        print(server.url, flush=True)
        await server.shutdown.wait()
    finally:
        try:
            await server.stop()
        finally:
            await app.conductor.stop()
    return 0


def _post(port, path, data, headers, timeout=30):
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                     headers={**headers, "Content-Type": "application/json"})
    # 로컬 요청에 PC의 프록시 설정을 물려주지 않는다.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return json.loads(response.read().decode("utf-8"))


def _call(record, path, data=None, timeout=30):
    return _post(record["port"], path, data, {"X-GAF-Key": record["key"]}, timeout)


def _looks_mangled(text: str) -> bool:
    """표준 입력으로 받은 글의 한글이 「?」로 바뀌어 온 것 같은가(어림이다).

    Windows PowerShell 5.1 은 파이프로 프로그램에 넘기는 글을 ASCII 로 바꾼다($OutputEncoding 의 기본값) — 한글이 모두 「?」가
    되어 오류 없이 들어온다(11-12 QA 결함-19). 받은 쪽에서는 되살릴 수 없으니 보내지 않고 오류로 알린다.
    ASCII 뿐인 글에 「??」가 있고, 물음표가 넷 넘으면서 빈칸 아닌 글자의 반을 넘거나 여덟 넘으면서 넷에 하나를 넘을 때다.
    """
    if not text.isascii() or "??" not in text:
        return False
    marks = text.count("?")
    solid = sum(1 for char in text if not char.isspace())
    return (marks >= 4 and marks * 2 > solid) or (marks >= 8 and marks * 4 > solid)


def _body(value, file):
    """내용 글: 그대로 · `-` 면 표준 입력 · --file 이면 그 파일(긴 글 · 따옴표가 든 글을 셸 걱정 없이 넘긴다)."""
    if file is not None:
        if value not in (None, "-"):
            raise InputError("오류: 내용과 --file 을 같이 쓰지 않는다")
        try:
            return Path(file).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            raise InputError(f"오류: 파일을 읽지 못했다: {file}") from None
    if value == "-":
        text = sys.stdin.read()
        if _looks_mangled(text):
            raise InputError("오류: 표준 입력으로 받은 글의 한글이 「?」로 깨져 온 것 같다(Windows PowerShell 은 파이프로 넘기는 글을 "
                             "ASCII 로 바꾼다). 보내지 않았다 — 글을 UTF-8 파일로 써서 --file <경로> 로 넘긴다")
        return text
    if value is None:
        raise InputError("오류: 내용이 없다 — 글을 적거나, `-` 로 표준 입력에서 넘기거나, --file 로 넘긴다")
    return value


OUTSIDE_STATES = {"open": "열림", "closed": "닫힘", "refused": "못엶"}
FOLDERS_ENV = "GAF_FOLDERS"              # 지휘자가 세션 · 일 앞뒤 명령 · 검사 · 배경 일에 주는 「이름 → 이 PC 의 폴더」(JSON · 그 역할이 지금 쓸 수 있는 이름만)


def _fail(text: str) -> int:
    print(f"오류: {text}", file=sys.stderr)
    return 1


def _folder_from_env(args) -> int:
    """`gaf outside path <이름>` 을 지휘자가 준 값(GAF_FOLDERS)에서만 답한다 — 세션 · 일 앞뒤 명령 · 검사 · 배경 일 안에서 친 것이다.

    접근 값 폴더의 짝 파일을 읽지 않는다: 세션은 그 파일을 못 읽게 돼 있고, 일 앞뒤 명령이 다시 읽으면 그 일이 시작한 뒤 바뀐 짝으로 답해
    앞뒤가 어긋난다(11-26 GPT 검토). 준 값에 없으면 모르는 것이다 — 파일로 물러서지 않는다."""
    if len(args.folders) != 1:
        return _fail("이름 하나를 적는다 — python -m gaf outside path <이름>")
    if args.root is not None:
        return _fail("세션 · 일 앞뒤 명령 안에서는 --root 를 받지 않는다(지휘자가 이 역할에 준 이름만 답한다)")
    try:
        table = json.loads(os.environ.get(FOLDERS_ENV) or "")
    except ValueError:
        table = None
    if not isinstance(table, dict):
        return _fail(f"지휘자가 준 폴더 표({FOLDERS_ENV})가 없거나 읽을 수 없다")
    wanted = outside.name_key(args.folders[0])
    folder = next((value for name, value in table.items() if outside.name_key(name) == wanted), None)
    if not isinstance(folder, str) or not folder:
        return _fail(f"이 역할이 지금 쓸 수 있는 이름이 아니다: {args.folders[0]}(쓰기 범위에 없거나 이 PC 에서 폴더를 고르지 않았다)")
    print(folder)
    return 0


def _other_roots(paths) -> list[str]:
    """이 PC 의 목록에 있는 다른 프로젝트의 뿌리들 — 프로젝트 밖 폴더가 그 안팎과 겹치면 열지 않는다."""
    mine = os.path.normcase(str(paths.root.resolve()))
    return [row["root"] for row in projects.entries() if os.path.normcase(str(Path(row["root"]).resolve())) != mine]


def _outside_names(paths, args):
    """이름 명령 — declare · undeclare(gaf.json 의 folders) · pair · unpair(이 PC 의 짝) · path(그 이름의 폴더를 한 줄로 찍는다)."""
    values = list(args.folders)
    if args.action == "path":
        if os.environ.get(FOLDERS_ENV) is not None:  # 일 앞뒤 명령 · 검사 안이다(세션 표는 없지만 지휘자가 값을 줬다)
            return _folder_from_env(args)
        if len(values) != 1:
            return _fail("이름 하나를 적는다 — python -m gaf outside path <이름>")
        name = values[0]
        if outside.name_error(name):
            return _fail(f"{outside.NAME_RULE}: {name}")
        try:
            declared = {outside.name_key(have) for have in load_config(paths).folders}
        except ConfigError as error:
            return _fail(str(error))
        if outside.name_key(name) not in declared:
            return _fail(f"{outside.UNDECLARED}: {name} — python -m gaf outside declare {name} 로 적는다")
        folder = next((value for have, value in outside.pairs(paths.root).items() if outside.name_key(have) == outside.name_key(name)), "")
        if not folder:
            return _fail(f"{outside.UNPAIRED}: {name} — python -m gaf outside pair {name} <폴더> 로 고른다")
        why = outside.pair_problem(folder, paths.root, others=_other_roots(paths))[1]
        if why:                                      # 없는 폴더 · 못 여는 곳 · 다른 곳으로 가는 연결 — 못 쓰는 짝의 경로는 내주지 않는다
            return _fail(f"{why}: {folder}")
        print(folder)
        return 0
    tail = "떠 있는 지휘자는 다음 점검 때 알아챈다"
    try:
        if args.action == "declare":
            if not values:
                return _fail("적을 이름을 적는다 — python -m gaf outside declare <이름> [설명]")
            change_folders(paths, declare={values[0]: " ".join(values[1:])})
            print(f"적었다 — 이름 @{values[0]}(gaf.json 의 folders · git 으로 같이 받는다). 폴더는 PC 마다 고른다: "
                  f"python -m gaf outside pair {values[0]} <폴더>")
        elif args.action == "undeclare":
            if not values:
                return _fail("지울 이름을 적는다 — python -m gaf outside undeclare <이름>…")
            change_folders(paths, undeclare=values)
            print(f"지웠다 — 이름 {len(values)}개(이 PC 의 짝은 그대로 남는다 — 풀려면 python -m gaf outside unpair <이름>). {tail}")
        elif args.action == "pair":
            if len(values) != 2:
                return _fail("이름과 폴더를 적는다 — python -m gaf outside pair <이름> <폴더>")
            name, folder = values
            if not outside.name_error(name) and outside.name_key(name) not in {outside.name_key(have) for have in load_config(paths).folders}:
                return _fail(f"{outside.UNDECLARED}: {name} — 먼저 python -m gaf outside declare {name} 로 적는다")
            left = outside.pair(paths.root, name, folder, others=_other_roots(paths))
            real = next(value for have, value in left.items() if outside.name_key(have) == outside.name_key(name))
            print(f"골랐다 — @{name} = {real}(이 PC · 이 프로젝트 — 그 폴더를 이 이름으로 쓰는 것의 승인이다). {tail}")
        else:
            if not values:
                return _fail("풀 이름을 적는다 — python -m gaf outside unpair <이름>…")
            left = outside.unpair(paths.root, values)
            print(f"풀었다 — 이름 {len(values)}개(남은 짝 {len(left)}개). {tail}(열려 있는 세션에서도 닫는다)")
    except (outside.OutsideError, ConfigError) as error:
        return _fail(f"{error} — 아무것도 적지 않았다")
    return 0


def _outside(paths, args):
    """역할이 쓰려는 프로젝트 밖 폴더를 보이고, 이 PC 에서 승인하거나 거둔다(사람만 — gaf/outside.py · 1.13.0).

    `gaf hooks trust` 와 같은 꼴: 지휘자가 꺼져 있어도 된다(역할 글과 접근 값 폴더의 승인 파일만 다룬다) · 떠 있는 지휘자는 다음 점검 때 알아챈다."""
    if args.action in ("declare", "undeclare", "pair", "unpair", "path"):
        return _outside_names(paths, args)
    allowed = outside.approved(paths.root)
    if args.action == "revoke":                      # 거두기는 역할 글을 읽지 않는다 — 상관없는 역할 하나가 깨졌다고 못 거두면 안 된다
        folders = list(allowed) if args.all else list(args.folders)
        if not folders and not args.all:
            print("오류: 거둘 폴더를 적거나 --all 을 준다", file=sys.stderr)
            return 1
        try:
            left = outside.revoke(paths.root, folders)
        except outside.OutsideError as error:
            print(f"오류: {error} — 아무것도 거두지 않았다(python -m gaf outside 로 승인 목록을 본다)", file=sys.stderr)
            return 1
        print(f"거뒀다 — 폴더 {len(folders)}곳(남은 승인 {len(left)}곳). 떠 있는 지휘자는 다음 점검 때 닫는다(열려 있는 세션에서도 닫는다)")
        return 0
    config = load_config(paths)
    mine = os.path.normcase(str(paths.root.resolve()))
    others = [row["root"] for row in projects.entries() if os.path.normcase(str(Path(row["root"]).resolve())) != mine]
    places = outside.Places(paths.root, others)
    rows: dict[str, dict] = {}
    pairs = outside.pairs(paths.root)
    paired = {outside.name_key(name): folder for name, folder in pairs.items()}
    named: dict[str, dict] = {outside.name_key(name): {"name": name, "about": about, "declared": True, "roles": []}
                              for name, about in config.folders.items()}
    for name in pairs:                               # 선언에서 빠진 뒤 남은 짝도 보인다 — 보여야 사람이 푼다
        named.setdefault(outside.name_key(name), {"name": name, "about": "", "declared": False, "roles": []})
    wrong: list[str] = []
    for folder in _role_folders(paths):              # 하나씩 읽는다 — 깨진 역할 글은 건너뛰고 알린다
        try:
            role = load_role(paths, config, folder.name)
        except RoleError as error:
            print(f"알림: 역할 「{folder.name}」의 글을 읽지 못해 건너뛴다: {error}", file=sys.stderr)
            continue
        if role.off:
            continue
        for pattern in role.write:
            spot = outside.anchor(pattern, paths.root)
            if spot is None:
                continue
            row = rows.setdefault(spot, {"roles": [], "why": outside.refusal(spot, paths.root, places=places)})
            if role.name not in row["roles"]:
                row["roles"].append(role.name)
        for ref in role.refs:                        # 이름 참조(1.14.0) — 이름마다 쓰는 역할을 모은다
            try:
                name = outside.ref_parts(ref)[0]
            except outside.OutsideError as error:
                wrong.append(f"  잘못적음  {outside.shown(ref)}  {role.name} — {error}")
                continue
            item = named.setdefault(outside.name_key(name), {"name": name, "about": "", "declared": False, "roles": []})
            if role.name not in item["roles"]:
                item["roles"].append(role.name)
    if args.action is None:
        if not rows and not named and not wrong:
            print("프로젝트 밖을 쓰려는 역할이 없다" + (f" · 승인해 둔 폴더 {len(allowed)}곳: {', '.join(allowed)}" if allowed else ""))
            return 0
        if named or wrong:
            print("프로젝트 밖 폴더의 이름(gaf.json 의 folders — 진짜 폴더는 PC 마다 고른다)")
            for key in sorted(named):
                item, folder = named[key], paired.get(key, "")
                problem = outside.pair_problem(folder, paths.root, others=others, places=places)[0] if folder else ""
                state = ("선언없음" if not item["declared"] else "안고름" if not folder
                         else {"": "고름", "missing": "없는폴더"}.get(problem, "다시고름"))
                print(f"  {state}  @{item['name']}  {folder or '-'}  {', '.join(item['roles']) or '-'}" + (f" — {item['about']}" if item["about"] else ""))
            for line in wrong:
                print(line)
            print("이름 적기: python -m gaf outside declare <이름> [설명] · 폴더 고르기: python -m gaf outside pair <이름> <폴더> · "
                  "풀기: python -m gaf outside unpair <이름>")
        if not rows:
            return 0
        print("역할이 쓰려는 프로젝트 밖 폴더(실제로 열리는 폴더 — 파일 하나만 적어도 그 윗폴더 전체가 열린다)")
        shut = 0
        for folder in sorted(rows):
            row = rows[folder]
            state = "refused" if row["why"] else "open" if outside.covered(folder, allowed) else "closed"
            shut += state == "closed"
            print(f"  {OUTSIDE_STATES[state]}  {folder}  {', '.join(row['roles'])}" + (f" — {row['why']}" if row["why"] else ""))
        print(f"이 PC 의 승인: {', '.join(allowed) if allowed else '없음'}")
        if shut:
            print(f"닫힌 곳 {shut}곳 — python -m gaf outside approve --all (전부) 이나 python -m gaf outside approve <폴더>… 로 승인한다(이 PC · 이 프로젝트)")
        return 0
    folders = [folder for folder in sorted(rows) if not rows[folder]["why"]] if args.all else list(args.folders)
    if not folders:
        if args.all:
            print("승인할 프로젝트 밖 폴더가 없다")
            return 0
        print("오류: 승인할 폴더를 적거나 --all 을 준다", file=sys.stderr)
        return 1
    try:
        left = outside.approve(paths.root, folders, others=others)
    except outside.OutsideError as error:
        print(f"오류: {error} — 아무것도 승인하지 않았다", file=sys.stderr)
        return 1
    print(f"승인했다 — 폴더 {len(folders)}곳(이 PC · 이 프로젝트 · 승인은 모두 {len(left)}곳). 떠 있는 지휘자는 다음 점검 때 알아챈다")
    return 0


def _tool_request(args):
    """명령줄 인자 → (총괄 명령 이름, 인자). 총괄 명령이 아니면 None. 이름과 인자는 lead_tools.build 의 것과 같다."""
    command = args.command
    if command == "check":
        if args.action == "list":
            return "check_list", {}
        if args.action == "remove":
            return "check_remove", {"name": args.name}
        return "check_add", {"name": args.name, "command": args.check_command,
                             "when": [s.strip() for s in args.when.split(",") if s.strip()],
                             "shell": args.shell, "timeout_min": args.timeout_min, "why": args.why}
    if command == "review":
        if args.action == "list":
            return "review_list", {}
        if args.action == "remove":
            return "review_remove", {"name": args.name}
        split = lambda text: [s.strip() for s in (text or "").split(",") if s.strip()]      # noqa: E731
        small = {key: value for key, value in (("area", args.small_area), ("lines", args.small_lines),
                                               ("chars", args.small_chars)) if value is not None}
        return "review_add", {"name": args.name, "when": split(args.when), "reviewers": split(args.reviewers),
                              "files": split(args.files), "brief": args.brief, "small": small,
                              "keep_going": not args.no_keep_going, "why": args.why}
    if command == "library":
        if args.action == "list":
            return "library_list", {"kind": args.kind, "engine": args.engine, "find": args.find}
        if args.action == "show":
            return "library_show", {"item": args.item}
        return "library_take", {"items": args.items, "to": args.to}
    if command == "show":
        return "show", {"path": str(args.path.resolve()), "caption": args.caption}
    if command == "commit":
        return "commit", {"paths": args.paths, "message": _body(args.message, args.file)}
    if command == "push":
        return "push", {}
    if command == "custom":
        return "custom", {"scope": args.scope, "role": args.role, "text": _body(args.text, args.file), "why": args.why}
    if command == "research":
        if args.action != "run":
            return None                          # 출처를 켜고 끄는 것 · 보는 것은 사람만 한다
        try:
            plan = json.loads(_body(None, args.file))
        except ValueError:
            plan = None
        if not isinstance(plan, dict):
            raise InputError("오류: 계획 파일은 JSON 객체여야 한다(topic · purpose · queries · window_days · depth)")
        return "research", plan
    if command == "rea":
        return None                              # REA 를 보고 깔고 켜고 끄는 것은 사람만 한다(역할은 rea_tools · rea_call 로 쓴다)
    if command == "job":
        if args.action == "run":
            return "job_run", {"command": _body(args.job_command, args.file), "name": args.name, "cwd": args.cwd,
                               "shell": args.shell, "timeout_min": args.timeout_min}
        if args.action == "list":
            return "jobs", {}
        if args.action == "out":
            return "job_out", {"job": args.job, "lines": args.lines}
        return "job_stop", {"job": args.job}
    if command == "team":
        return "team", {}
    if command == "assign":
        return "assign", {"role": args.role, "title": args.title, "brief": _body(args.brief, args.file),
                          "done_when": args.done_when,
                          **({"expect": [s.strip() for s in args.expect.split(",") if s.strip()],
                              "check_rounds": args.check_rounds} if args.expect else {}),
                          **({"check_rounds": args.check_rounds} if args.check_rounds != 3 else {}),
                          **({"check": args.check, "check_rounds": args.check_rounds,
                              "check_timeout_min": 10 if args.check_timeout_min is None else args.check_timeout_min, "check_shell": args.check_shell}
                             if args.check else {}),
                          **({"review": [s.strip() for s in args.review.split(",") if s.strip()],
                              "review_files": [s.strip() for s in (args.review_files or "").split(",") if s.strip()],
                              "review_brief": args.review_brief or "", "review_keep_going": args.review_keep_going}
                             if args.review else {}),
                          **({"default_review": False} if args.no_default_review else {})}
    if command == "tell":
        return "tell", {"role": args.role, "text": _body(args.text, args.file)}
    if command == "send":
        return "send", {"to": args.to, "text": _body(args.text, args.file), "reply": bool(args.reply)}
    if command == "result":
        return "result", {"task": args.task}
    if command == "ask":
        return "ask", {"question": _body(args.question, args.file), "options": list(args.option)}
    if command == "stop" and args.role:
        return "stop", {"role": args.role}
    if command == "role" and args.action != "list":
        if args.action == "new":
            data = {"name": args.name, "duty": args.duty or ""}
            if args.template is not None:
                data["template"] = args.template
            data.update({key: getattr(args, key) for key in ("engine", "model", "effort")
                         if getattr(args, key) is not None})
            if args.write is not None:
                data["write"] = [value.strip() for value in args.write.split(",") if value.strip()]
            return "new_role", data
        if args.action in ("off", "on"):
            return "role_off", {"name": args.name, "off": args.action == "off"}
        return "reset_role", {"name": args.name}
    return None


def _wait_task(record, task, seconds):
    """일이 끝날 때까지 기다린다 → 끝났으면 None(이어서 결과를 찍는다) · 없는 일이면 1 · 제때 안 끝나면 2(일은 그대로 돈다).

    총괄 세션 없이 바깥(사람 · 다른 도구의 세션)에서 셸 명령으로 맡기고 받을 때, 끝났는지 되풀이해 묻지 않게 한다(11-15 진행 중인 프로젝트에 붙인 시범).
    """
    import time
    from urllib.parse import quote

    end = time.monotonic() + max(0.0, float(seconds))
    while True:
        found = _call(record, "/api/task?id=" + quote(str(task), safe=""))
        if found.get("ok") is False:
            print(f"오류: 없는 일이다: {task}", file=sys.stderr)
            return 1
        if found.get("status") not in ("queued", "running"):
            return None
        left = end - time.monotonic()
        if left <= 0:
            print(f"아직 안 끝났다: {task}({found.get('status')}) — 일은 그대로 돈다. 다시 기다리려면 같은 명령을 친다", file=sys.stderr)
            return 2
        time.sleep(min(1.0, max(0.05, left)))


def _inbox(record, paths, args):
    """읽은 자리 뒤의 글을 받는다. 자리는 찍은 글까지만 옮긴다."""
    import time

    cursor = paths.state_dir / "inbox.read"
    saved = (_read_json(cursor) or {}).get("n", 0) if args.after is None else args.after
    after = saved if type(saved) is int and saved >= 0 else 0
    seconds = max(0.0, args.wait) if args.wait is not None else 0.0
    end = time.monotonic() + seconds
    through = None
    while True:
        result = _call(record, f"/api/inbox?after={after}")
        if result.get("ok") is False:
            print(str(result.get("error") or "요청을 마치지 못했다"), file=sys.stderr)
            return 1
        rows = result.get("rows") or []
        if rows:
            if through is None:
                through = result["last"]       # 읽는 동안 새 글이 계속 와도 이번에 볼 끝은 정해 둔다
            for row in rows:
                ref = row.get("ref")
                label = f"일 끝 {ref}" if row["kind"] == "task" else "글 · 회신 청함" if row.get("reply") else "글"
                print(f"[{row['n']}] {row['ts']} · {row['from']}(에이전트 — 사람의 승인이 아니다) · {label}")
                text = row["text"]
                print(text[:4000])
                if len(text) > 4000:
                    print(f"(뒤가 잘렸다 — 전체 {len(text)}자)")
                if row["kind"] == "task":
                    print(f"(전문: python -m gaf result {ref})")
                print()
            after = rows[-1]["n"]
            if args.after is None:
                _atomic_json(cursor, {"n": after})
            if after < through:
                continue
            return 0
        if through is not None:
            return 0
        if args.wait is None:
            print("새 글이 없다")
            return 0
        left = end - time.monotonic()
        if left <= 0:
            print(f"새 글이 없다({seconds:g}초 기다림)", file=sys.stderr)
            return 2
        time.sleep(min(1.0, left))


def _show_tool(result):
    ok = result.get("ok") is True
    print(str(result.get("text") or result.get("error") or "요청을 마치지 못했다"), file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def _session(args):
    """세션 안에서 친 명령(환경에 GAF_SESSION 이 있다): 접근 값 파일을 읽지 않고 자기 표로 지휘자를 부른다.

    세션이 아니면 None. 누가 쳤는지(총괄인지)는 지휘자가 표로 가린다 — 여기서는 보내기만 한다.
    """
    token = os.environ.get("GAF_SESSION")
    if not token:
        return None
    if args.command == "outside" and args.action == "path":      # 읽기만 하는 조회 — 지휘자가 이 세션에 준 값에서만 답한다(짝 파일을 읽지 않는다)
        return _folder_from_env(args)
    request = None if args.command in ("serve", "status", "init") else _tool_request(args)
    if request is None:
        print(f"오류: 이 명령은 사람만 쓴다. 세션 안에서 되는 것: {SESSION_ONLY}", file=sys.stderr)
        return 1
    port = os.environ.get("GAF_PORT", "")
    if not port.isdigit():
        print("오류: 지휘자 주소를 모른다(GAF_PORT 가 없다) — 지휘자를 `python -m gaf serve` 로 띄웠는지 본다", file=sys.stderr)
        return 1
    return _show_tool(_post(int(port), "/api/tool", {"name": request[0], "args": request[1]},
                            {"X-GAF-Session": token}, TOOL_TIMEOUT))


def _busy_lines(view):
    """끄면 끊기는 것을 항목마다 한 줄로 적는다."""
    lines = []
    if view.get("turns"):
        turns = []
        for turn in view["turns"]:
            task = " ".join(str(turn.get(key) or "") for key in ("task", "title")).strip()
            turns.append(turn["role"] + (f"({task})" if task else ""))
        lines.append("도는 턴 — " + " · ".join(turns))
    if view.get("jobs"):
        lines.append("배경 일 — " + " · ".join(
            f"{job['id']} {job['name']}({_elapsed(job.get('seconds'))})" for job in view["jobs"]))
    if view.get("queued"):
        lines.append(f"줄에 선 글 {view['queued']}개(다시 켜면 이어진다)")
    if view.get("commits"):
        lines.append(f"진행 중인 커밋 · 푸시 {view['commits']}개")
    return lines


def _shutdown(record, minutes):
    import time

    end = time.monotonic() + minutes * 60 if minutes is not None else None
    waiting = False
    while True:
        if waiting and time.monotonic() >= end:
            break
        result = _call(record, "/api/shutdown", {"if_idle": True} if minutes is not None else {})
        if result.get("ok") is False:
            print(str(result.get("error") or "요청을 마치지 못했다"), file=sys.stderr)
            return 1
        if result.get("stopped", True):
            print("마쳤다")
            for line in _busy_lines(result.get("cut") or {}):
                print("끊긴 것: " + line)
            return 0
        summary = " · ".join(_busy_lines(result.get("busy") or {}))
        if not waiting:
            print("기다린다 — " + summary, flush=True)
            waiting = True
        left = end - time.monotonic()
        if left <= 0:
            break
        time.sleep(min(SHUTDOWN_WAIT_STEP, left))
    print(f"끄지 않았다 — {minutes:g}분 안에 비지 않았다: {summary}", file=sys.stderr)
    return 1


def _remote(args, paths):
    if args.command == "job" and args.action == "run":
        print("오류: 이 명령은 총괄 세션이 쓴다", file=sys.stderr)
        return 1
    if args.command in ("ask", "show"):
        print("오류: 이 명령은 총괄 세션만 쓴다 — 사람은 `python -m gaf say` 로 총괄에게 말한다", file=sys.stderr)
        return 1
    record = read_server(paths)
    if record is None:
        print("지휘자가 떠 있지 않다 — python -m gaf serve", file=sys.stderr)
        return 1
    if not record.get("key"):
        print(f"오류: 떠 있는 지휘자(pid {record.get('pid')})의 접근 값 파일을 읽지 못했다 — 지휘자를 띄운 계정으로 친다", file=sys.stderr)
        return 1
    command = args.command
    if command == "shutdown":
        return _shutdown(record, args.wait)
    if command == "inbox":
        return _inbox(record, paths, args)
    if command in ("team", "tell", "send", "result", "job", "commit", "push", "custom"):  # 사람이 친 총괄 명령(보낸 이 없음)
        if command == "result" and args.wait is not None:
            waited = _wait_task(record, args.task, args.wait)
            if waited is not None:
                return waited
        name, data = _tool_request(args)
        return _show_tool(_call(record, "/api/tool", {"name": name, "args": data}, TOOL_TIMEOUT))
    if command == "say":
        if len(args.image) > 4:
            raise ValueError("그림은 4장까지 보낸다")
        images = []
        for path in args.image:
            if path.suffix.lower() not in IMAGE_TYPES or path.stat().st_size > IMAGE_LIMIT:
                raise ValueError("그림 확장자와 장당 8MB 한도를 확인한다")
            images.append({"name": path.name, "data": base64.b64encode(path.read_bytes()).decode("ascii")})
        result = _call(record, "/api/say", {"text": args.text, "images": images})
    elif command == "assign":
        _, payload = _tool_request(args)
        result = _call(record, "/api/assign", payload)
    elif command == "role":
        if args.action == "list":
            result = _call(record, "/api/state")
            if result.get("ok") is not False:
                _table(result["roles"])
                return 0
        else:
            data = {"action": args.action, "name": args.name}
            if args.action == "new":
                data["duty"] = args.duty or ""
                if args.template is not None:
                    data["template"] = args.template
                data.update({key: getattr(args, key) for key in ("engine", "model", "effort")
                             if getattr(args, key) is not None})
                if args.write is not None:
                    data["write"] = [value.strip() for value in args.write.split(",") if value.strip()]
            result = _call(record, "/api/role", data)
    else:
        result = _call(record, "/api/" + command, {"role": args.role} if command == "stop" else {})
    if result.get("ok") is False:
        print(str(result.get("error") or "요청을 마치지 못했다"), file=sys.stderr)
        return 1
    print("마쳤다" + (f" · {result['task']}" if result.get("task") else ""))
    return 0


def _projects(args):
    if args.action == "forget":
        if projects.forget(args.folder):
            print(f"목록에서 뺐다: {args.folder} (프로젝트 폴더는 그대로다)")
            return 0
        print(f"목록에 없는 프로젝트다: {args.folder}", file=sys.stderr)
        return 1
    print(projects.table(projects.listing()))
    return 0


def _public_access(args, paths):
    record = read_server(paths)
    if record is not None:
        if not record.get("key"):
            print("지휘자의 접근 값을 읽지 못했다 — 이 PC에서 지휘자를 띄운 사용자로 다시 한다", file=sys.stderr)
            return 1
        data = {"action": args.action}
        if args.action == "on" and args.funnel_port is not None:
            data["funnel_port"] = args.funnel_port
        result = _call(record, "/api/remote", data, timeout=2 * tailnet.TIMEOUT + 10)
    elif args.action == "on":
        result = remote_ops.public_on(paths, funnel_port=args.funnel_port, find=tailnet.find)
    else:
        result = remote_ops.public_off(paths, find=tailnet.find)
    ok = result.get("ok") is True
    for line in result.get("lines") or [result.get("error") or "요청을 마치지 못했다"]:
        print(line, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def _remote_on(args, paths):
    if args.public:
        return _public_access(args, paths)
    if any(row["public"] for row in projects.entries()):
        print(remote_ops.MIXED_ROUTES, file=sys.stderr)
        return 1
    if not paths.config.is_file():
        print(f"gaf 프로젝트가 아니다: {paths.root} — 먼저 python -m gaf init --root <폴더>", file=sys.stderr)
        return 1
    config = load_config(paths)
    exe, name = None, ""
    if args.host:                                   # 다른 앞단: 이름만 적는다(앞단은 사람이 건다)
        hosts, users = list(args.host), list(args.user)
    else:
        exe = tailnet.find()
        if exe is None:
            print("Tailscale 을 찾지 못했다 — Tailscale 을 깔고 로그인한 뒤 다시 하거나, 다른 앞단을 쓰면 --host <이름> 을 준다",
                  file=sys.stderr)
            return 1
        info, error = tailnet.info(exe)
        if info is None:
            print(error, file=sys.stderr)
            return 1
        name, hosts = info["name"], list(info["hosts"])
        users = [*([info["user"]] if info["user"] else []), *args.user]
    projects.remember(paths.root, config.project)       # 목록에 있어야 포트를 적는다
    port = config.server["port"] or projects.port_of(paths.root) or _fixed_port(paths.root)
    serve_port = args.serve_port or port
    projects.set_port(paths.root, port)
    # 앞에 우리가 Tailscale 에 걸어 둔 넘김이 이번 것과 다르면 끈다. 우리가 걸지 않은 넘김(--host)은 건드리지 않는다.
    old_port, old_https = projects.serve_of(paths.root)
    ours = projects.serve_by_tailscale(paths.root)
    if ours and (exe is None or (old_port, old_https) != (serve_port, args.https)):
        old_exe = exe or tailnet.find()
        if old_exe is not None:
            tailnet.serve(old_exe, port, old_port, https=old_https, off=True)
    if exe is not None:
        opened, said = tailnet.serve(exe, port, serve_port, https=args.https)
        if not opened:
            print("사설망에 열지 못했다 — " + (said or "tailscale serve 가 실패했다"), file=sys.stderr)
            return 1
    projects.set_serve(paths.root, serve_port, args.https, tailscale=exe is not None)
    now = remote.read_settings()
    header = now["identity_header"] if args.identity_header is None else args.identity_header
    saved = remote.enable(list(dict.fromkeys([*now["hosts"], *hosts])),
                          users=list(dict.fromkeys([*now["users"], *users])), identity_header=header)
    print("원격 접속을 켰다 — 지휘자는 여전히 이 PC 안(127.0.0.1)에만 듣고, 사설망 앞단을 거쳐 온 등록한 기기만 들인다")
    if exe is not None:
        print("주소: " + tailnet.address(name, serve_port, https=args.https))
    else:
        print(f"앞단(프록시)이 127.0.0.1:{port} 로 넘기게 건다 — 들이는 이름: " + ", ".join(saved["hosts"]))
    print("들이는 사람: " + (", ".join(saved["users"]) or "가리지 않음"))
    record = read_server(paths)
    if record is not None and record.get("port") != port:
        print(f"지금 지휘자는 다른 포트({record.get('port')})에 떠 있다 — python -m gaf shutdown 뒤 python -m gaf open 으로 다시 띄워야 통한다")
    elif record is None:
        print("다음: ① 지휘자를 띄운다 — python -m gaf open")
    print("      ② 폰 · 다른 PC에서 위 주소를 연다 ③ PC 화면의 「설정 → 원격 기기 → 기기 등록」이나 python -m gaf pair 가 보여 주는 코드를 넣는다")
    return 0


def _remote_access(args, paths):
    action = args.action
    if action == "on":
        return _remote_on(args, paths)
    if action == "off":
        if any(row["public"] for row in projects.entries()):       # 공개 길은 PC 하나의 문 — 어느 프로젝트에서 꺼도 같다
            return _public_access(args, paths)
        serve_port, https = projects.serve_of(paths.root)
        if serve_port:
            # 우리가 Tailscale 에 건 넘김만 끈다 — 이름만 적어 켠 것(--host)은 사람이 건 앞단이라 Tailscale 을 부르지 않는다
            exe = tailnet.find() if projects.serve_by_tailscale(paths.root) else None
            if exe is not None:
                closed, said = tailnet.serve(exe, projects.port_of(paths.root), serve_port, https=https, off=True)
                if not closed:
                    print("사설망의 넘김을 끄지 못했다(앞단을 손으로 걸었으면 손으로 끈다) — " + said, file=sys.stderr)
            projects.set_serve(paths.root, 0)
        left = sum(1 for entry in projects.entries() if entry["serve"])
        if left:
            print(f"이 프로젝트를 원격에서 닫았다 — 다른 프로젝트 {left}개는 열려 있다")
        else:
            remote.disable()
            print("원격 접속을 껐다(등록한 기기 목록은 남는다 — 지우려면 python -m gaf remote forget all)")
        return 0
    door = remote.Remote()
    if action == "devices":
        rows = door.devices()
        print("기기\t이름\t사람\t등록\t마지막 접속" if rows else "등록한 기기가 없다")
        for row in rows:
            print("\t".join(str(row[key] or "—") for key in ("id", "name", "user", "created_at", "seen_at")))
        return 0
    if action == "forget":
        if args.device == "all":
            count = sum(1 for row in door.devices() if door.forget(row["id"]))
            print(f"기기 {count}대를 끊었다")
            return 0
        if door.forget(args.device):
            print(f"끊었다: {args.device}")
            return 0
        print(f"없는 기기다: {args.device} — python -m gaf remote devices 로 본다", file=sys.stderr)
        return 1
    settings = remote.read_settings()
    if not settings["hosts"]:
        print("원격 접속: 꺼짐 — 켜려면 python -m gaf remote on")
        return 0
    public = any(row["public"] for row in projects.entries())
    address = remote_ops.address_of(paths)
    print("원격 접속: 켜짐(공개 길 — 인터넷에서 열린다 · 등록한 기기만)" if public
          else "원격 접속: 켜짐(사설망 앞단을 거쳐 온 등록한 기기만)")
    if public:
        print("주소(이 PC 에 켜져 있는 프로젝트를 모두 이 주소 하나로 연다): " + address)
    else:
        print("이 프로젝트의 주소: " + (address or "열지 않았다 — python -m gaf remote on"))
    print("들이는 사람: " + (", ".join(settings["users"]) or "가리지 않음"))
    print(f"등록한 기기 {len(door.devices())}대 — python -m gaf remote devices")
    return 0


def _pair(paths):
    record = read_server(paths)
    if record is None or not record.get("key"):
        print("지휘자가 떠 있지 않다 — python -m gaf open", file=sys.stderr)
        return 1
    result = _call(record, "/api/pair/start", {})
    if result.get("ok") is not True:
        print(str(result.get("error") or "요청을 마치지 못했다"), file=sys.stderr)
        return 1
    print(f"등록 코드: {result['code']} — {result['seconds'] // 60}분 안에 폰 화면에 넣는다(다섯 번 틀리면 다시 받는다)")
    return 0


def _voice(action="status", *, run=subprocess.run):
    from . import dictation

    if action == "status":
        info = dictation.status()
        for key, label in (("installed", "음성 인식"), ("model", "모델"), ("gpu_libs", "그래픽 카드용 라이브러리")):
            print(f"{label}: {'있음' if info[key] else '없음'}")
        print(f"폴더: {info['dir']}")
        if info["real"] != info["dir"]:
            print(f"실제 위치: {info['real']}")
        return 0
    # 판을 못 박은 목록으로 깐다 — 딸린 꾸러미의 판이 달라지면 풀기 · 받아쓰기가 죽는다(11-16: faster-whisper 와 PyAV).
    base = Path(__file__).resolve().parent.parent
    lists = [base / "requirements-voice.txt"]
    print("음성 인식 꾸러미를 깐다(약 0.3GB)", flush=True)
    if shutil.which("nvidia-smi"):
        lists.append(base / "requirements-voice-gpu.txt")
        print("그래픽 카드용 라이브러리도 깐다(약 2GB)", flush=True)
    missing = [item.name for item in lists if not item.is_file()]
    if missing:
        print("꾸러미 목록이 없다: " + " · ".join(missing), file=sys.stderr)
        return 1
    print("large-v3-turbo 모델을 받는다(약 1.6GB)", flush=True)
    install = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    for item in lists:
        install += ["-r", str(item)]
    download = ("from faster_whisper import download_model; "
                f"download_model({dictation.MODEL!r}, cache_dir={str(dictation.model_dir())!r})")
    for stage, command in (("꾸러미 설치", install),
                           ("모델 받기", [sys.executable, "-X", "utf8", "-c", download])):
        try:
            result = run(command)
        except OSError:
            print(f"{stage}를 시작하지 못했다", file=sys.stderr)
            return 1
        if result.returncode:
            print(f"{stage} 실패 — 종료 코드 {result.returncode}", file=sys.stderr)
            return 1
    info = dictation.status()
    if not info["installed"] or not info["model"]:
        print("음성 인식 준비를 마치지 못했다", file=sys.stderr)
        return 1
    print(f"음성 인식 준비를 마쳤다 — {'그래픽 카드로' if info['gpu_libs'] else 'CPU로'} 받아 적는다")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream, encoding in ((sys.stdout, "utf-8"), (sys.stderr, "utf-8"), (sys.stdin, "utf-8-sig")):
        try:
            stream.reconfigure(encoding=encoding)
        except (AttributeError, OSError, ValueError):
            pass
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "assign":
            for field, parent, given in (
                    ("check_timeout_min", "check", args.check_timeout_min is not None),
                    ("check_shell", "check", bool(args.check_shell)),
                    ("review_files", "review", args.review_files is not None),
                    ("review_brief", "review", args.review_brief is not None),
                    ("review_keep_going", "review", args.review_keep_going)):
                if given and not getattr(args, parent):
                    parser.usage_error(f"--{field.replace('_', '-')} 은 --{parent} 과 함께 쓴다")
        # 둘 다 안 적은 것만 쓰는 법 오류(종료 2)다. `--duty "  "` 처럼 빈 글을 적은 것은 전처럼 지휘자가 거절한다(종료 1).
        if args.command == "role" and args.action == "new" and args.duty is None and not args.template:
            parser.usage_error("--duty 또는 --from 가운데 하나는 꼭 적는다")
        if args.command == "serve" and args.port is not None and not 0 <= args.port <= 65535:
            parser.usage_error("포트는 0~65535여야 한다")
        if args.command == "remote" and args.serve_port is not None and not 1 <= args.serve_port <= 65535:
            parser.usage_error("포트는 1~65535여야 한다")
        if args.command == "remote":
            if args.public and (args.host or args.https or args.serve_port is not None or args.user or args.identity_header is not None):
                parser.usage_error("--public 은 사설망 선택사항과 같이 쓰지 않는다")
            if args.funnel_port is not None and (not args.public or args.funnel_port not in tailnet.FUNNEL_PORTS):
                parser.usage_error("포트는 --public 과 함께 443 · 8443 · 10000 중 하나를 쓴다")
        if ((args.command == "projects" and args.action == "forget" and args.folder is None)
                or (args.command == "remote" and args.action == "forget" and not args.device)):
            parser.usage_error("무엇을 뺄지 적는다")
    except SystemExit as exit_status:
        return int(exit_status.code)
    try:
        done = _session(args)                # 세션 안에서 친 것이면 여기서 끝난다(접근 값 파일을 읽지 않는다)
        if done is not None:
            return done
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, urllib.error.URLError):
        print("오류: 지휘자에 연결하지 못했다 — 잠시 뒤 다시 한다", file=sys.stderr)
        return 1
    if args.command == "open" and args.last:
        # 공용 아이콘(1.8.0): 작업 폴더(프로그램을 깐 폴더)에 gaf.json 이 있어도 보지 않고 마지막에 쓰던 프로젝트를 연다.
        if args.root is not None:
            print("오류: --last 는 --root 와 같이 쓰지 않는다", file=sys.stderr)
            return 1
        recent = projects.last()
        if recent is None:
            if sys.stderr is None:          # 창 없는 실행(pythonw)에는 글을 찍을 곳이 없다
                desktop.message_box(desktop.APP_NAME, "열 프로젝트가 없다 — 설치.cmd 를 다시 더블클릭해 게임 폴더를 고른다")
            else:
                print("열 프로젝트가 없다 — python -m gaf setup 으로 게임 폴더를 고른다", file=sys.stderr)
            return 1
        args.root = recent
    paths = Paths(args.root.resolve() if args.root is not None else find_root())
    if args.command == "open" and args.root is None and not paths.config.is_file():
        recent = projects.last()                  # 프로젝트 밖에서 쳤다 — 마지막에 연 프로젝트를 연다
        if recent is None:
            print("열 프로젝트가 없다 — python -m gaf init --root <폴더> 로 프로젝트를 만든다", file=sys.stderr)
            return 1
        paths = Paths(recent)
    try:
        if args.command == "shutdown" and args.wait is not None:
            if not math.isfinite(args.wait * 60) or args.wait <= 0:
                raise InputError("오류: --wait는 0보다 큰 분이어야 한다(유한한 수)")
        if args.command == "library":
            if args.action == "list":
                print(library.list_text(args.kind, engine=args.engine, find=args.find))
                return 0
            if args.action == "show":
                print(library.show_text(args.item))
                return 0
            failed = False
            for item in args.items:
                try:
                    print(library.take(paths.root, *library.split_item(item), to=args.to))
                except library.LibraryError as exc:
                    print(f"오류: {exc}")
                    failed = True
            return int(failed)
        if args.command == "voice":
            return _voice(args.action)
        if args.command == "rea":
            return _rea(args)
        if args.command == "setup":
            return firstrun.setup(args.root, no_shortcut=args.no_shortcut, no_open=args.no_open, codex=args.codex, ask=args.ask)
        if args.command == "open":
            return launch.open_project(paths.root, browser=None if args.no_browser else launch.webbrowser.open, app=args.app)
        if args.command == "shortcut":
            return _shortcut(paths, args.dir, args.name, last=args.last)
        if args.command == "doctor":
            return doctor.main(paths if paths.config.is_file() else None)
        if args.command == "hooks":
            return _hooks(paths, args.action)
        if args.command == "outside":
            return _outside(paths, args)
        if args.command == "check":
            return _checks(paths, args)
        if args.command == "review":
            return _reviews(paths, args)
        if args.command == "research":
            return _research(paths, args)
        if args.command == "projects":
            return _projects(args)
        if args.command == "remote":
            return _remote_access(args, paths)
        if args.command == "pair":
            return _pair(paths)
        if args.command == "serve":
            return asyncio.run(_serve(paths, args.port))
        if args.command == "status":
            return _status(paths)
        if args.command == "init":
            return _init(paths)
        if args.command == "wiki":
            return _wiki_init(paths)
        return _remote(args, paths)
    except KeyboardInterrupt:
        return 0
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 1
    except ValueError as error:              # 까닭을 같이 보인다(11-12 QA 결함-18) — 깨진 gaf.json 의 어느 키인지 등
        print(f"설정이나 요청 값이 잘못됐다 — {error}", file=sys.stderr)
        return 1
    except (OSError, urllib.error.URLError) as error:
        print(f"파일을 읽거나 지휘자에 연결하지 못했다 — {error}", file=sys.stderr)
        return 1
