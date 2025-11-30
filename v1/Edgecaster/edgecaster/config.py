from dataclasses import dataclass


default_seed = 12345


@dataclass
class GameConfig:
    view_width: int = 1280
    view_height: int = 900
    tile_size: int = 24
    world_width: int = 40
    world_height: int = 24
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
    xp_base: int = 20          # XP needed for level 2
    xp_per_level: int = 10     # incremental growth per level (linear)
