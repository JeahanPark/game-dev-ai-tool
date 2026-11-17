"""상태, 이벤트, 대화와 일 기록을 파일에 적는다."""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import threading
import time
from datetime import datetime
from itertools import chain, islice

from .paths import (JsonlLog, Paths, _atomic_json, _json_lines, _last_record, _no_constant, _read_json, _replace,
                    _reverse_lines)

OPEN_TASK = ("queued", "running")           # 끝나지 않은 일의 상태
TALK_TEXT_MAX = 20_000                      # 역할별 대화 줄 하나에 적는 글의 한도(넘으면 자른다)
TALK_ROLL_BYTES = 8 * 1024 * 1024           # 역할의 대화 파일이 이보다 커지면 옛 것을 한 벌(<역할>.jsonl.1)만 남기고 새로 쓴다
TALK_ROLL_EVERY = 200                       # 그 크기를 살피는 간격(줄)
_TALK_NAME = re.compile(r'[^\\/:*?"<>|\x00-\x1f]+')    # 파일 이름이 될 수 있는 역할 이름
_SEQ_HEAD = re.compile(rb'\{"seq": (\d+)[,}]')     # 이벤트 한 줄의 머리 — 줄을 다 풀지 않고 순번만 본다


def redact(data):
    """이벤트에 적을 값을 고른다: 비밀 키는 지우고 · 긴 글은 자르고 · JSON 이 못 되는 수는 값 없음으로."""
    if isinstance(data, dict):
        return {key: redact(value) for key, value in data.items()
                if not str(key).lower().endswith(("token", "secret", "password", "key"))}
    if isinstance(data, (list, tuple)):          # 튜플 속의 것도 살핀다(11-12 QA 결함-11) — 적힐 때는 목록이다
        return [redact(value) for value in data]
    if isinstance(data, float) and not math.isfinite(data):
        return None                              # NaN · Infinity 가 적히면 화면으로 가는 흐름이 그 줄에서 끊긴다(결함-15)
    return data[:2000] if isinstance(data, str) else data


def _copy(record: dict) -> dict:
    return json.loads(json.dumps(record, ensure_ascii=False), parse_constant=_no_constant)


