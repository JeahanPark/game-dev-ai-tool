---
title: Godot 참조를 쓰기 전에
group: 엔진 Godot
summary: 받은 그대로 둔 Godot 참조의 원문 판과 날짜 · 쓰는 법 · 전수검사가 짚은 의심
game_engine: godot
source: docs/engine-reference/godot/VERSION.md
---
## 이 참조는 무엇인가
- `engines/godot/` 의 글 11개는 공개 저장소 claude-code-game-studios(v1.3.0 · MIT)의 엔진 참조를 받은 그대로 둔 영어 글이다. 출처와 라이선스는 서랍의 `NOTICE.md` 에 있다.
- 이 프레임워크에서 Godot를 돌려 확인한 글이 아니다. 판 · API · 예제 · 실측 기록은 모두 원문 저자가 한 말이다.
- 그래서 후보로만 쓴다. 프로젝트에 설치된 판에서 확인한 것만 구현 · 검사의 근거로 삼는다.
- 이 글은 원문 `VERSION.md` 의 판과 날짜를 옮긴 것이다. 원문 `VERSION.md` · `README.md` 는 서랍에 넣지 않았다(그쪽 작업 흐름과 모델 학습일 단정이 섞여 있다).

## 원문이 밝힌 판과 날짜
아래는 원문 `VERSION.md` 의 주장이다. 이 틀이 확인한 사실이 아니다.

| 항목 | 원문이 적은 값 |
|---|---|
| 기준 판 | Godot 4.7 |
| 4.7이 나온 때 | 2026년 6월 · 마지막 패치 4.7.2는 2026년 8월 |
| 기준 판을 고른 날 | 2026-10-17 |
| 설치된 판 | 정해지지 않음(`NOT DETERMINED`) — 설치된 편집기의 판을 재서 적지 않았다 |

부분마다 확인했다는 날이 다르다.

| 부분 | 원문이 확인했다는 날 | 무엇과 맞춰 봤다나 |
|---|---|---|
| Windows 드라이버 기본값 · Compositor | 2026-10-21 | Godot 소스 |
| 발표 일정 · 4.7 변경 | 2026-10-17 | 공식 이전 안내 · 발표 글 |
| 4.4~4.6 내용 | 2026-02-12 | (적지 않음) |

- 글마다 머리의 `Last verified` 도 이렇게 나뉜다. 보통 「4.7 변경은 2026-10-17, 나머지는 2026-02-12」다(렌더링 글은 일부가 2026-10-21). 제목에 4.7이 있어도 본문 전체를 4.7에서 확인했다는 뜻이 아니다.
- 발표일(원문이 2026-10-17에 GitHub 릴리스 목록에서 봤다는 값) — 4.4는 2025-03-03 · 4.5는 2025-09-15 · 4.6은 2026-01-26 · 4.7은 2026-06-18 · 4.7.2는 2026-08-18.
- 원문이 꼽은 판별 주제 — 4.5는 접근성(AccessKit) · 가변 인자 · `@abstract` · 셰이더 베이커 · SMAA. 4.6은 Jolt 기본 · glow 손질 · IK 복원. 4.7은 AreaLight3D · HDR 출력 · Asset Store가 Asset Library를 대신함 · VirtualJoystick · Jolt 소프트바디와 월드 경계 변경 · 타입 반환 재정의.
- 원문도 이 참조가 설치된 편집기보다 앞설 수 있다고 경고한다. 맞게 인용해도 설치판에서는 컴파일되지 않는 API가 나올 수 있다. 「정해지지 않음」은 차이가 없다는 뜻이 아니라 모른다는 뜻이다.

## 쓰는 법
1. 프로젝트에 설치된 Godot의 판(패치까지)과 쓰는 애드온(예: gdUnit4)의 판을 먼저 적는다.
2. 맡은 일에 필요한 모듈 글만 읽는다. 설치판과 4.7 사이가 걸리면 `breaking-changes` 의 그 판 절을 더 본다.
3. 참조의 API · 예제는 그 판에서 파싱 · 실행해 본 뒤에 쓴다.
4. 「사라졌다 · 폐기됐다 · 바꾸는 것이 좋다」를 가른다. 표만 보고 일괄 치환하지 않는다.
5. 확인하지 못한 것은 보고에 「확인 안 됨」으로 적는다.

## 알려진 의심
2026-11-16 전수검사(`docs/조사/ccgs-전수/engines/정리.md` 2절 · 4절)가 짚은 것이다. 전수검사도 인터넷이나 설치된 엔진으로 다시 확인하지는 않았다. 줄 번호는 받은 저장소 커밋 abc0de79의 원문 파일 기준이다. 서랍 글에는 머리말 9줄이 붙어 있어 원문 N줄은 서랍 글의 N+9줄이다.

