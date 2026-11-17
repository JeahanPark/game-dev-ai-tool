"""Tailscale 손잡이 — Serve 는 사설망 안, Funnel 은 인터넷에 공개한다(사용자가 고른 때만 쓴다).

사용자가 PC에 깔고 로그인해 둔 Tailscale 을 쓴다. 설치나 로그인은 하지 않는다.
Serve 는 지휘자 포트로, Funnel 은 별도의 공개 문 포트로 넘긴다.
다른 앞단은 `gaf remote on --host <이름>` 으로 이름만 적고 손으로 건다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

TIMEOUT = 20                                    # 명령 하나를 기다리는 시간(초)
FUNNEL_PORTS = (443, 8443, 10000)
WINDOWS_DEFAULT = Path("C:/Program Files/Tailscale/tailscale.exe")


def find(which=shutil.which, default: Path = WINDOWS_DEFAULT) -> str | None:
    """PATH 의 tailscale → 없으면 Windows 의 기본 자리."""
    found = which("tailscale")
    if found:
        return str(found)
    return str(default) if Path(default).is_file() else None


def _run(run, command):
    try:
        # 표준 입력을 닫아 둔다 — tailscale 이 무엇을 물으면(덮어쓸까 등) 기다리지 않고 실패로 끝나고, 그 글을 사람에게 보인다.
        done = run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                   encoding="utf-8", errors="replace", timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done


def info(exe: str, *, run=subprocess.run) -> tuple[dict | None, str]:
    """이 PC의 사설망 이름들과 로그인한 사람 → ({"name", "hosts", "user"}, "") 또는 (None, 까닭)."""
    done = _run(run, [exe, "status", "--json"])
    if done is None:
        return None, "tailscale 을 돌리지 못했다"
    try:
        data = json.loads(done.stdout or "")
    except ValueError:
        data = None
    if done.returncode != 0 or not isinstance(data, dict):
        return None, "tailscale 의 상태를 읽지 못했다"
    if data.get("BackendState") != "Running":
        return None, "Tailscale 이 꺼져 있거나 로그인되어 있지 않다 — Tailscale 을 켜고 로그인한 뒤 다시 한다"
    me = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    name = str(me.get("DNSName") or "").strip().rstrip(".").lower()
    if not name:
        return None, "이 PC의 사설망 이름을 알 수 없다(MagicDNS 가 꺼져 있을 수 있다) — `gaf remote on --host <이름>` 으로 직접 적는다"
    hosts = [name, name.split(".", 1)[0]]
    hosts += [ip for ip in me.get("TailscaleIPs") or [] if isinstance(ip, str) and ip]
    users = data.get("User") if isinstance(data.get("User"), dict) else {}
    mine = users.get(str(me.get("UserID")))
    login = str(mine.get("LoginName") or "") if isinstance(mine, dict) else ""
    return {"name": name, "hosts": list(dict.fromkeys(hosts)), "user": login}, ""


def serve(exe: str, local_port, serve_port, *, https: bool = False, off: bool = False,
          run=subprocess.run) -> tuple[bool, str]:
    """사설망의 포트 serve_port 를 127.0.0.1:local_port 로 넘기게 한다(off 면 그 넘김을 끈다) → (됐나, 명령이 한 말)."""
    if not all(type(port) is int and 0 < port <= 65535 for port in (local_port, serve_port)):
        return False, "포트는 1~65535 사이의 정수여야 한다"
    listen = f"--{'https' if https else 'http'}={serve_port}"
    command = [exe, "serve", listen, "off"] if off else [exe, "serve", "--bg", listen, f"http://127.0.0.1:{local_port}"]
    done = _run(run, command)
    if done is None:
        return False, "tailscale 을 돌리지 못했다"
    said = (done.stdout if done.returncode == 0 else (done.stderr or done.stdout) or "").strip()
    return done.returncode == 0, said


def address(name: str, serve_port: int, *, https: bool) -> str:
    """폰 · 다른 PC의 브라우저에 넣을 주소."""
    default = 443 if https else 80
    return f"{'https' if https else 'http'}://{name}" + ("" if serve_port == default else f":{serve_port}") + "/"


def funnel(exe, local_port, funnel_port, *, off=False, run=subprocess.run) -> tuple[bool, str]:
    """공개 포트를 이 PC의 공개 문으로 넘긴다. 승인 대기 때 찍힌 주소도 그대로 돌려준다."""
    if type(funnel_port) is not int or funnel_port not in FUNNEL_PORTS:
        return False, "공개 포트는 443 · 8443 · 10000 가운데 하나여야 한다"
    if type(local_port) is not int or not 0 < local_port <= 65535:
        return False, "포트는 1~65535 사이의 정수여야 한다"
    listen = f"--https={funnel_port}"
    command = [exe, "funnel", listen, "off"] if off else [exe, "funnel", "--bg", listen, f"http://127.0.0.1:{local_port}"]
    try:
        done = run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                   encoding="utf-8", errors="replace", timeout=TIMEOUT)
    except subprocess.TimeoutExpired as error:
        parts = [part.decode("utf-8", errors="replace") if isinstance(part, bytes) else part or ""
                 for part in (error.stdout, error.stderr)]
        said = "\n".join(part.strip() for part in parts if part.strip())
        return False, (said + "\n(Tailscale 이 승인을 기다린다 — 위 주소에서 승인한 뒤 다시 켠다)"
                       if said and not off else said or "tailscale 을 돌리지 못했다")
    except OSError:
        return False, "tailscale 을 돌리지 못했다"
    said = (done.stdout if done.returncode == 0 else (done.stderr or done.stdout) or "").strip()
    return done.returncode == 0, said
