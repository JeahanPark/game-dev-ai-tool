"""GPT 로 받아쓰기 — OpenAI 의 받아쓰기 API 를 부른다(1.9.0). 키가 있을 때만 쓰고, 새 꾸러미 없이 표준 라이브러리로 보낸다.

사용자 11-24 「마이크 품질이 너무 낮은데 …」 → 「아니면 gpt마이크여도 상관없어」 → 「GPT 마이크 구독으로 할수있는방법 찾아봐」.
ChatGPT Pro 구독에는 달마다 API 크레딧이 들어 있다(help.openai.com/en/articles/20001626) — 그것을 받아 만든 API 키를 화면의
설정에 넣으면 이 길로 받아쓴다. 요청 꼴은 공식 글(developers.openai.com/api/docs/guides/speech-to-text)에서 읽었다.

키는 접근 값 폴더(프로젝트 밖 — 세션(AI 역할)이 읽지 못한다)의 파일 하나에 둔다. 키 값은 답 · 기록 · 까닭 글 어디에도 싣지 않는다.
이 길을 쓰면 녹음한 소리가 OpenAI 로 간다 — 키를 넣는 사람이 정하는 일이다.
"""
from __future__ import annotations

import contextlib
import json
import re
import secrets
import urllib.error
import urllib.request

from .paths import _atomic_text, run_dir

URL = "https://api.openai.com/v1/audio/transcriptions"
CHECK_URL = "https://api.openai.com/v1/models"          # 키가 맞는지만 본다(읽기 — 돈이 들지 않는다)
MODEL = "gpt-transcribe"
ENV = "OPENAI_API_KEY"
KEY_FILE = "voice-key"
LIMIT = 25 * 1024 * 1024          # API 가 받는 파일 크기
TIMEOUT = 60
WORDS_MAX, WORD_CHARS = 30, 60
_TYPES = {"wav": "audio/wav", "webm": "audio/webm", "ogg": "audio/ogg", "mp4": "audio/mp4"}


class CloudError(RuntimeError):
    """GPT 로 받아쓰지 못한 까닭 — 글을 그대로 화면에 보인다(키 값은 들어 있지 않다)."""


def key_path():
    return run_dir() / KEY_FILE


def read_key(env_key: str = "") -> str:
    """받아쓰기에 쓸 키 — 사람이 화면에서 넣은 것(파일)이 먼저고, 없으면 지휘자를 켤 때의 환경변수 값."""
    try:
        saved = key_path().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        saved = ""
    return saved or env_key.strip()


def key_source(env_key: str = "") -> str:
    """키가 어디에서 왔나(「화면」 · 「환경변수」 · 없으면 빈 글) — 값은 주지 않는다."""
    try:
        if key_path().read_text(encoding="utf-8").strip():
            return "화면"
    except (OSError, UnicodeDecodeError):
        pass
    return "환경변수" if env_key.strip() else ""


def save_key(value: str) -> None:
    value = value.strip()
    if len(value) < 20 or re.search(r"\s", value):
        raise CloudError("API 키의 꼴이 아니다 — OpenAI 에서 만든 키를 그대로 붙여 넣는다")
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(path, value)


def clear_key() -> bool:
    try:
        key_path().unlink()
        return True
    except OSError:
        return False


def keywords(words) -> list[str]:
    """힌트 낱말 — API 는 `<` `>` 줄바꿈이 든 낱말이 하나라도 있으면 요청 전체를 거절한다."""
    cleaned = []
    for word in words or ():
        word = " ".join(re.sub(r"[<>\r\n]", " ", str(word)).split())[:WORD_CHARS]
        if word and word not in cleaned:
            cleaned.append(word)
    return cleaned[:WORDS_MAX]


def _kind(audio: bytes) -> str:
    from .dictation import sniff

    kind = sniff(audio)
    return kind if kind in _TYPES else "webm"


def body(audio: bytes, *, language: str = "ko", words=(), prompt: str = "") -> tuple[bytes, str]:
    """multipart/form-data 몸과 Content-Type. gpt-transcribe 는 `languages[]` 를 받는다(단수 `language` 와 같이 보내지 않는다)."""
    boundary = "gaf" + secrets.token_hex(16)
    kind = _kind(audio)
    fields = [("model", MODEL), ("languages[]", language)] + [("keywords[]", word) for word in keywords(words)]
    if prompt.strip():
        fields.append(("prompt", prompt.strip()))
    parts = []
    for name, value in fields:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
                     + value.encode("utf-8") + b"\r\n")
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="voice.{kind}"\r\n'
                 f"Content-Type: {_TYPES[kind]}\r\n\r\n".encode("utf-8") + audio + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def _reason(error: Exception, key: str) -> str:
    """사람이 볼 까닭 — 서버가 준 글을 실어도 키는 가린다."""
    if isinstance(error, urllib.error.HTTPError):
        detail = ""
        with contextlib.suppress(Exception):
            payload = json.loads(error.read().decode("utf-8", "replace"))
            message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
            detail = str(message or "")[:200]
        if error.code == 401:
            return "API 키가 맞지 않는다 — 설정의 음성 입력에서 키를 다시 넣는다"
        if error.code == 429:
            return "API 크레딧 · 한도가 모자라다(429) — OpenAI 의 결제 화면에서 남은 크레딧을 본다"
        text = f"OpenAI 가 거절했다({error.code})" + (f": {detail}" if detail else "")
        return text.replace(key, "<키>") if key else text
    return "OpenAI 에 연결하지 못했다 — 인터넷 연결을 본다"


def _send(request, key: str, opener, timeout):
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise CloudError(_reason(error, key)) from None


def transcribe(audio: bytes, *, key: str, language: str = "ko", words=(), prompt: str = "", opener=None,
               timeout: float = TIMEOUT) -> str:
    """녹음 하나를 글로 바꾼다 → 받아쓴 글. 안 되면 CloudError(까닭)."""
    if not key:
        raise CloudError("GPT 받아쓰기의 키가 없다")
    if len(audio) > LIMIT:
        raise CloudError("소리가 너무 크다 — GPT 받아쓰기는 25MB까지 받는다")
    data, content_type = body(audio, language=language, words=words, prompt=prompt)
    request = urllib.request.Request(URL, data=data, method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": content_type})
    raw = _send(request, key, opener, timeout)
    try:
        text = json.loads(raw.decode("utf-8"))["text"]
        if not isinstance(text, str):
            raise ValueError
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise CloudError("GPT 의 답을 읽지 못했다") from None
    return text.strip()


def check_key(key: str, *, opener=None, timeout: float = 20) -> None:
    """맞는 키인가 — 모델 목록을 한 번 읽어 본다(받아쓰지 않으니 돈이 들지 않는다). 아니면 CloudError."""
    request = urllib.request.Request(CHECK_URL, method="GET", headers={"Authorization": "Bearer " + key})
    _send(request, key, opener, timeout)
