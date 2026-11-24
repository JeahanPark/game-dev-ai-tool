"""원격 접속 — 믿는 앞단(사설망 프록시) 뒤에서, 등록한 기기만 들인다(11-13 사용자 「폰/원격 접속 … 이것도 필요해」).

지휘자는 전처럼 127.0.0.1 에만 듣는다. 사설망 프로그램(예: Tailscale Serve)이 이 PC 안에서 그 포트로 넘겨 주면,
그 요청은 Host 가 이 PC의 사설망 이름이고 앞단이 「누가 보냈나」 머리를 붙여 온다. 그런 요청은 접근 값 대신
등록한 기기의 쿠키로 가린다. 등록은 PC 화면(또는 `gaf pair`)이 보여 주는 한 번짜리 코드로 한다.

설정과 기기 목록은 프로젝트 밖(접근 값 폴더)의 remote.json 에 둔다 — PC마다 하나고, git 에 올라가지 않으며,
세션은 그 폴더를 읽지도 쓰지도 못한다(gaf/scope.py). 기기의 값은 해시만 적는다.

등록 코드도 같은 파일에 둔다(1.12.0 — 해시 · 끝나는 시각 · 남은 횟수): 공개 길은 PC 하나의 문이라, 사람이 어느 프로젝트의 PC
화면에서 「기기 등록」을 눌렀든 폰의 요청을 받는 지휘자(앞문을 쥔 지휘자 — gaf/doors.py)가 그 코드를 알아야 한다.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import time
import unicodedata
from datetime import datetime

from .paths import _atomic_json, _read_json, file_lock, run_dir

FILE = "remote.json"
COOKIE = "gaf_device"
IDENTITY_HEADER = "tailscale-user-login"     # Tailscale Serve 가 붙이는 「누가 보냈나」(공개 길 Funnel 에는 붙지 않는다)
CODE_SECONDS = 300                           # 등록 코드가 사는 시간(초)
CODE_TRIES = 5                               # 틀릴 수 있는 횟수 — 다 쓰면 코드를 버린다
DEVICE_LIMIT = 16
DEVICE_SECONDS = 180 * 86400                 # 등록한 기기가 사는 시간 — 지나면 다시 등록한다
SEEN_EVERY = 3600                            # 마지막 접속 시각을 적는 간격(초) — 요청마다 파일을 쓰지 않게
_PORT = re.compile(r"[0-9]{1,5}")
_HEADER = re.compile(r"[!#$%&'*+.^_`|~0-9a-z-]*")


def remote_file():
    return run_dir() / FILE


def _name(host: str) -> str:
    """이름 하나를 견줄 꼴로: 소문자 · 끝 점 없음 · IP 는 줄인 꼴. 견줄 수 없는 글이면 빈 글."""
    text = host.strip().lower().rstrip(".")
    if not text:
        return ""
    try:
        return ipaddress.ip_address(text.strip("[]")).compressed
    except ValueError:
        return text


def _clean(data) -> dict:
    """파일에서 읽은 것을 늘 같은 모양으로(잘못된 칸은 버린다 — hosts 가 비면 꺼진 것이다)."""
    data = data if isinstance(data, dict) else {}
    hosts = data.get("hosts")
    hosts = [_name(h) for h in hosts if isinstance(h, str)] if isinstance(hosts, list) else []
    users = data.get("users")
    users = [u.strip() for u in users if isinstance(u, str) and u.strip()] if isinstance(users, list) else []
    header = data.get("identity_header", IDENTITY_HEADER)
    header = header.strip().lower() if isinstance(header, str) else IDENTITY_HEADER
    devices = []
    for item in data.get("devices") if isinstance(data.get("devices"), list) else []:
        if (isinstance(item, dict) and all(isinstance(item.get(key), str) and item.get(key) for key in ("id", "hash"))
                and all(type(item.get(key)) in (int, float) for key in ("created", "seen"))):
            devices.append({"id": item["id"], "hash": item["hash"], "name": str(item.get("name") or "기기"),
                            "user": str(item.get("user") or ""), "created": item["created"], "seen": item["seen"]})
    cleaned = {"v": 1, "hosts": [h for h in dict.fromkeys(hosts) if h], "users": list(dict.fromkeys(users)),
               "identity_header": header if _HEADER.fullmatch(header) else IDENTITY_HEADER, "devices": devices}
    pairing = data.get("pairing")
    if (isinstance(pairing, dict) and all(isinstance(pairing.get(key), str) and pairing.get(key) for key in ("salt", "hash"))
            and type(pairing.get("until")) in (int, float) and type(pairing.get("tries")) is int and pairing["tries"] > 0):
        # 살아 있는 등록 코드(있을 때만 적는다) — 코드 글자는 두지 않는다
        cleaned["pairing"] = {key: pairing[key] for key in ("salt", "hash", "until", "tries")}
    return cleaned


def read_settings() -> dict:
    return _clean(_read_json(remote_file()))


def write_settings(data: dict) -> None:
    folder = run_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            folder.chmod(0o700)
        except OSError:
            pass
    _atomic_json(remote_file(), _clean(data))


def update_settings(mutate):
    """설정 파일을 잠그고 다시 읽어 고친 뒤 쓴다 → mutate(data) 가 돌려준 값.

    다른 지휘자나 `gaf remote` 가 같은 때 고쳐도 서로의 것을 지우지 않는다(프로젝트 목록의 QA 결함-31 과 같은 자리).
    """
    with file_lock(remote_file()):
        data = read_settings()
        result = mutate(data)
        write_settings(data)
    return result


def enable(hosts, *, users=(), identity_header: str = IDENTITY_HEADER) -> dict:
    """원격 접속을 켠다: 들일 Host 이름들 · (있으면) 들일 사람 · 앞단이 붙이는 사람 표시 머리. 기기 목록은 그대로 둔다."""
    update_settings(lambda data: data.update(hosts=list(hosts), users=list(users), identity_header=identity_header))
    return read_settings()


def disable() -> None:
    """끈다 — 들일 이름을 비운다. 기기 목록은 지우지 않는다(다시 켜면 등록한 기기가 그대로 붙는다). 내 둔 등록 코드는 버린다."""
    def off(data):
        data["hosts"] = []
        data.pop("pairing", None)

    update_settings(off)


def _iso(stamp: float) -> str:
    try:
        return datetime.fromtimestamp(stamp).astimezone().isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return ""


def _view(device: dict) -> dict:
    return {"id": device["id"], "name": device["name"], "user": device["user"],
            "created_at": _iso(device["created"]), "seen_at": _iso(device["seen"])}


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class Remote:
    """지휘자 하나가 쥐는 원격 문. 설정 파일이 밖에서 바뀌면(다른 지휘자 · `gaf remote`) 다음에 볼 때 다시 읽는다."""

    def __init__(self, *, clock=time.time) -> None:
        self.clock = clock
        self._stamp: object = ()              # 읽은 파일의 (고친 시각, 크기) — 달라지면 다시 읽는다
        self._data = _clean(None)

    def _load(self) -> dict:
        try:
            status = os.stat(remote_file())
            stamp = (status.st_mtime_ns, status.st_size)
        except OSError:
            stamp = None
        if stamp != self._stamp:
            self._data = read_settings() if stamp is not None else _clean(None)
            self._stamp = stamp
        return self._data

    def _change(self, mutate):
        """파일을 잠그고 다시 읽어 고친다(update_settings) → mutate 가 돌려준 값. 다음에 볼 때 파일에서 다시 읽는다."""
        result = update_settings(mutate)
        self._stamp = ()
        return result

    @property
    def enabled(self) -> bool:
        return bool(self._load()["hosts"])

    def allows_host(self, host) -> bool:
        """요청의 Host 머리가 적어 둔 사설망 이름인가(포트는 무엇이든 — 앞단이 여는 포트다)."""
        hosts = self._load()["hosts"]
        if not hosts or not isinstance(host, str) or not host or host != host.strip():
            return False
        if host.startswith("["):
            name, bracket, rest = host[1:].partition("]")
            if not bracket or (rest and not (rest.startswith(":") and _PORT.fullmatch(rest[1:]))):
                return False
            name = f"[{name}]"
        else:
            name, colon, port = host.partition(":")
            if colon and not _PORT.fullmatch(port):
                return False
        name = _name(name)
        return bool(name) and name in hosts

    def user(self, headers) -> str | None:
        """앞단이 붙인 「누가 보냈나」. 붙어 있지 않거나 들일 사람이 아니면 None(= 들이지 않는다).

        이 머리가 없다는 것은 앞단을 거치지 않았거나 공개 길(Tailscale Funnel)로 왔다는 뜻이다. 머리를 붙이지 않는 앞단을
        쓰려면 identity_header 를 빈 글로 둔다 — 그때는 사람을 가리지 않는다.
        """
        data = self._load()
        header = data["identity_header"]
        if not header:
            return ""
        value = headers.get(header)
        if not isinstance(value, str) or not value.strip():
            return None
        users = data["users"]
        if users and value.strip().lower() not in {user.lower() for user in users}:
            return None
        return value.strip()

    def start_pairing(self) -> dict | None:
        """등록 코드를 새로 낸다(앞의 코드는 버린다) → 꺼져 있으면 None.

        코드는 PC 하나의 것이다 — 이 PC 의 어느 지휘자가 받아도 통한다. 파일에는 해시 · 끝나는 시각 · 남은 횟수만 적는다."""
        if not self.enabled:
            return None
        digits = f"{secrets.randbelow(10 ** 8):08d}"
        salt = secrets.token_hex(8)
        pairing = {"salt": salt, "hash": _digest(salt + digits), "until": self.clock() + CODE_SECONDS, "tries": CODE_TRIES}
        self._change(lambda data: data.update(pairing=pairing))
        return {"code": f"{digits[:4]}-{digits[4:]}", "seconds": CODE_SECONDS}

    def pair(self, code, *, name, user) -> tuple[str | None, str]:
        """코드가 맞으면 기기를 등록하고 쿠키에 넣을 값을 준다 → (값, "") 또는 (None, 까닭).

        파일을 잠그고 한 번에 한다(코드 보기 · 횟수 깎기 · 기기 적기) — 지휘자 여럿이 같이 받아도 횟수를 넘겨 찍어 보지 못한다."""
        if not self.enabled:
            return None, "원격 접속이 꺼져 있다"
        if not self._load().get("pairing"):
            # 낸 코드가 없다 — 파일을 잠그지도 쓰지도 않는다. 이 길은 등록하지 않은 기기(인터넷의 누구나)가 부른다: 요청마다
            # 파일을 쓰면 쏟아붓는 것만으로 지휘자가 느려진다(11-24 검수). 쓰는 것은 코드가 살아 있는 동안의 틀린 시도(다섯 번까지) ·
            # 맞은 시도 · 시간이 지난 코드를 버릴 때 한 번뿐이다.
            return None, "PC에서 먼저 기기 등록을 시작한다"
        # 전각 숫자(１２３…)도 같은 숫자로 본다 — 폰의 글자판에 따라 그렇게 들어올 수 있다.
        given = re.sub(r"[\s-]", "", unicodedata.normalize("NFKC", code)) if isinstance(code, str) else ""
        now = self.clock()
        secret = secrets.token_urlsafe(32)
        label = " ".join("".join(c if c.isprintable() else " " for c in str(name or "")).split())[:40] or "기기"

        def attempt(data):
            pairing = data.get("pairing")
            if not pairing:
                return None, "PC에서 먼저 기기 등록을 시작한다"
            if now > pairing["until"]:
                del data["pairing"]
                return None, "코드의 시간이 지났다 — PC에서 기기 등록을 다시 시작한다"
            if not hmac.compare_digest(_digest(pairing["salt"] + given).encode("ascii"), pairing["hash"].encode("utf-8")):
                tries = pairing["tries"] - 1
                if tries <= 0:
                    del data["pairing"]
                    return None, "코드를 여러 번 틀려서 버렸다 — PC에서 기기 등록을 다시 시작한다"
                pairing["tries"] = tries
                return None, f"코드가 맞지 않는다 — {tries}번 더 해 볼 수 있다"
            del data["pairing"]                   # 한 번만 쓴다
            data["devices"] = [d for d in data["devices"] if now - d["created"] <= DEVICE_SECONDS]
            if len(data["devices"]) >= DEVICE_LIMIT:
                return None, f"기기는 {DEVICE_LIMIT}대까지 등록한다 — 안 쓰는 기기를 끊고 다시 한다"
            taken = {d["id"] for d in data["devices"]}
            device_id = next(i for i in iter(lambda: "d-" + secrets.token_hex(4), None) if i not in taken)
            data["devices"].append({"id": device_id, "hash": _digest(secret), "name": label,
                                    "user": " ".join(str(user or "").split())[:100], "created": now, "seen": now})
            return f"{device_id}.{secret}", ""

        return self._change(attempt)

    def device(self, cookie_header) -> dict | None:
        """요청의 Cookie 머리에서 등록한 기기를 찾는다 → 없거나 값이 틀리거나 오래됐으면 None."""
        data = self._load()
        if not data["hosts"] or not isinstance(cookie_header, str):
            return None
        value = ""
        for part in cookie_header.split(";"):
            name, equals, text = part.strip().partition("=")
            if equals and name == COOKIE:
                value = text
                break
        device_id, dot, secret = value.partition(".")
        if not dot or not device_id or not secret:
            return None
        now = self.clock()
        for device in data["devices"]:
            if device["id"] == device_id:
                # 바이트로 견준다 — 파일의 해시 칸이 손으로 고쳐져 이상한 글이어도 오류 없이 「아니다」가 되게.
                if (not hmac.compare_digest(_digest(secret).encode("ascii"), device["hash"].encode("utf-8"))
                        or now - device["created"] > DEVICE_SECONDS):
                    return None
                if now - device["seen"] >= SEEN_EVERY:
                    def touch(data, device_id=device_id):
                        for item in data["devices"]:
                            if item["id"] == device_id:
                                item["seen"] = now

                    try:
                        self._change(touch)
                    except OSError:
                        pass                  # 접속 시각을 못 적어도 들이는 데는 지장이 없다
                    device = {**device, "seen": now}
                return _view(device)
        return None

    def devices(self) -> list[dict]:
        now = self.clock()
        return [_view(d) for d in self._load()["devices"] if now - d["created"] <= DEVICE_SECONDS]

    def forget(self, device_id) -> bool:
        if not any(d["id"] == device_id for d in self._load()["devices"]):
            return False

        def drop(data):
            kept = [d for d in data["devices"] if d["id"] != device_id]
            found = len(kept) != len(data["devices"])
            data["devices"] = kept
            return found

        return bool(self._change(drop))

    def cookie(self, value: str, *, secure: bool) -> str:
        return (f"{COOKIE}={value}; Max-Age={DEVICE_SECONDS}; Path=/; HttpOnly; SameSite=Strict"
                + ("; Secure" if secure else ""))
