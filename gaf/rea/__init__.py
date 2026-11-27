"""REA(`rea-agents` · MIT npm 프로그램 — 실행 파일 · 빌드를 뜯어보는 MCP 서버)를 전용 역할이 지휘자를 거쳐 쓰게 한다(1.17.0).

깔기 · PC 마다 켠 상태 · 자식의 환경은 `setup` · `state` 가, 자식과 말하기 · 효과 · 경로 · 결과 줄이기는 다른 모듈이 한다.
판과 받는 곳은 여기 한 곳에 못 박는다 — 깔 판(VERSION)과 그 판이 바라는 Node 의 범위(NODE_RANGE), 맞는 Node 가 없을 때 받을 판과
그 SHA-256(nodejs.org 의 SHASUMS256.txt · 2026-11-27 읽음), 그 판이 받는 Ghidra 의 줄(GHIDRA_LINE · GHIDRA_MIN_MINOR).
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
# 자식에게 더 주는 값(있으면 좋은 것) — 지휘자의 손(client)은 자식이 initialize 전에 끝나면 REA_* 없이 다시 띄운다
CHILD_ENV = {"REA_MCP_OUTPUT_SCHEMA_PROFILE": "off", "NO_UPDATE_NOTIFIER": "1"}
