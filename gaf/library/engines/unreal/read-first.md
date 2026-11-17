---
title: Unreal 참조를 쓰기 전에
group: 엔진 Unreal
summary: 받은 그대로 둔 Unreal 참조의 원문 판과 날짜 · 쓰는 법 · 전수검사가 짚은 의심
game_engine: unreal
source: docs/engine-reference/unreal/VERSION.md
---
## 이 참조는 무엇인가
- `engines/unreal/` 의 글 16개는 공개 저장소 claude-code-game-studios(v1.3.0 · MIT)의 엔진 참조를 받은 그대로 둔 영어 글이다. 출처와 라이선스는 서랍의 `NOTICE.md` 에 있다.
- 이 프레임워크에서 Unreal Engine을 돌려 확인한 글이 아니다. 판 · API · 예제 · 실측 기록은 모두 원문 저자가 한 말이다.
- 그래서 후보로만 쓴다. 프로젝트에 설치된 엔진 · 플러그인 판에서 확인한 것만 구현 · 검사의 근거로 삼는다.
- 이 글은 원문 `VERSION.md` 의 판과 날짜를 옮긴 것이다. 원문 `VERSION.md` · `README.md` 는 서랍에 넣지 않았다(그쪽 작업 흐름과 모델 학습일 단정이 섞여 있다).

## 원문이 밝힌 판과 날짜
아래는 원문 `VERSION.md` 의 주장이다. 이 틀이 확인한 사실이 아니다.

| 항목 | 원문이 적은 값 |
|---|---|
| 기준 판 | Unreal Engine 5.8 |
| 5.8이 나온 때 | 2026년 6월(5.8.0) · 마지막 핫픽스 5.8.3은 2026년 9월 |
| 기준 판을 고른 날 | 2026-10-17 |
| 설치된 판 | 정해지지 않음(`NOT DETERMINED`) — 설치된 편집기의 판을 재서 적지 않았다 |

부분마다 확인했다는 날이 다르다.

| 부분 | 원문이 확인했다는 날 |
|---|---|
| Iris 상태(`modules/networking`) · MegaLights(`modules/rendering`) | 2026-10-18 |
| 발표 일정 · 5.8 발표 노트 · 이 폴더에 적은 5.8 변경 | 2026-10-17 |
| 나머지 전부(5.7을 기준으로 쓴 것) | 2026-02-13 |

- 글마다 머리의 `Last verified` 도 이렇게 나뉜다. 보통 「UE 5.8 changes」 블록(또는 이름을 댄 절)만 2026-10-17이고, 나머지 본문은 5.7 기준 2026-02-13이다. 제목에 5.8이 있어도 본문 전체를 5.8에서 확인했다는 뜻이 아니다.
- 발표일(원문이 2026-10-17에 Epic 발표 글과 맞춰 봤다는 값) — 5.4는 2024-04-23 · 5.5는 2024-11-12 · 5.6은 2025-06-03 · 5.7은 2025-11-12 · 5.8은 2026-06-17. 핫픽스 5.8.1은 2026-07-28 · 5.8.2는 2026-08-25 · 5.8.3은 2026-09-22.
- 원문이 꼽은 5.7 · 5.8 주제 — 5.7은 PCG 정식 · Substrate 정식(새 프로젝트는 기본으로 켜짐) · MegaLights 베타 · 편집기 안 AI Assistant(실험). 5.8은 MegaLights 정식 · Iris 복제 정식 · Mesh Terrain(실험) · Lumen Lite(베타) · Enhanced Input과 Common UI 통합 · MCP Server 플러그인(실험) · C++20 최소 · Windows 기본 오디오가 WASAPI · 모바일은 multi-pass deferred가 기본.
- 원문은 일부(C++ 표준 · Substrate 기본값)를 2026-10-17에 UE 5.8.1 설치본의 엔진 소스에서 읽었다고 한다. 원문 저자의 관찰이다.
- 원문도 이 참조가 설치된 편집기보다 앞설 수 있다고 경고한다. 맞게 인용해도 설치판에서는 컴파일되지 않는 API가 나올 수 있다. 「정해지지 않음」은 차이가 없다는 뜻이 아니라 모른다는 뜻이다.

