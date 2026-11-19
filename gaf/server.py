"""표준 라이브러리로 여는 작은 로컬 HTTP · SSE 서버."""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime
from http import HTTPStatus
from pathlib import Path, PureWindowsPath
from time import monotonic
from urllib.parse import parse_qs, urlsplit

from . import __version__, changes, desktop, launch, library, projects, remote_ops, tailnet
from .app import read_server
from .dictation import Dictation, WAV_MAX_BYTES, STREAM_ID
from .paths import Paths
from .remote import Remote, remote_file, read_settings

HEADER_LIMIT = 64 * 1024
BODY_LIMIT = 48 * 1024 * 1024
PUBLIC_CLIENTS = 32
PUBLIC_HEADER_SECONDS = 5
PUBLIC_OPEN_BODY = 4096
PUBLIC_OPEN_SECONDS = 5
DRAIN_SECONDS, DRAIN_BYTES = 3.0, 64 * 1024 * 1024      # 오류 답 뒤에 읽지 않은 본문을 받아 버리는 한도
STOP_WAIT = 3.0                                         # 멎을 때 듣는 소켓이 다 닫히기를 기다리는 시간(초)
FILE_LIMIT = 50 * 1024 * 1024
IMAGE_LIMIT = 8 * 1024 * 1024
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
FILE_TYPES = {**IMAGE_TYPES, ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
              ".mp4": "video/mp4", ".webm": "video/webm",
              ".md": "text/plain; charset=utf-8", ".csv": "text/plain; charset=utf-8"}
TEXT_TYPES = frozenset({"text/html", "text/plain", "application/json", "application/manifest+json",
                        "text/css", "application/javascript"})
CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self'; "
       "style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
# 프로젝트와 사람에 관한 값이 없는 앱 파일. 브라우저가 접근 값·쿠키 없이 가져온다.
OPEN_ASSETS = {"/manifest.webmanifest": None, "/icon-192.png": (192, False), "/icon-512.png": (512, False),
               "/icon-maskable-512.png": (512, True), "/apple-touch-icon.png": (180, True)}
