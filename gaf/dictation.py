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
import wave

from . import paths

MODEL = "large-v3-turbo"
IDLE_SECONDS = 300
REQUEST_SECONDS = 120
RATE = 16000
MIN_SECONDS = 0.3
MAX_SECONDS = 300
WAV_MAX_BYTES = 10 * 1024 * 1024


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


def _decode(data: bytes):
    """압축한 소리(WebM · Ogg · MP4)를 16kHz 모노 float32 표본으로 푼다 — PyAV 를 직접 부른다.

    faster_whisper.audio.decode_audio 는 쓰지 않는다. 같이 깔리는 PyAV 의 판에 따라 그 함수가 넘기는
    `av.open()` 인자가 없어 TypeError 로 죽는다(11-16 실측 — faster-whisper 1.2.1 + av 19.0.2).
    """
    import av
    import numpy as np

    chunks, total, limit = [], 0, int(MAX_SECONDS * RATE)
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
                    # 작은 파일이 몇 시간짜리 소리로 풀리며 메모리를 먹지 않게, 한도를 넘는 순간 멈춘다.
                    raise _AudioError("소리가 너무 길다 — 5분까지 받는다")
                chunks.append(chunk)

        for frame in container.decode(stream):
            keep(resampler.resample(frame))
        keep(resampler.resample(None))
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

    def recognize(samples, language):
        segments, _ = model.transcribe(samples, language=language, beam_size=5, vad_filter=True,
                                       condition_on_previous_text=False, without_timestamps=True)
        return "".join(segment.text for segment in segments).strip()

    for line in iter(stdin.readline, ""):
        try:
            request = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(request, dict) or type(request.get("id")) is not int:
            continue
        reply = {"id": request["id"], "ok": False}
        try:
            data = base64.b64decode(request.get("audio"), validate=True)
            seconds, error = check_audio(data)
            if error:
                raise _AudioError(error)
            if sniff(data) == "wav":
                import numpy as np

                with wave.open(io.BytesIO(data), "rb") as source:
                    samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype("float32") / 32768.0
            else:
                samples = decode(data)
                seconds = len(samples) / RATE
                if seconds < MIN_SECONDS:
                    raise _AudioError("소리가 너무 짧다")
                if seconds > MAX_SECONDS:
                    raise _AudioError("소리가 너무 길다 — 5분까지 받는다")
            language = _language(request.get("language"))
            retry = False
            try:
                if model is None:
                    model = load(chosen)
                text = recognize(samples, language)
            except Exception:
                if chosen != "cuda":
                    raise
                retry = True
            if retry:
                # 첫 transcribe 의 DLL 오류도 여기로 온다. GPU 모델 참조를 먼저 놓는다.
                model = None
                chosen = "cpu"
                model = load(chosen)
                text = recognize(samples, language)
            reply.update(ok=True, text=text, seconds=seconds, device=chosen)
        except _AudioError as error:
            reply["error"] = str(error)
        except Exception:
            # 모델 예외 원문에는 입력 정보가 섞일 수 있다. 소리와 받아쓴 글은 기록하지 않는다.
            reply["error"] = "소리를 글로 바꾸지 못했다 — 음성 인식 설치와 소리 파일을 확인한다"
        stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0


class Dictation:
    def __init__(self, *, command=None, idle=IDLE_SECONDS):
        self.command = command if command is not None else [sys.executable, "-X", "utf8", "-m", "gaf.dictation", "worker"]
        self._lock = asyncio.Lock()
        self._process = None
        self._id = 0
        self.idle = idle
        self._idle_handle = None
        self._idle_tasks = set()
        self._idle_version = 0

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

    def ready(self):
        return ready()

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
        if response["ok"]:
            seconds = response.get("seconds")
            if (not isinstance(response.get("text"), str) or type(seconds) not in (int, float)
                    or not 0 <= seconds < float("inf")):
                raise ValueError("잘못된 받아쓰기")
        return response

    async def transcribe(self, wav: bytes, language: str = "ko") -> dict:
        seconds, error = check_audio(wav)
        if error:
            return {"ok": False, "error": error}
        self._cancel_idle()
        async with self._lock:
            self._cancel_idle()
            try:
                self._id += 1
                request = {"id": self._id, "audio": base64.b64encode(wav).decode("ascii"), "language": _language(language)}
                duration = seconds if sniff(wav) == "wav" else MAX_SECONDS
                reply = await asyncio.wait_for(self._exchange(request), REQUEST_SECONDS + 2 * duration)
                if reply["ok"]:
                    return {"ok": True, "text": reply["text"], "seconds": reply["seconds"]}
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
        self._cancel_idle()
        async with self._lock:
            self._cancel_idle()
            await self._stop()
        if self._idle_tasks:
            await asyncio.gather(*self._idle_tasks, return_exceptions=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["worker"]:
        raise SystemExit(worker_main(sys.stdin, sys.stdout))
    raise SystemExit(2)
