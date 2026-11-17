"""글에 번호를 붙이고 받음과 실패를 기억하는 우체국."""
from __future__ import annotations

import threading
import time
from dataclasses import MISSING, asdict, dataclass, field, replace
from typing import Callable

from .paths import JsonlLog, Paths, _json_lines
from .store import Store


class PostError(Exception):
    pass


class UnknownRecipient(PostError):
    pass


class TooManyHops(PostError):
    pass


@dataclass
class Letter:
    id: str
    sender: str
    to: str
    kind: str
    body: str
    hops: int
    human: bool
    ref: str | None
    sent_at: float
    acked_at: float | None
    tries: int
    error: str | None
    images: list[str] = field(default_factory=list)     # 같이 보낸 그림(프로젝트 뿌리 기준 경로)
    reply: bool = False                                 # 보낸 세션이 회신을 청했다 — 받은 쪽의 마지막 답을 돌려준다(11-13)


class PostOffice:
    def __init__(self, paths: Paths, store: Store, *, known: Callable[[], set[str]],
                 max_hops: int = 0, clock=time.time):
        self.paths, self.store = paths, store
        self.known, self.max_hops, self.clock = known, max_hops, clock
        self.paths.ensure()
        self._lock = threading.RLock()
        self._letters: dict[str, Letter] = {}
        self._number = 0
        self._log = JsonlLog(paths.post)         # 못 쓴 줄은 쥐고 있다가 다음에 쓴다(기록 한 줄 때문에 글이 막히지 않게)
        for record in _json_lines(paths.post):
            try:
                # 기본값이 있는 칸(나중에 더한 칸)은 옛 기록에 없어도 된다.
                letter = Letter(**{key: record[key] for key, spec in Letter.__dataclass_fields__.items()
                                   if key in record or (spec.default is MISSING and spec.default_factory is MISSING)})
                number = int(letter.id.removeprefix("m-"))
            except (KeyError, TypeError, ValueError):
                continue
            self._letters[letter.id] = letter
            self._number = max(self._number, number)

    def _record(self, operation: str, letter: Letter) -> None:
        self._letters[letter.id] = letter
        self._log.append({"op": operation, **asdict(letter)})
        if self._log.recovered is not None:
            held, dropped = self._log.recovered
            self._log.recovered = None
            self.store.emit("error", message=f"기록 파일 {self.paths.post.name} 을 한동안 쓰지 못했다 — 쥐고 있던 {held}줄을 지금 썼다"
                                             + (f" · 한도를 넘어 버린 줄 {dropped}" if dropped else ""))
        self.store.emit(f"post.{operation}", **{"id": letter.id, "from": letter.sender,
                        "to": letter.to, "hops": letter.hops, "error": letter.error})

    def flush(self) -> bool:
        """쥐고 있는 줄을 써 본다 → 남은 것이 없으면 True."""
        with self._lock:
            return self._log.flush()

    def trouble(self) -> str:
        with self._lock:
            if not self._log.error:
                return ""
            return f"{self.paths.post.name}(쥔 줄 {len(self._log.pending)} · {self._log.error.split(':', 1)[0]})"

    def _get(self, letter_id) -> Letter:
        try:
            return self._letters[letter_id]
        except KeyError as exc:
            raise PostError(f"없는 글 번호다: {letter_id}") from exc

    def cap(self, hops: int) -> int:
        """지휘자가 스스로 보내는 답 글 · 알림의 건너온 횟수 — 한도가 있으면 한도에서 멈춘다(그 글은 버리지 않는다)."""
        return min(hops, self.max_hops) if self.max_hops else hops

    def send(self, *, sender, to, kind, body, ref=None, hops=0, human=False, images=None, reply=False) -> Letter:
        with self._lock:
            if to not in self.known():
                raise UnknownRecipient(f"받는 역할이 없거나 꺼져 있다: {to}")
            if self.max_hops and hops > self.max_hops:       # 0 = 한도 없음
                raise TooManyHops(f"글이 너무 많이 건너왔다: {hops} (한도: {self.max_hops})")
            if kind not in ("user", "task", "reply", "note", "review"):
                raise PostError(f"모르는 글 종류다: {kind}")
            # 번호는 적기 전에 올린다 — 적다가 오류가 나도 같은 번호를 두 번 쓰지 않는다(11-12 QA 결함-04)
            self._number += 1
            letter = Letter(f"m-{self._number:06d}", sender, to, kind, body, hops,
                            human, ref, self.clock(), None, 0, None, [str(path) for path in images or []], bool(reply))
            self._record("sent", letter)
            return letter

    def ack(self, letter_id) -> None:
        with self._lock:
            self._record("acked", replace(self._get(letter_id), acked_at=self.clock(), error=None))

    def fail(self, letter_id, error: str) -> None:
        with self._lock:
            self._record("failed", replace(self._get(letter_id), error=error))

    def retry(self, letter_id) -> Letter:
        with self._lock:
            letter = self._get(letter_id)
            if letter.acked_at is not None:
                raise PostError(f"이미 받은 글이다: {letter_id}")
            if letter.to not in self.known():
                raise UnknownRecipient(f"받는 역할이 없거나 꺼져 있다: {letter.to}")
            retried = replace(letter, tries=letter.tries + 1, error=None)
            self._record("retried", retried)
            return retried

    def pending(self, to: str | None = None) -> list[Letter]:
        with self._lock:
            return sorted((letter for letter in self._letters.values()
                           if letter.acked_at is None and letter.error is None
                           and (to is None or letter.to == to)),
                          key=lambda letter: int(letter.id[2:]))

    def header(self, letter) -> str:
        who = "사람" if letter.human else "에이전트"
        return f"[글 {letter.id} · 보낸 이 {letter.sender}({who}) · 건너옴 {letter.hops}]"
