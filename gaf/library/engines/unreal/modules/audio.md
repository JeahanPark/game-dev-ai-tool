---
title: Unreal 오디오 모듈
group: 엔진 Unreal
summary: 소리 재생 · 감쇠와 공간음 · MetaSounds · 가림과 잔향 · 동시 재생 제한 · 원문 기준 5.8
game_engine: unreal
source: docs/engine-reference/unreal/modules/audio.md
---
> 받은 그대로 둔 참조다(영어 · claude-code-game-studios v1.3.0 · MIT — 출처와 라이선스는 서랍의 NOTICE.md). 이 프레임워크에서 엔진을 돌려 확인하지 않았다 — 프로젝트에 설치된 판에서 확인하기 전에는 후보로만 쓴다. 알려진 의심은 `engines/unreal/read-first` 에 있다.

# Unreal Engine 5.8 — Audio Module Reference

**Last verified:** 2026-10-17 (UE 5.8 changes block); 2026-02-13 (rest of file, written against 5.7)
**Knowledge Gap:** UE 5.7 MetaSounds production-ready; UE 5.8 WASAPI default on Windows

> **UE 5.8 changes:** The default Windows audio backend is now WASAPI
> (XAudio2 is an opt-in fallback; Epic: "No changes are required on the
> developers end"). `au.EnableRelativeRenderCostVoiceLimit` is on by default,
> so fewer sounds may play under the same voice count — revisit voice-count
> tuning. Attenuation submix sends that were implicitly disabled by
> `bEnableSubmixSends` may now be audible. Audio Insights is production-ready;
> Audio Subtitles is Beta; MetaSound Node Configuration and Templates are
> Experimental. Custom MetaSound nodes: `FNodeClassMetadata::DefaultInterface`
> now takes `FClassInterface`. Source: UE 5.8 release notes, https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-5-8-release-notes?application_version=5.8

---

## Overview

UE 5.8 audio systems:
- **MetaSounds**: Node-based procedural audio (RECOMMENDED, production-ready)
- **Sound Cues**: Legacy node-based audio (use for simple cases)
- **Audio Component**: Play sounds on actors

---

## Basic Audio Playback

### Play Sound at Location

```cpp
#include "Kismet/GameplayStatics.h"

// ✅ Play 2D sound (no spatialization)
UGameplayStatics::PlaySound2D(GetWorld(), ExplosionSound);

// ✅ Play sound at location (3D spatial audio)
UGameplayStatics::PlaySoundAtLocation(GetWorld(), ExplosionSound, GetActorLocation());

// ✅ With volume and pitch
UGameplayStatics::PlaySoundAtLocation(GetWorld(), ExplosionSound, GetActorLocation(), 0.7f, 1.2f);
```

---

## Audio Component

### Audio Component (Persistent Sound)

```cpp
// Create audio component
UAudioComponent* AudioComp = CreateDefaultSubobject<UAudioComponent>(TEXT("Audio"));
AudioComp->SetupAttachment(RootComponent);
AudioComp->SetSound(LoopingAmbience);

// Play/Stop
AudioComp->Play();
AudioComp->Stop();

// Fade in/out
AudioComp->FadeIn(2.0f); // 2 seconds
AudioComp->FadeOut(1.5f, 0.0f); // 1.5s to volume 0

// Adjust volume/pitch
AudioComp->SetVolumeMultiplier(0.5f);
AudioComp->SetPitchMultiplier(1.2f);
```

---

## 3D Spatial Audio

### Attenuation Settings

```cpp
// Create Sound Attenuation asset:
// Content Browser > Sounds > Sound Attenuation

// Configure:
// - Attenuation Shape: Sphere, Capsule, Box, Cone
// - Falloff Distance: Distance where sound becomes inaudible
// - Attenuation Function: Linear, Logarithmic, Inverse, etc.

// Assign in C++:
AudioComp->AttenuationSettings = AttenuationAsset;
```

### Attenuation Override in Code

```cpp
FSoundAttenuationSettings AttenuationOverride;
AttenuationOverride.AttenuationShape = EAttenuationShape::Sphere;
AttenuationOverride.FalloffDistance = 1000.0f;
AttenuationOverride.AttenuationShapeExtents = FVector(1000.0f);

AudioComp->AttenuationOverrides = AttenuationOverride;
AudioComp->bOverrideAttenuation = true;
```

---

## MetaSounds (Procedural Audio)

### Create MetaSound Source

1. Content Browser > Sounds > MetaSound Source
2. Open MetaSound editor
3. Build node graph:
   - **Inputs**: Triggers, parameters
   - **Generators**: Oscillators, noise, samples
   - **Modulators**: Envelopes, LFOs
   - **Effects**: Filters, reverb, delay
   - **Output**: Audio output

### Play MetaSound

```cpp
// Play MetaSound like any sound
UGameplayStatics::PlaySound2D(GetWorld(), MetaSoundSource);

// Or with Audio Component
AudioComp->SetSound(MetaSoundSource);
AudioComp->Play();
```

### Set MetaSound Parameters

```cpp
// Define parameter in MetaSound (Input node with exposed parameter)
// Set parameter in C++:
AudioComp->SetFloatParameter(FName("Volume"), 0.8f);
AudioComp->SetIntParameter(FName("OctaveShift"), 2);
AudioComp->SetBoolParameter(FName("EnableReverb"), true);
```

---

## Sound Cues (Legacy)

### Create Sound Cue

1. Content Browser > Sounds > Sound Cue
2. Open Sound Cue editor
3. Add nodes: Random, Modulator, Mixer, etc.

### Use Sound Cue

```cpp
// Play like any sound
UGameplayStatics::PlaySound2D(GetWorld(), SoundCue);
```

---

## Sound Classes & Sound Mixes

### Sound Class (Volume Groups)

```cpp
// Create Sound Class: Content Browser > Sounds > Sound Class
// Hierarchy: Master > Music, SFX, Dialogue

// Assign to sound asset:
// Sound Wave > Sound Class = SFX

// Set volume in C++:
UAudioSettings* AudioSettings = GetMutableDefault<UAudioSettings>();
// Configure via Sound Class hierarchy
```

### Sound Mix (Dynamic Mixing)

```cpp
// Create Sound Mix asset
// Define adjustments: Lower music during dialogue, etc.

// Push sound mix
UGameplayStatics::PushSoundMixModifier(GetWorld(), DuckedMusicMix);

// Pop sound mix
UGameplayStatics::PopSoundMixModifier(GetWorld(), DuckedMusicMix);
```

---

## Audio Occlusion & Reverb

### Audio Occlusion (Walls Block Sound)

```cpp
// Enable in Audio Component:
AudioComp->bEnableOcclusion = true;

// Requires geometry with collision
```

### Reverb Volumes

```cpp
// Add Audio Volume to level (Volumes > Audio Volume)
// Configure reverb settings in Details panel
// Audio component automatically picks up reverb when inside volume
```

---

## Common Patterns

### Footstep Sounds (Random Variation)

```cpp
// Use Sound Cue with Random node, or:
UPROPERTY(EditAnywhere, Category = "Audio")
TArray<TObjectPtr<USoundBase>> FootstepSounds;

void PlayFootstep() {
    int32 Index = FMath::RandRange(0, FootstepSounds.Num() - 1);
    UGameplayStatics::PlaySoundAtLocation(GetWorld(), FootstepSounds[Index], GetActorLocation());
}
```

### Music Crossfade

```cpp
UAudioComponent* MusicA;
UAudioComponent* MusicB;

void CrossfadeMusic(float Duration) {
    MusicA->FadeOut(Duration, 0.0f);
    MusicB->FadeIn(Duration);
}
```

### Check if Sound is Playing

```cpp
if (AudioComp->IsPlaying()) {
    // Sound is playing
}
```

---

## Audio Concurrency

### Limit Concurrent Sounds

```cpp
// Create Sound Concurrency asset:
// Content Browser > Sounds > Sound Concurrency

// Configure:
// - Max Count: Maximum instances of this sound
// - Resolution Rule: Stop Oldest, Stop Quietest, etc.

// Assign to sound:
// Sound Wave > Concurrency Settings
```

---

## Performance Tips

### Audio Optimization

```cpp
// Compression settings (Sound Wave asset):
// - Compression Quality: 40 (balance quality/size)
// - Streaming: Enable for large files (music)

// Reduce audio mixing cost:
// - Limit concurrent sounds via Sound Concurrency
// - Use simple attenuation shapes

// Disable audio for distant actors:
if (Distance > MaxAudibleDistance) {
    AudioComp->Stop();
}
```

---

## Debugging

### Audio Debug Commands

```cpp
// Console commands:
// au.Debug.Sounds 1 - Show active sounds
// au.3dVisualize.Enabled 1 - Visualize 3D audio
// stat soundwaves - Show sound statistics
// stat soundmixes - Show active sound mixes
```

---

## Sources
- https://docs.unrealengine.com/5.7/en-US/audio-in-unreal-engine/
- https://docs.unrealengine.com/5.7/en-US/metasounds-in-unreal-engine/
