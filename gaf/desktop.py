"""설치된 브라우저의 프로그램 창과 Windows 바로 가기(표준 라이브러리만 쓴다)."""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import zlib
from functools import lru_cache
from math import ceil, floor, sqrt
from pathlib import Path

BREAKAWAY = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)


APP_NAME = "Game Dev AI Tool"          # 공용 아이콘의 이름(1.8.0 — 마지막에 쓰던 프로젝트를 연다 · 프로젝트는 프로그램 안에서 고른다)


def shortcut_name(root) -> str:
    return f"{APP_NAME} — {Path(root).name}"


def app_browsers(env=None, exists=os.path.isfile, which=shutil.which) -> list[str]:
    env = os.environ if env is None else env
    candidates = []
    if sys.platform == "win32":
        for parts, keys in (
            ("Microsoft/Edge/Application/msedge.exe", ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")),
            ("Google/Chrome/Application/chrome.exe", ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")),
        ):
            for key in keys:
                if env.get(key):
                    path = str(Path(env[key]) / parts)
                    if exists(path):
                        candidates.append(path)
        candidates.extend(which(name) for name in ("msedge", "chrome"))
    else:
        candidates.extend(which(name) for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"))
        if sys.platform == "darwin":
            for path in ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                         "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
                if exists(path):
                    candidates.append(path)
    result, seen = [], set()
    for path in candidates:
        if path:
            identity = os.path.normcase(os.path.normpath(path))
            if identity not in seen:
                seen.add(identity)
                result.append(str(path))
    return result


def open_app(url, *, profile, popen=subprocess.Popen, browsers=None) -> bool:
    candidates = app_browsers() if browsers is None else browsers
    for exe in candidates:
        command = [exe, "--app=" + url, "--user-data-dir=" + str(profile),
                   "--no-first-run", "--no-default-browser-check"]
        options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        try:
            if sys.platform == "win32":
                flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                try:
                    popen(command, creationflags=flags | BREAKAWAY, **options)
                except OSError:
                    popen(command, creationflags=flags, **options)
            else:
                popen(command, start_new_session=True, **options)
            return True
        except OSError:
            continue
    return False


def message_box(title, text, *, box=None) -> None:
    if sys.platform == "win32":
        if box is None:
            import ctypes

            box = ctypes.windll.user32.MessageBoxW
        box(None, text, title, 0x10)          # MB_ICONERROR
    elif sys.stderr is not None:
        print(f"{title}: {text}", file=sys.stderr)


def icon_bytes() -> bytes:
    """48×48 BGRA 비트맵 하나와 AND 마스크를 담은 ICO. 바깥 그림 파일은 필요 없다."""
    pixels, mask = bytearray(), bytearray()
    for y in reversed(range(48)):          # BMP는 아래 줄부터 담는다
        row = bytearray(8)                 # AND 마스크의 줄은 32비트 경계에 맞춘다
        for x in range(48):
            dx, dy = x - min(38, max(9, x)), y - min(38, max(9, y))
            visible = dx * dx + dy * dy <= 9 * 9
            if not visible:
                pixels.extend((0, 0, 0, 0))
                row[x // 8] |= 1 << (7 - x % 8)
            elif (x - 23.5) ** 2 + (y - 23.5) ** 2 <= 11 ** 2:
                pixels.extend((0x8A, 0xC3, 0x4C, 255))
            else:
                pixels.extend((0x1A, 0x19, 0x17, 255))
        mask.extend(row)
    bitmap = struct.pack("<IiiHHIIiiII", 40, 48, 96, 1, 32, 0, len(pixels) + len(mask), 0, 0, 0, 0)
    bitmap += pixels + mask
    return struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 48, 48, 0, 0, 1, 32, len(bitmap), 22) + bitmap


def png_icon(size, *, maskable=False) -> bytes:
    """ICO와 같은 그림을 줄마다 구간으로 채운다. 폰이 깎을 그림은 네모 바탕을 남긴다."""
    if type(size) is not int or not 16 <= size <= 1024:
        raise ValueError("아이콘 크기는 16 ~ 1024의 정수여야 한다")
    return _png_icon(size, bool(maskable))


@lru_cache(maxsize=32)
def _png_icon(size, maskable) -> bytes:
    clear, dark, green = bytes(4), bytes((0x17, 0x19, 0x1A, 255)), bytes((0x4C, 0xC3, 0x8A, 255))
    corner, radius, center = size * 9 / 48, size * 11 / 48, (size - 1) / 2
    rows = []
    for y in range(size):
        inset = 0
        if not maskable:
            dy = max(corner - y, y - (size - 1 - corner), 0)
            inset = ceil(corner - sqrt(max(0, corner * corner - dy * dy)))
        edge = clear * inset
        reach = radius * radius - (y - center) ** 2
        if reach >= 0:
            half = sqrt(reach)
            left, right = ceil(center - half), floor(center + half) + 1
            row = dark * (left - inset) + green * (right - left) + dark * (size - inset - right)
        else:
            row = dark * (size - 2 * inset)
        rows.append(b"\0" + edge + row + edge)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


png_icon.cache_clear = _png_icon.cache_clear


# Windows PowerShell 5.1의 인자 따옴표/한글 변환을 피한다. 코드만 ASCII, 값은 전부 환경으로 받는다.
# WScript는 이 PC의 문자표에 없는 이름(긴 대시 등)을 '?'로 바꾼다. 임시 이름으로 저장하고 .NET으로 옮긴다.
SHORTCUT_SCRIPT = """$ErrorActionPreference='Stop';
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);
$dir=$env:GAF_LNK_DIR;
if ([string]::IsNullOrEmpty($dir)) { $dir=[Environment]::GetFolderPath('Desktop'); }
$dir=[IO.Path]::GetFullPath($dir);
[void][IO.Directory]::CreateDirectory($dir);
$path=Join-Path $dir ($env:GAF_LNK_NAME+'.lnk');
$temp=Join-Path $dir ([Guid]::NewGuid().ToString('N')+'.lnk');
$shell=New-Object -ComObject WScript.Shell;
try {
$link=$shell.CreateShortcut($temp);
$link.TargetPath=$env:GAF_LNK_TARGET;
$link.Arguments=$env:GAF_LNK_ARGS;
$link.WorkingDirectory=$env:GAF_LNK_WORKDIR;
$link.IconLocation=$env:GAF_LNK_ICON;
$link.Description=$env:GAF_LNK_DESC;
$link.Save();
[IO.File]::Copy($temp,$path,$true);
} finally { if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp); } }
[Console]::WriteLine($path);
"""


def make_shortcut(link_dir, name, *, target, arguments, workdir, icon, description="", run=subprocess.run) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "이 OS 에서는 바로 가기를 만들지 않는다"
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '', name).strip(' .')
    if not name:
        return False, "바로 가기 이름이 비어 있다"
    values = dict(DIR="" if link_dir is None else str(link_dir), NAME=name, TARGET=str(target), ARGS=arguments,
                  WORKDIR=str(workdir), ICON=str(icon), DESC=description)
    env = {**os.environ, **{"GAF_LNK_" + key: value for key, value in values.items()}}
    try:
        done = run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", SHORTCUT_SCRIPT],
                   env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False, "바로 가기를 만들지 못했다 — PowerShell을 쓸 수 있는지 확인한다"
    if done.returncode != 0 or not done.stdout.strip():
        return False, "바로 가기를 만들지 못했다 — 고른 폴더에 쓸 수 있는지 확인한다"
    return True, done.stdout.strip()
