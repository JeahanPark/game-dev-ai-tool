"""낱말 맞추기 · 발췌 · 글 정리 — 모델 없이. 뜻은 모른다: 총괄이 준 낱말이 글에 있는지만 본다."""
from __future__ import annotations

import functools
import re
import unicodedata

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ASCII_TERM = re.compile(r"[\x20-\x7e]+")
_VOWELS = "aeiou"


def norm(value) -> str:
    """견주기 위한 꼴: NFC · 소문자 · 빈칸 한 칸."""
    return " ".join(unicodedata.normalize("NFC", str(value or "")).casefold().split())


def _forms(word: str) -> str:
    """영문 낱말의 흔한 어미 꼴: 복수 · 소유 · 과거 · 진행(reward → rewards · rewarded · rewarding / skip → skipped / pace → paced ·
    pacing / strategy → strategies). 다른 낱말을 만드는 어미(-er · -ment)와 card 같은 딴 낱말(car + d)은 보지 않는다."""
    base = re.escape(word)
    forms = [base + "(?:s|es|ed|ing|'s|’s)?"]
    if len(word) >= 3:
        last, prev = word[-1], word[-2]
        if last == "e":
            forms += [base + "d", re.escape(word[:-1]) + "ing"]
        elif last == "y" and prev not in _VOWELS:
            forms.append(re.escape(word[:-1]) + "ie[sd]")
        elif last not in _VOWELS + "wxy" and prev in _VOWELS:
            forms.append(base + last + "(?:ed|ing)")
    return "(?:" + "|".join(forms) + ")"


@functools.lru_cache(maxsize=1024)
def _pattern(word: str, exact: bool):
    parts = word.split()
    body = [re.escape(part) for part in parts]
    if not exact and parts[-1].isalpha():
        body[-1] = _forms(parts[-1])
    return re.compile(r"(?<![0-9a-z])" + r"\s+".join(body) + r"(?![0-9a-z])")


def find(term, lowered: str, start: int = 0, exact: bool = False) -> int:
    """낱말(구)이 소문자로 바꾼 글의 어디에 있는가(start 부터 찾는다 · 없으면 -1). 영문 · 숫자 낱말은 낱말 경계로 맞추되(start 안의
    art 는 아니다) 끝 낱말에 흔한 어미가 붙은 꼴은 같은 낱말로 본다(reward 는 rewards 에도 맞는다 — exact 면 딱 그 꼴만).
    한글이 든 낱말은 부분 문자열로(조사가 붙어도 맞는다)."""
    word = norm(term)
    if not word:
        return -1
    if _ASCII_TERM.fullmatch(word):
        match = _pattern(word, exact).search(lowered, start)
        return match.start() if match else -1
    return lowered.find(word, start)


def has(term, normed: str) -> bool:
    return find(term, normed) >= 0


def hits(terms, body) -> list[str]:
    """맞은 낱말을 준 차례로 한 번씩(대소문자만 다른 것은 하나)."""
    normed, out, seen = norm(body), [], set()
    for term in terms:
        key = norm(term)
        if key and key not in seen and has(term, normed):
            seen.add(key)
            out.append(term)
    return out


def clean(value) -> str:
    """증거에 넣을 글: 제어 문자를 지우고 줄 끝 · 겹친 빈 줄 · 겹친 빈칸을 정리한다."""
    body = unicodedata.normalize("NFC", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    body = _CONTROL.sub("", body).replace("\t", " ")
    lines = [" ".join(line.split()) for line in body.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def excerpt(value, terms, limit: int = 400) -> str:
    """처음 맞은 낱말 둘레를 잘라 준다(잘린 쪽에 「…」). 맞은 낱말이 없으면 앞에서부터."""
    body = clean(value)
    if len(body) <= limit:
        return body
    lowered, start = body.casefold(), 0
    for exact in (True, False):                 # 딱 맞는 자리를 먼저 쓴다 — 없으면 어미가 붙은 자리
        at = next((at for at in (find(term, lowered, exact=exact) for term in terms) if at >= 0), -1)
        if at >= 0:
            start = max(0, at - limit // 3)
            break
    start = min(start, len(body) - limit)
    cut = body[start:start + limit].strip()
    return ("…" if start > 0 else "") + cut + ("…" if start + limit < len(body) else "")
