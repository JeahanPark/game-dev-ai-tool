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


def door_row(root=None) -> dict | None:
    """공개 길을 켠 프로젝트의 줄(문 줄) — PC 하나에 문 하나다(1.12.0). 그 프로젝트가 바로 켠 프로젝트면 그 줄, 아니면 첫 줄."""
    rows = [row for row in projects.entries() if row["public"]]
    if root is not None:
        same = projects._same(root)
        mine = next((row for row in rows if projects._same(row["root"]) == same), None)
        if mine is not None:
            return mine
    return rows[0] if rows else None


def address_of(paths) -> str:
    """폰 · 다른 PC 에서 열 주소. 공개 길이면 어느 프로젝트에서 보든 그 문 하나의 주소다 — 켜져 있는 프로젝트를 모두 그 주소로 연다."""
    door = door_row(paths.root)
    port, https = (door["serve"], door["https"]) if door else projects.serve_of(paths.root)
    hosts = remote.read_settings()["hosts"]
    return tailnet.address(hosts[0], port, https=https) if port and hosts else ""


def public_on(paths, *, funnel_port=None, find=tailnet.find, run=subprocess.run) -> dict:
    def failed(line):
        return {"ok": False, "address": "", "public_port": projects.public_port_of(paths.root), "lines": [line]}

    if not paths.config.is_file():
        return failed(f"gaf 프로젝트가 아니다: {paths.root} — 먼저 python -m gaf init --root <폴더>")
    door = door_row(paths.root)
    if door is not None:
        # 공개 길은 PC 하나에 문 하나다 — 다른 프로젝트에서 켜 두었어도 새 주소를 만들지 않는다(Tailscale 을 부르지 않는다).
        if funnel_port is not None and funnel_port != door["serve"]:
            return failed(f"공개 길이 이미 {door['serve']}번 포트로 켜져 있다 — 포트를 바꾸려면 먼저 끈다: python -m gaf remote off")
        address = address_of(paths)
        return {"ok": True, "address": address, "public_port": door["public_port"],
                "lines": ["공개 길이 이미 켜져 있다 — 주소: " + address,
                          "이 PC 에 켜져 있는 프로젝트는 모두 이 주소 하나로 연다(화면의 프로젝트 목록에서 고른다)"]}
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
    if funnel_port is None:
        funnel_port = tailnet.FUNNEL_PORTS[0]
    if type(funnel_port) is not int or funnel_port not in tailnet.FUNNEL_PORTS:
        return failed("공개 포트는 443 · 8443 · 10000 가운데 하나여야 한다")
    ok, said = tailnet.funnel(exe, public_port, funnel_port, run=run)
    if not ok:
        return failed(said or "공개 길을 켜지 못했다")
    projects.set_serve(paths.root, funnel_port, True, tailscale=True, public=True)
    remote.enable([info["name"]], users=[], identity_header="")
    address = address_of(paths)
    return {"ok": True, "address": address, "public_port": public_port, "lines": [
        "공개 길을 켰다 — 주소: " + address,
        "이 주소는 인터넷 어디서나 열린다. 들어오는 것은 등록한 기기뿐이다 — 등록한 기기는 이 PC 에 켜져 있는 프로젝트를 모두 연다",
        "이 PC 의 Tailscale 기기 이름이 공개 인증서 기록에 남는다(지울 수 없다)",
        "다음: 폰 · 다른 컴퓨터에서 주소를 열고, PC 화면의 「설정 → 기기 등록」 이 보여 주는 코드를 넣는다"]}


def public_off(paths, *, find=tailnet.find, run=subprocess.run) -> dict:
    """공개 길을 끈다 — PC 하나의 문이라 어느 프로젝트에서 꺼도 같다(옛 판이 프로젝트마다 켜 둔 문이 여럿이면 모두 끈다)."""
    doors = [row for row in projects.entries() if row["public"]]
    if not doors:
        return {"ok": True, "lines": ["공개 길은 꺼져 있다"]}
    lines = []
    exe = find() if any(row["tailscale"] for row in doors) else None
    for row in doors:
        funnel_port = row["serve"]
        by_hand = f"Tailscale 의 넘김이 남아 있을 수 있다 — 터미널에서 `tailscale funnel --https={funnel_port} off` 로 끈다(이 PC 의 문은 닫았다)"
        if exe is not None and row["tailscale"]:
            ok, said = tailnet.funnel(exe, row["public_port"], funnel_port, off=True, run=run)
            if not ok:
                lines += [said or "Tailscale 의 넘김을 끄지 못했다", by_hand]
        elif row["tailscale"]:
            lines += ["Tailscale 을 찾지 못했다", by_hand]
        projects.set_serve(row["root"], 0)
    warn = "\n".join(lines)
    if not any(row["serve"] for row in projects.entries()):
        remote.disable()
    # 공개 길은 「누가 보냈나」 머리를 비워 두고 켠다(Funnel 은 그 머리를 붙이지 않는다). 공개 길을 닫으면 기본값으로
    # 되돌린다 — 그대로 두면 다음에 켜는 사설망 길(`gaf remote on`)이 빈 값을 이어받아 사람을 가리지 않는다.
    remote.update_settings(lambda data: data.update(identity_header=remote.IDENTITY_HEADER))
    lines.append("공개 길을 껐다(등록한 기기 목록은 남는다)")
    return {"ok": True, "lines": lines, "warn": warn}
