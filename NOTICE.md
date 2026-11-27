# 남이 만든 것 — 출처와 라이선스

## 이 묶음 안에 들어 있는 것

| 무엇 | 출처 | 라이선스 |
|---|---|---|
| `gaf/library/` 서랍의 글(역할 · 절차 · 문서 틀 · 규칙 · 장르 시트 · 엔진 참조) | [claude-code-game-studios](https://github.com/donchitos/claude-code-game-studios) 의 글을 한국어로 고쳐 옮긴 것(엔진 참조는 영어 원문 그대로). 레퍼런스 조사 · 게임 뜯어보기의 글은 이 프로그램에서 쓴 글이다 — 게임 뜯어보기의 일하는 법은 [REA](https://github.com/morluto/rea)(MIT · Copyright (c) 2026 morluto)의 안내 글에서 생각만 가져왔다(글은 옮기지 않았다) | MIT — 전문과 자세한 출처는 [gaf/library/NOTICE.md](gaf/library/NOTICE.md) |
| `gaf/vendor/qrcodegen.py` QR 코드 만들기 | Project Nayuki 「[QR Code generator library](https://www.nayuki.io/page/qr-code-generator-library)」 | MIT — 전문은 파일 머리말 · [gaf/vendor/README.md](gaf/vendor/README.md) |

위키(`wiki/`)의 꼴은 Andrej Karpathy 의 「[LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)」 아이디어를 따랐다. 그 글은 옮겨 넣지 않았고, 규칙은 이 프로그램의 말로 새로 썼다.

## 설치할 때 각자 받는 것(이 묶음에 들어 있지 않다)

`설치.cmd` 가 아래를 **공식 자리에서 그 PC 로 직접** 받는다. 쓰는 조건은 각 회사 · 프로젝트의 것을 따른다.

| 무엇 | 받는 곳 | 라이선스 · 조건 |
|---|---|---|
| Python | python.org 판(Windows 의 winget) | PSF License |
| `claude-agent-sdk`(Claude 를 부르는 부품 — Claude Code 실행 파일이 같이 온다) | PyPI | 부품은 MIT · Claude Code 와 Claude 사용은 Anthropic 의 약관과 각자의 Claude 구독을 따른다 |
| `openai-codex`(GPT 를 부르는 부품) · Codex CLI — GPT 를 쓸 때만 | PyPI · winget(OpenAI.Codex) | Apache-2.0 · ChatGPT 사용은 OpenAI 의 약관과 각자의 구독을 따른다 |
| 옵시디언 — 깔겠다고 할 때만 | winget(Obsidian.Obsidian) · obsidian.md | 무료 앱(회사 일에도 무료 — 2025-02-20부터) · obsidian.md 의 약관 |
| 음성 입력 부품(faster-whisper 등) — 켤 때만 | PyPI | 각 부품의 라이선스(대부분 MIT) · NVIDIA 그래픽 부품은 NVIDIA 라이선스 |
| Tailscale — 폰 · 다른 PC 에서 쓰기를 켤 때만 | tailscale.com | 무료 요금제는 개인 · 비상업용이다 — 회사 일에 쓰면 유료 요금제를 살핀다 |
| `yt-dlp`(영상 정보를 받는 도구) — 레퍼런스 조사에서 YouTube 출처를 켤 때만 | PyPI | Unlicense(공개 도메인). YouTube 를 쓰는 조건은 YouTube 의 약관을 따른다 — 그 약관은 자동 접근을 막는다(승인 카드에 적혀 있다) |
| `rea-agents` 6.10.1(REA — 게임 빌드를 뜯어보는 분석 도구) — 사람이 REA 를 켤 때만(승인 카드 · `gaf rea on`) | npm 저장소(registry.npmjs.org). 같이 깔리는 선택 부품 Frida 는 설치 스크립트가 미리 지은 파일을 GitHub 릴리스에서 받는다 | MIT(Copyright (c) 2026 morluto). 묶여 오는 것 가운데 `openpgp` 는 LGPL-3.0+ · 선택 부품 `frida` 는 LGPL-2.0 WITH WxWindows-exception-3.1 이다. 이 프로그램의 자리(`~/.gaf/tools/rea/`)에만 깔린다 |
| Node.js 22.23.5 — REA 를 켤 때, 맞는 판의 Node 가 PC 에 없을 때만 | nodejs.org(받은 묶음의 SHA-256 을 nodejs.org 에 적힌 값과 맞춘 뒤 푼다) | MIT 와 묶인 것들의 라이선스(nodejs.org 의 LICENSE). 이 프로그램의 자리(`~/.gaf/tools/node/`)에만 풀고 PC 의 Node 는 건드리지 않는다 |

레퍼런스 조사(1.15.0)가 켰을 때 부르는 바깥 서비스: Reddit 의 글은 공개 아카이브 **Arctic Shift**(`arctic-shift.photon-reddit.com` — 운영자의 안내 「free service, be considerate」 · 이 프로그램은 요청 사이를 3초 두고 조사 하나에 서른 번까지만 부른다)에서, YouTube 의 영상 정보는 yt-dlp 가 YouTube 에서 받는다. 두 곳 다 로그인 · 쿠키 없이 부르고, 받은 글의 권리는 글쓴이와 그 서비스에 있다 — 프로그램은 발췌를 이 PC 의 프로젝트 폴더에만 두고 기본으로 git 에 올리지 않는다.

Claude · Anthropic, ChatGPT · Codex · OpenAI, Obsidian, Tailscale 은 각 회사의 상표다. 이 프로그램은 그 회사들과 관계가 없다.
