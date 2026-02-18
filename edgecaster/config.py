from dataclasses import dataclass


default_seed = 12345


@dataclass
class GameConfig:
    # Render surface resolution (letterboxed to display). Increased ~50% for more viewport area.
    view_width: int = 1920
    view_height: int = 1350
    # Slightly larger tiles to better fill the viewport.
    tile_size: int = 26
    # Zone dimensions in tiles (per-screen dungeon slice).
    world_width: int = 60
    world_height: int = 40
    world_map_screens: int = 100
    seed: int = default_seed
    max_vertices: int = 50000
    action_time_instant: int = 0
    action_time_fast: int = 10  # ticks; movement/fractal ops/activation baseline
    action_time_slow: int = 20  # ticks; slow actions resolve next turn, but place uses a special 5
    place_time_ticks: int = 5   # ticks for placing a terminus (resolves next turn start)
    pattern_damage_radius: float = 1.25
    pattern_damage_per_vertex: int = 1
    pattern_damage_cap: int = 5
    pattern_overlay_ttl: int = 15  # ticks
    place_range: float = 8.0       # Euclidean tiles
    activate_neighbor_depth: int = 2  # depth for Activate N (seed + N-hop neighbors)
    # progression
    xp_per_imp: int = 10
    xp_base: int = 10          # XP needed for level 2
    xp_per_level: int = 10     # incremental growth per level (linear)

    # Ambient hostile population maintenance (active-zone top-up).
    # These knobs are intentionally conservative to avoid sudden spawn bursts.
    # Turning this off for now at least, causing huge lag and implementing more yogic spawn controller entities.
    ambient_spawn_enabled: bool = False
    ambient_spawn_interval_ticks: int = 120
    ambient_spawn_target_base: int = 2
    ambient_spawn_target_per_tier: float = 0.5
    ambient_spawn_target_max: int = 8
    ambient_spawn_max_per_cycle: int = 1
    ambient_spawn_player_safe_radius: int = 8
    
    allow_zone_prewarm_during_tick = False
    allow_zone_prewarm_during_move = False


    # Lightweight performance profiler (logs timing windows to debug.log).
    # Set to False to disable all profiler instrumentation overhead.
    perf_profiler_enabled: bool = True
    perf_profiler_flush_seconds: float = 2.0
    perf_profiler_top_n: int = 8
    perf_profiler_min_avg_ms: float = 0.05

    # Attention-driven resolver controls (camera LoD + distance from camera center).
    # Defaults target:
    # - nearby screens can resolve deep (down to room-level for deep hierarchies),
    # - distant entities stay coarse.
    resolve_disable_above_lod: float = 2.0
    resolve_depth_cap_default: int = 6
    resolve_depth_ladder: tuple[tuple[float, int], ...] = (
        (2.00, 1),
        (1.20, 2),
        (0.80, 3),
        (0.50, 4),
        (0.20, 5),
        (0.00, 6),
    )
    resolve_distance_depth_ladder: tuple[tuple[float, int], ...] = (
        (1.25, 6),
        (2.50, 5),
        (4.00, 4),
        (7.00, 3),
        (12.0, 2),
        (9999.0, 1),
    )