## 쓰는 법
1. 프로젝트에 설치된 엔진 판(핫픽스까지)과 켜 둔 플러그인 · 그 상태(정식 · 베타 · 실험)를 먼저 적는다.
2. 맡은 일에 필요한 모듈 · 플러그인 글만 읽는다. 글 안에서도 「UE 5.8 changes」 블록과 5.7 기준 본문을 나눠 읽는다.
3. 참조의 API · 예제는 그 판에서 컴파일 · 실행해 본 뒤에 쓴다.
4. 「사라졌다 · 폐기됐다 · 바꾸는 것이 좋다」를 가른다. 표만 보고 일괄 치환하지 않는다.
5. 확인하지 못한 것은 보고에 「확인 안 됨」으로 적는다.

## 알려진 의심
2026-11-16 전수검사(`docs/조사/ccgs-전수/engines/정리.md` 2절 · 4절)가 짚은 것이다. 전수검사도 인터넷이나 설치된 엔진으로 다시 확인하지는 않았다. 「의심」은 확정 오류도, 최신 정답도 아니다. 줄 번호는 받은 저장소 커밋 abc0de79의 원문 파일 기준이다. 서랍 글에는 머리말 9줄이 붙어 있어 원문 N줄은 서랍 글의 N+9줄이다.

| 어느 글 | 무엇이 의심스러운가 | 어떻게 다루나 |
|---|---|---|
| `engines/unreal/current-best-practices`(원문 3줄) · 모든 모듈 · 플러그인 글의 머리 | 5.8 변경만 10월에 확인했고, 나머지는 5.7 기준 2월 확인이다. | 확인한 날을 부분마다 나눠 본다. 이 틀이 읽은 날(2026-11-16)을 API 확인일로 덮어쓰지 않는다. |
| `engines/unreal/breaking-changes` | 5.7에서 남은 항목과 5.8에서 확인한 항목이 한 글에 있다. 자동 설치 · 일괄 전환 지시가 있다. | 둘을 나눠 본다. 설치 · 전환은 프로젝트마다 정한다. |
| `engines/unreal/deprecated-apis`(원문 50줄 · 112줄) · `engines/unreal/current-best-practices`(원문 305줄) | Forward shading→Deferred+Lumen, `DOREPLIFETIME`→`DOREPLIFETIME_CONDITION` 을 폐기 대체처럼 적었다. 그런데 `current-best-practices` 는 다시 `DOREPLIFETIME` 을 쓴다. | 공식 폐기 · 선택적 이전 · 성능 권장을 나눈다. `DOREPLIFETIME` 을 통째로 금지하지 않는다. 표만 보고 자동 치환하지 않는다. |
| `engines/unreal/current-best-practices`(원문 35줄 · 46~49줄) | macOS의 명령줄 실행 파일은 원문도 못 찾았다(`NOT SOURCEABLE`). Linux 시험 줄은 문헌을 이어 붙인 것이다. 실제로 돌린 것은 Windows 열(5.7)뿐이다. | 그 환경의 관찰로만 본다. 종료 코드만 보지 말고 시험 결과 · 시험 수 · 실행 범위를 함께 본다. |
| `engines/unreal/current-best-practices` | 고정된 최신 판을 강제하는 말, 검증 없는 성능 주장 · 서버 검증 주장이 있다. | 프로젝트 판을 따른다. 성능 · 서버 검증은 그 판에서 재서 확인한다. |
| `engines/unreal/modules/networking`(원문 129~140줄 · 218~221줄) | 피해 RPC는 피해 범위(0~100)만 검사한다. 이동 RPC의 검증은 주석뿐이다. | 개념 예제다. 완성 보안 코드로 쓰지 않는다. 권한 · 행동 가능 상태 · 대상 · 빈도 · 서버가 계산할 값을 검사하게 만들고 거부 시험을 붙인다. |
| `engines/unreal/modules/networking`(원문 99~100줄) | `COND_InitialOnly` 를 「바뀔 때만」으로 설명한다. 처음 한 번 보내는 조건과 헷갈린 것이다. | 주석의 뜻을 고친 뒤 실제 판에서 확인한다. |
| `engines/unreal/modules/networking`(원문 348~380줄) | 바깥 세션에 붙는 예제다. 플러그인 켜기도 따른다. | 기본으로 실행하지 않는다. 승인된 별도 일로만 한다. |
| `engines/unreal/modules/navigation`(원문 169~175줄 · 161~162줄 · 311줄) | `ProjectPointToNavigation` 성공을 「도달 가능」으로 쓴다. 투영이 됐을 뿐 출발점에서 가는 길이 있다는 증명이 아니다. 경로 타입은 컴파일 확인이 필요하다. 순찰 배열이 비면 나머지 연산이 0으로 나눈다. | 투영과 도달을 나누고, 경로 타입을 컴파일로 확인하고, 빈 배열을 처리한다. |
| `engines/unreal/modules/navigation`(원문 320~337줄) | 매 프레임 이동을 다시 요청한다. 자동 설정 지시가 있다. | 요청 횟수를 줄이고, 설정은 프로젝트마다 정한다. |
| `engines/unreal/modules/ui`(원문 338~340줄 · 82줄) | 포커스를 끄는 줄(`bIsFocusable = false`)을 tick 끄기로 설명한다. `MaxHealth` 가 0이면 0으로 나눈다. | 주석의 뜻을 고치고 0을 처리한다. |
| `engines/unreal/modules/ui` | 이벤트를 되풀이해 등록할 수 있다. 무조건 재시작 · 플러그인 켜기 지시가 있다. | 등록을 한 번만 하게 한다. 재시작 · 플러그인은 프로젝트마다 정한다. |
| `engines/unreal/modules/rendering`(원문 74~77줄) | Nanite가 머티리얼의 WPO를 못 쓴다고 단정한다. | 다시 확인해야 한다. 확인 전에는 제한으로 단정하지 않는다. |
| `engines/unreal/modules/rendering` | 툴팁과 소스가 어긋난다는 기록(원문 92줄 절)은 값진 기록이다. RDG 예제는 다시 확인해야 한다. 고급 렌더러를 일괄 적용하라는 말이 있다. | 기록은 살린다. RDG 예제는 그 판에서 확인한다. 렌더러는 프로젝트마다 정한다. |
| `engines/unreal/plugins/common-ui`(원문 130줄 · 188줄 · 272~275줄) | `SetButtonText` · `DeactivateWidget` · `PushStreamedGameplayUIInputConfig` / `PopInputConfig` 같은 메서드가 실제로 있는지 의심된다. 되풀이 등록도 있다. | 설치판에서 확인한다. 등록은 한 번만 하게 한다. |
| `engines/unreal/plugins/common-ui`(원문 235줄 · 356줄) | 5.8의 Enhanced Input 통합과 옛 DataTable 방식 입력 액션 절차가 섞여 있다. 자동 설치 지시가 있다. | 둘을 나눠 본다. 설치는 프로젝트마다 정한다. |
| `engines/unreal/plugins/common-ui`(원문 284~303줄) | Xbox · PlayStation 등 플랫폼 버튼 이름이 나온다. | MIT만으로 상표 이용 범위까지 확인됐다고 보지 않는다. 로고 · 아이콘 자산은 따로 확인한다. |
| `engines/unreal/plugins/gameplay-camera-system`(원문 7~14줄 · 169~180줄 · 303~315줄) | 5.8 변경(블록)과 예전 `SetCameraRig` · `BlendToCameraRig` 계열 예제가 맞는지 의심된다. Spring Arm을 낡았다며 바꾸라고 한다. | 실험 기능이다. 그 판에서 확인한다. Spring Arm을 일괄 교체하지 않는다. |
| `engines/unreal/plugins/gameplay-ability-system` | `IAbilitySystemInterface` 같은 연결이 빠져 있다. 서버에서 부여 · 속성 제한 · 태그 변경 근거는 값진 부분이다. | 빠진 연결을 채워 컴파일로 확인한다. GAS를 모든 장르에 강제하지 않는다. |
| `engines/unreal/plugins/pcg` | 개념 그래프와 실제 노드 이름이 섞여 있다. 그래프 일괄 재작성 · 플러그인 켜기 지시가 있다. | 실제 노드 이름을 확인하고, 판 · seed · 생성 범위를 밝힌다. 일괄 재작성은 하지 않는다. |
| `engines/unreal/modules/animation` | 5.8 변경 블록과 5.7 예제가 함께 있다. null 검사와 Notify가 중간에 끊겼을 때의 복구(원문 171~185줄)가 없다. 고정된 기술 묶음을 강제하는 말이 있다. | 둘을 나눠 보고, null 검사 · 중단 복구를 더한다. 기술 묶음은 프로젝트마다 정한다. |
| `engines/unreal/modules/audio`(원문 217줄 · 192줄) | 발소리 배열이 비면 잘못된 번호를 고른다. 컴포넌트 유효성 검사가 없다. 가림 API는 확인이 필요하다. | 빈 배열 · 유효성을 처리하고 가림 API를 확인한다. 권장값을 프로젝트 필수값으로 쓰지 않는다. |
| `engines/unreal/modules/input`(원문 253~265줄) | 재바인딩 예제와 클래스 선언이 생략돼 있다(`/* Get subsystem */` 등). 일괄 설치 · 옛 입력 금지 지시가 있다. | 예제를 채워 컴파일로 확인한다. 옛 입력 금지 대신 적용 조건을 둔다. |
| `engines/unreal/modules/physics`(원문 278줄 · 242~249줄 · 264~265줄) | 오래된 `pxvis` 명령, 생략된 파괴 예제가 있다. 서브스텝 줄이기를 조건 없이 권한다. 자동 플러그인 설정 지시가 있다. | 명령 · 예제를 그 판에서 확인한다. 서브스텝은 조건을 보고 정한다. |
| `engines/unreal/plugins`(원문 178~181줄 · 13줄 · 230~234줄) | MCP Server 플러그인을 소개하고, WebSearch로 찾으라고 한다. | 남의 MCP를 들이지 않는다. 읽었다고 인터넷 조회가 허락되는 것은 아니다. 실험 · 베타 표시와 확인 날짜는 살리고, 권장과 공식 폐기를 나눈다. |

