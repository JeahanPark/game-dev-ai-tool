---
title: Godot 물리 모듈
group: 엔진 Godot
summary: Jolt 기본 엔진과 4.7 변경 · Jolt와 GodotPhysics 비교 · 기본 설정 · 레이캐스트 · 원문 기준 4.7
game_engine: godot
source: docs/engine-reference/godot/modules/physics.md
---
> 받은 그대로 둔 참조다(영어 · claude-code-game-studios v1.3.0 · MIT — 출처와 라이선스는 서랍의 NOTICE.md). 이 프레임워크에서 엔진을 돌려 확인하지 않았다 — 프로젝트에 설치된 판에서 확인하기 전에는 후보로만 쓴다. 알려진 의심은 `engines/godot/read-first` 에 있다.

# Godot Physics — Quick Reference

Last verified: 2026-10-17 (4.7 changes); 2026-02-12 (rest) | Engine: Godot 4.7

## What Changed Since ~4.3 (LLM Cutoff)

### 4.7 Changes
- **Jolt `WorldBoundaryShape3D`**: `plane.d` sign now matches Godot Physics,
  flipped from 4.6; negate it to keep 4.6 behavior
- **Jolt `SoftBody3D`**: mass defaults to 1 kg for the whole body (was 1 kg per
  point) and `linear_stiffness` is applied differently; re-tune
  `linear_stiffness` and `damping_coefficient`
- **Jolt `Area3D`** reports overlaps with `SoftBody3D`; filter with layers/masks
- **`PhysicsServer2D.body_set_shape_as_one_way_collision()`**: optional
  `direction` parameter (required in `PhysicsServer2DExtension`) (4.7 migration guide: https://docs.godotengine.org/en/stable/tutorials/migrating/upgrading_to_godot_4.7.html)

### 4.6 Changes
- **Jolt Physics is the DEFAULT 3D engine** for new projects
  - Existing projects keep their current physics engine setting
  - Better determinism, stability, and performance than GodotPhysics3D
  - Some HingeJoint3D properties (`damp`) only work with GodotPhysics3D
  - 2D physics UNCHANGED (still Godot Physics 2D)

### 4.5 Changes
- **3D physics interpolation rearchitected**: Moved from RenderingServer to SceneTree
  - User-facing API unchanged, but internal behavior may differ in edge cases

## Physics Engine Selection (4.6)

```
Project Settings → Physics → 3D → Physics Engine:
- Jolt Physics (DEFAULT for new projects)
- GodotPhysics3D (legacy, still available)
```

### Jolt vs GodotPhysics3D

| Feature | Jolt (default) | GodotPhysics3D |
|---------|---------------|----------------|
| Determinism | Better | Inconsistent |
| Stability | Better | Adequate |
| Performance | Better for complex scenes | Adequate |
| HingeJoint3D `damp` | NOT supported | Supported |
| Runtime warnings | Yes, for unsupported properties | No |
| Collision margins | May behave differently | Original behavior |

## Current API Patterns

### Basic Physics Setup (unchanged)
```gdscript
# CharacterBody3D movement — API unchanged across engines
extends CharacterBody3D

@export var speed: float = 5.0
@export var jump_velocity: float = 4.5

func _physics_process(delta: float) -> void:
    if not is_on_floor():
        velocity += get_gravity() * delta

    if Input.is_action_just_pressed("jump") and is_on_floor():
        velocity.y = jump_velocity

    var input_dir: Vector2 = Input.get_vector("left", "right", "forward", "back")
    var direction: Vector3 = (transform.basis * Vector3(input_dir.x, 0, input_dir.y)).normalized()
    velocity.x = direction.x * speed
    velocity.z = direction.z * speed

    move_and_slide()
```

### Raycasting (unchanged)
```gdscript
var space_state: PhysicsDirectSpaceState3D = get_world_3d().direct_space_state
var query := PhysicsRayQueryParameters3D.create(from, to)
query.collision_mask = collision_mask
var result: Dictionary = space_state.intersect_ray(query)
if result:
    var hit_point: Vector3 = result.position
    var hit_normal: Vector3 = result.normal
```

## Common Mistakes
- Assuming GodotPhysics3D is the default (Jolt since 4.6)
- Using HingeJoint3D `damp` property without checking physics engine (Jolt ignores it)
- Not testing collision edge cases when switching between physics engines
