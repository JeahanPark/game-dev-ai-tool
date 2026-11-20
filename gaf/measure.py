"""바뀐 양을 잰다 — 역할이 파일을 바꾸면 지휘자가 이 수로 기본 검수를 가볍게 할지 제대로 할지 고른다. AI 없이 센다(토큰을 쓰지 않는다).

- 그림(`changed_pixels`): 두 그림에서 색이 바뀐 점의 수. PNG 는 표준 라이브러리(zlib · struct)로 직접 읽는다 — Pillow 가
  깔려 있든 없든 같은 값이 나온다. PNG 가 아닌 그림(JPEG · BMP · TGA · PSD …)은 Pillow 가 있을 때만 읽는다.
- 글(`text_change`): 바뀐 줄 수와 바뀐 글자 수. 줄 끝(CRLF · CR · LF)만 다른 것은 세지 않는다.
그림을 못 읽으면 None 이다 — 오류를 올리지 않는다. 점이 너무 많은 그림 · 한없이 풀리는 압축은 읽지 않는다.
글을 견주는 일에도 끝을 둔다(`LINE_WORK` · `CHAR_WORK`) — 같은 줄 · 같은 글자가 아주 많은 큰 글에서 SequenceMatcher 는
수십 초를 쓴다(11-20 잰 값: 같은 행 2만 줄의 표에 고친 줄 둘 37초 · 2만 글자 한 줄에 흩어진 오타 20개 37초). 끝에 닿으면
어림이 커지는 쪽으로만 틀린다 — 실제 차이보다 적게 세지 않으니 가벼운 검수를 잘못 고르지 않는다.
"""
from __future__ import annotations

import io
import math
import struct
import zlib
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache

NOISE = 24   # 점의 색 차이가 이 값(0~255)을 넘어야 「바뀐 점」으로 센다 — 조명 · 테두리의 작은 흔들림은 세지 않는다
MAX_PIXELS = 40_000_000     # 점이 이보다 많은 그림은 읽지 않는다(압축 폭탄 · 메모리)
CHAR_LIMIT = 20_000         # 바꾼 덩이의 글이 이보다 길면 글자끼리 견주지 않고 두 쪽 글자를 다 센다
LINE_WORK = 1_000_000       # 줄끼리 견주는 일의 양(같은 줄의 짝 수) 한도 — 넘으면 흔한 줄을 빼고 견주고, 그래도 넘으면 가운데를 다 센다
CHAR_WORK = 1_000_000       # 글자끼리 견주는 일의 양 한도(한 번 부를 때 덩이를 모두 합쳐) — 넘는 덩이는 흔한 글자를 빼고 견주고, 그래도 넘으면 다 센다

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}          # PNG 색 종류 → 점 하나의 값 수(회색 · RGB · 팔레트 · 회색+알파 · RGBA)
_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
# Pillow 로 여는 꼴. 바깥 프로그램을 부르는 꼴(EPS → Ghostscript)은 넣지 않는다. TGA 는 머리 표시가 없어 맨 뒤에 둔다.
_PILLOW_FORMATS = ("JPEG", "BMP", "GIF", "WEBP", "TIFF", "PSD", "DDS", "ICO", "TGA")
_BLOCK = 4096               # 점을 견줄 때 한 번에 보는 점의 수(같은 덩이는 건너뛴다)
_PIECE = 4096               # 필터를 풀 때 큰 정수 하나로 다루는 바이트 수(아주 넓은 그림도 이만큼씩)


def read_image(data: bytes) -> tuple[int, int, bytes] | None:
    """그림 바이트 → (너비, 높이, RGBA 바이트 — 점마다 4바이트). 못 읽으면 None."""
    try:
        if bytes(memoryview(data)[:8]) == _SIGNATURE:
            return _decode_png(data)            # PNG 는 Pillow 가 있어도 직접 읽는다(어디서나 같은 값)
        return _decode_other(data)
    except Exception:  # noqa: BLE001 — 깨진 그림 · 모자란 메모리 · 망가진 Pillow 까지 모두 「못 읽음」이다
        return None


def changed_pixels(before: bytes, after: bytes, *, noise: int = NOISE) -> tuple[int, int] | None:
    """두 그림 바이트 → (바뀐 점 수, 전체 점 수). 둘 중 하나를 못 읽거나 너비 · 높이가 다르면 None.

    R · G · B · A 가운데 하나라도 차이가 noise 보다 크면 바뀐 점이다.
    """
    first = read_image(before)
    if first is None:
        return None
    second = first if before == after else read_image(after)
    if second is None or first[:2] != second[:2]:
        return None
    width, height, old = first
    new = second[2]
    total = width * height
    limit = math.floor(noise)
    if limit < 0:
        return total, total                 # 차이 0 도 noise 보다 크다
    if limit >= 255 or old == new:
        return 0, total                     # 차이는 255 를 넘지 못한다
    return _count_changed(old, new, limit), total