MANIFEST = {
    "name": "Game Dev AI Tool", "short_name": "Game Dev AI Tool", "lang": "ko", "id": "/", "start_url": "/", "scope": "/",
    "display": "standalone", "background_color": "#17191A", "theme_color": "#17191A",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
              {"src": "/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
}
ROUTES = {
    **{path: "GET" for path in OPEN_ASSETS},
    "/api/job": "GET", "/api/job/stop": "POST",
    "/api/library": "GET",
    "/": "GET", "/favicon.ico": "GET", "/api/state": "GET", "/api/stream": "GET",
    "/api/chat": "GET", "/api/inbox": "GET", "/api/role": "GET POST", "/api/task": "GET", "/api/file": "GET",
    "/api/say": "POST", "/api/decide": "POST", "/api/stop": "POST", "/api/assign": "POST", "/api/custom": "POST",
    "/api/notices": "POST", "/api/shutdown": "POST", "/api/dictate": "POST",
    # 총괄 명령(`gaf team` · `gaf assign` …). 세션은 접근 값 대신 자기 표(X-GAF-Session)로 부른다.
    "/api/tool": "POST",
    # 문: 엔진의 PreToolUse 훅(gaf/hook.py)이 「이 도구를 써도 되나」를 묻는다. 세션의 표로만 부른다.
    "/api/gate": "POST",
    # 원격 접속(gaf/remote.py): 기기 등록은 원격 화면에서 · 등록 시작과 기기 관리는 PC에서만 한다.
    "/api/pair": "POST", "/api/pair/start": "POST", "/api/devices": "GET POST",
    "/api/remote": "POST", "/api/remote/qr": "GET",
    # 이 PC에서 연 프로젝트 목록(gaf/projects.py) — 꺼진 프로젝트를 켜는 것(POST)은 PC에서만 한다.
    "/api/projects": "GET POST",
    # 역할별 대화(받은 글 · 한 말 · 도구 · 턴의 끝)를 이어 받는다 · 사람이 역할에게 글을 보낸다(화면의 입력칸 · 원격 기기도 쓴다).
    "/api/talk": "GET", "/api/send": "POST",
    # 작업 폴더에서 바뀐 파일 목록과 파일 하나의 차이(gaf/changes.py — git 을 읽기만 부른다 · 화면의 「바뀐 코드」).
    "/api/changes": "GET", "/api/change": "GET",
}
# 사설망 앞단을 거쳐 온 요청(원격)이 쓰지 못하는 길 — 세션 길 · 끄기 · 기기 관리는 PC에서만 한다.
LOCAL_ONLY = frozenset({"/api/tool", "/api/gate", "/api/shutdown", "/api/pair/start", "/api/devices",
                        "/api/remote", "/api/remote/qr"})
# 등록하지 않은 기기에도 여는 길 — 화면 한 장과 앱 파일, 등록.
REMOTE_OPEN = frozenset({("GET", "/"), ("GET", "/favicon.ico"), ("POST", "/api/pair")}
                        | {("GET", path) for path in OPEN_ASSETS})


class RequestError(Exception):
    def __init__(self, status: int, message: str, **extra):
        self.status, self.message, self.extra = status, message, extra      # extra = 오류 답에 덧붙일 칸


def _json(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _number(value: str, name: str) -> int:
    if not re.fullmatch(r"[0-9]{1,18}", value):
        raise RequestError(400, f"{name}는 0 이상의 정수여야 한다")
    return int(value)


def _text(data: dict, name: str, default="") -> str:
    value = data.get(name, default)
    if not isinstance(value, str):
        raise RequestError(400, f"{name}는 글이어야 한다")
    return value


def _bad_constant(value):
    raise ValueError("JSON에 쓸 수 없는 수다")


def _accepts_gzip(value):
    for item in value.lower().split(","):
        name, *parameters = item.split(";")
        if name.strip() != "gzip":
            continue
        quality = 1.0
        for parameter in parameters:
            key, _, number = parameter.partition("=")
            if key.strip() == "q":
                try:
                    quality = float(number.strip())
                except ValueError:
                    quality = 0
        return 0 < quality <= 1
    return False


class Server:
    def __init__(self, app, *, host="127.0.0.1", port=0, key: str | None = None, remote: Remote | None = None, public_port=0, dictation=None):
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError("뷰어는 127.0.0.1에만 연다")
        self.app, self.host, self.port = app, host, port
        self.key = key or secrets.token_urlsafe(24)
        # 원격 문 — 꺼져 있으면(기본) 사설망 이름의 요청은 전처럼 Host 에서 막힌다. 서버는 그래도 127.0.0.1 에만 듣는다.
        self.remote = remote if remote is not None else Remote()
        self.dictation = dictation if dictation is not None else Dictation()
        self._voice_available = None
        self._voice_checked_at = 0
        self._remote_stamp = ()
        self._remote_cached = {}
        self.url = ""
        self.shutdown = asyncio.Event()
        self._server = None
        self.public_port = public_port
        self._public_server = None
        self._public_clients: set[asyncio.Task] = set()
        self._public_writers: set[asyncio.StreamWriter] = set()
        self._remote_lock = asyncio.Lock()
        self._clients: set[asyncio.Task] = set()
        self._refusals: set[asyncio.Task] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._upload_n = 0

    async def start(self) -> None:
        if self._server is not None:
            return
        self.shutdown.clear()
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", self.port,
                                                 limit=HEADER_LIMIT)
        self.port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}/?k={self.key}"
        try:
            from .app import write_server          # 접근 값은 프로젝트 밖에 적는다(11-12 QA 결함-21)

            write_server(self.app.paths, port=self.port, key=self.key, url=self.url,
                         started_at=datetime.fromtimestamp(self.app.store.clock()).astimezone().isoformat())
        except BaseException:
            await self.stop()
            raise
        if self.public_port:
            await self.open_public(self.public_port)

    async def open_public(self, port):
        """공개 문만 따로 연다. 못 열어도 지휘자의 본 포트는 계속 듣는다."""
        if self._public_server is not None and self.public_port == port:
            return {"ok": True}
        await self.close_public()
        self.public_port = port
        if not port:
            return {"ok": False, "error": "공개 문 포트가 없다"}
        try:
            self._public_server = await asyncio.start_server(self._accept_public, "127.0.0.1", port, limit=HEADER_LIMIT)
        except OSError:
            reason = "공개 문 포트를 열지 못했다 — 다른 프로그램이 쓰는지 확인한다"
            self.app.store.emit("error", message=reason)
            return {"ok": False, "error": reason}
        return {"ok": True}

    async def close_public(self):
        listener, self._public_server = self._public_server, None
        if listener is not None:
            listener.close()
        for writer in tuple(self._public_writers):
            writer.close()
        tasks = tuple(self._public_clients)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if listener is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(listener.wait_closed(), STOP_WAIT)

    def _accept_public(self, reader, writer):
        if len(self._public_clients) >= PUBLIC_CLIENTS:
            writer.close()
            return
        self._public_writers.add(writer)
        task = asyncio.create_task(self._handle(reader, writer, public=True))
        self._public_clients.add(task)
        task.add_done_callback(self._public_clients.discard)

    async def stop(self) -> None:
        await self.close_public()
        listener, self._server = self._server, None
        try:
            if listener is not None:
                listener.close()
            for writer in tuple(self._writers):
                writer.close()
            tasks = tuple(self._clients | self._refusals)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if listener is not None:
                # 파이썬 3.12부터 wait_closed 는 모든 연결이 끝나기를 기다린다. Windows(프로액터 루프)에서는 상대가 강제로 끊은
                # 연결(WinError 10054)이 끝난 것으로 세어지지 않는 때가 있어 끝없이 기다리게 된다 — 그러면 `gaf shutdown` 뒤에도
                # 듣는 소켓만 닫힌 채 지휘자가 남는다(11-13 겪음). 잠깐만 기다리고 넘어간다.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(listener.wait_closed(), STOP_WAIT)
        finally:
            from .app import clear_server

            with contextlib.suppress(Exception):
                await self.dictation.close()
            clear_server(self.app.paths)

    def _headers(self, status, content_type, length=None, *, stream=False, extra=None) -> bytes:
        headers = {"Content-Type": content_type, "Connection": "keep-alive" if stream else "close",
                   "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                   "Referrer-Policy": "no-referrer"}
        if length is not None:
            headers["Content-Length"] = str(length)
        headers.update(extra or {})
        return (f"HTTP/1.1 {status} {HTTPStatus(status).phrase}\r\n" +
                "".join(f"{key}: {value}\r\n" for key, value in headers.items()) + "\r\n").encode("ascii")

    def _accept(self, reader, writer) -> None:
        if len(self._clients) >= 64:
            body = _json({"ok": False, "error": "연결이 많다 — 잠시 뒤 다시 한다"})
            # 11-12 QA 결함-16: Windows의 RST로 503이 사라지지 않게 요청을 비우되 거절 태스크는 32개까지만 둔다.
            if len(self._refusals) >= 32:
                with contextlib.suppress(Exception):
                    writer.write(self._headers(503, "application/json; charset=utf-8", len(body)) + body)
                with contextlib.suppress(Exception):
                    writer.close()
            else:
                self._writers.add(writer)
                task = asyncio.create_task(self._refuse(reader, writer, body))
                self._refusals.add(task)
                task.add_done_callback(self._refusals.discard)
            return
        self._writers.add(writer)
        task = asyncio.create_task(self._handle(reader, writer))
        self._clients.add(task)
        task.add_done_callback(self._clients.discard)

    async def _refuse(self, reader, writer, body):
        async def discard():
            while await reader.read(65536):
                pass

        try:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            with contextlib.suppress(Exception):
                writer.write(self._headers(503, "application/json; charset=utf-8", len(body)) + body)
                await asyncio.wait_for(writer.drain(), 2)
            with contextlib.suppress(Exception):
                writer.write_eof()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(discard(), 1)
        finally:
            self._writers.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()

    async def _drain_input(self, reader, writer) -> None:
        """오류 답을 보낸 뒤 — 보내는 쪽만 닫고, 상대가 아직 보내는 것(읽지 않은 본문)을 잠깐 받아 버린다.

        읽지 않은 것이 남은 채 닫으면 Windows 에서는 닫힘이 RST 로 가서 상대가 방금 보낸 오류 답(413 · 431 · 400 …)을
        못 받는다(11-12 QA 결함-16). 상대가 닫으면 바로 끝나고, 길어도 DRAIN_SECONDS · DRAIN_BYTES 까지만 받는다.
        """
        async def discard():
            left = DRAIN_BYTES
            while left > 0:
                chunk = await reader.read(min(65536, left))
                if not chunk:
                    return
                left -= len(chunk)

        with contextlib.suppress(Exception):
            writer.write_eof()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(discard(), DRAIN_SECONDS)

    async def _send(self, writer, status, body, content_type="application/json; charset=utf-8", *, extra=None,
                    request=None, cache=False):
        if not isinstance(body, bytes):
            body = _json(body)
        extra = dict(extra or {})
        request = request or {}
        headers = request.get("headers", {})
        # 문을 지난 응답만 보내기 직전에 줄인다. 흐름 · 범위 · 이미 줄인 본문은 그대로 둔다.
        if (len(body) >= 1024 and content_type.split(";", 1)[0].strip().lower() in TEXT_TYPES
                and request.get("method") != "HEAD" and "range" not in headers and status not in (206, 304, 416)
                and not any(key.lower() in ("content-encoding", "content-range") for key in extra)):
            vary = next((key for key in extra if key.lower() == "vary"), "Vary")
            values = [value.strip() for value in extra.get(vary, "").split(",") if value.strip()]
            if not any(value.lower() in ("*", "accept-encoding") for value in values):
                values.append("Accept-Encoding")
            extra[vary] = ", ".join(values)
            if _accepts_gzip(headers.get("accept-encoding", "")):
                if len(body) > 256 * 1024:
                    body = await asyncio.to_thread(gzip.compress, body, compresslevel=5, mtime=0)
                else:
                    body = gzip.compress(body, compresslevel=5, mtime=0)
                extra["Content-Encoding"] = "gzip"
        if cache:
            etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
            extra.update({"ETag": etag, "Cache-Control": "no-cache"})
            matches = (value.strip().removeprefix("W/") for value in headers.get("if-none-match", "").split(","))
            if any(value in ("*", etag) for value in matches):
                status, body = 304, b""
        writer.write(self._headers(status, content_type, len(body), extra=extra) + body)
        await asyncio.wait_for(writer.drain(), 60)

    async def _request(self, reader, *, public=False, response=None):
        try:
            block = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), PUBLIC_HEADER_SECONDS if public else 10)
        except asyncio.LimitOverrunError:
            raise RequestError(431, "요청 머리가 너무 크다") from None
        except asyncio.IncompleteReadError:
            raise RequestError(400, "요청 머리가 끊겼다") from None
        except asyncio.TimeoutError:
            raise RequestError(408, "요청 머리를 기다리는 시간이 지났다") from None
        if len(block) > HEADER_LIMIT:
            raise RequestError(431, "요청 머리가 너무 크다")
        lines = block[:-4].decode("iso-8859-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or parts[2] != "HTTP/1.1" or not re.fullmatch(r"[A-Z]+", parts[0]):
            raise RequestError(400, "요청 줄이 잘못됐다")
        method, target, _ = parts
        if response is not None:
            response["method"] = method
        if not target.startswith("/") or target.startswith("//") or "#" in target:
            raise RequestError(400, "요청 주소가 잘못됐다")
        headers = {}
        if response is not None:
            response["headers"] = headers
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if not separator or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                raise RequestError(400, "요청 머리가 잘못됐다")
            name, value = name.lower(), value.strip(" \t")
            if name in headers or any(ord(c) < 32 and c != "\t" for c in value):
                raise RequestError(400, "요청 머리가 겹치거나 잘못됐다")
            headers[name] = value
        host = headers.get("host")
        local = not public and host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")
        if not local and not self.remote.allows_host(host):
            raise RequestError(403, "허용하지 않은 Host다")
        if public:
            try:
                hostname = (urlsplit("//" + host).hostname or "").rstrip(".")
            except ValueError:
                raise RequestError(403, "허용하지 않은 Host다") from None
            if not projects.serve_public(self.app.paths.root) or hostname in ("127.0.0.1", "localhost", "::1"):
                raise RequestError(403, "공개 문으로 들일 수 없는 요청이다")
        try:
            url = urlsplit(target)
            query = parse_qs(url.query, keep_blank_values=True, max_num_fields=100)
        except ValueError:
            raise RequestError(400, "요청 주소가 잘못됐다") from None
        path = url.path
        peer = {"remote": not local, "user": "", "device": None, "public": public}
        if local:
            # 세션이 친 명령은 접근 값을 보지 않는다 — 표가 맞는지 · 총괄인지는 지휘자가 가린다(_tool).
            by_session = path in ("/api/tool", "/api/gate") and "x-gaf-session" in headers
            if not by_session and not (method == "GET" and (path in ("/", "/favicon.ico") or path in OPEN_ASSETS)):
                candidates = (headers.get("x-gaf-key", ""), query.get("k", [""])[0])
                if not any(hmac.compare_digest(value.encode("utf-8"), self.key.encode("utf-8")) for value in candidates):
                    raise RequestError(403, "접근 값이 맞지 않는다")
            if method == "POST" and headers.get("origin") not in (
                    None, f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
                raise RequestError(403, "허용하지 않은 Origin이다")
        else:
            self._admit_remote(method, path, headers, host, peer)
        if method == "POST" and headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise RequestError(415, "Content-Type은 application/json이어야 한다")
        if "transfer-encoding" in headers:
            raise RequestError(400, "본문은 Content-Length로 보낸다")
        length = _number(headers.get("content-length", "0"), "Content-Length")
        unregistered = peer["remote"] and peer["device"] is None
        if unregistered and length > PUBLIC_OPEN_BODY:
            raise RequestError(413, "등록 전 본문은 4096바이트까지 보낸다")
        if length > BODY_LIMIT:
            raise RequestError(413, "본문은 48MB까지 보낸다")
        if path not in ROUTES:
            raise RequestError(404, "없는 길이다")
        if method not in ROUTES[path].split():
            raise RequestError(405, "이 길에 쓸 수 없는 메서드다")
        try:
            body = await asyncio.wait_for(reader.readexactly(length), PUBLIC_OPEN_SECONDS if unregistered else 60) if length else b""
        except asyncio.IncompleteReadError:
            raise RequestError(400, "본문이 끊겼다") from None
        except asyncio.TimeoutError:
            raise RequestError(408, "본문을 기다리는 시간이 지났다") from None
        data = {}
        if method == "POST" and (body or path != "/api/shutdown"):
            try:
                data = json.loads(body.decode("utf-8"), parse_constant=_bad_constant)
            except (ValueError, UnicodeError, RecursionError):
                raise RequestError(400, "JSON을 읽을 수 없다") from None
            if not isinstance(data, dict):
                raise RequestError(400, "JSON은 객체여야 한다")
        return method, path, query, headers, data, peer

    def _admit_remote(self, method, path, headers, host, peer) -> None:
        """사설망 앞단이 넘겨 준 요청을 들일지 가린다(gaf/remote.py) — 들일 수 없으면 RequestError.

        접근 값은 보지 않는다: 원격에서는 등록한 기기의 쿠키만 통한다. 앞단이 붙인 「누가 보냈나」가 없으면(앞단을 거치지 않았거나
        공개 길로 왔다) 화면도 주지 않는다. POST 는 같은 주소의 화면에서 온 것만 받는다(Origin — 쿠키를 쓰므로 꼭 본다).
        """
        user = self.remote.user(headers)
        if user is None:
            raise RequestError(403, "원격으로 들일 수 없는 요청이다")
        if path in LOCAL_ONLY:
            raise RequestError(403, "이 일은 PC에서 한다")
        if method == "POST" and headers.get("origin") not in (f"http://{host}", f"https://{host}"):
            raise RequestError(403, "허용하지 않은 Origin이다")
        peer["user"] = user
        peer["device"] = self.remote.device(headers.get("cookie", ""))
        if (method, path) in REMOTE_OPEN:
            return
        if peer["device"] is None:
            raise RequestError(401, "등록하지 않은 기기다", pair=True)

    async def _handle(self, reader, writer, *, public=False):
        sent = False
        response = {}

        async def send(status, body, content_type="application/json; charset=utf-8", **options):
            await self._send(writer, status, body, content_type, request=response, **options)

        try:
            method, path, query, headers, data, peer = await self._request(reader, public=public, response=response)
            if path.startswith("/api/") and not getattr(self.app.conductor, "started", True):
                # serve 는 포트를 먼저 잡고(세션에 줄 GAF_PORT) 지휘자를 켠다 — 그 사이에 온 요청
                raise RequestError(503, "지휘자가 뜨는 중이거나 꺼지는 중이다 — 잠시 뒤 다시 한다")
            if path == "/api/stream":
                after = _number(headers.get("last-event-id", query.get("after", ["0"])[0]), "after")
                sent = True
                await self._stream(reader, writer, after)
            elif path == "/api/file":
                file = self._file(query.get("path", [""])[0])
                # 열기에 실패했을 때도 HTTP 오류를 보낼 수 있게 머리보다 먼저 연다.
                with file.open("rb") as source:
                    size = os.fstat(source.fileno()).st_size
                    if size > FILE_LIMIT:
                        raise RequestError(413, "파일은 50MB까지 볼 수 있다")
                    status, start, length = 200, 0, size
                    extra = {"Accept-Ranges": "bytes"}
                    match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", headers.get("range", ""))
                    if match and any(match.groups()):
                        first, last = match.groups()
                        first = (first.lstrip("0") or "0") if first else ""
                        last = (last.lstrip("0") or "0") if last else ""
                        # 끝이 시작보다 앞인 꼴은 조각 없는 요청으로 본다.
                        if not first or not last or (len(first), first) <= (len(last), last):
                            # 아주 긴 수는 파일 크기 밖인지 가릴 만큼만 읽는다.
                            if first:
                                start = min(int(first[:20]), size)
                                end = min(int(last[:20]), size - 1) if last else size - 1
                            else:
                                start, end = max(0, size - int(last[:20])), size - 1
                            if start >= size:
                                status, length = 416, 0
                                extra["Content-Range"] = f"bytes */{size}"
                            else:
                                status, length = 206, end - start + 1
                                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
                    source.seek(start)
                    content_type = FILE_TYPES[file.suffix.lower()]
                    if (content_type.split(";", 1)[0] in TEXT_TYPES and "range" not in headers
                            and length >= 1024 and _accepts_gzip(headers.get("accept-encoding", ""))):
                        body = await asyncio.to_thread(source.read, length)
                        await send(status, body, content_type, extra=extra)
                        sent = True
                    else:
                        if content_type.split(";", 1)[0] in TEXT_TYPES and "range" not in headers and length >= 1024:
                            extra["Vary"] = "Accept-Encoding"
                        sent = True
                        writer.write(self._headers(status, content_type, length, extra=extra))
                        remaining = length
                        while remaining:
                            chunk = source.read(min(65536, remaining))
                            if not chunk:
                                break
                            writer.write(chunk)
                            remaining -= len(chunk)
                            await asyncio.wait_for(writer.drain(), 60)
            elif path == "/api/tool":
                result = await self._tool(headers, data)
                denied = result.pop("denied", False)
                await send(403 if denied else 200, result)
                sent = True
            elif path == "/api/pair":
                await self._pair(writer, headers, data, peer)
                sent = True
            elif path == "/api/gate":
                # 답은 늘 200 이다 — 훅 프로그램은 allow 가 true 일 때만 도구를 보낸다(표가 틀려도 allow: false 로 답한다).
                allowed, reason = await self.app.conductor.gate_async(
                    headers.get("x-gaf-session"), data.get("tool_name"), data.get("tool_input"), data.get("cwd"))
                await send(200, {"ok": True, "allow": bool(allowed), "reason": str(reason or "")})
                sent = True
            else:
                result = await self._route(method, path, query, data, peer)
                if path == "/":
                    await send(200, result, "text/html; charset=utf-8", extra={"Content-Security-Policy": CSP})
                elif path == "/favicon.ico":
                    await send(204, b"")
                elif path in OPEN_ASSETS:
                    content_type = "application/manifest+json; charset=utf-8" if path == "/manifest.webmanifest" else "image/png"
                    await send(200, result, content_type, cache=True)
                else:
                    status = 200
                    if (isinstance(result, dict) and result.get("ok") is False
                            and not (path == "/api/dictate" and "stream" in data and data.get("warm") is not True)):
                        error = str(result.get("error") or "")
                        status = 404 if error.startswith(("없는 역할이다:", "없는(또는 이미 답한) 정할 것이다:")) else 400
                    await send(status, result)
                sent = True
                if path == "/api/shutdown" and result.get("stopped"):
                    self.shutdown.set()
        except RequestError as exc:
            if not sent:
                with contextlib.suppress(ConnectionError, OSError, asyncio.TimeoutError):
                    await send(exc.status, {"ok": False, "error": exc.message, **exc.extra})
                if not public or exc.status != 408:
                    await self._drain_input(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            pass
        except Exception:
            # 예외에는 요청 주소나 접근 값이 섞일 수 있으므로 원문을 적지 않는다.
            with contextlib.suppress(Exception):
                self.app.store.emit("error", message="뷰어 요청 처리 중 서버 안의 오류")
            if not sent:
                with contextlib.suppress(ConnectionError, OSError, asyncio.TimeoutError):
                    await send(500, {"ok": False, "error": "서버 안의 오류"})
        finally:
            (self._public_writers if public else self._writers).discard(writer)
            writer.close()
            with contextlib.suppress(ConnectionError, OSError, asyncio.TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 2)

    async def _pair(self, writer, headers, data, peer) -> None:
        """원격 화면에서 넣은 등록 코드가 맞으면 그 기기에 쿠키를 준다. PC 화면에는 새 기기를 알린다."""
        if not peer["remote"]:
            raise RequestError(400, "기기 등록은 원격으로 연 화면에서 한다")
        value, error = self.remote.pair(_text(data, "code"), name=_text(data, "name"), user=peer["user"])
        if value is None:
            if error.startswith("코드를 여러 번 틀려서"):
                with contextlib.suppress(Exception):
                    self.app.store.emit("remote", data={"action": "code_burned"})
                    self.app.conductor._notice("remote:code", "warn", "원격 기기 등록 코드를 여러 번 틀렸다(코드는 버렸다) — "
                                               "내가 한 것이 아니면 터미널에서 python -m gaf remote off 로 닫는다")
            raise RequestError(403, error)
        device_id = value.partition(".")[0]
        name = next((d["name"] for d in self.remote.devices() if d["id"] == device_id), "기기")
        with contextlib.suppress(Exception):
            self.app.store.emit("remote", data={"action": "paired", "device": device_id, "name": name})
            self.app.conductor._notice(f"remote:{device_id}", "warn",
                                       f"원격 기기를 새로 등록했다: {name} — 모르는 기기면 「설정 → 원격 기기」에서 끊는다")
        secure = peer["public"] or headers.get("x-forwarded-proto", "").strip().lower() == "https"
        await self._send(writer, 200, {"ok": True}, extra={"Set-Cookie": self.remote.cookie(value, secure=secure)})

    async def _projects(self, method, data, peer) -> dict:
        """이 PC에서 연 프로젝트들의 지금 모습(파일만 읽는다) · POST {"open": 폴더} 는 꺼진 프로젝트의 지휘자를 띄운다.

        PC 화면에는 떠 있는 프로젝트의 주소(접근 값이 들어 있다)를 같이 준다. 원격 기기에는 주소 · 폴더를 주지 않고
        원격으로 여는 포트만 준다 — 화면이 제 주소의 이름에 그 포트를 붙여 간다.
        """
        here = self.app.paths.root
        entries = projects.entries()
        if method == "POST":
            if peer["remote"]:
                raise RequestError(403, "이 일은 PC에서 한다")
            wanted = _text(data, "open")
            if not wanted:
                raise RequestError(400, "open에 켤 프로젝트의 폴더를 적는다")
            try:
                known = {os.path.normcase(str(Path(entry["root"]).resolve())) for entry in entries}
                listed = os.path.normcase(str(Path(wanted).resolve())) in known
            except (OSError, ValueError):
                listed = False
            if not listed:
                raise RequestError(404, "목록에 없는 프로젝트다")
            record, error, _ = await asyncio.to_thread(launch.start, Path(wanted))
            if record is None:
                raise RequestError(400, error)
            return {"ok": True, "url": record["url"]}
        same = os.path.normcase(str(here))
        rows = [projects.overview(entry) for entry in entries]
        if not any(os.path.normcase(row["root"]) == same for row in rows):
            rows.insert(0, projects.overview({"root": str(here), "name": self.app.config.project}))
        out = []
        for row in rows:
            current = os.path.normcase(row["root"]) == same
            view = {key: row[key] for key in ("name", "status", "decisions", "working", "blocked", "running")}
            view["current"] = current
            if peer["remote"]:
                view.update(serve=row["serve"], https=row["https"])
            else:
                view["root"] = row["root"]
                if row["running"] and not current:
                    record = read_server(Paths(Path(row["root"]))) or {}
                    if record.get("url"):
                        view["url"] = record["url"]
            out.append(view)
        out.sort(key=lambda view: not view["current"])          # 지금 프로젝트를 맨 앞에(나머지는 최근에 연 차례)
        return {"projects": out, "can_open": not peer["remote"]}

    def _remote_view(self, peer) -> dict:
        if peer["remote"]:
            return {"enabled": True, "via": "remote"}
        stamps = []
        for path in (projects._file(), remote_file()):
            try:
                stat = path.stat()
                stamps.append((stat.st_mtime_ns, stat.st_size))
            except OSError:
                stamps.append(None)
        if stamps != self._remote_stamp:
            rows, settings = projects.entries(), read_settings()
            here = os.path.normcase(str(self.app.paths.root.resolve()))
            row = next((r for r in rows if os.path.normcase(str(Path(r["root"]).resolve())) == here), {})
            public, hosts = row.get("public", False), settings["hosts"]
            # 다른 프로젝트만 공개로 열었어도 이 프로젝트에서는 켜기 단추를 보여 준다.
            enabled = bool(hosts) and (public or not any(r["public"] for r in rows))
            address = tailnet.address(hosts[0], row["serve"], https=row["https"]) if hosts and row.get("serve") else ""
            self._remote_cached = {"enabled": enabled, "via": "local", "devices": len(self.remote.devices()),
                                   "public": public, "address": address}
            self._remote_stamp = stamps
        return {**self._remote_cached, "listening": self._public_server is not None}

    def _voice_ready(self, *, refresh=False):
        now = monotonic()
        if refresh or self._voice_available is None or (not self._voice_available and now - self._voice_checked_at >= 60):
            self._voice_available = self.dictation.ready()
            self._voice_checked_at = now
        return self._voice_available

    async def _dictate(self, data):
        try:
            if data.get("warm") is True:
                return await self.dictation.warm()
            if "stream" in data:
                sid, at = data["stream"], data.get("at")
                if not isinstance(sid, str) or not STREAM_ID.fullmatch(sid):
                    raise RequestError(400, "stream 꼴이 맞지 않는다 — 영문 · 숫자 · _ · - 8~64자")
                if type(at) is not int or at < 0:
                    raise RequestError(400, "at 은 음이 아닌 정수여야 한다")
                for flag in ("final", "cancel"):
                    if flag in data and type(data[flag]) is not bool:
                        raise RequestError(400, flag + " 은 bool이어야 한다")
                try:
                    encoded = data.get("chunk", "" if data.get("cancel") else None)
                    if not isinstance(encoded, str):
                        raise ValueError
                    chunk = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error):
                    raise RequestError(400, "소리 덩이의 base64를 읽지 못했다") from None
                if not self._voice_ready(refresh=True):
                    raise RequestError(503, "이 PC에 음성 인식이 깔려 있지 않다 — python -m gaf voice setup")
                return await self.dictation.stream(sid, at, chunk, final=data.get("final", False),
                                                  cancel=data.get("cancel", False), language=data.get("language", "ko"))
            try:
                encoded = data.get("audio")
                if not isinstance(encoded, str):
                    raise ValueError
                audio = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise RequestError(400, "소리를 읽지 못했다") from None
            if len(audio) > WAV_MAX_BYTES:
                raise RequestError(413, "소리가 너무 크다 — 10MB까지 받는다")
            if not self._voice_ready(refresh=True):
                raise RequestError(503, "이 PC에 음성 인식이 깔려 있지 않다 — python -m gaf voice setup")
            result = await self.dictation.transcribe(audio, data.get("language", "ko"))
            if not result.get("ok"):
                raise RequestError(400, str(result.get("error") or "소리를 글로 바꾸지 못했다"))
            return {"ok": True, "text": result["text"]}
        finally:
            self._voice_ready(refresh=True)

    async def _remote_control(self, data):
        action = data.get("action")
        if action not in ("on", "off"):
            raise RequestError(400, "action은 on 또는 off여야 한다")
        port = data.get("funnel_port")
        if "funnel_port" in data and (type(port) is not int or port not in tailnet.FUNNEL_PORTS):
            raise RequestError(400, "공개 포트는 443 · 8443 · 10000 가운데 하나여야 한다")
        async with self._remote_lock:
            if action == "on":
                result = await asyncio.to_thread(remote_ops.public_on, self.app.paths, funnel_port=port, find=tailnet.find)
                if result["ok"]:
                    opened = await self.open_public(result["public_port"])
                    if not opened["ok"]:
                        result = {**result, "ok": False, "lines": [opened["error"]]}
            else:
                result = await asyncio.to_thread(remote_ops.public_off, self.app.paths, find=tailnet.find)
                await self.close_public()
            return {"ok": result["ok"], "address": result.get("address", ""), "lines": result["lines"],
                    "warn": result.get("warn", "")}

    async def _route(self, method, path, query, data, peer):
        c, store = self.app.conductor, self.app.store
        if path == "/":
            return (Path(__file__).parent / "web" / "index.html").read_bytes()
        if path == "/favicon.ico":
            return b""
        if path == "/manifest.webmanifest":
            return MANIFEST
        if path in OPEN_ASSETS:
            size, maskable = OPEN_ASSETS[path]
            return desktop.png_icon(size, maskable=maskable)
        if path == "/api/state":
            # 일의 확인 상태는 역할마다의 `last.verify`(여기)와 `/api/role` 의 일 목록에 있다 — 화면이 자주 부르는 이 길에서
            # 일 파일을 읽지 않는다(11-16 총괄이 뺌: GPT 의 첫 판은 부를 때마다 일 파일 50개를 읽었다).
            # version: 화면이 「서버가 새 판으로 다시 켜졌다」를 알아채 저를 새로 연다(11-19 폰이 업데이트 전 화면을 그대로 돌림).
            return {**c.snapshot(), "notices": c.notice_views(remote=peer["remote"]),
                    "seq": store.seq, "remote": self._remote_view(peer),
                    "voice": "pc" if self._voice_ready() else "browser", "version": __version__}
        if path == "/api/dictate":
            return await self._dictate(data)
        if path == "/api/remote":
            return await self._remote_control(data)
        if path == "/api/remote/qr":
            address = remote_ops.address_of(self.app.paths)
            if not address:
                raise RequestError(400, "이 프로젝트의 원격 접속이 꺼져 있다")
            from .vendor.qrcodegen import QrCode

            code = QrCode.encode_text(address, QrCode.Ecc.MEDIUM)
            size = code.get_size()
            return {"ok": True, "size": size,
                    "rows": ["".join("1" if code.get_module(x, y) else "0" for x in range(size)) for y in range(size)]}
        if path == "/api/library":
            try:
                return {"items": library.items(query.get("kind", [None])[0])}
            except library.LibraryError as exc:
                raise RequestError(400, str(exc)) from exc
        if path == "/api/chat":
            after = _number(query["after"][0], "after") if "after" in query else max(0, store.chat_count - 200)
            limit = _number(query.get("limit", ["200"])[0], "limit")
            return {"rows": store.read_chat(after, limit), "last": store.chat_count}
        if path == "/api/inbox":
            try:
                after = int(query.get("after", ["0"])[0])
            except ValueError:
                after = 0
            return {"rows": store.read_inbox(after, 200), "last": store.inbox_count}
        if method == "GET" and path in ("/api/role", "/api/task"):
            result = c.role_detail(query.get("name", [""])[0]) if path == "/api/role" else c.task(query.get("id", [""])[0])
            if result is None:
                raise RequestError(404, "없는 역할 또는 일이다")
            return result
        if path == "/api/job":
            result = c.job_output(query.get("id", [""])[0], _number(query.get("lines", ["40"])[0], "lines"))
            if result is None:
                raise RequestError(404, "없는 배경 일이다")
            return result
        if path == "/api/job/stop":
            result = await c.job_stop(_text(data, "id"), by=None)
            if not result["ok"]:
                raise RequestError(404, "없는 배경 일이다")
            return result
        if path == "/api/say":
            return await self._say(data)
        if path == "/api/custom":
            result = c.save_custom(data.get("scope"), data.get("role"), data.get("text"))
            if not result["ok"]:
                raise RequestError(400, result["error"])
            return result
        if path == "/api/decide":
            answers = data.get("answers")
            if not isinstance(answers, dict) or not all(isinstance(v, str) for v in answers.values()):
                raise RequestError(400, "answers는 {질문: 답 글}이어야 한다")
            return await c.decide(_text(data, "id"), answers)
        if path == "/api/stop":
            role = _text(data, "role").strip()
            return await c.stop_role(role) if role else await c.stop_all()
        if path == "/api/assign":
            return await c.assign(_text(data, "role"), _text(data, "title"), _text(data, "brief"),
                                  done_when=_text(data, "done_when"), sender=None,
                                  **{key: data[key] for key in ("check", "check_rounds", "check_timeout_min", "check_shell",
                                     "review", "review_files", "review_brief", "review_keep_going", "expect")
                                     if key in data})
        if path == "/api/role":
            action, name = _text(data, "action"), _text(data, "name")
            if action == "new":
                options = {k: _text(data, k) for k in ("engine", "model", "effort", "template") if k in data}
                write = data.get("write")
                if write is not None and (not isinstance(write, list) or not all(isinstance(p, str) for p in write)):
                    raise RequestError(400, "write는 글 목록이어야 한다")
                return c.new_role(name, duty=_text(data, "duty"), write=write, **options)
            if action in ("off", "on"):
                return c.set_role_off(name, action == "off")
            if action == "reset":
                return await c.reset_role(name)
            raise RequestError(400, "action은 new · off · on · reset 중 하나다")
        if path == "/api/notices":
            if data.get("clear") is not True:
                raise RequestError(400, "clear: true를 보낸다")
            c.clear_notices(remote=peer["remote"])
            return {"ok": True}
        if path == "/api/projects":
            return await self._projects(method, data, peer)
        if path == "/api/talk":
            name = query.get("role", [""])[0]
            if name not in c.roles:
                raise RequestError(404, "없는 역할이다")
            marks = {key: _number(query[key][0], key) for key in ("after", "before") if key in query}
            limit = min(500, _number(query.get("limit", ["200"])[0], "limit"))
            return {"rows": store.read_talk(name, limit=limit, **marks), "last": store.talk_count(name)}
        if path == "/api/changes":
            name, globs = query.get("role", [""])[0], None
            if name:                                    # 역할을 주면 그 역할의 쓰기 범위 안에서 바뀐 것만(총괄이면 전부)
                role = c.roles.get(name)
                if role is None:
                    raise RequestError(404, "없는 역할이다")
                if not role.lead:
                    globs = list(role.write)
            listed = await asyncio.to_thread(changes.listing, self.app.paths.root, globs=globs)
            return {**listed, **({"scope": globs} if globs is not None else {})}
        if path == "/api/change":
            found = await asyncio.to_thread(changes.file_diff, self.app.paths.root, query.get("path", [""])[0])
            if found is None:
                raise RequestError(404, "바뀐 파일이 아니다")
            return found
        if path == "/api/send":
            result = await c.send(_text(data, "to"), _text(data, "text"), sender=None)
            if result.get("ok") is False and str(result.get("error") or "").startswith("없는 역할"):
                raise RequestError(404, str(result["error"]))
            return result
        if path == "/api/pair/start":
            started = self.remote.start_pairing()
            if started is None:
                raise RequestError(400, "원격 접속이 꺼져 있다 — PC의 「설정 → 원격 기기 → 폰 · 웹에서 쓰기 켜기」를 누른다")
            return {"ok": True, **started}
        if path == "/api/devices":
            if method == "GET":
                return {"devices": self.remote.devices()}
            device_id = _text(data, "forget")
            if not self.remote.forget(device_id):
                raise RequestError(404, "없는 기기다")
            with contextlib.suppress(Exception):
                store.emit("remote", data={"action": "forgot", "device": device_id})
                c._clear_notice(f"remote:{device_id}")
            return {"ok": True}
        if path == "/api/shutdown":
            if_idle = data.get("if_idle", False)
            if not isinstance(if_idle, bool):
                raise RequestError(400, "if_idle은 bool이어야 한다")
            busy = c.busy_view()
            if if_idle and not busy["idle"]:
                return {"ok": True, "stopped": False, "busy": busy}
            return {"ok": True, "stopped": True, "cut": busy}
        raise RequestError(404, "없는 길이다")

    async def _tool(self, headers, data):
        """총괄 명령 하나. 답은 {"ok", "text"} — 명령이 안 된 것(없는 역할 등)도 200 이고, 쓸 수 없는 세션만 403 이다."""
        args = data.get("args", {})
        if not isinstance(args, dict):
            raise RequestError(400, "args는 객체여야 한다")
        # 머리가 있으면(빈 값이어도) 세션이 친 것으로 본다 — 접근 값 확인을 건너뛴 요청이 사람으로 통하지 않게.
        return await self.app.conductor.command(_text(data, "name"), args, token=headers.get("x-gaf-session"))

    async def _say(self, data):
        text = _text(data, "text")
        from .conductor import too_long            # 긴 글은 그림을 풀기 전에 거른다(11-12 QA 결함-22)

        if too_long(text.strip()):
            raise RequestError(400, too_long(text.strip()))
        images = data.get("images", [])
        if not isinstance(images, list) or len(images) > 4:
            raise RequestError(400, "그림은 4장까지 보낸다")
        decoded = []
        for entry in images:
            if not isinstance(entry, dict):
                raise RequestError(400, "그림은 이름과 base64 자료로 보낸다")
            name, value = _text(entry, "name"), _text(entry, "data")
            if Path(name).suffix.lower() not in IMAGE_TYPES:
                raise RequestError(400, "그림 확장자는 png · jpg · jpeg · gif · webp만 쓴다")
            if len(value) > ((IMAGE_LIMIT + 2) // 3) * 4:
                raise RequestError(400, "그림은 장당 8MB까지 보낸다")
            try:
                content = base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error):
                raise RequestError(400, "그림의 base64 자료가 잘못됐다") from None
            if len(content) > IMAGE_LIMIT:
                raise RequestError(400, "그림은 장당 8MB까지 보낸다")
            # 긴 이름도 확장자를 잃지 않게 끝의 확장자는 따로 남긴다.
            suffix = Path(name).suffix.lower()
            stem = re.sub(r"[^A-Za-z0-9가-힣ㄱ-ㅎㅏ-ㅣ._-]", "", name[:-len(suffix)])
            cleaned = (stem.strip(".") or "그림")[:60 - len(suffix)] + suffix
            decoded.append((cleaned, content))
        paths = []
        stamp = datetime.fromtimestamp(self.app.store.clock()).strftime("%Y%m%d-%H%M%S")
        try:
            for name, content in decoded:
                while True:
                    self._upload_n += 1
                    path = self.app.paths.uploads / f"{stamp}-{self._upload_n}-{name}"
                    try:
                        with path.open("xb") as output:
                            output.write(content)
                    except FileExistsError:
                        continue
                    paths.append(path)
                    break
            result = await self.app.conductor.say(text, images=paths)
            if result.get("ok"):
                return result
        except BaseException:
            for path in paths:
                path.unlink(missing_ok=True)
            raise
        for path in paths:
            path.unlink(missing_ok=True)
        return result

    def _file(self, value):
        try:
            return self._resolve_file(value)
        except (OSError, ValueError, RuntimeError):
            raise RequestError(404, "없는 파일이다") from None

    def _resolve_file(self, value):
        relative = value.replace("\\", "/")
        if not relative or Path(relative).is_absolute() or PureWindowsPath(relative).drive or "\x00" in relative:
            raise RequestError(404, "없는 파일이다")
        root = self.app.paths.root.resolve()
        if relative.startswith(("uploads/", ".gaf/uploads/")):
            base = self.app.paths.uploads.resolve()
            tail = relative.split("uploads/", 1)[1]
            candidate = (base / tail).resolve()
            allowed = candidate.is_relative_to(base) and base.is_relative_to(root)
        else:
            candidate = (root / relative).resolve()
            allowed = candidate.is_relative_to(root) and any(
                entry.get("path") == value for entry in self.app.conductor.artifacts())
        if not allowed or candidate.suffix.lower() not in FILE_TYPES or not candidate.is_file():
            raise RequestError(404, "없는 파일이다")
        if candidate.stat().st_size > FILE_LIMIT:
            raise RequestError(413, "파일은 50MB까지 볼 수 있다")
        return candidate

    async def _stream(self, reader, writer, after):
        store = self.app.store
        queue = store.subscribe()
        disconnected = asyncio.create_task(reader.read(1))
        pending = None
        try:
            writer.write(self._headers(200, "text/event-stream; charset=utf-8", stream=True))
            writer.write(b"retry: 3000\n\n")
            await writer.drain()
            last = after
            # 먼저 구독했으므로 지난 기록을 읽는 동안 온 것도 놓치지 않는다.
            while True:
                rows = store.read_events(last)
                for event in rows:
                    writer.write(f"id: {event['seq']}\ndata: ".encode("ascii") + _json(event) + b"\n\n")
                    last = event["seq"]
                    await asyncio.wait_for(writer.drain(), 60)
                if len(rows) < 500:
                    break
            while True:
                pending = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait((pending, disconnected), timeout=25,
                                             return_when=asyncio.FIRST_COMPLETED)
                if disconnected in done:
                    break
                if pending in done:
                    event = pending.result()
                    if event["seq"] <= last:
                        continue
                    last = event["seq"]
                    writer.write(f"id: {last}\ndata: ".encode("ascii") + _json(event) + b"\n\n")
                else:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                    writer.write(b": \n\n")
                await asyncio.wait_for(writer.drain(), 60)
        finally:
            store.unsubscribe(queue)
            disconnected.cancel()
            if pending is not None:
                pending.cancel()
            await asyncio.gather(disconnected, *([pending] if pending is not None else []), return_exceptions=True)