class Store:
    def __init__(self, paths: Paths, *, clock=time.time, max_events_bytes: int = 5 * 1024 * 1024):
        self.paths = paths
        self.clock = clock
        self.max_events_bytes = max_events_bytes
        self.paths.ensure()
        self._lock = threading.RLock()
        self._queues: set[asyncio.Queue] = set()
        self._archive_first: dict[str, int] = {}
        # 이벤트 · 대화는 못 써도 올리지 않는다 — 쥐고 있다가 다음에 쓴다(기록 한 줄 때문에 일꾼이 죽지 않게)
        self._events_log = JsonlLog(paths.events)
        self._chat_log = JsonlLog(paths.chat)
        self._inbox_log = JsonlLog(paths.state_dir / "inbox.jsonl")
        # 역할별 대화(받은 글 · 한 말 · 부른 도구 · 턴의 끝) — 역할마다 파일 하나 · 번호도 따로다(11-13)
        self._talk_logs: dict[str, JsonlLog] = {}
        self._talk_n: dict[str, int] = {}
        # 일 기록도 못 쓰면 쥐고 있다가 쓴다. 끝나지 않은 일은 쓴 뒤에도 쥐고 있다 — 읽기는 이것이 먼저다(11-12 QA 결함-17)
        self._tasks: dict[str, dict] = {}
        self._tasks_unwritten: dict[str, str] = {}       # 파일에 못 쓴 일 번호 → 까닭
        self.seq = max(((_last_record(path, "seq") or {}).get("seq", 0)
                        for path in self._event_files()), default=0)
        self._chat_n = (_last_record(paths.chat, "n") or {}).get("n", 0)
        self._inbox_n = (_last_record(self._inbox_log.path, "n") or {}).get("n", 0)
        self._task_counter = (_read_json(self._counter_path) or {}).get("n", 0)
        if type(self._task_counter) is not int:
            self._task_counter = 0
        for path in paths.tasks_dir.glob("t-*.json"):
            if re.fullmatch(r"t-[0-9]+", path.stem):
                self._task_counter = max(self._task_counter, int(path.stem[2:]))

    @property
    def _counter_path(self):
        return self.paths.state_dir / "task-seq.json"

    def _first_seq(self, path) -> int:
        # 넘긴 파일은 다시 바뀌지 않으므로 첫 순번을 기억해 둔다(같은 초에 넘긴 파일은 이름순이 만든 순서와 다르다).
        cached = self._archive_first.get(path.name)
        if cached is None:
            cached = next((record["seq"] for record in _json_lines(path) if type(record.get("seq")) is int), 0)
            self._archive_first[path.name] = cached
        return cached

    def _event_files(self):
        archives = sorted(self.paths.state_dir.glob("events-*.jsonl"), key=lambda path: (self._first_seq(path), path.name))
        return [*archives, self.paths.events]

    def _timestamp(self) -> str:
        return datetime.fromtimestamp(self.clock()).astimezone().isoformat(timespec="seconds")

    def _rotate(self) -> None:
        try:
            if not self.paths.events.exists() or self.paths.events.stat().st_size <= self.max_events_bytes:
                return
            stamp = datetime.fromtimestamp(self.clock()).strftime("%Y%m%d-%H%M%S")
            target = self.paths.state_dir / f"events-{stamp}.jsonl"
            index = 1
            while target.exists():
                target = self.paths.state_dir / f"events-{stamp}-{index}.jsonl"
                index += 1
            _replace(self.paths.events, target)
            self.paths.events.touch()
        except OSError:
            return                               # 이번에는 넘기지 못했다(파일이 잠김 등) — 다음 이벤트 때 다시 본다

    def emit(self, kind: str, *, role: str | None = None, task: str | None = None,
             data: dict | None = None, **more) -> dict:
        """이벤트 한 줄을 적는다. data 칸의 이름이 kind · role · task 와 겹치면 data= 로 넘긴다.

        파일에 못 써도(잠김 · 디스크) 오류를 올리지 않는다: 순번을 매기고 구독자에게 알린 뒤, 줄은 쥐고 있다가 다음에 쓴다.
        """
        with self._lock:
            event = {"seq": self.seq + 1, "ts": self._timestamp(), "kind": kind,
                     "role": role, "task": task, "data": redact({**(data or {}), **more})}
            written = self._events_log.append(event)     # 못 적을 값이면 여기서 오류가 난다(순번은 그대로다)
            self.seq = event["seq"]
            if written:
                self._rotate()
            for queue in tuple(self._queues):
                queue.put_nowait(event)
            self._note_recovery(self._events_log)
            return event

    def _note_recovery(self, log: JsonlLog) -> None:
        """막혔던 기록 파일이 풀렸으면 그 일을 이벤트로 남긴다."""
        if log.recovered is None:
            return
        held, dropped = log.recovered
        log.recovered = None
        self.emit("error", message=f"기록 파일 {log.path.name} 을 한동안 쓰지 못했다 — 쥐고 있던 {held}줄을 지금 썼다"
                                   + (f" · 한도를 넘어 버린 줄 {dropped}" if dropped else ""))

    def flush(self, patient: bool | None = None) -> bool:
        """쥐고 있는 것(이벤트 · 대화의 줄 · 못 쓴 일 기록)을 써 본다 → 남은 것이 없으면 True. 지휘자가 상태를 쓸 때마다 부른다.

        막혀 있던 파일은 한 번씩만 해 본다. patient 를 True 로 주면 쉬어 가며 다시 해 본다(끄기 전의 마지막 쓰기).
        """
        with self._lock:
            done = self._events_log.flush(patient)
            done = self._chat_log.flush(patient) and done
            done = self._inbox_log.flush(patient) and done
            for log in self._talk_logs.values():
                done = log.flush(patient) and done
            self._note_recovery(self._chat_log)
            self._note_recovery(self._inbox_log)
            self._note_recovery(self._events_log)
            written = [task_id for task_id in sorted(self._tasks_unwritten)
                       if self._write_task_file(task_id, patient=bool(patient))]
            if written:
                self.emit("error", message="일 기록 파일을 한동안 쓰지 못했다 — 쥐고 있던 기록을 지금 썼다: " + ", ".join(written))
            return done and not self._tasks_unwritten

    def trouble(self) -> list[str]:
        """지금 못 쓰고 있는 기록 파일마다 한 마디(없으면 빈 목록)."""
        with self._lock:
            stuck = [f"{log.path.name}(쥔 줄 {len(log.pending)} · {log.error.split(':', 1)[0]})"
                     for log in (self._events_log, self._chat_log, self._inbox_log) if log.error]
            return stuck + [f"tasks/{task_id}.json(못 씀 · {error.split(':', 1)[0]})"
                            for task_id, error in sorted(self._tasks_unwritten.items())]

    def read_events(self, after: int = 0, limit: int = 500) -> list[dict]:
        """after 뒤의 이벤트를 오래된 것부터 limit 개.

        새 것만 볼 때(after 가 끝에 가깝다)는 파일을 끝에서부터 읽는다. 오래된 데서부터 볼 때는 그 순번이 든 파일을 앞에서부터
        읽어 limit 개에서 멈춘다 — after 보다 새 줄을 다 읽지 않는다(11-12 QA 결함-23: 20만 줄을 after=0 으로 받는 손님 하나가
        500줄마다 20만 줄을 다시 읽게 해서 지휘자가 몇 분 막혔다).
        """
        if limit <= 0:
            return []
        with self._lock:
            held = [record for record in self._events_log.pending if record["seq"] > after]     # 아직 못 쓴 줄(파일의 것보다 새것이다)
            if self.seq - after <= max(limit, 500) * 2:
                records = self._newer_events(after)
            else:
                records = self._events_from(after, limit)
            return sorted(records + held, key=lambda row: row["seq"])[:limit]

    def _newer_events(self, after: int) -> list[dict]:
        """after 보다 새 이벤트 전부 — 파일을 끝에서부터 읽는다(새 것이 적을 때 쓴다)."""
        records: list[dict] = []
        for path in reversed(self._event_files()):
            reached = False
            try:
                for line in _reverse_lines(path):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line, parse_constant=_no_constant)
                    except (ValueError, UnicodeError, RecursionError):
                        continue
                    if not isinstance(record, dict) or type(record.get("seq")) is not int:
                        continue
                    if record["seq"] <= after:
                        reached = True
                        break
                    records.append(record)
            except FileNotFoundError:
                continue
            if reached:
                break
        return records

    def _events_from(self, after: int, limit: int) -> list[dict]:
        """after 뒤의 이벤트를 앞에서부터 limit 개 — 통째로 지난 파일은 건너뛰고, 지난 줄은 순번만 보고 넘긴다."""
        files = self._event_files()
        firsts = [self._first_seq(path) for path in files[:-1]]
        firsts.append(next((record["seq"] for record in _json_lines(files[-1]) if type(record.get("seq")) is int), 0))
        records: list[dict] = []
        for index, path in enumerate(files):
            following = next((first for first in firsts[index + 1:] if first), None)
            if following is not None and following <= after + 1:
                continue                             # 다음 파일이 after 이하에서 시작한다 — 이 파일의 줄은 다 지났다
            try:
                with path.open("rb") as stream:
                    for line in stream:
                        head = _SEQ_HEAD.match(line)
                        if head is not None and int(head.group(1)) <= after:
                            continue
                        try:
                            record = json.loads(line, parse_constant=_no_constant)
                        except (ValueError, UnicodeError, RecursionError):
                            continue
                        if not isinstance(record, dict) or type(record.get("seq")) is not int or record["seq"] <= after:
                            continue
                        records.append(record)
                        if len(records) >= limit:
                            return records
            except FileNotFoundError:
                continue
        return records

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue) -> None:
        self._queues.discard(queue)

    def set_state(self, state: dict, *, patient: bool = True) -> None:
        """상태를 통째로 바꿔 쓴다. 기다리지 않는다 — 자주 쓰지 않게 묶는 일은 부르는 쪽(지휘자)이 한다.

        못 쓰면 OSError 를 올린다(다시 쓰는 것은 부르는 쪽이 한다). patient 가 False 면 잠긴 파일을 한 번만 두드린다.
        """
        with self._lock:
            self.flush()
            _atomic_json(self.paths.state, {**state, "seq": self.seq, "updated_at": self._timestamp()}, patient=patient)

    def read_state(self) -> dict | None:
        return _read_json(self.paths.state)

    def append_chat(self, who: str, text: str, *, images: list[str] | None = None,
                    task: str | None = None) -> dict:
        with self._lock:
            record = {"n": self._chat_n + 1, "ts": self._timestamp(), "who": who,
                      "text": text, "images": list(images or []), "task": task}
            self._chat_log.append(record)                # 못 써도 올리지 않는다(쥐고 있다가 다음에 쓴다)
            self._chat_n = record["n"]
            self._note_recovery(self._chat_log)
            return record

    def append_inbox(self, sender: str, text: str, *, kind: str, ref: str | None = None,
                     reply: bool = False) -> dict:
        """밖의 총괄에게 갈 글을 번호와 함께 덧붙인다. 못 쓴 줄은 대화처럼 쥐고 있다가 쓴다."""
        with self._lock:
            record = {"n": self._inbox_n + 1, "ts": self._timestamp(), "from": sender,
                      "kind": kind, "ref": ref, "reply": reply, "text": text}
            self._inbox_log.append(record)
            self._inbox_n = record["n"]
            self._note_recovery(self._inbox_log)
            return record

    @property
    def inbox_count(self) -> int:
        """우편함의 마지막 번호다. 파일을 다시 읽지 않는다."""
        return self._inbox_n

    def read_inbox(self, after: int = 0, limit: int = 200) -> list[dict]:
        with self._lock:
            held = list(self._inbox_log.pending)
        records = (record for record in chain(_json_lines(self._inbox_log.path), held)
                   if type(record.get("n")) is int and record["n"] > after)
        return list(islice(records, max(0, limit)))

    # ── 역할별 대화 ──────────────────────────────────────────────────────────────────
    def _talk_name(self, role) -> str:
        name = role if isinstance(role, str) else ""
        if (not name or name != name.strip(" .") or name in (".", "..") or not _TALK_NAME.fullmatch(name)):
            raise ValueError("대화를 적을 수 없는 역할 이름이다")
        return name

    def _talk_path(self, name: str, old: bool = False):
        return self.paths.state_dir / "talk" / (name + (".jsonl.1" if old else ".jsonl"))

    def _talk_last(self, name: str) -> int:
        """그 역할의 마지막 줄 번호(없으면 0) — 지금 파일 · 없으면 넘긴 파일에서 읽는다."""
        known = self._talk_n.get(name)
        if known is not None:
            return known
        for path in (self._talk_path(name), self._talk_path(name, old=True)):
            number = (_last_record(path, "n") or {}).get("n", 0)
            if type(number) is int and number > 0:
                return number
        return 0

    def append_talk(self, role: str, kind: str, **data) -> dict:
        """역할의 대화 줄 하나를 적는다 → 적은 줄({"n", "ts", "kind", …}). 화면의 역할별 대화가 읽는다.

        kind: in(받은 글 — from · what · text · title · task · images) · text(한 말) · tool(name · target) ·
        end(턴의 끝 — ok · seconds · error) · error · denied(문이 막은 도구) · system(지휘자의 알림) · file(올린 파일).
        파일에 못 써도 올리지 않는다(쥐고 있다가 다음에 쓴다). 이름이 파일 이름이 못 되면 ValueError.
        """
        name = self._talk_name(role)
        with self._lock:
            log = self._talk_logs.get(name)
            if log is None:
                self._talk_n[name] = self._talk_last(name)
                log = self._talk_logs[name] = JsonlLog(self._talk_path(name))
            record = {"n": self._talk_n[name] + 1, "ts": self._timestamp(), "kind": str(kind)}
            for key, value in data.items():
                if value is None:
                    continue
                if isinstance(value, str) and len(value) > TALK_TEXT_MAX:
                    value = value[:TALK_TEXT_MAX] + "\n…(줄임)"
                elif isinstance(value, float) and not math.isfinite(value):
                    continue
                record[key] = value
            log.append(record)
            self._talk_n[name] = record["n"]
            if record["n"] % TALK_ROLL_EVERY == 0:
                self._roll_talk(name, log)
            return record

    def _roll_talk(self, name: str, log: JsonlLog) -> None:
        path = self._talk_path(name)
        try:
            if path.stat().st_size <= TALK_ROLL_BYTES or not log.flush():
                return
            os.replace(path, self._talk_path(name, old=True))
        except OSError:
            return                               # 이번에는 못 넘겼다(누가 읽는 중 등) — 다음에 다시 본다

    def talk_count(self, role) -> int:
        try:
            name = self._talk_name(role)
        except ValueError:
            return 0
        with self._lock:
            return self._talk_last(name)

    def read_talk(self, role, *, after: int | None = None, before: int | None = None, limit: int = 200) -> list[dict]:
        """역할의 대화 줄을 오래된 것부터. 늘 끝에서부터 읽어 limit 줄에서 멈춘다(파일 전체를 읽지 않는다).

        after 를 주면 그 번호 뒤의 줄 가운데 가장 새 limit 줄(화면이 이어 받을 때 — 사이가 너무 벌어졌으면 끝 쪽만 온다),
        before 를 주면 그 번호 앞의 limit 줄(위로 더 읽기), 둘 다 없으면 마지막 limit 줄이다.
        """
        try:
            name = self._talk_name(role)
        except ValueError:
            return []
        if limit <= 0:
            return []
        with self._lock:
            log = self._talk_logs.get(name)
            held = list(log.pending) if log is not None else []     # 아직 못 쓴 줄(파일의 것보다 새것이다)
        rows: list[dict] = []
        for record in chain(reversed(held), self._talk_backwards(self._talk_path(name)),
                            self._talk_backwards(self._talk_path(name, old=True))):
            number = record["n"]
            if before is not None and number >= before:
                continue
            if after is not None and number <= after:
                break
            if rows and number >= rows[-1]["n"]:
                continue                         # 쥐고 있던 줄이 그 사이 파일에 쓰였다 — 두 번 세지 않는다
            rows.append(record)
            if len(rows) >= limit:
                break
        rows.reverse()
        return rows

    @staticmethod
    def _talk_backwards(path):
        try:
            for line in _reverse_lines(path):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line, parse_constant=_no_constant)
                except (ValueError, UnicodeError, RecursionError):
                    continue
                if isinstance(record, dict) and type(record.get("n")) is int:
                    yield record
        except OSError:
            return

    @property
    def chat_count(self) -> int:
        """지금까지의 대화 줄 수(마지막 n)."""
        return self._chat_n

    def read_chat(self, after: int = 0, limit: int = 200) -> list[dict]:
        with self._lock:
            held = list(self._chat_log.pending)          # 아직 못 쓴 줄(파일의 것보다 뒤다)
        records = (record for record in chain(_json_lines(self.paths.chat), held)
                   if type(record.get("n")) is int and record["n"] > after)
        return list(islice(records, max(0, limit)))

    def _task_path(self, task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r"t-[0-9]+", task_id):
            raise ValueError("일 번호는 t-0001 같은 모양이어야 한다")
        return self.paths.tasks_dir / f"{task_id}.json"

    def write_task(self, record: dict) -> bool:
        """일 기록을 통째로 바꿔 쓴다 → 파일에 썼으면 True.

        파일에 못 써도(잠김 · 디스크) 오류를 올리지 않는다: 가장 새 기록을 쥐고 있다가 다음 flush 때 다시 쓰고, 그동안
        read_task · list_tasks 는 쥐고 있는 것을 돌려준다 — 일이 끝나는 순간 파일이 잠겨도 결과 글을 잃지 않는다(11-12 QA 결함-17).
        끝나지 않은 일(queued · running)은 쓴 뒤에도 쥐고 있어, 그 파일을 읽지 못하는 때에도 일이 이어진다.
        번호 모양이 틀리면 ValueError, JSON 으로 못 적을 값이면 TypeError 다(둘 다 아무것도 바꾸지 않는다).
        """
        with self._lock:
            task_id = record.get("id")
            self._task_path(task_id)
            snapshot = _copy(record)                     # 사본을 쥔다 — 부르는 쪽이 뒤에 고쳐도 바뀌지 않는다 · NaN 은 값 없음으로(결함-15)
            self._task_counter = max(self._task_counter, int(task_id[2:]))
            self._tasks[task_id] = snapshot
            return self._write_task_file(task_id, patient=task_id not in self._tasks_unwritten)

    def _write_task_file(self, task_id: str, *, patient: bool) -> bool:
        record = self._tasks[task_id]
        try:
            _atomic_json(self._task_path(task_id), record, patient=patient)
        except OSError as exc:
            self._tasks_unwritten[task_id] = f"{type(exc).__name__}: {exc}"
            return False
        self._tasks_unwritten.pop(task_id, None)
        if record.get("status") not in OPEN_TASK:
            del self._tasks[task_id]                     # 끝난 일은 파일이 원본이다
        return True

    def read_task(self, task_id) -> dict | None:
        path = self._task_path(task_id)
        with self._lock:
            held = self._tasks.get(task_id)
            if held is not None:
                return _copy(held)
        record = _read_json(path)
        if record is not None and record.get("id") == task_id and record.get("status") in OPEN_TASK:
            with self._lock:                             # 지난 실행이 남긴 끝나지 않은 일 — 이제부터 쥐고 있는다
                self._tasks.setdefault(task_id, _copy(record))
        return record

    def open_tasks(self) -> list[dict]:
        """끝나지 않은 일(queued · running)을 번호순으로 — 쥐고 있는 기록에서만 읽는다(파일은 읽지 않는다).

        끝나지 않은 일은 쓴 뒤에도 쥐고 있고(write_task), 지난 실행이 남긴 것은 읽은 뒤부터 쥔다(read_task — 지휘자는
        켤 때 줄에 남은 일을 읽는다). 끝난 일까지 다 읽는 list_tasks 를 자주 부르는 자리에 쓰지 않게 따로 둔다.
        """
        with self._lock:
            rows = [_copy(record) for record in self._tasks.values() if record.get("status") in OPEN_TASK]
        return sorted(rows, key=lambda record: int(record["id"][2:]))

    def list_tasks(self, role: str | None = None, limit: int = 20) -> list[dict]:
        records: list[dict] = []
        if limit <= 0:
            return records
        # 일 번호는 만든 순서다. 결과를 나중에 고쳐도 순서는 바뀌지 않는다. 새것부터 필요한 만큼만 읽는다.
        numbered = {int(path.stem[2:]): path for path in self.paths.tasks_dir.glob("t-*.json")
                    if re.fullmatch(r"t-[0-9]+", path.stem)}
        with self._lock:                                 # 쥐고 있는 기록(끝나지 않은 일 · 못 쓴 일)이 파일보다 새것이다
            held = {int(task_id[2:]): _copy(record) for task_id, record in self._tasks.items()}
        for number in sorted(numbered.keys() | held.keys(), reverse=True):
            record = held.get(number) or _read_json(numbered[number])
            if record and re.fullmatch(r"t-[0-9]+", str(record.get("id", ""))):
                if role is None or record.get("role") == role:
                    records.append(record)
                    if len(records) >= limit:
                        break
        return records

    def next_task_id(self) -> str:
        with self._lock:
            number = self._task_counter + 1
            with contextlib.suppress(OSError):           # 못 써도 된다 — 켤 때 일 파일의 번호로도 맞춘다
                _atomic_json(self._counter_path, {"n": number})
            self._task_counter = number
            return f"t-{number:04d}"