| 어느 글 | 무엇이 의심스러운가 | 어떻게 다루나 |
|---|---|---|
| `engines/godot/deprecated-apis`(원문 9~10줄) · `engines/godot/breaking-changes` · `engines/godot/modules/animation` | 「Deprecated 칸의 API는 반드시 Use Instead 칸으로 바꾼다」고 한다. 그런데 표에는 제거 · 이름 변경 · 권장 패턴이 섞여 있다. 같은 글 5~7줄은 4.7 이전 안내에 공식 폐기가 없다고 적는다. | 항목마다 제거인지 · 공식 폐기인지 · 권장 변경인지 가른다. 표만 보고 자동 치환하지 않는다. |
| `engines/godot/modules/networking`(원문 39~49줄 · 70~72줄) | 서버 검증 자리가 주석 한 줄뿐이다. 실행 함수와 접속 인증 함수의 몸은 `pass` 다. | 개념 예제다. 완성 서버 · 보안 코드로 쓰지 않는다. 소유권 · 행동 가능 상태 · 대상 · 빈도 · 서버가 계산할 값을 검사하게 만들고 거부 시험을 붙인다. |
| `engines/godot/current-best-practices`(원문 196~224줄) | 원문 저자가 Windows · 4.6.1/4.7.x에서 본 기록이다. 시험이 0개여도 종료 코드 0, `--import` · `--quit-after` 는 파싱 오류에도 0, 새 프로젝트의 첫 `--import` 가 끝날 때 가끔 죽음(139). 223줄의 파싱 검사 스크립트(`res://.claude/scripts/…`)는 그쪽 저장소 것이라 이 틀에 없다. | 그 환경의 관찰로만 본다. 종료 코드 0을 성공으로 세지 않는다 — 결과 파일 · 이번 실행의 보고서인지 · 실행된 시험 수 · 실패를 함께 본다. ERROR를 다 무시하거나 무한 재시도로 감추지 않는다. |
| 모든 글(머리의 `Last verified` · 「POST-CUTOFF」 · 「in training data」) | 4.7 부분만 10월에 확인했고 나머지는 2026-02-12 확인이다. 모델이 약 4.3까지 안다는 전제로 쓴 문장이 곳곳에 있다. | 확인한 날을 부분마다 나눠 본다. 이 틀이 읽은 날(2026-11-16)을 API 확인일로 덮어쓰지 않는다. 학습일 대신 설치판과 확인한 API로 고른다. |
| 공식 링크(예: `engines/godot/breaking-changes` 원문 112~117줄) | `docs.godotengine.org/en/stable/…` 는 움직이는 문서다. 지금 열면 원문이 읽은 때와 다를 수 있다. | 링크가 있다는 것과 그 주장에 근거가 있다는 것을 나눈다. 확인하면 판 태그 · 그때의 절 · 확인 환경을 함께 남긴다. |
| `engines/godot/modules/audio` · `input` · `navigation` · `physics` · `rendering` · `ui` | 예제가 설정과 변수를 생략한다(노드 경로 · 프로젝트 설정 등). | 구현 역할이 설치판과 모듈 설정에 맞춰 채우고, QA가 확인한다. |

## 들어 있는 글
| 글 id | 한마디 |
|---|---|
| `engines/godot/breaking-changes` | 4.3→4.7 판마다 바뀐 API · 동작 · 기본값 표와 공식 이전 안내 링크 |
| `engines/godot/current-best-practices` | GDScript 새 문법 · 물리 · 렌더링 · UI · 편집기 변화와 명령줄 시험의 종료 코드 실측 |
| `engines/godot/deprecated-apis` | 폐기되거나 이름이 바뀐 노드 · 메서드 · 속성과 대신 쓸 것 표, 옛 코드 패턴 |
| `engines/godot/modules/animation` | AnimationMixer 기반 클래스 · 3D IK 노드 · 뼈 제약 · Tween 변경 |
| `engines/godot/modules/audio` | 4.7의 area_mask 기본값 변경 · 재생 · 3D 공간음 · 오디오 버스 · 효과음 재생기 풀 |
| `engines/godot/modules/input` | 입력 액션과 이벤트 · 마우스/키보드 기기 ID 변경 · 이중 포커스 · SDL3 게임패드 |
| `engines/godot/modules/navigation` | NavigationAgent 2D/3D · 저수준 경로 질의 · 회피 · 내비게이션 레이어 |
| `engines/godot/modules/networking` | 고수준 멀티플레이 · RPC · 노드 생성과 속성 동기화 · 접속 인증 설정 예제 |
| `engines/godot/modules/physics` | Jolt 기본 엔진과 4.7 변경 · Jolt와 GodotPhysics 비교 · 기본 설정 · 레이캐스트 |
| `engines/godot/modules/rendering` | AreaLight3D · HDR 출력 · 후처리 · 안티에일리어싱 · 렌더링 백엔드 선택 |
| `engines/godot/modules/ui` | 테마 · 이중 포커스 · 접는 컨테이너 · 재귀 비활성 · 번역 대비 UI |
