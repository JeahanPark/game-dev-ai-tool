---
title: Unity 참조를 쓰기 전에
group: 엔진 Unity
summary: 받은 그대로 둔 Unity 참조의 원문 판과 날짜 · 쓰는 법 · 전수검사가 짚은 의심
game_engine: unity
source: docs/engine-reference/unity/VERSION.md
---
## 이 참조는 무엇인가
- `engines/unity/` 의 글 15개는 공개 저장소 claude-code-game-studios(v1.3.0 · MIT)의 엔진 참조를 받은 그대로 둔 영어 글이다. 출처와 라이선스는 서랍의 `NOTICE.md` 에 있다.
- 이 프레임워크에서 Unity를 돌려 확인한 글이 아니다. 판 · API · 예제 · 실측 기록은 모두 원문 저자가 한 말이다.
- 그래서 후보로만 쓴다. 프로젝트에 설치된 편집기 · 패키지 판에서 확인한 것만 구현 · 검사의 근거로 삼는다.
- 이 글은 원문 `VERSION.md` 의 판과 날짜를 옮긴 것이다. 원문 `VERSION.md` · `README.md` 는 서랍에 넣지 않았다(그쪽 작업 흐름과 모델 학습일 단정이 섞여 있다).

## 원문이 밝힌 판과 날짜
아래는 원문 `VERSION.md` 의 주장이다. 이 틀이 확인한 사실이 아니다.

| 항목 | 원문이 적은 값 |
|---|---|
| 기준 판 | Unity 6.3 LTS |
| 6.3 LTS가 나온 때 | 2025년 12월 · 첫 편집기 6000.3.0f1은 2025-12-03 · 발표 글은 2025-12-04 |
| 그 줄의 마지막 편집기 | 6000.3.26f1(2026-10-08) |
| 기준 판을 고른 날 | 2026-02-13 |
| 설치된 판 | 정해지지 않음(`NOT DETERMINED`) — 설치된 편집기의 판을 재서 적지 않았다 |
| 지원 기간 | 6.3 LTS는 2027년 12월까지 · 6.0 LTS는 2026년 10월에 끝남 |

부분마다 확인했다는 날이 다르다.

| 부분 | 원문이 확인했다는 날 |
|---|---|
| 발표 일정 · 뒤에 나온 판(6.4~6.7) · 6.3 LTS의 마지막 편집기 | 2026-10-17 |
| 이 폴더의 나머지 전부 | 2026-02-13 |

- 서랍의 글 15개는 모두 머리가 `Last verified: 2026-02-13` 이다. 10월 확인은 `VERSION.md` 의 연혁 부분뿐이고, 본문은 2월 확인이다.
- 뒤에 나온 판(원문이 2026-10-17에 본 상태). 원문은 이 판들의 API를 이 폴더 어디에서도 다루지 않으니, 그 판의 문서로 확인하기 전에는 확인 안 된 것으로 보라고 한다. 기준 판은 6.7 LTS가 나올 때까지 6.3 LTS로 둔다고 한다.

| 판 | 나온 때 | 상태 | 원문이 꼽은 주제 |
|---|---|---|---|
| 6.4 | 2026-03 | 지원 끝 | ECS 패키지가 Core 패키지로 · Project Auditor 내장 · Windows DirectStorage · `EntityId` 가 `InstanceID` 를 대신 · URP 호환 모드 제거(Render Graph로) |
| 6.5 | 2026-06 | 지원 끝 | `InstanceID`→`EntityId` 통합 · CoreCLR 편집기로 가는 Editor Lifecycle API · 2D(사용자 조명과 그림자 · `RenderSprite` · Physics Core 2D) · 모바일 on-tile 후처리 · Built-In 렌더 파이프라인 폐기 |
| 6.6 | 2026-09 | 6.7이 나올 때까지 지원 | Unity 6 마지막 지원판 · 새 프로젝트는 Play Mode에서 도메인 리로드 생략이 기본 · Content Directories · Build Analysis 창 · WebGPU가 웹의 정식 선택지 |
| 6.7 | 베타 | 아직 안 나옴 | 6000.7.0b1(2026-09-17)~6000.7.0b5(2026-10-15) · Unity의 6.6 발표 글은 「6.7 LTS」라 부름 |