def text_change(before: str, after: str) -> tuple[int, int]:
    """(바뀐 줄 수, 바뀐 글자 수).

    줄 수는 뺀 줄 + 더한 줄이다(줄끼리 SequenceMatcher · autojunk 끔). 글자 수는 뺀 글자 + 더한 글자이고 줄바꿈도 한 글자다.
    바꾼 덩이(replace)는 그 덩이의 글끼리 다시 견준다 — 긴 줄의 오타 하나는 2(뺀 하나 + 더한 하나)다.
    앞 · 뒤의 같은 줄은 먼저 덜어 낸다. 견주는 일이 한도를 넘으면 `_matcher` 를 따른다.
    """
    old_text, new_text = _one_newline(before), _one_newline(after)
    if old_text == new_text:
        return 0, 0
    old, new = _lines(old_text), _lines(new_text)
    head, tail = _ends(old, new)
    old, new = old[head:len(old) - tail], new[head:len(new) - tail]
    matcher, _ = _matcher(old, new, LINE_WORK)
    blocks = matcher.get_opcodes() if matcher else [("replace", 0, len(old), 0, len(new))]
    lines = chars = 0
    left = CHAR_WORK
    for tag, i1, i2, j1, j2 in blocks:
        if tag == "equal":
            continue
        lines += (i2 - i1) + (j2 - j1)
        changed, used = _char_change("".join(old[i1:i2]), "".join(new[j1:j2]), left)
        chars += changed
        left -= used
    return lines, chars


# ── 그림 ───────────────────────────────────────────────────────────────────────────────

def _decode_png(data) -> tuple[int, int, bytes] | None:
    """PNG → (너비, 높이, RGBA). 늘 이 파일의 순수 파이썬 길로 읽는다(Pillow 를 쓰지 않는다). 못 읽으면 None."""
    try:
        return _png(memoryview(data).cast("B"))
    except Exception:  # noqa: BLE001 — 깨진 덩이 · zlib 오류 · 모자란 메모리 모두 「못 읽음」이다
        return None


def _decode_other(data) -> tuple[int, int, bytes] | None:
    """PNG 가 아닌 그림은 Pillow 로 읽는다 — 깔려 있지 않으면 None."""
    try:
        from PIL import Image
    except ImportError:
        return None
    Image.init()
    formats = [name for name in _PILLOW_FORMATS if name in Image.OPEN]
    with Image.open(io.BytesIO(bytes(data)), formats=formats) as image:
        width, height = image.size              # 여기까지는 머리만 읽었다 — 점을 풀기 전에 크기를 본다
        if width < 1 or height < 1 or width * height > MAX_PIXELS:
            return None
        return width, height, image.convert("RGBA").tobytes()


def _chunks(view):
    """PNG 덩이(chunk)를 차례로 → (종류, 내용). IEND 에서 멈춘다. IEND 까지 온전하지 않으면 ValueError(CRC 는 보지 않는다)."""
    pos = len(_SIGNATURE)
    while True:
        length, kind = struct.unpack_from(">I4s", view, pos)
        start = pos + 8
        end = start + length
        if end + 4 > len(view):
            raise ValueError("끊긴 덩이")
        yield kind, view[start:end]
        if kind == b"IEND":
            return
        pos = end + 4


