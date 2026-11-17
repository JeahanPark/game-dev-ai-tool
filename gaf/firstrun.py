"""처음 한 번 하는 준비. 바깥 손을 인자로 받아 실제 로그인 없이 시험한다."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

from . import desktop, doctor, launch, paths as user_paths
from .paths import Paths, _atomic_json, _read_json


def choose_project(*, input=input):
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        tkinter = None
    if tkinter is not None:
        try:
            window = tkinter.Tk()
            try:
                window.withdraw()
                return filedialog.askdirectory(parent=window, title="게임 프로젝트 폴더를 고른다")
            finally:
                window.destroy()
        except (tkinter.TclError, RuntimeError):
            pass                 # 설치됐어도 화면을 쓸 수 없는 PC에서는 글로 받는다
    try:
        return input("게임 프로젝트 폴더의 경로를 넣는다: ").strip().strip('"')
    except (EOFError, KeyboardInterrupt):
        return ""


def _skip(name, reason, fix):
    print(f"{name} 건너뜀 — {reason} → {fix}")
    return f"건너뜀({reason})"


def _install(run, command) -> bool:
    # 실패한 명령의 원문에는 계정·경로가 섞일 수 있어 고정된 안내만 보여 준다.
    try:
        done = run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        return done.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _codex(*, run, which, find_spec):
    retry = "인터넷 연결을 확인하고 설치.cmd를 다시 연다"
    if find_spec("openai_codex") is None:
        print("GPT에 필요한 부품을 넣는다. 잠시 기다린다")
        requirements = Path(__file__).resolve().parent.parent / "requirements-codex.txt"
        if not _install(run, [sys.executable, "-m", "pip", "install", "-r", str(requirements)]):
            return _skip("GPT", "부품 설치 실패", retry)
        importlib.invalidate_caches()
    cli = doctor._codex_cli(None, which)
    if cli is None:
        winget = which("winget")
        if not winget:
            return _skip("GPT", "winget이 없다", "https://github.com/openai/codex 에서 Codex를 받고 설치.cmd를 다시 연다")
        print("GPT를 연결하는 Codex를 설치한다. 잠시 기다린다")
        if not _install(run, [winget, "install", "-e", "--id", "OpenAI.Codex",
                              "--accept-package-agreements", "--accept-source-agreements"]):
            return _skip("GPT", "Codex 설치 실패", "https://github.com/openai/codex 에서 설치하고 설치.cmd를 다시 연다")
        cli = doctor._codex_cli(None, which)
        if cli is None:
            return _skip("GPT", "Codex를 찾지 못했다", "설치.cmd를 닫았다가 다시 연다 · https://github.com/openai/codex")
    check = doctor._codex_login(run, cli)
    if check.status != "ok":
        print("브라우저가 열리면 ChatGPT 구독 계정으로 로그인한다. 끝나면 이 창으로 돌아온다")
        try:
            run([cli, "login"])       # 이 콘솔의 입력·출력을 그대로 쓴다.
        except (OSError, subprocess.TimeoutExpired):
            pass
        check = doctor._codex_login(run, cli)
    if check.status != "ok":
        return _skip("GPT", "로그인 확인 실패", "codex login 으로 로그인한 뒤 설치.cmd를 다시 연다")
    return "✓"


def _obsidian(*, run, which):
    if doctor._obsidian_installed(run, which):
        return "✓"
    winget = which("winget")
    if not winget:
        return _skip("옵시디언", "winget이 없다", "https://obsidian.md 에서 앱을 받는다")
    print("옵시디언을 설치한다. 잠시 기다린다")
    if not _install(run, [winget, "install", "-e", "--id", "Obsidian.Obsidian", "--scope", "user",
                          "--accept-package-agreements", "--accept-source-agreements"]):
        return _skip("옵시디언", "설치 실패", "https://obsidian.md 에서 앱을 받거나 설치.cmd를 다시 연다")
    return "✓"


def _answer(saved, key, prompt, input):
    if type(saved.get(key)) is bool:
        return saved[key]
    if sys.stdin is None or not sys.stdin.isatty():
        return False
    try:
        return input(prompt).strip().casefold() in {"y", "yes", "예", "네"}
    except (EOFError, OSError, KeyboardInterrupt):
        return False


STRONG_PLANS = {"max", "ultra", "enterprise"}     # `claude auth status` 의 subscriptionType(11-18 이 PC: "max")


def lead_defaults(plan) -> dict:
    """새 프로젝트 총괄의 모델 · 세기. 구독이 Max 이상이면 틀의 기본(Opus 5.5 · max — 사용자 11-17 「나는 오푸스 5.5로할꼐」),
    그 아래(Pro · 모름 · API 키)면 계정의 기본값(빈 값) — 지인 배포(1.3.0)에서 Pro 구독이 Opus · max 로 사용량을 빨리 다 쓰지 않게."""
    return {} if isinstance(plan, str) and plan.strip().lower() in STRONG_PLANS else {"lead_model": "", "lead_effort": ""}


def setup(root=None, *, no_shortcut=False, no_open=False, codex=False, ask=False,
          run=subprocess.run, input=input, choose_folder=None, shortcut=None, opener=None, init=None,
          find_spec=importlib.util.find_spec, which=shutil.which) -> int:
    print("[1/7] AI를 쓰는 부품을 확인한다")
    spec = find_spec("claude_agent_sdk")
    if spec is None:
        print("설치가 덜 됐다 — 설치 파일(설치.cmd)을 다시 더블클릭한다")
        return 1

    print("[2/7] Claude 로그인을 확인한다")
    cli = None
    def status_run(command, **options):
        nonlocal cli
        cli = command[0]           # doctor가 실제로 고른 CLI로 로그인한다(번들 우선, PATH 다음)
        return run(command, **options)

    check = doctor._claude_login(status_run, which, spec)
    if check.status != "ok":
        if cli is not None:
            print("브라우저가 열리면 Claude 구독 계정으로 로그인한다. 끝나면 이 창으로 돌아온다")
            try:
                run([cli, "auth", "login"])     # 이 콘솔에 붙인다. 출력·입력을 가로채지 않는다.
            except OSError:
                pass
            check = doctor._claude_login(status_run, which, spec)
        if check.status != "ok":
            print(check.detail)
            print("로그인이 안 됐다 — 설치 파일을 다시 더블클릭하면 여기부터 이어서 한다")
            return 1

    saved = {} if ask else (_read_json(user_paths.user_dir() / "setup.json") or {})
    print("[3/7] GPT를 준비한다(선택)")
    choices = {"codex": codex or _answer(saved, "codex", "GPT(ChatGPT 구독)도 쓸까요? [y/N] ", input)}
    gpt_result = _codex(run=run, which=which, find_spec=find_spec) if choices["codex"] else "건너뜀(선택하지 않음)"
    print("[4/7] 옵시디언을 준비한다(선택)")
    choices["obsidian"] = _answer(saved, "obsidian", "옵시디언(위키를 보는 무료 앱)도 깔까요? [y/N] ", input)
    try:
        _atomic_json(user_paths.user_dir() / "setup.json", choices, patient=False)
    except OSError:
        print("고른 답을 저장하지 못했다 — 다음에 다시 고른다")
    print("다시 고르려면 `설치.cmd ask`를 연다")
    obsidian_result = _obsidian(run=run, which=which) if choices["obsidian"] else "건너뜀(선택하지 않음)"

    print("[5/7] 게임 프로젝트 폴더를 고른다")
    folder = root if root is not None else (choose_folder() if choose_folder is not None else choose_project(input=input))
    if folder is None or not str(folder).strip():
        print("폴더를 고르지 않았다")
        return 1
    project = Path(folder).expanduser().resolve()
    if project == Path(project.anchor):
        print("드라이브 뿌리는 프로젝트로 쓰지 않는다 — 게임 폴더를 고른다")
        return 1
    if project == Path.home().resolve():
        print("사용자 집 폴더는 프로젝트로 쓰지 않는다 — 그 안의 게임 폴더를 고른다")
        return 1
    if not project.is_dir():
        print("없는 폴더이거나 폴더가 아니다 — 이미 있는 게임 폴더를 고른다")
        return 1

    print("[6/7] 프로젝트의 첫 파일을 준비한다")
    paths = Paths(project)
    if paths.config.exists():
        print("이미 준비된 프로젝트다")
    else:
        if init is None:
            from .cli import _init

            init = _init
        lead = lead_defaults(getattr(check, "plan", ""))
        if lead:
            print("총괄은 Claude 계정의 기본 모델로 둔다(구독이 Max 아래면 Opus · 최대 세기가 사용량을 빨리 쓴다 — 역할 글에서 바꿀 수 있다)")
        else:
            print("총괄은 Claude Opus 5.5 · 최대 세기로 둔다")
        if (init(paths, **lead) if lead else init(paths)) != 0:
            print("프로젝트의 첫 파일을 만들지 못했다")
            return 1

    if choices["obsidian"]:
        print(f"옵시디언에서 『폴더를 보관함으로 열기』로 {project / 'wiki'} 를 연다")

    print("[7/7] 바로 가기를 만들고 프로그램을 켠다")
    if no_shortcut:
        print("바로 가기 만들기는 건너뛴다")
    else:
        if shortcut is None:
            from .cli import _shortcut

            shortcut = lambda root: _shortcut(Paths(root))
        if shortcut(project) != 0:
            return 1
    if no_open:
        print("프로그램 켜기는 건너뛴다")
    elif (opener or launch.open_project)(project, app=True) != 0:
        return 1
    name = desktop.shortcut_name(project)
    print(f"\n준비 요약\nClaude ✓ · GPT {gpt_result} · 옵시디언 {obsidian_result}\n"
          f"프로젝트 폴더: {project}\n바탕화면 아이콘: " + ("건너뜀" if no_shortcut else name))
    next_time = (f"다음부터는 바탕화면의 「{name}」 을 더블클릭한다." if not no_shortcut else
                 f'다음부터는 python -m gaf open --app --root "{project}" 로 켠다.')
    print(f"끝났다. {next_time} 창을 닫아도 AI 는 뒤에서 계속 일한다 — 다 끄려면 화면의 설정 → 프로그램 끄기.")
    return 0