- 6.6과 6.5 줄의 Built-In · WebGPU 설명은 아래 「알려진 의심」의 판 경계 항목에 걸린다.
- 앞선 판의 발표일(원문이 2026-10-17에 Unity 발표 글과 맞춰 봤다는 값) — 6.0 LTS는 2024-10-17 · 6.1은 2025-04-24 · 6.2는 2025-08-12.
- 원문도 이 참조가 설치된 편집기보다 앞설 수 있다고 경고한다. 맞게 인용해도 설치판에서는 컴파일되지 않는 API가 나올 수 있다. 「정해지지 않음」은 차이가 없다는 뜻이 아니라 모른다는 뜻이다.

## 쓰는 법
1. 프로젝트에 설치된 편집기 판(예: 6000.3.x)과 쓰는 패키지의 판(`Packages/manifest.json` · `packages-lock.json`)을 먼저 적는다. 렌더 파이프라인(URP · HDRP · Built-In)도 적는다.
2. 맡은 일에 필요한 모듈 · 패키지 글만 읽는다.
3. 참조의 API · 예제는 그 판에서 컴파일 · 실행해 본 뒤에 쓴다.
4. 「사라졌다 · 폐기됐다 · 바꾸는 것이 좋다」를 가른다. 표만 보고 일괄 치환하지 않는다.
5. 확인하지 못한 것은 보고에 「확인 안 됨」으로 적는다.

## 알려진 의심
2026-11-16 전수검사(`docs/조사/ccgs-전수/engines/정리.md` 2절 · 4절)가 짚은 것이다. 전수검사도 인터넷이나 설치된 엔진으로 다시 확인하지는 않았다. 「의심」은 확정 오류도, 최신 정답도 아니다. 줄 번호는 받은 저장소 커밋 abc0de79의 원문 파일 기준이다. 서랍 글에는 머리말 9줄이 붙어 있어 원문 N줄은 서랍 글의 N+9줄이다.

