"""PC의 Whisper 받아쓰기. 모델은 부모가 쉬면 끄는 별도 일꾼에서만 연다.

Windows 에서 다른 스레드가 stdin 파이프를 읽는 동안에는 numpy 같은 확장 모듈 import 가 끝나지 않는다 — 11-16 실측.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import site
import struct
import subprocess
import sys
from time import monotonic
import wave

from . import paths

MODEL = "large-v3-turbo"
IDLE_SECONDS = 300
REQUEST_SECONDS = 120
RATE = 16000
MIN_SECONDS = 0.3
MAX_SECONDS = 300
WAV_MAX_BYTES = 10 * 1024 * 1024
STREAM_WINDOW_SECONDS = 15
STREAM_TAIL_SECONDS = 4
STREAM_IDLE_SECONDS = 120
STREAM_LIMIT = 4
STREAM_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def voice_dir() -> Path:
    if os.environ.get("GAF_VOICE_DIR"):
        return Path(os.environ["GAF_VOICE_DIR"])
    if os.name != "nt" and not os.environ.get("GAF_RUN_DIR"):
        return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "gaf" / "voice"
    return paths.run_dir().parent / "voice"


def model_dir() -> Path:
    return voice_dir() / "models"


def installed() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def model_ready() -> bool:
    return any(path.is_file() for path in model_dir().glob("models--*/snapshots/*/model.bin"))


def ready() -> bool:
    return installed() and model_ready()


def _gpu_dirs() -> list[Path]:
    folders = list(site.getsitepackages())
    if site.ENABLE_USER_SITE:
        folders.append(site.getusersitepackages())
    return sorted({folder.resolve() for entry in folders
                   for folder in Path(entry).glob("nvidia/*/bin") if folder.is_dir()})


def status() -> dict:
    return {"installed": installed(), "model": model_ready(), "gpu_libs": bool(_gpu_dirs()),
            "dir": str(voice_dir()), "real": os.path.realpath(voice_dir())}


def check_wav(data: bytes) -> tuple[float, str]:
    if not isinstance(data, bytes):
        return 0.0, "소리를 읽지 못했다"
    if len(data) > WAV_MAX_BYTES:
        return 0.0, "소리가 너무 크다 — 10MB까지 받는다"
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            if (source.getcomptype() != "NONE" or source.getnchannels() != 1
                    or source.getsampwidth() != 2 or source.getframerate() != RATE):
                return 0.0, "소리 꼴이 맞지 않는다 — 압축하지 않은 모노 · 16비트 · 16000Hz WAV가 필요하다"
            frames = source.getnframes()
            seconds = frames / RATE
            if seconds < MIN_SECONDS:
                return 0.0, "소리가 너무 짧다"
            if seconds > MAX_SECONDS:
                return 0.0, "소리가 너무 길다 — 5분까지 받는다"
            if len(source.readframes(frames)) != frames * 2:
                return 0.0, "소리가 중간에 끊겼다"
            return seconds, ""
    except (wave.Error, EOFError, OSError, ValueError, struct.error):
        return 0.0, "소리를 읽지 못했다"


def sniff(data: bytes) -> str:
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if data.startswith(b"OggS"):
        return "ogg"
    if data[4:8] == b"ftyp":
        return "mp4"
    return ""


def check_audio(data: bytes) -> tuple[float, str]:
    if not isinstance(data, bytes):
        return 0.0, "소리를 읽지 못했다"
    if len(data) > WAV_MAX_BYTES:
        return 0.0, "소리가 너무 크다 — 10MB까지 받는다"
    kind = sniff(data)
    if kind == "wav":
        return check_wav(data)
    if len(data) < 200:
        return 0.0, "소리가 너무 짧다"
    if not kind:
        return 0.0, "소리 꼴이 맞지 않는다"
    return 0.0, ""


class _AudioError(ValueError):
    """소리 검사에서 정한, 화면에 그대로 보여 줄 까닭."""


def _decode(data: bytes, *, partial=False):
    """압축한 소리(WebM · Ogg · MP4)를 16kHz 모노 float32 표본으로 푼다 — PyAV 를 직접 부른다.

    faster_whisper.audio.decode_audio 는 쓰지 않는다. 같이 깔리는 PyAV 의 판에 따라 그 함수가 넘기는
    `av.open()` 인자가 없어 TypeError 로 죽는다(11-16 실측 — faster-whisper 1.2.1 + av 19.0.2).
    """
    import av
    import numpy as np

    chunks, total, limit = [], 0, int(MAX_SECONDS * RATE)
    try:
        with av.open(io.BytesIO(data), mode="r") as container:
            stream = next(item for item in container.streams if item.type == "audio")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)

            def keep(result):
                nonlocal total
                frames = result if isinstance(result, list) else [] if result is None else [result]
                for frame in frames:
                    chunk = frame.to_ndarray().reshape(-1)
                    total += len(chunk)
                    if total > limit:
                        raise _AudioError("소리가 너무 길다 — 5분까지 받는다")
                    chunks.append(chunk)

            for frame in container.decode(stream):
                keep(resampler.resample(frame))
            keep(resampler.resample(None))
    except (OSError, av.error.FFmpegError):
        if not partial:
            raise
        if not chunks:
            raise _AudioError("소리를 읽지 못했다") from None
    if not chunks:
        return np.zeros(0, dtype="float32")
    return np.concatenate(chunks).astype("float32") / 32768.0


def _language(value) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[a-z]{2,3}", value) else "ko"


# add_dll_directory 의 손잡이가 닫히면 검색 경로도 사라진다. 일꾼이 끝날 때까지 쥔다.
_dll_handles = []


def _load_model(device):
    for folder in _gpu_dirs():
        directory = str(folder)
        if directory not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            _dll_handles.append(os.add_dll_directory(directory))
    from faster_whisper import WhisperModel

    options = {"device": device, "compute_type": "float16" if device == "cuda" else "int8",
               "download_root": str(model_dir()), "local_files_only": True}
    if device == "cpu":
        options["cpu_threads"] = min(8, os.cpu_count() or 1)
    return WhisperModel(MODEL, **options)


def worker_main(stdin, stdout, *, load=None, device=None, decode=None) -> int:
    """JSON 한 줄에 한 번 받아쓴다. 시험은 load 로 가짜 모델을, decode 로 가짜 풀기를 준다."""
    choice = device or os.environ.get("GAF_VOICE_DEVICE", "auto")
    chosen = choice if choice in ("cpu", "cuda") else "cuda" if _gpu_dirs() else "cpu"
    load = load if load is not None else _load_model
    decode = decode if decode is not None else _decode
    model = None
    warmed = False

    def recognize(samples, language, *, warm=False, partial=False, offset=0):
        # 데울 때만 무음도 남겨 VAD와 GPU 추론을 한 번에 준비한다.
        options = {"vad_parameters": {"threshold": 0.0, "neg_threshold": 0.0, "min_speech_duration_ms": 0}} if warm else {}
        segments, _ = model.transcribe(samples, language=language, beam_size=1 if partial else 5, vad_filter=True,
                                       condition_on_previous_text=False, without_timestamps=False, **options)
        segments = list(segments)
        return ("".join(segment.text for segment in segments).strip(),
                [] if warm else [[round(offset + segment.start, 2), round(offset + segment.end, 2),
                                  segment.text.strip()] for segment in segments])

    for line in iter(stdin.readline, ""):
        try:
            request = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(request, dict) or type(request.get("id")) is not int:
            continue
        reply = {"id": request["id"], "ok": False}
        try:
            warm = request.get("warm") is True
            partial = request.get("partial") is True and not warm
            if warm and warmed:
                stdout.write(json.dumps({**reply, "ok": True, "warm": True, "device": chosen}) + "\n")
                stdout.flush()
                continue
            data = b"" if warm else base64.b64decode(request.get("audio"), validate=True)
            seconds, error = (0.5, "") if warm else check_audio(data)
            if partial and error == "소리가 너무 짧다":
                error = ""
            if error:
                raise _AudioError(error)
            if warm:
                import numpy as np

                samples = np.zeros(int(RATE * .5), dtype="float32")
            elif sniff(data) == "wav":
                import numpy as np

                with wave.open(io.BytesIO(data), "rb") as source:
                    samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype("float32") / 32768.0
            else:
                samples = decode(data, partial=True) if partial else decode(data)
                seconds = len(samples) / RATE
                if seconds < MIN_SECONDS and not partial:
                    raise _AudioError("소리가 너무 짧다")
                if seconds > MAX_SECONDS:
                    raise _AudioError("소리가 너무 길다 — 5분까지 받는다")
            if partial and sniff(data) == "wav":
                seconds = len(samples) / RATE
            offset = request.get("offset", 0)
            offset = min(offset, seconds) if type(offset) in (int, float) and offset >= 0 else 0
            if warm:
                offset = 0
            if offset:
                samples = samples[int(offset * RATE):]
            if not warm and len(samples) < int(MIN_SECONDS * RATE):
                stdout.write(json.dumps({**reply, "ok": True, "text": "", "segments": [],
                                         "seconds": seconds, "device": chosen}) + "\n")
                stdout.flush()
                continue
            language = _language(request.get("language"))
            retry = False
            try:
                if model is None:
                    model = load(chosen)
                text, segments = recognize(samples, language, warm=warm, partial=partial, offset=offset)
            except Exception:
                if chosen != "cuda":
                    raise
                retry = True
            if retry:
                # 첫 transcribe 의 DLL 오류도 여기로 온다. GPU 모델 참조를 먼저 놓는다.
                model = None
                chosen = "cpu"
                model = load(chosen)
                text, segments = recognize(samples, language, warm=warm, partial=partial, offset=offset)
            if warm:
                warmed = True
                reply.update(ok=True, warm=True, device=chosen)
            else:
                reply.update(ok=True, text=text, segments=segments, seconds=seconds, device=chosen)
        except _AudioError as error:
            reply["error"] = str(error)
        except Exception:
            # 모델 예외 원문에는 입력 정보가 섞일 수 있다. 소리와 받아쓴 글은 기록하지 않는다.
            reply["error"] = "소리를 글로 바꾸지 못했다 — 음성 인식 설치와 소리 파일을 확인한다"
        stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0


class Dictation:
    def __init__(self, *, command=None, idle=IDLE_SECONDS, clock=monotonic, env_key="", cloud=None, words=None):
        self.command = command if command is not None else [sys.executable, "-X", "utf8", "-m", "gaf.dictation", "worker"]
        # GPT 로 받아쓰기(1.9.0 · gaf/cloudvoice.py): 키가 있으면 마칠 때의 글을 GPT 로 받아쓰고, 없거나 안 되면 이 PC 의 것으로.
        self.env_key = env_key                    # 지휘자를 켤 때의 환경변수 값(환경을 정리하기 전에 읽은 것)
        self._cloud = cloud                       # 시험이 가짜 손을 준다
        self.words = words if words is not None else (lambda: ())      # 힌트 낱말(프로젝트 · 역할 이름)을 주는 손
        self._lock = asyncio.Lock()
        self._process = None
        self._id = 0
        self.idle = idle
        self._idle_handle = None
        self._idle_tasks = set()
        self._idle_version = 0
        self.clock = clock
        self._streams = {}

    def _cancel_idle(self):
        self._idle_version += 1
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

    def _arm_idle(self):
        self._cancel_idle()
        if self._process is not None and self._process.returncode is None:
            self._idle_handle = asyncio.get_running_loop().call_later(self.idle, self._on_idle, self._idle_version)

    def _on_idle(self, version):
        if version != self._idle_version:
            return
        self._idle_handle = None
        task = asyncio.create_task(self._stop_idle(version))
        self._idle_tasks.add(task)
        task.add_done_callback(self._idle_tasks.discard)

    async def _stop_idle(self, version):
        if version != self._idle_version:
            return                      # 새 요청이 자기 타이머를 건다.
        if self._lock.locked():
            self._arm_idle()
            return
        async with self._lock:
            if version == self._idle_version:
                await self._stop()

    def local_ready(self):
        """이 PC 의 음성 인식(꾸러미 + 모델)이 준비됐나."""
        return ready()

    def cloud_key(self) -> str:
        from . import cloudvoice

        return cloudvoice.read_key(self.env_key)

    def ready(self):
        return self.local_ready() or bool(self.cloud_key())

    async def _gpt(self, audio: bytes, language: str):
        """키가 있으면 GPT 로 받아쓴다 → (글, "") · 안 되면 (None, 까닭) · 키가 없으면 (None, "")."""
        key = self.cloud_key()
        if not key:
            return None, ""
        from . import cloudvoice

        try:
            return await asyncio.to_thread(self._cloud or cloudvoice.transcribe, audio, key=key,
                                           language=_language(language), words=list(self.words())), ""
        except cloudvoice.CloudError as error:
            return None, str(error)

    async def _exchange(self, request):
        if self._process is None or self._process.returncode is not None:
            await self._stop()
            self._process = await asyncio.create_subprocess_exec(
                *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=1024 * 1024,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                cwd=str(Path(__file__).resolve().parent.parent),
                env={**os.environ, "GAF_VOICE_DIR": str(voice_dir().resolve())})
        self._process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
        await self._process.stdin.drain()
        response = json.loads(await self._process.stdout.readline())
        if (not isinstance(response, dict) or response.get("id") != request["id"]
                or type(response.get("ok")) is not bool):
            raise ValueError("잘못된 일꾼 답")
        if response["ok"] and request.get("warm") is True:
            if response.get("warm") is not True or response.get("device") not in ("cpu", "cuda"):
                raise ValueError("잘못된 데우기 답")
        elif response["ok"]:
            seconds = response.get("seconds")
            if (not isinstance(response.get("text"), str) or type(seconds) not in (int, float)
                    or not 0 <= seconds < float("inf")):
                raise ValueError("잘못된 받아쓰기")
        return response

    async def transcribe(self, wav: bytes, language: str = "ko") -> dict:
        seconds, error = check_audio(wav)
        if error:
            return {"ok": False, "error": error}
        text, note = await self._gpt(wav, language)
        if text is not None:
            return {"ok": True, "text": text, "seconds": seconds, "engine": "gpt"}
        if note and not self.local_ready():
            return {"ok": False, "error": note}          # GPT 가 안 됐고 이 PC 의 음성 인식도 없다
        duration = seconds if sniff(wav) == "wav" else MAX_SECONDS
        reply = await self._request({"audio": base64.b64encode(wav).decode("ascii"), "language": _language(language)}, duration)
        return {**reply, "note": note} if note and reply.get("ok") else reply

    async def warm(self) -> dict:
        """녹음하는 동안 일꾼과 모델을 준비한다. 미설치는 조용히 돌려준다."""
        if not self.local_ready():
            if self.cloud_key():
                return {"ok": True, "warm": True, "device": "gpt"}          # 데울 것이 없다 — 마칠 때 GPT 로 받아쓴다
            return {"ok": False, "error": "이 PC에 음성 인식이 깔려 있지 않다"}
        return await self._request({"warm": True})

    async def stream(self, sid, at, chunk, *, final=False, cancel=False, language="ko") -> dict:
        """녹음의 바이트를 이어 받고, 아직 굳히지 않은 꼬리만 받아쓴다."""
        now = self.clock()
        for key, value in list(self._streams.items()):
            if now - value["touched"] > STREAM_IDLE_SECONDS:
                self._streams.pop(key, None)
        if not isinstance(sid, str) or not STREAM_ID.fullmatch(sid):
            return {"ok": False, "lost": True, "error": "stream 꼴이 맞지 않는다"}
        if cancel:
            self._streams.pop(sid, None)
            async with self._lock:
                self._streams.pop(sid, None)     # 데우기 뒤 차례를 기다리던 첫 덩이도 거둔다.
            return {"ok": True}
        self._cancel_idle()
        async with self._lock:
            def lost(reason):
                self._streams.pop(sid, None)
                return {"ok": False, "lost": True, "error": reason}

            try:
                if type(at) is not int or at < 0 or not isinstance(chunk, bytes):
                    return lost("덩이의 자리나 소리 꼴이 맞지 않는다")
                state = self._streams.get(sid)
                if state is None:
                    if at != 0:
                        return lost("앞 덩이가 없다")
                    if sniff(chunk) not in ("webm", "ogg", "mp4"):
                        return lost("이어 받을 소리 꼴이 맞지 않는다")
                    if len(self._streams) >= STREAM_LIMIT:
                        oldest = min(self._streams, key=lambda key: self._streams[key]["touched"])
                        self._streams.pop(oldest)
                    state = self._streams[sid] = dict(audio=bytearray(), until=0, committed="", touched=now, seconds=0)
                audio = state["audio"]
                if at > len(audio):
                    return lost("덩이 사이가 비었다")
                overlap = min(len(chunk), len(audio) - at)
                if audio[at:at + overlap] != chunk[:overlap]:
                    return lost("다시 보낸 덩이가 다르다")
                if len(audio) + len(chunk) - overlap > WAV_MAX_BYTES:
                    return lost("소리가 너무 크다 — 10MB까지 받는다")
                audio.extend(chunk[overlap:])
                state["touched"] = self.clock()
                # 키만 있고 이 PC 의 음성 인식은 없다 — 미리 보기 없이 마칠 때 GPT 로 받아쓴다(키가 없으면 전과 같이 일꾼에게 간다).
                gpt_only = bool(self.cloud_key()) and not self.local_ready()
                note = ""
                if final:
                    # 마칠 때의 글은 녹음 전체를 GPT 로 다시 받아쓴 것이다(키가 있을 때) — 말하는 동안의 미리 보기는 이 PC 가 한다.
                    whole, note = await self._gpt(bytes(audio), language)
                    if whole is not None:
                        return {"ok": True, "text": whole, "seconds": state["seconds"], "final": True, "engine": "gpt"}
                    if gpt_only:
                        return {"ok": False, "error": note or "GPT 로 받아쓰지 못했다"}
                elif gpt_only:
                    return {"ok": True, "text": "", "seconds": 0, "final": False}
                reply = await self._request_locked({"audio": base64.b64encode(audio).decode("ascii"),
                    "offset": state["until"], "partial": not final, "language": _language(language)}, MAX_SECONDS)
                if not reply["ok"]:
                    return reply
                state["seconds"] = reply["seconds"]
                text = " ".join(part.strip() for part in (state["committed"], reply["text"]) if part.strip())
                if not final and reply["seconds"] - state["until"] > STREAM_WINDOW_SECONDS:
                    # 마지막 구간은 끝 낱말을 다시 고칠 수 있게 늘 남긴다.
                    for start, end, words in reply.get("segments", [])[:-1]:
                        if end > reply["seconds"] - STREAM_TAIL_SECONDS:
                            break
                        if end > state["until"]:
                            state["committed"] = " ".join(part.strip() for part in (state["committed"], words) if part.strip())
                            state["until"] = end
                done = {"ok": True, "text": text, "seconds": reply["seconds"], "final": final}
                return {**done, "note": note} if note else done
            finally:
                if final:
                    self._streams.pop(sid, None)
                self._arm_idle()

    async def _request(self, payload: dict, duration: float = 0) -> dict:
        self._cancel_idle()
        async with self._lock:
            return await self._request_locked(payload, duration)

    async def _request_locked(self, payload: dict, duration: float = 0) -> dict:
        self._cancel_idle()
        try:
            self._id += 1
            request = {"id": self._id, **payload}
            reply = await asyncio.wait_for(self._exchange(request), REQUEST_SECONDS + 2 * duration)
            if reply["ok"]:
                if payload.get("warm"):
                    return {"ok": True, "warm": True, "device": reply["device"]}
                result = {"ok": True, "text": reply["text"], "seconds": reply["seconds"]}
                if "partial" in payload:
                    result["segments"] = reply.get("segments", [])
                return result
            return {"ok": False, "error": str(reply.get("error") or "소리를 글로 바꾸지 못했다")}
        except asyncio.CancelledError:
            await self._stop(force=True)
            raise
        except TimeoutError:
            await self._stop(force=True)
            return {"ok": False, "error": "소리를 글로 바꾸는 시간이 지났다 — 다시 누른다"}
        except (OSError, ValueError, RecursionError):
            await self._stop(force=True)
            return {"ok": False, "error": "음성 인식 일꾼이 멈췄다 — 다시 누른다"}
        finally:
            self._arm_idle()

    async def _stop(self, *, force=False):
        process, self._process = self._process, None
        if process is None:
            return
        if process.stdin is not None:
            with contextlib.suppress(OSError):
                process.stdin.close()
        if force and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
        try:
            await asyncio.wait_for(process.wait(), .5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()

    async def close(self):
        self._streams.clear()
        self._cancel_idle()
        async with self._lock:
            self._streams.clear()
            self._cancel_idle()
            await self._stop()
        if self._idle_tasks:
            await asyncio.gather(*self._idle_tasks, return_exceptions=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["worker"]:
        raise SystemExit(worker_main(sys.stdin, sys.stdout))
    raise SystemExit(2)
