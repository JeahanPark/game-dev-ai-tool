"""준비 점검 — 이 PC에서 프레임워크를 돌릴 준비가 됐는지 본다.

바깥 일(명령 돌리기 · 프로그램 찾기 · 모듈 찾기 · 판 읽기)은 모두 인자로 받아, 시험이 진짜 도구 없이 돌 수 있다.
계정 정보(메일 · 조직 · 토큰)는 읽어도 화면에 싣지 않는다 — 로그인 여부와 구독 종류만 쓴다.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import hooks
from .config import ConfigError, load_config
from .engines.codex import find_codex
from .paths import Paths, run_dir
from .roles import RoleError, has_roles, load_roles

CLI_TIMEOUT = 30            # 로그인 확인 명령 하나를 기다리는 시간(초)
VERIFIED_PYTHON = (3, 13)   # 돌려 본 파이썬 판 — 이보다 낮은 판은 안 된다고 하지 않고 「확인하지 않았다」고만 한다


@dataclass(frozen=True)
class Check:
    name: str       # 짧은 이름
    status: str     # "ok" | "fail" | "skip" (skip = 쓰지 않아서 보지 않았거나, 알려 주기만 하는 줄)
    detail: str     # 본 것 한 줄
    fix: str = ""   # fail 일 때 고치는 법 한 줄
    plan: str = ""  # Claude 로그인이 알린 구독(subscriptionType — 예 max · pro) · 첫 설치가 총괄의 기본값을 고를 때 쓴다(1.3.0)


def _python(python) -> Check:
    version = tuple(python if python is not None else sys.version_info)[:3]
    text = ".".join(str(part) for part in version)
    if version[:2] >= VERIFIED_PYTHON:
        return Check("파이썬", "ok", text)
    return Check("파이썬", "skip", f"{text} — 3.13에서만 확인했다")


def _version(version, package: str, found: str) -> str:
    try:
        return f"{package} {version(package)}"
    except Exception:  # noqa: BLE001 — 판을 못 읽어도 깔려 있는 건 맞다
        return f"{package} {found}"


def _login_command(run, command: list[str]):
    """로그인 확인 명령을 돌린다 → (결과, 돌리지 못했는지). 출력은 부르는 쪽이 필요한 만큼만 쓴다."""
    try:
        done = run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=CLI_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None, True
    return done, False


def _claude_login(run, which, spec) -> Check:
    name = "Claude 로그인"
    cli = None
    origin = getattr(spec, "origin", None)
    if origin:
        bundled = Path(origin).parent / "_bundled" / ("claude.exe" if os.name == "nt" else "claude")
        if bundled.is_file():
            cli = str(bundled)
    cli = cli or which("claude")
    if not cli:
        return Check(name, "fail", "SDK 안의 claude CLI 도 PATH 의 claude 도 찾지 못했다",
                     "`.venv`에 claude-agent-sdk 를 다시 넣는다(0.2.180에서 확인)")
    done, failed = _login_command(run, [cli, "auth", "status"])
    if failed or done is None:
        return Check(name, "fail", "CLI 를 돌리지 못했다", "Claude CLI 가 열리는지 터미널에서 `claude auth status` 로 본다")
    try:
        data = json.loads(done.stdout or "")
    except ValueError:
        data = None
    if isinstance(data, dict):
        if data.get("loggedIn") is True:
            plan = data.get("subscriptionType")
            plan = plan if isinstance(plan, str) else ""
            return Check(name, "ok", "로그인됨" + (f" · 구독 {plan}" if plan else ""), plan=plan)
        return Check(name, "fail", "로그인되어 있지 않다", "터미널에서 `claude` 를 열고 `/login` 을 한다")
    if done.returncode == 0:
        return Check(name, "ok", "로그인됨")
    return Check(name, "fail", "로그인되어 있지 않다", "터미널에서 `claude` 를 열고 `/login` 을 한다")


def _codex_used(paths: Paths | None) -> bool:
    if paths is None:
        return False
    try:
        config = load_config(paths)
        if config.defaults.get("engine") == "codex":
            return True
        return any(role.engine == "codex" for role in load_roles(paths, config).values())
    except (ConfigError, RoleError, OSError, ValueError):
        return False


def _codex_cli(paths: Paths | None, which) -> str | None:
    configured = ""
    if paths is not None:
        try:
            configured = str(load_config(paths).codex.get("bin") or "")
        except (ConfigError, OSError, ValueError):
            configured = ""
    if configured:
        return configured if Path(configured).is_file() else None
    cli = find_codex(which)
    if cli:
        return cli
    # 방금 winget으로 깔았으면 이 콘솔의 PATH에는 아직 없을 수 있다.
    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = Path(local) / "Microsoft/WinGet"
        link = winget / "Links/codex.exe"
        if link.is_file():
            return str(link)
        for binary in sorted((winget / "Packages").glob("OpenAI.Codex_*/**/codex.exe")):
            if binary.is_file():
                return str(binary)
    return None


def _obsidian_installed(run, which) -> bool:
    """흔한 설치 자리와 winget 목록으로 옵시디언 설치 여부를 본다."""
    for key, relative in (("LOCALAPPDATA", "Obsidian/Obsidian.exe"),
                          ("LOCALAPPDATA", "Programs/Obsidian/Obsidian.exe"),
                          ("ProgramFiles", "Obsidian/Obsidian.exe"),
                          ("ProgramFiles(x86)", "Obsidian/Obsidian.exe")):
        folder = os.environ.get(key)
        if folder and (Path(folder) / relative).is_file():
            return True
    if which("obsidian"):
        return True
    winget = which("winget")
    if not winget:
        return False
    done, failed = _login_command(run, [winget, "list", "--id", "Obsidian.Obsidian", "--exact",
                                        "--accept-source-agreements", "--disable-interactivity"])
    return bool(not failed and done is not None and done.returncode == 0
                and "obsidian.obsidian" in (done.stdout or "").lower())


def _codex_engine(paths, which, find_spec, version) -> tuple[Check, str | None]:
    name = "Codex 엔진"
    if paths is not None:
        try:
            configured = load_config(paths).codex.get("bin")
            if configured and not Path(configured).is_file():
                return Check(name, "fail", "codex.bin 경로에 실행 파일이 없다", "gaf.json 의 codex.bin 경로를 확인한다"), None
        except (ConfigError, OSError, ValueError):
            pass
    sdk = find_spec("openai_codex") is not None
    cli = _codex_cli(paths, which)
    if sdk and cli:
        return Check(name, "ok", _version(version, "openai-codex", "있음") + " · CLI 있음"), cli
    if not _codex_used(paths):
        return Check(name, "skip", "쓰는 역할이 없고 깔려 있지 않다"), None
    missing = " · ".join(part for part, there in (("SDK(openai-codex)", sdk), ("CLI", bool(cli))) if not there)
    return Check(name, "fail", f"{missing} 없음", "`.venv`에 openai-codex 를 넣고 Codex CLI 를 깐다(쓰는 역할이 있다)"), None


def _codex_login(run, cli: str | None) -> Check:
    name = "Codex 로그인"
    if cli is None:
        return Check(name, "skip", "엔진이 없어 보지 않았다")
    done, failed = _login_command(run, [cli, "login", "status"])
    if failed or done is None:
        return Check(name, "fail", "CLI 를 돌리지 못했다", "Codex CLI 가 열리는지 터미널에서 `codex login status` 로 본다")
    if done.returncode == 0:
        return Check(name, "ok", "로그인됨")      # 출력에는 계정 정보가 들어 있을 수 있어 싣지 않는다
    return Check(name, "fail", "로그인되어 있지 않다", "터미널에서 `codex login` 을 한 번 한다")


def _project(paths: Paths) -> Check:
    name = "프로젝트"
    try:
        config = load_config(paths)
    except ConfigError as exc:
        return Check(name, "fail", str(exc), "gaf.json 을 고친다")
    lead = f"team/{config.lead_role}/역할.md"
    # 총괄 역할 없이 다른 역할만 둔 프로젝트도 돈다 — 일은 밖(사람 · 다른 도구의 세션)에서 `gaf assign` 으로 맡기고 `gaf result` 로
    # 받는다(11-15 진행 중인 프로젝트에 역할 하나만 붙여 본 꼴). 그때는 총괄 역할 글을 없다고 하지 않고, init 도 권하지 않는다.
    no_lead = not (paths.root / lead).is_file() and has_roles(paths)
    wanted = (("gaf.json", paths.config), (lead, paths.root / lead), ("team/공통.md", paths.team / "공통.md"))
    missing = [label for label, path in wanted if not path.is_file() and not (no_lead and label == lead)]
    if missing:
        fix = (f"없는 파일을 손으로 만든다(`python -m gaf init` 은 {lead} 도 만든다)" if no_lead
               else "python -m gaf init --root <폴더>")
        return Check(name, "fail", " · ".join(missing) + " 없음", fix)
    if no_lead:
        return Check(name, "ok", f"gaf.json · team/공통.md 있음 · 총괄 역할({lead}) 없음 — 일은 밖에서 `gaf assign` 으로 맡긴다")
    return Check(name, "ok", f"gaf.json · {lead} · team/공통.md 있음")


def _run_dir() -> Check:
    name = "접근 값 폴더"
    folder = run_dir()
    probe = folder / f".doctor-{uuid.uuid4().hex}.tmp"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return Check(name, "fail", "쓰지 못했다", f"그 폴더를 쓸 수 있게 한다: {folder}")
    return Check(name, "ok", "쓸 수 있다")


def _hooks(paths: Paths) -> Check:
    name = "일 앞뒤 명령 · 기본 검사"
    try:
        config = load_config(paths)
        commands = config.hooks
    except ConfigError:
        return Check(name, "skip", "설정 오류로 보지 못했다")
    fingerprint = hooks.digest(commands, config.checks, config.reviews)
    if not fingerprint:
        return Check(name, "skip", "없다")
    count = sum(len(commands.get(key, [])) for key in ("before_work", "after_work")) + len(config.checks) + len(config.reviews)
    if hooks.trusted(paths.root) == fingerprint:
        return Check(name, "ok", f"{count}개 · 믿음")
    return Check(name, "fail", f"{count}개 · 아직 믿지 않았다", f"python -m gaf hooks trust --root {paths.root}")


def _conductor(paths: Paths) -> Check:
    from .app import read_server          # app 은 무거워서 여기서만 불러온다

    record = read_server(paths)
    if record:
        return Check("지휘자", "ok", f"떠 있다(포트 {record.get('port')})")     # 접근 값(key · url)은 싣지 않는다
    return Check("지휘자", "skip", "꺼져 있다")


def _rea(run, which) -> list[Check]:
    """REA(고를 것 · PC 마다) — 알려 주기만 하는 줄이다. 없어도 「안 됨」으로 세지 않는다. 켠 적도 깐 적도 없으면 한 줄만 싣는다."""
    try:
        from .rea import setup as rea_setup

        # 기본 찾기(shutil.which)는 Windows 에서 지금 폴더를 PATH 보다 먼저 본다 — REA 줄은 setup 의 찾기(PATH 의 온전한 폴더에서만)를
        # 쓴다(11-27 다른 눈의 검수). 시험이 갈아 끼운 찾기는 그대로 넘긴다.
        data = rea_setup.status(run=run, which=None if which is shutil.which else which)
        rows = rea_setup.status_rows(data)
    except Exception:  # noqa: BLE001 — 알려 주기만 하는 줄이 점검 전체를 멈추게 하지 않는다
        return [Check("REA", "skip", "보지 못했다")]
    if not data.get("enabled") and not (data.get("rea") or {}).get("installed"):
        rows = rows[:1]
    return [Check(name if name == "REA" else f"REA · {name}", "ok" if ready else "skip", text) for name, ready, text in rows]


def checks(paths: Paths | None = None, *, run=subprocess.run, which=shutil.which,
           find_spec=importlib.util.find_spec, version=importlib.metadata.version, python=None) -> list[Check]:
    results = [_python(python)]
    spec = find_spec("claude_agent_sdk")
    if spec is None:
        results.append(Check("Claude 엔진", "fail", "claude-agent-sdk 가 없다", "`.venv`에 claude-agent-sdk 를 넣는다(0.2.180에서 확인)"))
        results.append(Check("Claude 로그인", "skip", "엔진이 없어 보지 않았다"))
    else:
        results.append(Check("Claude 엔진", "ok", _version(version, "claude-agent-sdk", "있음")))
        results.append(_claude_login(run, which, spec))
    engine, cli = _codex_engine(paths, which, find_spec, version)
    results += [engine, _codex_login(run, cli)]
    if paths is not None:
        results.append(_project(paths))
    results.append(_run_dir())
    if paths is not None:
        results.append(_hooks(paths))
        results.append(_conductor(paths))
    results += _rea(run, which)
    return results


def render(results: list[Check]) -> str:
    lines = []
    for item in results:
        if item.status == "ok":
            lines.append(f"[됨] {item.name} — {item.detail}")
        elif item.status == "fail":
            lines.append(f"[안 됨] {item.name} — {item.detail}" + (f" → {item.fix}" if item.fix else ""))
        else:
            lines.append(f"[건너뜀] {item.name} — {item.detail}")
    failed = sum(1 for item in results if item.status == "fail")
    lines.append(f"고칠 것 {failed}개" if failed else "준비됐다")
    return "\n".join(lines)


def main(paths: Paths | None = None, **kwargs) -> int:
    results = checks(paths, **kwargs)
    print(render(results))
    return 1 if any(item.status == "fail" for item in results) else 0
