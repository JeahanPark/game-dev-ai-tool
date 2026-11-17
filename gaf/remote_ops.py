"""공개 길의 켜기 · 끄기 — 명령줄과 PC 화면이 같이 쓴다. 글은 찍지 않고 돌려준다."""
from __future__ import annotations

import socket
import subprocess

from . import projects, remote, tailnet
from .config import load_config
from .paths import _read_json

FIXED_PORTS = range(8765, 8965)
MIXED_ROUTES = "사설망 길과 공개 길은 같이 쓰지 않는다 — 먼저 python -m gaf remote off"


def fixed_port(root, *, avoid=()) -> int:
    """모든 프로젝트의 지휘자 · 공개 문 포트와 지금 쓰이는 포트를 피한다."""
    taken = {port for row in projects.entries() for port in (row["port"], row["public_port"]) if port}
    taken.update(avoid)
    for port in FIXED_PORTS:
        if port in taken:
            continue
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    # OS가 고르는 포트도 예약과 겹치면 피한다. 소켓을 쥐고 있어 같은 포트를 다시 뽑지 않는다.
    probes = []
    try:
        while True:
            probe = socket.socket()
            probes.append(probe)
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            if port not in taken:
                return port
    finally:
        for probe in probes:
            probe.close()


def address_of(paths) -> str:
    port, https = projects.serve_of(paths.root)
    hosts = remote.read_settings()["hosts"]
    return tailnet.address(hosts[0], port, https=https) if port and hosts else ""


def public_on(paths, *, funnel_port=None, find=tailnet.find, run=subprocess.run) -> dict:
    def failed(line):
        return {"ok": False, "address": "", "public_port": projects.public_port_of(paths.root), "lines": [line]}

    if not paths.config.is_file():
        return failed(f"gaf 프로젝트가 아니다: {paths.root} — 먼저 python -m gaf init --root <폴더>")
    if projects.serve_public(paths.root):
        address = address_of(paths)
        return {"ok": True, "address": address, "public_port": projects.public_port_of(paths.root),
                "lines": ["공개 길이 이미 켜져 있다 — 주소: " + address]}
    exe = find()
    if exe is None:
        return failed("Tailscale 을 찾지 못했다 — 이 PC에 Tailscale 을 깔고 로그인한 뒤 다시 한다")
    info, error = tailnet.info(exe, run=run)
    if info is None:
        return failed(error)
    if any(row["serve"] and not row["public"] for row in projects.entries()):
        return failed(MIXED_ROUTES)
    config = load_config(paths)
    projects.remember(paths.root, config.project)
    public_port = projects.public_port_of(paths.root)
    if not public_port:
        # 살아 있는 지휘자가 임시 포트를 쓸 수도 있다. 접근 값 파일은 읽지 않는다.
        record = _read_json(paths.server) or {}
        public_port = fixed_port(paths.root, avoid=(config.server["port"], projects.port_of(paths.root), record.get("port", 0)))
        projects.set_public_port(paths.root, public_port)
    taken = {row["serve"] for row in projects.entries() if row["public"]}
    if funnel_port is None:
        funnel_port = next((port for port in tailnet.FUNNEL_PORTS if port not in taken), None)
        if funnel_port is None:
            return failed("공개 길은 프로젝트 셋까지다")
    if type(funnel_port) is not int or funnel_port not in tailnet.FUNNEL_PORTS:
        return failed("공개 포트는 443 · 8443 · 10000 가운데 하나여야 한다")
    if funnel_port in taken:
        return failed("다른 프로젝트가 쓰는 공개 포트다 — 다른 포트를 고른다")
    ok, said = tailnet.funnel(exe, public_port, funnel_port, run=run)
    if not ok:
        return failed(said or "공개 길을 켜지 못했다")
    projects.set_serve(paths.root, funnel_port, True, tailscale=True, public=True)
    remote.enable([info["name"]], users=[], identity_header="")
    address = address_of(paths)
    return {"ok": True, "address": address, "public_port": public_port, "lines": [
        "공개 길을 켰다 — 주소: " + address,
        "이 주소는 인터넷 어디서나 열린다. 들어오는 것은 등록한 기기뿐이다",
        "이 PC 의 Tailscale 기기 이름이 공개 인증서 기록에 남는다(지울 수 없다)",
        "다음: 폰 · 다른 컴퓨터에서 주소를 열고, PC 화면의 「설정 → 기기 등록」 이 보여 주는 코드를 넣는다"]}


def public_off(paths, *, find=tailnet.find, run=subprocess.run) -> dict:
    if not projects.serve_public(paths.root):
        return {"ok": True, "lines": ["이 프로젝트의 공개 길은 꺼져 있다"]}
    lines = []
    funnel_port = projects.serve_of(paths.root)[0]
    by_hand = f"Tailscale 의 넘김이 남아 있을 수 있다 — 터미널에서 `tailscale funnel --https={funnel_port} off` 로 끈다(이 프로젝트의 문은 닫았다)"
    exe = find() if projects.serve_by_tailscale(paths.root) else None
    if exe is not None:
        ok, said = tailnet.funnel(exe, projects.public_port_of(paths.root), funnel_port, off=True, run=run)
        if not ok:
            lines += [said or "Tailscale 의 넘김을 끄지 못했다", by_hand]
    elif projects.serve_by_tailscale(paths.root):
        lines += ["Tailscale 을 찾지 못했다", by_hand]
    warn = "\n".join(lines)
    projects.set_serve(paths.root, 0)
    rows = projects.entries()
    if not any(row["serve"] for row in rows):
        remote.disable()
    if not any(row["public"] for row in rows):
        # 공개 길은 「누가 보냈나」 머리를 비워 두고 켠다(Funnel 은 그 머리를 붙이지 않는다). 공개 길이 다 닫히면 기본값으로
        # 되돌린다 — 그대로 두면 다음에 켜는 사설망 길(`gaf remote on`)이 빈 값을 이어받아 사람을 가리지 않는다.
        remote.update_settings(lambda data: data.update(identity_header=remote.IDENTITY_HEADER))
    lines.append("이 프로젝트의 공개 길을 껐다(등록한 기기 목록은 남는다)")
    return {"ok": True, "lines": lines, "warn": warn}
