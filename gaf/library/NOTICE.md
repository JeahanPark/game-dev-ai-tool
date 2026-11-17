# 서랍(gaf/library)의 출처와 라이선스

이 폴더의 글은 공개 저장소 **claude-code-game-studios** 의 글을 바탕으로 한 것이다(「이 틀에서 쓴 글」이라고 적힌 것만 빼고).

| 무엇 | 값 |
|---|---|
| 출처 | https://github.com/donchitos/claude-code-game-studios |
| 판 | v1.3.0 · 커밋 `abc0de79d6ceb0b5d7aa693945849b3937afbe6a`(2026-11-09) |
| 라이선스 | MIT — 아래 전문 |
| 받은 날 | 2026-11-16(읽기만 했다 — 그 저장소의 스크립트 · 훅 · 설정은 넣지 않았고 돌리지도 않았다) |

## 칸마다 — 무엇을 어떻게 옮겼나

각 글 머리말의 `source` 가 바탕이 된 원문의 경로다(그 저장소의 뿌리 기준).

| 칸 | 글 수 | 바탕이 된 원문 | 어떻게 옮겼나 |
|---|---|---|---|
| `roles/` 역할 | 49 | `.claude/agents/*.md` 49개 | 한국어로 고쳐 썼다 |
| `procedures/` 절차 | 22 | `.claude/skills/` 의 스킬 78개 가운데 72개 | 주제 22개로 묶어 한국어로 고쳐 썼다 |
| `templates/` 문서 틀 | 68 | `.claude/docs/templates/` 의 파일 74개(글 70개 + 웹 게임 시작 틀의 코드 · 설정 파일 4개) 가운데 글 67개 | 한국어로 고쳐 썼다. `feature-map` 하나는 이 틀에서 쓴 글이다 |
| `rules/` 규칙 | 21 | `.claude/rules/*.md` 14개 · `.claude/docs/director-gates.md` 와 `director-gates/` 의 28개 | 한국어로 고쳐 썼다(검토 질문 28개는 분야 일곱으로 묶었다) |
| `genres/` 장르 시트 | 72 | `.claude/genres/chipsets/*.md` 69개 · `.claude/genres/` 의 공통 글 3개 | 한국어로 고쳐 썼다 |
| `engines/` 엔진 참조 | 52 | `docs/engine-reference/<godot·unity·unreal·web>/` 의 글 48개 | **받은 그대로 둔 영어 글**이다 — 맨 위에 머리말과 알림 한 줄만 붙였다. 엔진마다의 `read-first` 4개는 원문 `VERSION.md` 와 전수검사의 지적을 한국어로 정리한 글이다 |

## 고친 것

- `engines/` 의 참조 48개를 뺀 나머지는 원문을 그대로 옮긴 것이 아니다. 한국어로 옮기면서 이 프레임워크의 꼴에 맞게 고쳐 썼다.
- 뺀 것: Claude Code 전용 약속(쓰기 전마다 사람에게 묻기 · 스킬 · 슬래시 명령 · 서브에이전트 · 그쪽 폴더 구조와 설정) · git 쓰기 · 설치 · 내려받기 · 배포를 실행하라는 지시 · 실제 게임 · 사람의 이름과 출처를 확인하지 못한 인용 · 그쪽 도구의 한계를 적은 문장.
- 낮춘 것: 조건 없는 숫자는 「시작 값의 예」로, 엔진 · 패키지 · 상점의 판에 묶인 사실은 「설치된 판에서 확인할 것」 · 「확인할 질문」으로 적었다.
- 더한 것: 이 프레임워크의 약속(판정 낱말 넷 — 통과 · 실패 · 못 봄 · 해당 없음 / 사람이 정할 것 / 검토한 역할 · 판정 · 근거 칸 / 검사로 만들 수 있는 것).
- **엔진 · 패키지의 API와 판 이야기는 이 프레임워크에서 엔진을 돌려 확인한 것이 아니다.** 받은 그대로 둔 참조에는 전수검사가 짚은 의심이 있다 — 엔진마다 `engines/<엔진>/read-first` 의 「알려진 의심」에 적어 두었다.
- 넣지 않은 것: 그 저장소의 훅 · 스크립트 · 설정 · 웹 게임 시작 틀 · 그쪽 글을 검사하는 명세 · 남의 글 인용 모음(`.claude/genres/sources.md`) · 엔진 참조의 `README.md` 와 `VERSION.md`.
- 어떤 파일을 왜 넣고 뺐는지는 이 저장소의 `docs/조사/ccgs-전수/`(파일 794개의 판정)와 `docs/조사/claude-code-game-studios-전수-2026-11-16.md` 에 있다.

## 이 틀에서 쓴 글

- `templates/feature-map.md`(기능 지도) — 머리말 `source` 에 「이 틀에서 쓴 글」이라고 적혀 있다. 위 저장소의 글이 아니다.

## 알아 둘 것

- 원문의 보증 부인(아래 전문의 마지막 문단)이 이 글에도 그대로 적용된다.
- Unity · Unreal Engine · Godot 같은 이름은 그 주인의 상표다. 이 글은 그 회사 · 단체와 관계가 없고, 인증 · 제휴를 뜻하지 않는다.
- 이 글이 그 엔진 · 패키지 · 외부 자산의 이용 권리를 주는 것은 아니다 — 각자의 라이선스를 따른다.

## MIT License(원문 저장소의 `LICENSE` 그대로)

```
MIT License

Copyright (c) 2026 Donchitos

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
