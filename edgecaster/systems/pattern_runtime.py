"""Pattern/chakra runtime extracted from ``game.py``.

This module owns runtime logic for:
- fractal operator application
- Activate R / Activate N pattern damage
- polygon/star/chakra generator actions
- chakra resonance/charge helpers used by pattern actions
"""

from __future__ import annotations

import math
from typing import Any, Optional, Tuple

from edgecaster.patterns import builder
from edgecaster.patterns.activation import project_vertices
from edgecaster.systems import chakra_items as chakra_items_system
from edgecaster.systems import damage_policy as damage_policy_system


def _normalize_chakra_node_id(node_id: Any) -> str:
    """Normalize chakra node ids to the dotted lowercase form used at runtime."""
    node_text = str(node_id or "").strip().lower()
    if not node_text:
        return ""
    return node_text.replace(":", ".").replace("/", ".")


def _average_reduced_charge(actor: Any, active_node_ids: set[str] | None = None) -> Optional[float]:
    """Return average charge from the reducer snapshot when a usable one exists."""
    effective_channels = getattr(actor, "_chakra_effective_channels", None)
    if not isinstance(effective_channels, dict) or not effective_channels:
        return None

    normalized_channels: dict[str, dict[str, float]] = {}
    for node_id, channel_values in effective_channels.items():
        normalized_node_id = _normalize_chakra_node_id(node_id)
        if not normalized_node_id or not isinstance(channel_values, dict):
            continue
        normalized_channels[normalized_node_id] = channel_values

    if not normalized_channels:
        return None

    if active_node_ids:
        targets = {
            _normalize_chakra_node_id(node_id)
            for node_id in active_node_ids
            if _normalize_chakra_node_id(node_id)
        }
        if not targets:
            return None
        if not any(node_id in normalized_channels for node_id in targets):
            return None
        total_charge = 0.0
        for node_id in targets:
            channel_values = normalized_channels.get(node_id) or {}
            try:
                total_charge += float(channel_values.get("charge", 0.0) or 0.0)
            except Exception:
                continue
        return total_charge / float(len(targets))

    charge_values: list[float] = []
    for channel_values in normalized_channels.values():
        try:
            charge_values.append(float(channel_values.get("charge", 0.0) or 0.0))
        except Exception:
            continue
    if not charge_values:
        return None
    return sum(charge_values) / float(len(charge_values))


def act_polygon(self, actor_id: str) -> None:
    """Place a regular polygon pattern centered on the player.

    Clears any existing pattern and creates a new polygon with the
    configured number of sides and radius. The root/terminus vertex
    is directly north of center, and vertices proceed clockwise.
    """
    level = self._level()
    player = self._player()

    # Get parameters from the param system
    num_sides = self._param_value("polygon", "sides")
    radius = self._param_value("polygon", "radius")

    # Default fallbacks if params not found
    if num_sides is None:
        num_sides = 6
    if radius is None:
        radius = 4

    # Create the polygon pattern and anchor it on the player (CANONICAL ABS).
    pat = builder.regular_polygon_pattern(num_sides, radius)

    # Compute canonical ABS anchor at the player's current position.
    anchor_abs = getattr(player, "abs_pos", None)
    if anchor_abs is None:
        anchor_abs = self.abs_from_zone_local(self.zone_coord, player.pos)

    st = self._pattern_state(depth=self.zone_coord[2])
    st["pattern"] = pat
    st["anchor_abs"] = (int(anchor_abs[0]), int(anchor_abs[1]))
    st["activation_points"] = []
    st["activation_ttl"] = 0
    st["pattern_motion"] = None
    st["choking_vines_state"] = None
    st["rune_choking_vines_state"] = None

    # Sync the current zone view to canonical state immediately.
    self._sync_level_pattern_view(level)

    # Clear per-level auxiliaries tied to the current pattern.
    level.pattern_motion = None
    level.acidic_pattern = False
    level.fern_active = False
    level.fern_growth_tips = []
    level.fern_accum = 0.0
    level.choking_vines_state = None
    level.rune_choking_vines_state = None

    self._commit_pattern_state_from_level(level)

    self.log.add(f"Polygon ({num_sides} sides, radius {radius}) placed.")