| 어느 글 | 무엇이 의심스러운가 | 어떻게 다루나 |
|---|---|---|
| `engines/unity/plugins`(원문 175~180줄) | 「Preview/Experimental」 절 안에서 Splines를 Production-Ready라고 한다. | 실제 패키지 상태를 공식 문서와 맞춰 보기 전에는 표준 지시로 쓰지 않는다. |
| `engines/unity/plugins`(원문 200~212줄) · `engines/unity/breaking-changes` · `engines/unity/modules/ui` | UGUI · 옛 파티클 시스템을 「폐기」로 단정한다. 같은 곳에 「아직 지원된다」는 말도 있다. | 다시 확인하기 전에는 UGUI를 배제하지 않는다. 폐기인지 권장인지 가른다. |
| `engines/unity/plugins`(원문 13~15줄 · 220~232줄) 등 여러 글의 설치 절차 | WebSearch로 찾으라는 지시와 Package Manager 설치 절차가 있다. | 읽었다고 설치 · 인터넷 조회가 허락되는 것은 아니다. 설치는 이 틀의 기존 경계(사용자 동의)를 따른다. |
| 원문 `VERSION.md`(48~49줄 · 이 글의 위 표) · `engines/unity/breaking-changes`(원문 125~133줄) | Built-In · WebGPU 설명의 판 경계가 의심스럽다. `breaking-changes` 는 6.0부터 WebGPU가 기본이라 하고, `VERSION.md` 는 6.6에서 WebGPU가 웹의 정식 선택지가 됐다고 한다. | 실제 판과 공식 판별 문서로 맞춰 보기 전에는 쓰지 않는다. 이번에 맞는 판을 정한 것은 아니다. |
| `engines/unity/breaking-changes`(원문 71~89줄) | 6.2부터 Addressables 로딩 실패가 기본으로 예외를 던지고 `TryLoad` 변형을 쓰라는 주장이다. | 확인하기 전에는 쓰지 않는다. 일괄 설치 · 교체 지시는 프로젝트마다 따로 정한다. |
| `engines/unity/plugins/cinemachine`(원문 98~146줄 · 217~223줄) | 3.x 표제 아래에 예전 Body/Aim 구성 · Free Look 세 리그 설명이 섞인 것으로 의심된다. | 3.x API와 편집기 메뉴를 설치된 패키지에서 다시 확인한다. |
| `engines/unity/deprecated-apis`(원문 64~65줄 · 73줄) | `RaycastAll`→`RaycastNonAlloc`, `velocity` 직접 쓰기→`AddForce`, `Resources.Load`→Addressables를 폐기 대체처럼 적었다. | 같은 동작의 치환이 아니다. `velocity` 설정을 `AddForce` 로 바꾸면 동작의 뜻이 달라진다. 성능 권장 · 선택적 이전으로 나눠 본다. |
| `engines/unity/modules/networking`(원문 68~72줄 · 134~138줄 · 284~286줄) | `RequireOwnership = false` 인 생성 RPC가 아무 `clientId` 나 받는다. 피해 RPC는 `damage` 를 검사 없이 뺀다. 이동 RPC는 입력을 검사 없이 더한다(검증은 주석뿐). | 개념 예제다. 완성 보안 코드로 쓰지 않는다. 소유권 · 행동 가능 상태 · 대상 · 빈도 · 서버가 계산할 값을 검사하게 만들고 거부 시험을 붙인다. |
| `engines/unity/modules/networking`(원문 98줄 · 171줄 · 275줄) | 예제가 옛 `Input.GetKey` · `GetAxis` 를 쓴다. 같은 참조의 「옛 Input을 쓰지 말라」와 부딪힌다. | 프로젝트의 입력 방식에 맞춰 고친 뒤 쓴다. |
| `engines/unity/plugins/addressables`(원문 174~191줄 · 320~329줄) | CDN 올리기 · 원격 내려받기 · 원격 카탈로그 갱신 예제다(바깥 호출). | 승인된 별도 일로만 한다. 기본으로 실행하지 않는다. |
| `engines/unity/plugins/addressables`(원문 118~128줄 · 287~296줄) | 생성 직후 바로 `ReleaseInstance` 한다. 같은 키를 동시에 부르면 중복 로딩이 겹친다. 실패했을 때의 정리가 없다. | 해제 시점 · 동시 요청 · 실패 정리를 보완하고 시험한다. |
| `engines/unity/plugins/dots-entities`(원문 155줄 · 237줄) | Baker가 「런타임에」 변환한다는 설명, Burst가 「10-100배 빠르다」는 배속의 근거가 부족하다. | 패키지 판을 고정하고 baking 시점 · 의존성 · 렌더링 연결을 확인한다. 근거 없는 배속은 쓰지 않는다. |
| `engines/unity/current-best-practices`(원문 306~336줄) | 원문 저자가 6000.3.23f1에서 본 기록이다. `Assets/` 밖의 시험은 컴파일되지 않아 `testcasecount="0"` · `Passed` · 종료 0(거짓 성공). 시험 패키지가 없으면 멈춘다. 컴파일 오류면 결과 파일이 없다. | 그 환경의 관찰로만 본다. 종료 코드 0을 성공으로 세지 않는다 — 결과 XML이 이번 실행의 것인지 · 시험 수 · 실패를 함께 본다. |
| `engines/unity/current-best-practices`(원문 54~63줄 등) | `GenerateSerializer` 의 출처, 성능 배수, NativeContainer 핸들 해제가 부족하다. 무조건 최신으로 옮기라는 말과 설치 지시가 있다. | 출처와 해제를 보강한 뒤 쓴다. 최신 전환 · 설치는 프로젝트마다 정한다. |
| `engines/unity/modules/animation`(원문 147줄 · 156줄 · 204줄) | 필요한 `using` · 리깅 제약의 `data` 를 고치는 방식 · `CrossFade` 시간 단위가 맞는지 확인이 필요하다. | 실제로 컴파일 · 실행해 확인한다. |
| `engines/unity/modules/audio`(원문 114줄 등) | 음량 0에서 `Log10` 이 음의 무한대가 된다. 대화가 겹칠 때 음악 낮추기 · 거리 감쇠 주석이 부족하다. | 0 처리 · 겹친 대화 · 감쇠 설명을 보강한다. |
| `engines/unity/modules/input` | 기기가 없을 때 · 재설정 취소 · 액션 끄기 · `Dispose` 처리가 부족하다. | 그 경우를 채우고 시험한다. |
| `engines/unity/modules/navigation`(원문 216~217줄 · 18~34줄 · 185~191줄) | 「Git URL로 추가」 칸에 패키지 이름만 있다. 옛 Bake 창 절차를 다시 확인해야 한다. 링크 위에 있는 동안 `Update` 가 매 프레임 코루틴을 새로 띄운다. | 설치판의 절차로 다시 확인하고, 중복 코루틴을 막는다. |
| `engines/unity/modules/physics`(원문 10줄 · 19~24줄 · 42~47줄) | 「PhysX 5.1」과 「기본 반복 6→8」 주장을 다시 확인해야 한다. 속도 설정과 힘 적용의 차이 · `NonAlloc` 버퍼 한도가 적혀 있지 않다. | 설치판에서 확인하고, 차이와 버퍼 한도를 밝힌다. |
| `engines/unity/modules/rendering`(원문 86~118줄) | URP와 HDRP API가 한데 섞여 있다. 「URP Lit」이라 적은 셰이더는 상수 빨강만 내는 최소 출력 예제다. | 파이프라인별로 나눠 본다. 그 셰이더는 최소 출력 예제로만 쓴다. |
| `engines/unity/modules/ui` | UGUI를 배제하는 단정(위 줄)이 있고, 이벤트 해제 · 패널 설정 · 화면 크기 확인이 빠져 있다. | 해제 · 패널 · 화면 크기 검사를 더한다. 2월 확인일과 실제 패키지 판을 따로 적는다. |
| 모든 글(머리의 `Knowledge Gap` · 「may not be in the LLM's training data」) | 본문 전부가 2026-02-13 확인이다(10월 확인은 `VERSION.md` 연혁뿐). 모델이 2022 LTS까지만 안다는 전제로 쓴 문장이 있다. | 학습일 대신 설치판과 확인한 API로 고른다. 이 틀이 읽은 날(2026-11-16)을 API 확인일로 덮어쓰지 않는다. |

## 들어 있는 글
| 글 id | 한마디 |
|---|---|
| `engines/unity/plugins` | 카메라 · 에셋 관리 · ECS 등 선택 패키지의 용도와 안정 · 미리보기 · 폐기 분류 |
| `engines/unity/breaking-changes` | 2022 LTS→6.3 LTS의 위험도별 API · 동작 변경, 플랫폼 변경, 이전 점검표 |
| `engines/unity/current-best-practices` | 렌더 파이프라인 · C# · DOTS · 입력 · UI · 에셋 · 성능 · 명령줄 시험 실측 |
| `engines/unity/deprecated-apis` | 입력 · UI · DOTS · 렌더링 · 물리 · 에셋 로딩에서 쓰지 말 것과 대체안 표 |
| `engines/unity/modules/animation` | Animator 상태 기계 · 블렌드 트리 · 루트 모션 · 리깅 · Timeline |
| `engines/unity/modules/audio` | AudioSource · 3D 공간음 · Audio Mixer · 로딩 설정 · 음악 교차 재생 |
| `engines/unity/modules/input` | Input System 패키지 · 입력 액션 · 기기 직접 읽기 · 터치 · 키 재설정 |
| `engines/unity/modules/navigation` | NavMesh 굽기 · NavMeshAgent · 영역 비용 · 장애물 · 오프메시 링크 · 런타임 굽기 |
| `engines/unity/modules/networking` | Netcode for GameObjects 생성 · 동기화 변수 · RPC · 소유권 · 서버 권위 예제 |
| `engines/unity/modules/physics` | Rigidbody · 충돌 · 레이캐스트와 질의 · 캐릭터 컨트롤러 · 조인트 · 고정 시간 |
| `engines/unity/modules/rendering` | URP/HDRP · RenderGraph · 셰이더 · 조명 · 후처리 · 배칭 |
| `engines/unity/modules/ui` | UI Toolkit(UXML · USS · C#)과 UGUI · TextMeshPro · 둘의 비교 |
| `engines/unity/plugins/addressables` | 비동기 로딩 · 참조 수와 해제 · 라벨 · 원격 콘텐츠 · 카탈로그 갱신 |
| `engines/unity/plugins/cinemachine` | 가상 카메라 · 따라가기와 조준 · 블렌드 · 흔들림 · 2.x에서 3.0으로 옮기기 |
| `engines/unity/plugins/dots-entities` | 엔티티 · 컴포넌트 · 시스템 · Baker · 질의 · 잡 · Burst · 명령 버퍼 |