## 들어 있는 글
| 글 id | 한마디 |
|---|---|
| `engines/unreal/plugins` | 5.8 설치판의 상태 표시 · GAS · CommonUI 등 플러그인 분류와 실험 · 폐기 목록 |
| `engines/unreal/breaking-changes` | 5.8 변경을 먼저 두고 5.3→5.8의 위험도별 변경 · 플랫폼별 변경 · 이전 점검표 |
| `engines/unreal/current-best-practices` | 프로젝트 구성 · 빌드와 자동 시험 명령 · C++ · 렌더링 · 입력 · GAS · 복제 · 최적화 |
| `engines/unreal/deprecated-apis` | 5.8에서 폐기된 것과 입력 · 렌더링 · 애니메이션 · 네트워크 등의 대체 API 표 |
| `engines/unreal/modules/animation` | 애니메이션 블루프린트 · 몽타주 · 블렌드 스페이스 · Control Rig · 리타기팅 |
| `engines/unreal/modules/audio` | 소리 재생 · 감쇠와 공간음 · MetaSounds · 가림과 잔향 · 동시 재생 제한 |
| `engines/unreal/modules/input` | Enhanced Input 액션 · 매핑 컨텍스트 · 트리거 · 게임패드 · 터치 · 재바인딩 |
| `engines/unreal/modules/navigation` | 내비 메시 설정 · AI 이동 · 경로 질의 · 내비 영역 · 동적 메시 · 군중 · 순찰 예제 |
| `engines/unreal/modules/networking` | 복제 변수와 조건 · RPC · 서버 권위 이동 · 연관성 · 소유권 · 세션 · Iris |
| `engines/unreal/modules/physics` | Chaos 강체 · 충돌 채널과 이벤트 · 라인 트레이스 · 캐릭터 이동 · 제약 · 파괴 |
| `engines/unreal/modules/rendering` | Lumen · Nanite · MegaLights · Substrate · 머티리얼 · 후처리 · RDG |
| `engines/unreal/modules/ui` | UMG 위젯 만들기와 바인딩 · 애니메이션 · 레이아웃 · 월드 UI · CommonUI · HUD |
| `engines/unreal/plugins/common-ui` | 활성화 위젯 스택 · 입력 인식 버튼 · 입력 액션 · 포커스와 탐색 · 플랫폼 입력 아이콘 |
| `engines/unreal/plugins/gameplay-ability-system` | 능력 시스템 컴포넌트 · 속성 세트 · 능력 · 효과 · 태그 · 쿨다운과 비용 |
| `engines/unreal/plugins/gameplay-camera-system` | 실험 기능인 카메라 리그 · 디렉터 · 노드 · 리그 전환과 블렌드 · 옛 카메라에서 옮기기 |
| `engines/unreal/plugins/pcg` | PCG 그래프 · 샘플러 · 필터 · 스포너 · 바이옴과 스플라인 예제 · 런타임 생성 |