def act_star(self, actor_id: str) -> None:
    """Place a star pattern centered on the player.

    Clears any existing pattern and creates a new star with the
    configured number of points, outer radius, and inner radius.
    The first point (root/terminus) is directly north.
    """
    level = self._level()
    player = self._player()

    # Get parameters from the param system
    num_points = self._param_value("star", "points")
    outer_radius = self._param_value("star", "outer_radius")
    inner_radius = self._param_value("star", "inner_radius")

    # Default fallbacks if params not found
    if num_points is None:
        num_points = 5
    if outer_radius is None:
        outer_radius = 5
    if inner_radius is None:
        inner_radius = 2

    # Create the star pattern and anchor it on the player (CANONICAL ABS).
    pat = builder.star_pattern(num_points, outer_radius, inner_radius)

    anchor_abs = getattr(player, "abs_pos", None)
    if anchor_abs is None:
        anchor_abs = self.abs_from_zone_local(self.zone_coord, player.pos)

    st = self._pattern_state(depth=self.zone_coord[2])
    st["pattern"] = pat
    st["anchor_abs"] = (int(anchor_abs[0]), int(anchor_abs[1]))
    st["activation_points"] = []
    st["activation_ttl"] = 0
    st["pattern_motion"] = None
    st["choking_vines_state"] = None
    st["rune_choking_vines_state"] = None

    self._sync_level_pattern_view(level)

    level.pattern_motion = None
    level.acidic_pattern = False
    level.fern_active = False
    level.fern_growth_tips = []
    level.fern_accum = 0.0
    level.choking_vines_state = None
    level.rune_choking_vines_state = None

    self._commit_pattern_state_from_level(level)

    self.log.add(f"Star ({num_points} points, outer {outer_radius}, inner {inner_radius}) placed.")


def chakra_modifiers(self, actor_id: str):
    """Return ChakraModifiers for the given actor (resonance + charge)."""
    try:
        actor = self._level().actors.get(actor_id)
    except Exception:
        actor = None
    if actor is None:
        return None

    try:
        from edgecaster.systems import chakras as chakra_system
    except Exception:
        return None

    # Prefer effective_active_nodes so item-granted temporary unlocks/auto-activations
    # feed both resonance-bonus detection and charge averaging, keeping the two reads
    # consistent with each other.
    active_node_ids = chakra_items_system.effective_active_nodes(self, actor)

    bonuses = chakra_system.check_resonance_bonuses_from_active_nodes(active_node_ids)
    mods = chakra_system.get_resonance_modifiers(bonuses)
    avg_charge = _average_reduced_charge(actor, active_node_ids)
    if avg_charge is None:
        # Reducer snapshot absent — read directly from ChakraComponent.
        avg_charge = chakra_items_system.get_actor_average_charge(self, actor)
    mods = chakra_system.apply_charge_to_modifiers(mods, avg_charge)
    return mods


def consume_chakra_charge(self, actor_id: str, amount: float) -> None:
    """Consume chakra charge from the actor's active chakras."""
    if amount <= 0:
        return
    try:
        actor = self._level().actors.get(actor_id)
    except Exception:
        actor = None
    if actor is None:
        return
    chakra_items_system.consume_actor_chakra_charge(actor, amount, game=self)


