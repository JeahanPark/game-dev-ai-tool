"""REA(`rea-agents` · MIT npm 프로그램 — 실행 파일 · 빌드를 뜯어보는 MCP 서버)를 전용 역할이 지휘자를 거쳐 쓰게 한다(1.17.0).

깔기 · PC 마다 켠 상태 · 자식의 환경은 `setup` · `state` 가, 자식과 말하기 · 효과 · 경로 · 결과 줄이기는 다른 모듈이 한다.
판과 받는 곳은 여기 한 곳에 못 박는다 — 깔 판(VERSION)과 그 판이 바라는 Node 의 범위(NODE_RANGE), 맞는 Node 가 없을 때 받을 판과
그 SHA-256(nodejs.org 의 SHASUMS256.txt · 2026-11-27 읽음), 그 판이 받는 Ghidra 의 줄(GHIDRA_LINE · GHIDRA_MIN_MINOR)과 Ghidra 가
바라는 JDK(JAVA_MIN_DEFAULT · GHIDRA_JAVA_MIN · 1.17.1).
"""

PACKAGE = "rea-agents"
VERSION = "6.10.1"
NODE_RANGE = "^22.23.4 || ^24.22.1 || >=26.12.1"
NODE_VERSION = "22.23.5"
NODE_SHA256 = {            # https://nodejs.org/dist/v22.23.5/SHASUMS256.txt (read 2026-11-27)
    "win-x64.zip": "4a191082e9706866830379d577d2cc8a50c776fcd9436a8d061b063ed0d9a0e3",
    "linux-x64.tar.xz": "cdeb8670a0c81f51b4dc4ac61919f9b1ad917b304bf8221eb939570663d240f8",
    "darwin-arm64.tar.gz": "e7131995759b61a0170b646010d2ad35a5d37b4cb0d471f3e8317eac6f742f3d",
    "darwin-x64.tar.gz": "c53383238206b9ae4db23c5d8a63645e2e753bec8ab51a49cc81939aa64e9422",
}
NODE_URL = "https://nodejs.org/dist/v{version}/node-v{version}-{name}"
# VERSION 이 받는 Ghidra — GHIDRA_LINE.x 가운데 부판이 GHIDRA_MIN_MINOR 이상(6.10.1 의 dist/mcp/binary-*.js 「GhidraReleasePolicy」:
# SUPPORTED_GHIDRA_VERSION "12.2" · 같은 주판에 부판 >= 2 · 2026-11-27 읽음). 이 PC 의 12.1.4 는 `unsupported_version` 으로 거절됐다(실측).
GHIDRA_LINE = 12
GHIDRA_MIN_MINOR = 2
# Ghidra 가 바라는 JDK — REA 6.10.1 은 고른 Ghidra 의 Ghidra/application.properties 의 `application.java.min` · `application.java.max`
# 로 JDK 를 가린다(dist/ghidra/GhidraInstallationPolicy.js · 「64-bit full JDK」라야 한다 · JRE 는 안 된다 · 2026-11-28 읽음).
# 12.2 는 min=25 · max 는 빈 값(GhidraDocs/GettingStarted.md 원문 「Java 25 64-bit Runtime and Development Kit (JDK)」), 12.1.4 는
# min=21 · max 는 빈 값. 1.17.0 은 이 PC 의 Ghidra 12.1.4 로만 재서 「JDK 21 이상」이라고 적었다 — 틀린 안내였다. 11-28 실측(Windows):
# 12.2 + JDK 21 은 `provider_unavailable`(`java: unsupported_version — 21.0.12.1; … accepted a 64-bit full JDK 25 or newer`),
# 12.2 + JDK 25(Temurin 25.0.5.1)는 됐다. 고른 Ghidra 에서 min 을 못 읽으면 JAVA_MIN_DEFAULT 와 견준다(12.1.4 에 적힌 값).
JAVA_MIN_DEFAULT = 21
GHIDRA_JAVA_MIN = 25       # GHIDRA_LINE.GHIDRA_MIN_MINOR(12.2)가 바라는 JDK — Ghidra 가 없을 때의 안내에 쓴다
# 자식에게 더 주는 값(있으면 좋은 것) — 지휘자의 손(client)은 자식이 initialize 전에 끝나면 REA_* 없이 다시 띄운다
CHILD_ENV = {"REA_MCP_OUTPUT_SCHEMA_PROFILE": "off", "NO_UPDATE_NOTIFIER": "1"}
