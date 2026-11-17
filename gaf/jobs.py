"""배경 프로세스와 출력 파일을 쥔다. 지휘자나 세션은 모른다."""
from __future__ import annotations

import contextlib
import ctypes
import locale
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path


class JobError(Exception):
    """배경 일을 띄우지 못한 까닭이다."""


def find_bash() -> str | None:
    if os.name != "nt":
        return "/bin/bash" if Path("/bin/bash").is_file() else shutil.which("sh")
    git = shutil.which("git")
    candidates = []
    if git:
        for root in Path(git).resolve().parents:
            candidates.extend((root / "bin/bash.exe", root / "usr/bin/bash.exe"))
    candidates.append(Path("C:/Program Files/Git/bin/bash.exe"))
    for path in candidates:
        if "system32" not in path.as_posix().casefold().split("/") and path.is_file():
            return str(path)
    return None


def shell_argv(shell, command) -> list[str]:
    if shell == "bash":
        bash = find_bash()
        if bash is None:
            raise JobError("bash 를 찾지 못했다 — shell 을 powershell 로 준다")
        return [bash, "-c", command]
    if shell == "powershell":
        head = ("$OutputEncoding = [Console]::OutputEncoding = "
                "[System.Text.UTF8Encoding]::new(); ")
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", head + command]
    raise JobError("모르는 shell 이다 — bash 또는 powershell 로 준다")


def _windows_job():
    """손잡이를 닫으면 그 안의 프로세스가 모두 죽는 Windows 묶음이다."""
    from ctypes import wintypes as w

    class Basic(ctypes.Structure):
        _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                    ("LimitFlags", w.DWORD), ("MinWorkingSet", ctypes.c_size_t),
                    ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in
                    ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("IO", IO), ("ProcessMemory", ctypes.c_size_t),
                    ("JobMemory", ctypes.c_size_t), ("PeakProcessMemory", ctypes.c_size_t),
                    ("PeakJobMemory", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.AssignProcessToJobObject.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        return kernel, None
    info = Extended()
    info.Basic.LimitFlags = 0x2000       # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 다.
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel.CloseHandle(handle)
        return kernel, None
    return kernel, handle


class _Handle:
    def __init__(self, process):
        self._process = process
        self.pid = process.pid

    def poll(self):
        return self._process.poll()

    def wait(self, timeout=None):
        return self._process.wait(timeout=timeout)

    def kill(self):
        if os.name == "nt":
            if self.poll() is None:
                # 샌드박스에서는 taskkill 자체가 거부되기도 한다. 그때는 같은 나무만 OS 로 끈다.
                try:
                    result = subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
                    if result.returncode == 0:
                        return
                except (OSError, subprocess.TimeoutExpired):
                    pass
                _kill_windows_tree(self._process)
            return
        # 부모가 먼저 끝났어도 남은 자손은 같은 프로세스 무리에 있다.
        try:
            os.killpg(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        end = time.monotonic() + 5
        while time.monotonic() < end:
            self.poll()                         # 부모의 좀비도 거둔다.
            try:
                os.killpg(self.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(.05)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.pid, signal.SIGKILL)


def _kill_windows_tree(process):
    """taskkill 을 못 부르는 곳에서, 이 프로세스의 자손만 찾아 아래부터 끈다."""
    from ctypes import wintypes as w

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("pid", w.DWORD),
                    ("heap", ctypes.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
                    ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD), ("exe", w.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32FirstW.restype = kernel.Process32NextW.restype = w.BOOL
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    kernel.TerminateProcess.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    # 부모를 먼저 멈추면 더는 자식을 만들지 않는다.
    process.kill()
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise JobError("프로세스 나무를 살피지 못했다")
    children = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            children.setdefault(entry.parent, []).append(entry.pid)
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)

    def stop(parent):
        for pid in children.get(parent, []):
            handle = kernel.OpenProcess(1, False, pid)       # PROCESS_TERMINATE 다.
            if handle:
                try:
                    kernel.TerminateProcess(handle, 1)
                finally:
                    kernel.CloseHandle(handle)
            stop(pid)

    stop(process.pid)


class JobRunner:
    def __init__(self):
        self.kill_on_close = False
        self._kernel = self._job = None
        self._closed = False
        if os.name == "nt":
            try:
                self._kernel, self._job = _windows_job()
                self.kill_on_close = self._job is not None
            except (OSError, AttributeError):
                pass

    def start(self, job_id, argv, *, cwd, env, log_path):
        if self._closed:
            raise JobError("이미 닫은 배경 일 실행기다")
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        options = {}
        resume = None
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            if self._job is not None:
                # 배정하기 전에 손자부터 띄우지 못하게 잠깐 멈춰서 만든다.
                resume = ctypes.WinDLL("ntdll").NtResumeProcess
                resume.argtypes = [ctypes.c_void_p]
                resume.restype = ctypes.c_long
                flags |= 0x4                   # CREATE_SUSPENDED 다.
            options["creationflags"] = flags
        else:
            options["start_new_session"] = True
        with log_path.open("wb") as log:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, **options)
        if resume is not None:
            try:
                if not self._kernel.AssignProcessToJobObject(self._job, int(process._handle)):
                    self.kill_on_close = False
            finally:
                if resume(int(process._handle)) < 0:
                    process.kill()
                    process.wait()
                    raise JobError("멈춰 만든 프로세스를 깨우지 못했다")
        return _Handle(process)

    def close(self):
        if self._job is not None:
            self._kernel.CloseHandle(self._job)
            self._job = None
        self._closed = True


def tail_text(log_path, lines, chars) -> str:
    """끝의 256KB 안에서 줄마다 글자표를 고른다. 파일 전체는 읽지 않는다."""
    if lines <= 0 or chars <= 0:
        return ""
    try:
        with Path(log_path).open("rb") as stream:
            end = stream.seek(0, os.SEEK_END)
            size = min(end, 256 * 1024)
            stream.seek(end - size)
            data = stream.read(size)
    except OSError:
        return ""
    decoded = []
    for line in data.splitlines()[-lines:]:
        try:
            decoded.append(line.decode("utf-8"))
        except UnicodeDecodeError:
            decoded.append(line.decode(locale.getpreferredencoding(False), errors="replace"))
    return "\n".join(decoded)[-chars:]