def act_chakra(self, actor_id: str) -> None:
    """Apply the actor's active chakra graph as a custom generator shape."""
    level = self._level()
    actor = level.actors.get(actor_id)
    if actor is None:
        return

    if not level.pattern.vertices:
        self.log.add("No pattern to modify. Place a terminus first.")
        return

    try:
        from edgecaster.systems.chakras import build_chakra_generator_seed_for_actor
    except Exception:
        self.log.add("Could not import chakra modules.")
        return

    try:
        seed = build_chakra_generator_seed_for_actor(
            actor,
            base_scale=1.0,
            game=self,
            require_root=True,
        )
    except ValueError as e:
        self.log.add(str(e))
        return
    except Exception as e:
        self.log.add(f"Chakra pattern generation failed: {e}")
        return

    # Debug trace (kept concise; useful for future chakra->vertex targeting).
    try:
        self._debug(
            f"[chakra_gen] root={seed.root_id} terminus={seed.terminus_id} base_len={seed.base_len:.4f}"
        )
        self._debug(f"[chakra_gen] nodes={seed.node_order}")
        self._debug(
            f"[chakra_gen] verts={[(round(x, 4), round(y, 4)) for (x, y) in seed.verts]}"
        )
        self._debug(f"[chakra_gen] edges={seed.edges}")
    except Exception:
        pass

    # Get amplitude from param system (like custom generator)
    amp = self._param_value("chakra", "amplitude")
    if amp is None:
        amp = 1.0
    # Apply chakra resonance/charge amp multiplier if available.
    mods = self._chakra_modifiers(actor_id)
    if mods is not None:
        amp *= mods.chakra_amp_mult
    # Finger finesse and similar passives can nudge chakra generator strength,
    # but one Chakra action press should always apply exactly one generator pass.
    try:
        amp += float(self.chakra_effect_value("chakra_generator_amp_bonus", actor_id=actor_id))
    except Exception:
        pass
    amp = max(0.01, float(amp))

    # Create a CustomGraphGenerator with the chakra shape
    gen = builder.CustomGraphGenerator(
        seed.verts,
        seed.edges,
        amplitude=amp,
        vertex_labels=seed.node_order,
    )

    # Apply to current pattern (same flow as _apply_fractal_op)
    segs = level.pattern.to_segments()
    level.pattern_motion = None  # Cancel any ongoing motion

    # Exactly one generator application per action press.
    segs = gen.apply_segments(segs, max_segments=self.cfg.max_vertices)
    segs = builder.cleanup_duplicates(segs)
    if len(segs) >= self.cfg.max_vertices:
        segs = segs[: self.cfg.max_vertices]

    if len(segs) > self.cfg.max_vertices:
        segs = segs[: self.cfg.max_vertices]
        self.log.add("Pattern capped at max vertices.")

    level.pattern = builder.Pattern.from_segments(segs)
    level.choking_vines_state = None
    level.rune_choking_vines_state = None
    # Preserve chakra seed metadata for future ability targeting.
    try:
        import json

        level.pattern.meta["chakra_seed_nodes"] = json.dumps(seed.node_order)
        level.pattern.meta["chakra_seed_verts"] = json.dumps(seed.verts)
        level.pattern.meta["chakra_seed_edges"] = json.dumps(seed.edges)
        level.pattern.meta["chakra_seed_root"] = str(seed.root_id)
        level.pattern.meta["chakra_seed_terminus"] = str(seed.terminus_id)
    except Exception:
        pass
    self.log.add(f"Chakra generator applied ({len(seed.verts)} chakra vertices).")

    # Spend a bit of chakra charge when applying the generator.
    if mods is not None:
        try:
            from edgecaster.systems import chakras as chakra_system

            self._consume_chakra_charge(
                actor_id,
                chakra_system.CHARGE_CONSUME_GENERATOR * mods.charge_consume_mult,
            )
        except Exception:
            pass


