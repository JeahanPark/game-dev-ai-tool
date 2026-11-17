---
title: 웹 Three.js
group: 엔진 웹
summary: r177~r186의 이름 변경 · 제거 · 폐기 · r186의 실제 동작 · WebGPURenderer · 원문 기준 r186
game_engine: web
source: docs/engine-reference/web/threejs.md
---
> 받은 그대로 둔 참조다(영어 · claude-code-game-studios v1.3.0 · MIT — 출처와 라이선스는 서랍의 NOTICE.md). 이 프레임워크에서 엔진을 돌려 확인하지 않았다 — 프로젝트에 설치된 판에서 확인하기 전에는 후보로만 쓴다. 알려진 의심은 `engines/web/read-first` 에 있다.

# three.js — What Changed

Last verified: 2026-10-29 | Pinned: `three` 0.186.1 (r186), `@types/three` 0.186.0

The model's training ends around r176. These are the changes from r177 to
r186 that a game on `WebGLRenderer` meets. Older code still mostly runs;
these are the lines that warn, break or look different.

**Grades:** **[S]** sourced — read in the named primary source on the date
above; **[J]** judgment.

## Setup

- `three` ships no type declarations; add `@types/three` at the matching
  version for TypeScript. **[S]** (npm)
- Package entry points: `three`, `three/addons/*` (add-ons, e.g.
  `three/addons/controls/OrbitControls.js`), `three/webgpu`, `three/tsl`.
  **[S]** (npm `exports`)
- The one-shot uses the same release, bundled into one plain script with a
  global `THREE` (`VERSION.md`).

## Renamed, removed or deprecated (game-relevant)

| Release | Change | |
|---------|--------|---|
| r177 | `ColorManagement.fromWorkingColorSpace()` → `workingToColorSpace()`; `toWorkingColorSpace()` → `colorSpaceToWorking()` | **[S]** |
| r178 | `MultiplyBlending` and `SubtractiveBlending` need `material.premultipliedAlpha = true` | **[S]** |
| r179 | `Timer` is in core (`THREE.Timer`); no add-on import | **[S]** |
| r179 | `WebGLRenderer({ reverseDepthBuffer })` → `reversedDepthBuffer` | **[S]** |
| r180 | `RGBELoader` → `HDRLoader`; `RGBMLoader` removed | **[S]** |
| r181 | PBR materials look slightly different (indirect specular, energy conservation: rough materials a bit brighter) | **[S]** |
| r182 | `PCFSoftShadowMap` deprecated on `WebGLRenderer`: `PCFShadowMap` is now soft too | **[S]** |
| r183 | `Clock` deprecated: use `Timer` | **[S]** |
| r183 | `Sky`/`SkyMesh` legacy gamma removed (looks different, no parameter restores it) | **[S]** |
| r184 | `FileLoader.load()` and `ImageBitmapLoader.load()` return nothing: use the `onLoad` callback | **[S]** |
| r184 | `FBXLoader` turns +Z-up models to +Y-up itself: remove manual rotations | **[S]** |
| r184 | Background and environment rotation now follow object rotation | **[S]** |
| r185 | With `matrixAutoUpdate = false` and a hand-set `matrix`, also set `matrixWorldNeedsUpdate = true` | **[S]** |
| r185 | `SVGLoader.createShapes()` deprecated (call `toShapes()` on each shape path); `DRACOLoader.setDecoderConfig()`, `LWOLoader`, `VTKLoader` (r184) deprecated | **[S]** |
| r186 | `Object3D.dispose()` exists: a custom class with its own `dispose()` calls `super.dispose()` | **[S]** |
| r186 | `Source` → `TextureSource` | **[S]** |
| r186 | `Sky`'s `up` uniform removed (always +Y up) | **[S]** |

## What r186 actually does

- `renderer.shadowMap.type = PCFSoftShadowMap` on `WebGLRenderer` logs a
  warning ("has been removed") and uses `PCFShadowMap`. Write `PCFShadowMap`
  or `VSMShadowMap`. **[S]** (2)
- `new THREE.Clock()` logs a deprecation warning. `Timer` is driven by hand:
  call `timer.update(timestamp)` once a frame (milliseconds, e.g. the
  animation-loop time), then `getDelta()` / `getElapsed()`, which return
  **seconds**. `timer.connect(document)` makes it skip the time the tab was
  hidden. **[S]** (3)

```ts
const timer = new THREE.Timer();
timer.connect(document);
renderer.setAnimationLoop((t) => {
  timer.update(t);
  step(Math.min(timer.getDelta(), 0.1)); // clamp: see current-best-practices.md
  renderer.render(scene, camera);
});
```

## WebGPURenderer

Only for a game that chose it (`import * as THREE from 'three/webgpu'`); keep
`WebGLRenderer` as the default, since WebGPU is not on every browser
(`current-best-practices.md`). **[J]** Changes that bite there: **[S]** (1)

- r181: the `*Async()` methods (`renderAsync`, `computeAsync`, …) are
  deprecated; the renderer initialises inside `setAnimationLoop()`, or call
  `await renderer.init()` first.
- r182: `colorBufferType` → `outputBufferType`.
- r183: `PostProcessing` → `RenderPipeline`; `WebGLCubeRenderTarget` → `CubeRenderTarget`.
- r185: premultiplied alpha changed; use an opaque background or clear colour
  unless the canvas must blend with the page.
- r186: `PCFSoftShadowMap` removed; `LightProbeGrid` → `LightProbeGridWebGL`.

## Sources

1. https://github.com/mrdoob/three.js/wiki/Migration-Guide (sections 176 → 177 to 185 → 186)
2. https://github.com/mrdoob/three.js/blob/r186/src/renderers/webgl/WebGLShadowMap.js
3. https://github.com/mrdoob/three.js/blob/r186/src/core/Timer.js ; `src/core/Clock.js`

npm: `registry.npmjs.org/three`, `registry.npmjs.org/@types/three`.
