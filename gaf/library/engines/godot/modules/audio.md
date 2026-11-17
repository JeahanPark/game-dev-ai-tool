---
title: Godot 오디오 모듈
group: 엔진 Godot
summary: 4.7의 area_mask 기본값 변경 · 재생 · 3D 공간음 · 오디오 버스 · 효과음 재생기 풀 · 원문 기준 4.7
game_engine: godot
source: docs/engine-reference/godot/modules/audio.md
---
> 받은 그대로 둔 참조다(영어 · claude-code-game-studios v1.3.0 · MIT — 출처와 라이선스는 서랍의 NOTICE.md). 이 프레임워크에서 엔진을 돌려 확인하지 않았다 — 프로젝트에 설치된 판에서 확인하기 전에는 후보로만 쓴다. 알려진 의심은 `engines/godot/read-first` 에 있다.

# Godot Audio — Quick Reference

Last verified: 2026-10-17 (4.7 changes); 2026-02-12 (rest) | Engine: Godot 4.7

## What Changed Since ~4.3 (LLM Cutoff)

No major breaking changes to the audio API in 4.4–4.6; 4.7 removes one
property and changes one default:

### 4.7 Changes
- **`AudioEffectSpectrumAnalyzer.tap_back_pos` removed**
- **`area_mask` on `AudioStreamPlayer2D`/`3D` defaults to `0`** (was layer `1`):
  `audio_bus_override` on an `Area2D`/`Area3D` stops working with the default
  mask until it is set back to layer `1` (4.7 migration guide: https://docs.godotengine.org/en/stable/tutorials/migrating/upgrading_to_godot_4.7.html)

### 4.6 Changes
- **No audio-specific breaking changes** in this release

### 4.5 Changes
- **No audio-specific breaking changes** in this release

## Current API Patterns

### Playing Audio
```gdscript
@onready var sfx_player: AudioStreamPlayer = %SFXPlayer
@onready var music_player: AudioStreamPlayer = %MusicPlayer

func play_sfx(stream: AudioStream) -> void:
    sfx_player.stream = stream
    sfx_player.play()

func play_music(stream: AudioStream, fade_time: float = 1.0) -> void:
    var tween: Tween = create_tween()
    tween.tween_property(music_player, "volume_db", -80.0, fade_time)
    await tween.finished
    music_player.stream = stream
    music_player.volume_db = 0.0
    music_player.play()
```

### 3D Spatial Audio
```gdscript
@onready var audio_3d: AudioStreamPlayer3D = %AudioPlayer3D

func _ready() -> void:
    audio_3d.max_distance = 50.0
    audio_3d.attenuation_model = AudioStreamPlayer3D.ATTENUATION_INVERSE_DISTANCE
    audio_3d.unit_size = 10.0
```

### Audio Buses
```gdscript
# Set bus volumes
AudioServer.set_bus_volume_db(AudioServer.get_bus_index(&"Music"), volume_db)
AudioServer.set_bus_volume_db(AudioServer.get_bus_index(&"SFX"), volume_db)

# Mute a bus
AudioServer.set_bus_mute(AudioServer.get_bus_index(&"Music"), true)
```

### Object Pooling for SFX
```gdscript
# Pre-create multiple AudioStreamPlayer nodes for concurrent sounds
var _sfx_pool: Array[AudioStreamPlayer] = []

func _ready() -> void:
    for i in range(8):
        var player := AudioStreamPlayer.new()
        player.bus = &"SFX"
        add_child(player)
        _sfx_pool.append(player)

func play_pooled(stream: AudioStream) -> void:
    for player in _sfx_pool:
        if not player.playing:
            player.stream = stream
            player.play()
            return
```

## Common Mistakes
- Creating new AudioStreamPlayer nodes at runtime instead of pooling
- Not using audio buses for volume categories (Music, SFX, UI, Voice)
- Using `_process()` for audio timing instead of signals (`finished`)