def apply_fractal_op(self, lvl: Any, kind: str) -> None:
    """Apply a fractal operator to the current pattern."""
    if not lvl.pattern.vertices:
        self.log.add("No pattern to modify. Place a terminus first.")
        return
    segs = lvl.pattern.to_segments()
    # Editing the pattern should cancel any ongoing motion.
    lvl.pattern_motion = None
    if kind == "subdivide":
        parts = self._param_value("subdivide", "parts")
        gen = builder.SubdivideGenerator(parts=parts)
    elif kind == "koch":
        height = self._param_value("koch", "height")
        flip = self._param_value("koch", "flip")
        gen = builder.KochGenerator(height_factor=height, flip=flip)
    elif kind == "branch":
        angle = self._param_value("branch", "angle")
        count = self._param_value("branch", "count")
        gen = builder.BranchGenerator(angle_deg=angle, length_factor=0.45, branch_count=count)
    elif kind == "extend":
        gen = builder.ExtendGenerator()
    elif kind == "zigzag":
        parts = self._param_value("zigzag", "parts")
        amp = self._param_value("zigzag", "amp")
        gen = builder.ZigzagGenerator(parts=parts, amplitude_factor=amp)
    elif kind.startswith("custom"):
        idx = 0
        if kind != "custom":
            try:
                idx = int(kind.split("_", 1)[1])
            except Exception:
                idx = 0
        if not self.custom_patterns or idx >= len(self.custom_patterns):
            self.log.add("No custom pattern saved.")
            return
        pattern = self.custom_patterns[idx]
        verts = None
        edges = []
        if isinstance(pattern, dict):
            verts = pattern.get("vertices")
            edges = pattern.get("edges", [])
        else:
            verts = pattern
        if not verts or len(verts) < 2:
            self.log.add("No custom pattern saved.")
            return
        amp = self._param_value("custom", "amplitude")
        if edges:
            gen = builder.CustomGraphGenerator(verts, edges, amplitude=amp)
        else:
            gen = builder.CustomPolyGenerator(verts, amplitude=amp)
    elif kind == "cultivate":
        pattern = getattr(self, "gardener_branch_pattern", None)
        if not pattern or not isinstance(pattern, dict):
            self.log.add("No branch pattern designed. Open the editor (+/=) first.")
            return
        verts = pattern.get("vertices")
        edges = pattern.get("edges", [])
        if not verts or len(verts) < 2:
            self.log.add("No branch pattern designed.")
            return
        amp = self._param_value("custom", "amplitude")
        if edges:
            gen = builder.CustomGraphGenerator(verts, edges, amplitude=amp)
        else:
            gen = builder.CustomPolyGenerator(verts, amplitude=amp)
    else:
        self.log.add("Unknown fractal op.")
        return

    segs = gen.apply_segments(segs, max_segments=self.cfg.max_vertices)
    segs = builder.cleanup_duplicates(segs)
    if len(segs) > self.cfg.max_vertices:
        segs = segs[: self.cfg.max_vertices]
        self.log.add("Pattern capped at max vertices.")
    lvl.pattern = builder.Pattern.from_segments(segs)
    lvl.choking_vines_state = None
    lvl.rune_choking_vines_state = None
    self._commit_pattern_state_from_level(lvl)


def activation_origin(self, level: Any) -> Optional[Tuple[int, int]]:
    return level.pattern_anchor


def _chakra_nodes_for_vertex(v: Any) -> set[str]:
    """Return chakra provenance node ids attached to a pattern vertex."""
    tags = getattr(v, "tags", {}) or {}
    nodes: set[str] = set()
    single = str(tags.get("chakra_node", "")).strip()
    if single:
        nodes.add(single)
    many = str(tags.get("chakra_nodes", "")).strip()
    if many:
        for part in many.split("|"):
            p = str(part).strip()
            if p:
                nodes.add(p)
    return nodes


