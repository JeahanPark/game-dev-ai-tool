"""바깥을 부르는 한 문 — 허락된 호스트에 https 로만, 천천히, 크기와 시간의 상한 안에서.

출처 모듈은 이 문으로만 바깥을 부른다(yt-dlp 는 따로 — 그 도구가 스스로 부른다). 프록시 설정은 쓰지 않고, 다른 호스트나
http 로 넘겨주면 따라가지 않는다. 막히면(403) 돌아가지 않고 그대로 알린다. 한도(429)와 서버 오류(5xx)는 한 번만 다시 부르고,
「그 검색을 끝내지 못했다」(422)는 다시 부르지 않는다 — 같은 무거운 검색을 또 보내는 것이 되어서다(물러날 길은 출처 모듈이 정한다).
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__

USER_AGENT = f"GameDevAITool/{__version__} (reference research)"      # 브라우저인 척하지 않는다
MAX_BYTES = 2 * 1024 * 1024
REQUEST_SECONDS = 20.0
LEAST_SECONDS = 3.0                     # 이보다 적게 남으면 부르지 않는다
RETRY_WAIT = 3.0
RATE_WAIT_MAX = 20.0                    # 한도에 걸렸을 때 기다리는 가장 긴 시간


class NetError(Exception):
    """부르지 못했다. status 는 증거의 출처 상태 낱말이다(rate-limited · blocked · timeout · schema-drift · skipped-time · error)."""

    def __init__(self, status: str, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


class _SameHost(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if new.scheme != "https" or new.hostname != old.hostname or new.port not in (None, 443):
            raise NetError("blocked", "다른 주소로 넘겨주어 따라가지 않았다")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url: str, timeout: float):
    """(상태, 머리말, 몸). 오류 상태의 몸은 앞 64KiB 만 읽는다."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _SameHost())
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                                   "Accept-Encoding": "identity"})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), _read_body(response, timeout)
    except urllib.error.HTTPError as error:
        with error:
            return error.code, dict(error.headers.items()), error.read(64 * 1024)


def _read_body(response, seconds: float, clock=time.monotonic) -> bytes:
    """본문을 조금씩 읽으며 시간을 본다 — 소켓의 시간 제한은 「한 번 읽기」마다라, 조금씩 흘리는 응답은 거기에 걸리지 않는다."""
    end = clock() + seconds
    chunks, size = [], 0
    while size <= MAX_BYTES:
        chunk = response.read(65536)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if clock() > end:
            raise TimeoutError("본문이 제때 다 오지 않았다")
    return b"".join(chunks)


def _no_constant(name):
    raise ValueError(f"JSON 에 없는 값이다: {name}")       # NaN · Infinity — 숫자 셈을 깨뜨린다


def _header(headers, name: str) -> str:
    wanted = name.casefold()
    return next((str(value) for key, value in (headers or {}).items() if str(key).casefold() == wanted), "")


class Door:
    def __init__(self, hosts, *, deadline: float, pace: float = 1.2, budget: int = 30, fetch=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.hosts = {host.casefold() for host in hosts}
        self.deadline, self.pace, self.budget = deadline, pace, budget
        self._fetch, self._clock, self._sleep = fetch or _fetch, clock, sleep
        self._memo: dict[str, object] = {}
        self._last: dict[str, float] = {}
        self.requests = 0

    def left(self) -> float:
        return self.deadline - self._clock()

    def _host(self, url) -> str:
        if not isinstance(url, str) or any(ch.isspace() or ord(ch) < 32 for ch in url):
            raise NetError("error", "허락되지 않은 주소다")
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").casefold()
        if parts.scheme != "https" or host not in self.hosts or parts.username or parts.password or parts.port not in (None, 443):
            raise NetError("error", "허락되지 않은 주소다")
        return host

    def _once(self, url: str, host: str):
        if self.requests >= self.budget:
            raise NetError("skipped-time", f"이 조사의 요청 수({self.budget}번)를 다 썼다")
        wait = self._last.get(host, float("-inf")) + self.pace - self._clock()
        if wait > 0:
            self._sleep(wait)
        left = self.left()
        if left < LEAST_SECONDS:
            raise NetError("skipped-time", "남은 시간이 요청 하나에 모자라 부르지 않았다")
        self._last[host] = self._clock()
        self.requests += 1
        try:
            return self._fetch(url, min(REQUEST_SECONDS, left))
        except NetError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise NetError("timeout", f"답이 오지 않았다({type(exc).__name__})") from None
        except urllib.error.URLError as exc:
            timed = isinstance(exc.reason, (TimeoutError, socket.timeout))
            raise NetError("timeout" if timed else "error", f"연결하지 못했다({type(exc.reason).__name__})") from None
        except OSError as exc:
            raise NetError("error", f"연결하지 못했다({type(exc).__name__})") from None

    def get_json(self, url: str):
        """JSON 을 받아 돌려준다. 같은 주소는 한 조사 안에서 한 번만 부른다. 못 받으면 NetError."""
        host = self._host(url)
        if url in self._memo:
            return self._memo[url]
        status, headers, body = self._once(url, host)
        if status == 429 or status >= 500:
            wait = RETRY_WAIT
            if status == 429:
                try:
                    wait = float(_header(headers, "X-RateLimit-Reset") or RETRY_WAIT)
                except ValueError:
                    wait = RETRY_WAIT
                if not 0 <= wait <= RATE_WAIT_MAX:
                    raise NetError("rate-limited", f"한도에 걸렸다(HTTP 429 · {wait:g}초 뒤에 풀린다고 한다)")
            if self.left() - wait >= LEAST_SECONDS and self.requests < self.budget:
                self._sleep(wait)
                status, headers, body = self._once(url, host)
        if status == 429 or status == 422:
            said = _error_text(body)        # 서버가 적어 보낸 글이다 — 그렇다고 표시해 싣는다(지시로 읽히지 않게)
            raise NetError("rate-limited", f"HTTP {status} — " + (f"서버의 말 「{said}」" if said else "한도에 걸렸거나 서버가 그 검색을 끝내지 못했다"))
        if status in (401, 403):
            raise NetError("blocked", f"HTTP {status} — 이 PC 의 요청을 받지 않는다")
        if status != 200:
            raise NetError("error", f"HTTP {status}")
        if len(body) > MAX_BYTES:
            raise NetError("schema-drift", "응답이 너무 크다")
        try:
            data = json.loads(body.decode("utf-8"), parse_constant=_no_constant)
        except (UnicodeDecodeError, ValueError):
            raise NetError("schema-drift", "JSON 이 아닌 응답이 왔다") from None
        self._memo[url] = data
        return data


def _error_text(body) -> str:
    try:
        data = json.loads(bytes(body).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ""
    value = data.get("error") if isinstance(data, dict) else None
    return " ".join(str(value).split())[:200] if isinstance(value, str) else ""
