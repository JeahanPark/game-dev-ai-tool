"""폴더 선택 창 — 화면의 「폴더 고르기」가 이 PC 에 띄운다(1.8.0).

화면은 브라우저라 고른 폴더의 전체 경로를 알 수 없다(브라우저는 경로를 감춘다). 그래서 지휘자가 이 모듈을 다른 프로세스로
띄우고(`python -m gaf.pickfolder [시작 폴더]`), 고른 경로를 표준 출력의 한 줄로 받는다 — 창 도구(Tk)를 지휘자 안에서 돌리지 않는다.
지휘자는 뒤에서 도는 프로그램이라 띄운 창이 앞에 나오지 못하니, 늘 위에 두는 보이지 않는 창에 딸려 띄운다
(11-24 이 PC 에서 확인: 창 없는 프로세스가 띄워도 1.4초 뒤 다른 창 위에 보였다).
"""
from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
from pathlib import Path

TITLE = "게임 프로젝트 폴더를 고른다"
WAIT_SECONDS = 600          # 창을 띄워 둔 채 기다리는 시간


class PickError(RuntimeError):
    """창을 못 띄웠거나 고르지 않았다 — 글을 그대로 화면에 보인다."""


def ask(start: str = "") -> str:
    """폴더 선택 창을 띄운다 → 고른 경로(고르지 않았으면 빈 글)."""
    import tkinter
    from tkinter import filedialog

    window = tkinter.Tk()
    try:
        window.withdraw()
        window.attributes("-topmost", True)
        window.update()
        picked = filedialog.askdirectory(parent=window, title=TITLE, initialdir=start or None, mustexist=True)
        return picked if isinstance(picked, str) else ""
    finally:
        window.destroy()


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        picked = ask(args[0] if args else "")
    except Exception:          # 창 도구가 없거나(ImportError) 화면을 쓸 수 없다(TclError) — 부른 쪽이 경로를 적게 한다
        return 2
    sys.stdout.buffer.write((picked + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


async def choose(start: str = "", *, spawn=None) -> str:
    """도우미 프로세스로 창을 띄우고 고른 경로를 받는다(고르지 않았으면 빈 글). 안 되면 PickError.

    기다리는 동안 부른 쪽이 그만두면(지휘자가 꺼진다 · 요청이 끊긴다 · 시간이 지났다) 도우미를 끈다 — 창이 남아 있는 동안
    지휘자가 꺼지지 못하는 일이 없게 한다.
    """
    try:
        begin = start if start and Path(start).is_absolute() and Path(start).is_dir() else ""
    except (OSError, ValueError):
        begin = ""
    options = dict(cwd=str(Path(__file__).resolve().parent.parent), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                   stderr=subprocess.DEVNULL)
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW          # 콘솔 창이 같이 뜨지 않게
    try:
        process = await (spawn or asyncio.create_subprocess_exec)(
            sys.executable, "-X", "utf8", "-m", "gaf.pickfolder", begin, **options)
    except (OSError, NotImplementedError):
        raise PickError("폴더 선택 창을 띄우지 못했다 — 경로를 직접 적는다") from None
    try:
        out, _ = await asyncio.wait_for(process.communicate(), WAIT_SECONDS)
    except asyncio.TimeoutError:
        raise PickError("폴더를 고르지 않았다 — 다시 누르거나 경로를 적는다") from None
    finally:
        if process.returncode is None:
            with contextlib.suppress(OSError):
                process.kill()
            with contextlib.suppress(BaseException):          # 그만두는 중에도 끝난 것을 잠깐 거둔다(밖으로 나가던 예외는 그대로 나간다)
                await asyncio.wait_for(asyncio.shield(process.wait()), 2)
    if process.returncode != 0:
        raise PickError("폴더 선택 창을 띄우지 못했다 — 경로를 직접 적는다")
    try:
        return out.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise PickError("고른 폴더를 읽지 못했다 — 경로를 직접 적는다") from None


if __name__ == "__main__":
    sys.exit(main())