def activate_pattern_all(self, level: Any, target_vertex: Optional[int]) -> None:
    """Activate vertices within radius of selected target vertex (Activate R)."""
    if not level.pattern.vertices:
        self.log.add("No pattern defined.")
        return
    origin = self._activation_origin(level)
    if origin is None:
        self.log.add("Pattern has no anchor.")
        return
    world_vertices = project_vertices(level.pattern, origin)
    # coherence check: overall pattern size vs INT
    coh_limit = self._coherence_limit()
    if len(world_vertices) > coh_limit and self._fizzle_roll(len(world_vertices) - coh_limit, coh_limit):
        self.log.add("This pattern strains your mind.")
        return
    if target_vertex is None or target_vertex < 0 or target_vertex >= len(world_vertices):
        self.log.add("Select a vertex to target the circle.")
        return
    center = world_vertices[target_vertex]
    dmg_radius = self.get_param_value("activate_all", "radius")
    per_vertex = self.get_param_value("activate_all", "damage")

    # Apply chakra resonance/charge modifiers (player only for now).
    mods = self._chakra_modifiers(self.player_id)
    if mods is not None:
        dmg_radius = max(0.1, float(dmg_radius) + float(mods.radius_bonus))
        per_vertex = int(math.ceil(float(per_vertex) * float(mods.damage_mult)))

    # Chakra passive bonuses (hand precision, palm reach).
    try:
        per_vertex += int(self.chakra_effect_value("activation_damage_bonus", actor_id=self.player_id))
        dmg_radius += float(self.chakra_effect_value("activation_range_bonus", actor_id=self.player_id))
    except Exception:
        pass

    # pick vertices in radius
    active_vertices: list[tuple[float, float]] = []
    active_indices: list[int] = []
    r2 = dmg_radius * dmg_radius
    for idx, v in enumerate(world_vertices):
        dx = v[0] - center[0]
        dy = v[1] - center[1]
        if dx * dx + dy * dy <= r2:
            active_vertices.append(v)
            active_indices.append(idx)
    str_limit = self._strength_limit()
    if len(active_vertices) > str_limit and self._fizzle_roll(len(active_vertices) - str_limit, str_limit):
        self.log.add("You strain to channel that many vertices at once and lose focus.")
        return

    mana_cost = len(active_vertices)
    player = self._player()
    if mana_cost == 0:
        self.log.add("No vertices in range of the target.")
        return
    if mods is not None:
        mana_cost = int(math.ceil(float(mana_cost) * float(mods.mana_cost_mult)))
    if player.stats.mana < mana_cost:
        self.log.add(f"Not enough mana ({player.stats.mana}/{mana_cost}).")
        return
    player.stats.mana -= mana_cost
    player.stats.clamp()

    level.activation_points = active_vertices
    level.activation_ttl = self.cfg.pattern_overlay_ttl

    # Union of chakra nodes for all illuminated vertices; generic activators
    # can use this to receive equipped item bonuses.
    illuminated_nodes: set[str] = set()
    for idx in active_indices:
        try:
            illuminated_nodes.update(_chakra_nodes_for_vertex(level.pattern.vertices[idx]))
        except Exception:
            continue

    total_vertices = len(active_vertices)
    # Soft-cap rune scaling so very large patterns don't explode damage.
    # First 8 vertices are full strength, extras contribute at 25%.
    effective_vertices = min(total_vertices, 8) + max(0, total_vertices - 8) * 0.25
    hits = 0
    # Centralized policy: Activate R damages hostiles only, never self.
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=False,
        include_friendly=False,
        include_environment=False,
    )
    for _tid, actor in damage_policy_system.iter_damage_targets(
        self,
        level,
        self.player_id,
        policy,
        include_actors=True,
        include_entities=False,
    ):
        if not getattr(actor, "alive", True):
            continue
        tile = level.world.get_tile(*actor.pos)
        if tile is None or not tile.visible:
            continue
        # Tile square center distance to circle, approximate coverage factor.
        ax = actor.pos[0] + 0.5
        ay = actor.pos[1] + 0.5
        dx = ax - center[0]
        dy = ay - center[1]
        dist = (dx * dx + dy * dy) ** 0.5
        half_diag = 0.7071
        if dist <= dmg_radius - half_diag:
            coverage = 1.0
        elif dist >= dmg_radius + half_diag:
            coverage = 0.0
        else:
            span = (dmg_radius + half_diag) - (dmg_radius - half_diag)
            coverage = max(0.0, min(1.0, 1 - (dist - (dmg_radius - half_diag)) / span))
        if coverage <= 0:
            continue
        base_dmg = int(per_vertex * effective_vertices * coverage)
        dmg = chakra_items_system.apply_damage_modifiers(
            self,
            self.player_id,
            "activate_all",
            base_dmg,
            illuminated_nodes=illuminated_nodes,
        )
        if dmg <= 0:
            continue
        hits += 1
        actor.stats.hp -= dmg
        self.log.add(f"Your rune sears {actor.name} for {dmg}.")
        if actor.stats.hp <= 0:
            self.log.add(f"{actor.name} is annihilated.")
            self._kill_actor(
                level,
                actor,
                killer_id=self.player_id,
                killer_is_player=True,
            )

    if hits == 0:
        self.log.add("Your rune fizzles; no foes in its reach.")

    # Spend chakra charge on activation.
    if mods is not None:
        try:
            from edgecaster.systems import chakras as chakra_system

            self._consume_chakra_charge(
                self.player_id,
                chakra_system.CHARGE_CONSUME_ACTIVATE * mods.charge_consume_mult,
            )
        except Exception:
            pass