def _png(view) -> tuple[int, int, bytes] | None:
    """PNG 바이트(memoryview) → (너비, 높이, RGBA) · 못 읽으면 None. 깨진 자료의 오류는 `_decode_png` 가 None 으로 바꾼다."""
    if view[:len(_SIGNATURE)] != _SIGNATURE:
        return None
    header, palette, alpha, parts = None, b"", b"", []
    for kind, body in _chunks(view):
        if header is None:
            if kind != b"IHDR" or len(body) != 13:
                return None
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            parts.append(body)
        elif kind == b"PLTE":
            palette = bytes(body)
        elif kind == b"tRNS":
            alpha = bytes(body)     # 팔레트의 알파만 쓴다 — 회색 · RGB 의 「투명한 색 하나」는 불투명으로 둔다
    width, height, depth, color, compression, method, interlace = header
    if width < 1 or height < 1 or width * height > MAX_PIXELS:
        return None
    if depth not in _DEPTHS.get(color, ()) or compression or method or interlace:
        return None                 # 엇갈려 쌓은 PNG(Adam7)는 읽지 않는다
    if color == 3 and len(palette) < 3:
        return None
    bits = _CHANNELS[color] * depth
    stride = (width * bits + 7) // 8                # 필터 바이트를 뺀 한 줄의 바이트 수
    bpp = max(1, bits // 8)                         # 필터가 「왼쪽」으로 보는 거리(1바이트보다 작은 점은 1)
    expected = height * (stride + 1)
    raw = zlib.decompressobj().decompress(b"".join(parts), expected)    # 필요한 만큼만 푼다(압축 폭탄)
    if len(raw) < expected:
        return None
    rows = _unfilter(memoryview(raw), height, stride, bpp)
    if rows is None:
        return None
    return width, height, _to_rgba(rows, width, height, stride, depth, color, palette, alpha)


def _unfilter(raw, height: int, stride: int, bpp: int) -> bytearray | None:
    """줄마다 필터(0 None · 1 Sub · 2 Up · 3 Average · 4 Paeth)를 풀어 이어 붙인다. 모르는 필터 번호면 None."""
    zero = bytes(stride)
    out = bytearray(height * stride)
    prev = zero
    pos = 0
    for start in range(0, height * stride, stride):
        kind = raw[pos]
        line = raw[pos + 1:pos + 1 + stride]
        pos += stride + 1
        if kind == 0:
            row = line
        elif kind == 1 or (kind == 4 and prev == zero):     # 윗줄이 다 0 이면 Paeth 는 늘 왼쪽을 고른다 = Sub
            row = _sub(line, bpp)
        elif kind == 2:
            row = _up(line, prev)
        elif kind == 3:
            row = _average(line, prev, bpp)
        elif kind == 4:
            row = _paeth(line, prev, bpp)
        else:
            return None
        out[start:start + stride] = row
        prev = row
    return out


@lru_cache(maxsize=64)
def _masks(size: int) -> tuple[int, int, int]:
    """size 바이트 큰 정수의 바이트마다 아래 7비트 · 바이트마다 맨 위 비트 · 모든 비트."""
    return int.from_bytes(b"\x7f" * size, "little"), int.from_bytes(b"\x80" * size, "little"), (1 << (8 * size)) - 1


def _add(left: int, right: int, low: int, high: int) -> int:
    """큰 정수 둘을 바이트끼리 더한다(바이트마다 256 으로 나눈 나머지 · 옆 바이트로 올림이 넘어가지 않는다).

    아래 7비트끼리 더하면 바이트를 넘지 않는다. 맨 위 비트는 두 값의 맨 위 비트와 XOR 로 맞춘다.
    """
    return ((left & low) + (right & low)) ^ ((left ^ right) & high)


def _up(line, prev) -> bytearray:
    """Up: 위의 값을 바이트끼리 더한다(조각마다 큰 정수 덧셈 한 번)."""
    row = bytearray(len(line))
    for start in range(0, len(line), _PIECE):
        part = line[start:start + _PIECE]
        size = len(part)
        low, high, _ = _masks(size)
        total = _add(int.from_bytes(part, "little"), int.from_bytes(prev[start:start + size], "little"), low, high)
        row[start:start + size] = total.to_bytes(size, "little")
    return row


def _sub(line, bpp: int) -> bytearray:
    """Sub: 바이트마다 왼쪽(bpp 앞)까지 풀린 값을 더한다 = 칸(bpp 간격)마다 앞에서부터의 누적 합.

    줄을 조각(bpp 의 배수)으로 나눠 조각마다 큰 정수 하나로 두고, 건너뛰는 거리를 bpp · 2bpp · 4bpp … 로 늘리며 자기를
    밀어 더한다(덧셈 log 번). 앞 조각의 마지막 점(bpp 바이트)을 조각 전체에 깔아 한 번 더 더하면 이어진다 — 줄이 아주
    길어도 조각의 크기만큼만 큰 정수를 쓴다.
    """
    piece = max(bpp, _PIECE - _PIECE % bpp)
    row = bytearray(len(line))
    carry = b""
    for start in range(0, len(line), piece):
        part = line[start:start + piece]
        size = len(part)
        low, high, full = _masks(size)
        total = int.from_bytes(part, "little")
        step = bpp
        while step < size:
            total = _add(total, (total << (8 * step)) & full, low, high)
            step *= 2
        if carry:
            total = _add(total, int.from_bytes((carry * (size // bpp + 1))[:size], "little"), low, high)
        done = total.to_bytes(size, "little")
        row[start:start + size] = done
        carry = done[-bpp:]
    return row


def _average(line, prev, bpp: int) -> bytearray:
    """Average: 왼쪽과 위의 평균(내림)을 더한다. 왼쪽이 방금 푼 값이라 칸(bpp 간격)마다 차례로 돈다."""
    row = bytearray(len(line))
    for lane in range(bpp):
        left = 0
        done = []
        keep = done.append
        for value, up in zip(line[lane::bpp], prev[lane::bpp]):
            left = (value + ((left + up) >> 1)) & 255
            keep(left)
        row[lane::bpp] = bytes(done)
    return row


def _paeth(line, prev, bpp: int) -> bytearray:
    """Paeth: 왼쪽(a) · 위(b) · 왼쪽 위(c) 가운데 a + b - c 에 가장 가까운 값을 더한다(같으면 a → b → c 차례)."""
    row = bytearray(len(line))
    for lane in range(bpp):
        left = corner = 0
        done = []
        keep = done.append
        for value, up in zip(line[lane::bpp], prev[lane::bpp]):
            near_left = up - corner             # |p - a| (p = a + b - c)
            near_up = left - corner             # |p - b|
            near_corner = near_left + near_up   # |p - c|
            if near_left < 0:
                near_left = -near_left
            if near_up < 0:
                near_up = -near_up
            if near_corner < 0:
                near_corner = -near_corner
            if near_left <= near_up and near_left <= near_corner:
                left = (value + left) & 255
            elif near_up <= near_corner:
                left = (value + up) & 255
            else:
                left = (value + corner) & 255
            corner = up
            keep(left)
        row[lane::bpp] = bytes(done)
    return row


def _unpack(rows: bytearray, width: int, height: int, stride: int, depth: int) -> bytes | bytearray:
    """1 · 2 · 4비트 값을 한 바이트에 하나씩 편다(앞 비트가 왼쪽 점). 줄 끝을 채운 비트는 버린다."""
    per = 8 // depth
    mask = (1 << depth) - 1
    wide = bytearray(len(rows) * per)
    for slot in range(per):
        shift = 8 - depth * (slot + 1)
        wide[slot::per] = rows.translate(bytes((value >> shift) & mask for value in range(256)))
    line = stride * per
    if line == width:
        return wide
    return b"".join(wide[start:start + width] for start in range(0, line * height, line))


def _to_rgba(rows, width: int, height: int, stride: int, depth: int, color: int, palette: bytes, alpha: bytes) -> bytes:
    """풀린 줄 → RGBA 8비트. 16비트 값은 높은 바이트만 쓰고, 1 · 2 · 4비트 회색은 0~255 로 늘린다."""
    if depth == 16:
        rows = rows[0::2]
    elif depth < 8:
        rows = _unpack(rows, width, height, stride, depth)
        if color == 0:
            mask = (1 << depth) - 1
            rows = rows.translate(bytes((value & mask) * 255 // mask for value in range(256)))
    if color == 6:
        return bytes(rows)
    rgba = bytearray(b"\xff") * (width * height * 4)
    if color == 2:
        for channel in range(3):
            rgba[channel::4] = rows[channel::3]
    elif color == 0:
        for channel in range(3):
            rgba[channel::4] = rows
    elif color == 4:
        gray = rows[0::2]
        for channel in range(3):
            rgba[channel::4] = gray
        rgba[3::4] = rows[1::2]
    else:                                           # 팔레트: 번호 → 색. 팔레트 밖의 번호는 검정 · 불투명
        entries = min(len(palette) // 3, 256)
        tables = [bytearray(256), bytearray(256), bytearray(256), bytearray(b"\xff") * 256]
        for channel in range(3):
            tables[channel][:entries] = palette[channel:entries * 3:3]
        shown = min(len(alpha), entries)
        tables[3][:shown] = alpha[:shown]
        for channel in range(4):
            rgba[channel::4] = rows.translate(tables[channel])
    return bytes(rgba)


def _count_changed(old: bytes, new: bytes, limit: int) -> int:
    """같은 길이의 RGBA 바이트 둘 → 네 값 가운데 하나라도 limit(0~254)보다 더 다른 점의 수.

    덩이(_BLOCK 점)마다 바이트를 16비트 칸에 하나씩 벌려 큰 정수로 두고 한꺼번에 뺀다. 칸마다 (가 - 나 + 32767 - limit)는
    0~65535 안이라 옆 칸으로 넘치지 않고, 맨 위 비트가 곧 「가 - 나 > limit」이다. 반대쪽(나 - 가)도 같이 보고,
    점 하나의 네 칸을 OR 로 모아 비트를 센다. 같은 덩이는 건너뛴다.
    """
    old, new = memoryview(old), memoryview(new)
    size = _BLOCK * 4
    made = {}
    count = 0
    for start in range(0, len(old), size):
        first, second = old[start:start + size], new[start:start + size]
        if first == second:
            continue
        length = len(first)
        if length not in made:
            ones = int.from_bytes(b"\x01\x00" * length, "little")                    # 칸마다 1
            heads = int.from_bytes((b"\x00\x80" + bytes(6)) * (length // 4), "little")   # 점마다 첫 칸의 맨 위 비트
            made[length] = ((32767 - limit) * ones, 0x8000 * ones, heads)
        lift, flag, head = made[length]
        wide = bytearray(2 * length)
        wide[0::2] = first
        x = int.from_bytes(wide, "little")
        wide[0::2] = second
        y = int.from_bytes(wide, "little")
        over = ((x + lift - y) | (y + lift - x)) & flag
        count += ((over | over >> 16 | over >> 32 | over >> 48) & head).bit_count()
    return count


# ── 글 ─────────────────────────────────────────────────────────────────────────────────

def _one_newline(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _lines(text: str) -> list[str]:
    """줄로 나눈다 — 줄마다 끝의 줄바꿈을 붙여 둔다(마지막 줄에 줄바꿈이 없으면 없는 그대로)."""
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _ends(old, new) -> tuple[int, int]:
    """앞에서 같은 마디 수 · 뒤에서 같은 마디 수(겹쳐 세지 않는다). 줄 목록에도 글에도 쓴다."""
    limit = min(len(old), len(new))
    head = 0
    while head < limit and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < limit - head and old[-1 - tail] == new[-1 - tail]:
        tail += 1
    return head, tail


def _matcher(old, new, left: int) -> tuple[SequenceMatcher | None, int]:
    """견줄 SequenceMatcher 를 고른다 → (고른 것 · 없으면 None, 쓸 일의 양). 줄 목록에도 글에도 쓴다.

    일의 양은 「같은 마디의 짝 수」다 — SequenceMatcher 가 한 번 훑을 때 도는 수라 같은 줄 · 같은 글자가 잦으면 크게 는다.
    1. left 안이면 autojunk 를 끄고 견준다(본래 방식).
    2. 넘으면 흔한 마디(new 에서 1% 넘게 나오는 것)를 일치의 시작점에서 빼고 다시 센다 — difflib 의 autojunk 다.
       흔한 마디도 일치를 늘일 때는 붙는다. 드문 줄(파일 번호 · 이름)이 있는 큰 씬 파일은 거의 그대로 맞는다.
    3. 그래도 넘으면 None — 부르는 쪽이 다 바뀐 것으로 센다.
    어느 길이든 실제로 맞는 마디끼리만 짝짓는다 — 덜 맞출 수는 있어도(더 센다) 실제 차이보다 적게 세지는 않는다.
    """
    counts = Counter(new)
    old_counts = Counter(old)
    work = sum(count * counts[item] for item, count in old_counts.items())
    if work <= left:
        return SequenceMatcher(None, old, new, autojunk=False), work
    if len(new) >= 200:                                         # difflib 이 흔한 마디를 빼는 크기 · 기준과 같다
        common = len(new) // 100 + 1
        work = sum(count * counts[item] for item, count in old_counts.items() if counts[item] <= common)
        if work <= left:
            return SequenceMatcher(None, old, new, autojunk=True), work
    return None, 0


def _char_change(gone: str, came: str, left: int) -> tuple[int, int]:
    """뺀 글 · 더한 글 → (바뀐 글자 수, 쓴 일의 양).

    두 쪽이 다 있고 CHAR_LIMIT 안이면 글자끼리 견준다(같은 앞 · 뒤를 먼저 덜어 낸다 · 남은 일의 양 left 안에서 `_matcher`).
    한쪽이 비었거나 CHAR_LIMIT 를 넘거나 `_matcher` 가 None 이면 두 쪽 글자를 다 센다.
    """
    if not gone or not came or len(gone) > CHAR_LIMIT or len(came) > CHAR_LIMIT:
        return len(gone) + len(came), 0
    head, tail = _ends(gone, came)
    gone, came = gone[head:len(gone) - tail], came[head:len(came) - tail]
    matcher, work = _matcher(gone, came, left) if gone and came else (None, 0)
    if matcher is None:
        return len(gone) + len(came), 0
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return len(gone) + len(came) - 2 * matched, work