def activate_pattern_seed_neighbors(self, level: Any, target_vertex: Optional[int]) -> None:
    """Activate selected seed vertex and graph neighbors (Activate N)."""
    if not level.pattern.vertices:
        self.log.add("No pattern defined.")
        return
    origin = self._activation_origin(level)
    if origin is None:
        self.log.add("Pattern has no anchor.")
        return
    world_vertices = project_vertices(level.pattern, origin)
    if not world_vertices:
        self.log.add("No vertices to activate.")
        return
    if target_vertex is None or target_vertex < 0 or target_vertex >= len(world_vertices):
        self.log.add("Select a vertex to target.")
        return

    seed_idx = target_vertex
    # coherence check: overall pattern size vs INT
    coh_limit = self._coherence_limit()
    if len(world_vertices) > coh_limit and self._fizzle_roll(len(world_vertices) - coh_limit, coh_limit):
        self.log.add("Your pattern destabilizes; the activation slips away.")
        return
    depth = self._param_value("activate_seed", "neighbor_depth")
    mods = self._chakra_modifiers(self.player_id)
    if mods is not None:
        depth = int(depth) + int(mods.neighbor_depth_bonus)
    active_indices = set(self.neighbor_set_depth(seed_idx, depth))
    active_entries: list[tuple[int, tuple[float, float]]] = [
        (i, world_vertices[i]) for i in active_indices if 0 <= i < len(world_vertices)
    ]
    active_vertices = [v for _i, v in active_entries]
    level.activation_points = active_vertices
    level.activation_ttl = self.cfg.pattern_overlay_ttl

    mana_cost = len(active_vertices)
    player = self._player()
    str_limit = self._strength_limit()
    if len(active_vertices) > str_limit and self._fizzle_roll(len(active_vertices) - str_limit, str_limit):
        self.log.add("This weave challenges your focus.")
        return
    if mods is not None:
        mana_cost = int(math.ceil(float(mana_cost) * float(mods.mana_cost_mult)))
    if player.stats.mana < mana_cost:
        self.log.add(f"Not enough mana ({player.stats.mana}/{mana_cost}).")
        return
    player.stats.mana -= mana_cost
    player.stats.clamp()

    per_vertex = self._param_value("activate_seed", "damage")
    if mods is not None:
        per_vertex = int(math.ceil(float(per_vertex) * float(mods.damage_mult)))

    # Chakra passive bonus (hand precision).
    try:
        per_vertex += int(self.chakra_effect_value("activation_damage_bonus", actor_id=self.player_id))
    except Exception:
        pass

    hits = 0
    # Centralized policy: Activate N damages hostiles only, never self.
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=False,
        include_friendly=False,
        include_environment=False,
    )
    # Damage hostiles in tiles containing active vertices.
    for idx, (ax, ay) in active_entries:
        tile_x = int(round(ax))
        tile_y = int(round(ay))
        target_actor = self._actor_at(level, (tile_x, tile_y))
        if (
            target_actor
            and damage_policy_system.can_damage_target(
                self,
                level,
                self.player_id,
                target_actor.id,
                target_actor,
                policy,
            )
        ):
            source_nodes = _chakra_nodes_for_vertex(level.pattern.vertices[idx])
            dmg = chakra_items_system.apply_damage_modifiers(
                self,
                self.player_id,
                "activate_seed",
                int(per_vertex),
                source_nodes=source_nodes,
                illuminated_nodes=source_nodes,
            )
            if dmg <= 0:
                continue
            target_actor.stats.hp -= dmg
            hits += 1
            self.log.add(f"Your focus bites {target_actor.name} for {dmg}.")
            if target_actor.stats.hp <= 0:
                self.log.add(f"{target_actor.name} crumbles.")
                self._kill_actor(level, target_actor, killer_id=self.player_id, killer_is_player=True)
    if hits == 0:
        self.log.add("Your focus fizzles; no foes in reach.")

    if mods is not None:
        try:
            from edgecaster.systems import chakras as chakra_system

            self._consume_chakra_charge(
                self.player_id,
                chakra_system.CHARGE_CONSUME_ACTIVATE * mods.charge_consume_mult,
            )
        except Exception:
            pass
