from __future__ import annotations

import math
from typing import Optional, Tuple

from edgecaster.math_utils import smoothstep_range


def describe_current_tile(game, for_examine: bool = False) -> None:
    """Describe entities under the player when manually examining ('x').

    The for_examine flag is accepted for compatibility with the renderer,
    but the current behavior is the same either way: this is only used
    for explicit 'look' commands, not auto-observe.
    """
    level = game._level()
    if game.player_id not in level.actors:
        return
    player = level.actors[game.player_id]
    pos = player.pos

    ent = game._entity_at(level, pos)

    # If there is no entity, or the only entity is the player themself,
    # show the cheeky message.
    if ent is None or getattr(ent, "id", None) == game.player_id:
        game.log.add("You see nothing here, save yourself.")
        return

    # Otherwise, describe whatever is here.
    describe_tile_log(game, level, pos, observer_id=game.player_id, auto=False)


def describe_abs_tile_at(game, abs_pos: Tuple[int, int], *, cam_lod: float | None = None) -> str:
    """Describe an ABS tile that may be outside the currently loaded zone."""
    try:
        zone, local = game.zone_local_from_abs(
            abs_pos,
            depth=getattr(game, "zone_coord", (0, 0, 0))[2],
            clamp_to_world=True,
        )
    except Exception:
        zone, local = None, None

    if zone is not None and local is not None:
        if zone == getattr(game, "zone_coord", None):
            return describe_tile_at(game, (int(local[0]), int(local[1])))

        # If this zone is already loaded, describe the local tile there.
        try:
            lvl = game.get_zone_for_render(zone)
        except Exception:
            lvl = None
        if lvl is not None:
            return describe_tile_at(game, (int(local[0]), int(local[1])), level=lvl, zone_coord=zone)

    zx, zy, _zz = zone if zone is not None else ("?", "?", "?")
    ax, ay = int(abs_pos[0]), int(abs_pos[1])

    # If we have macro entities (POIs, walls, etc.) in the world index,
    # try to describe the best-matching one by LOD.
    try:
        if cam_lod is None:
            cam_lod = 0.0
        dmin = float(getattr(game, "entity_lod_delta_min", -5.0))
        dmax = float(getattr(game, "entity_lod_delta_max", 3.0))
        fade_w = float(getattr(game, "entity_lod_fade_width", 0.45))

        def _lod_delta(abs_size: float) -> float | None:
            ent_lod = math.log2(max(1e-12, abs_size))
            delta = float(cam_lod) - ent_lod
            if delta < dmin - fade_w or delta > dmax + fade_w:
                return None
            return delta

        def _intersects_tile(abs_x: float, abs_y: float, abs_size: float) -> bool:
            half = 0.5 * float(abs_size)
            ex0 = abs_x - half
            ey0 = abs_y - half
            ex1 = abs_x + half
            ey1 = abs_y + half
            return not (ex1 <= ax or ex0 >= ax + 1 or ey1 <= ay or ey0 >= ay + 1)

        candidates: list[tuple[object, float]] = []
        zz = int(getattr(game, "zone_coord", (0, 0, 0))[2])

        world_index = getattr(game, "world_entity_index", None)
        if world_index is not None:
            for ref in world_index.query_abs_rect((ax, ay, ax + 1, ay + 1), z=zz, zone_span_cap=1):
                obj = ref.ent
                zx0, zy0, _z = ref.zone_coord
                ox, oy = ref.local_pos
                abs_x = float(zx0) * float(world_index.zone_w) + float(ox)
                abs_y = float(zy0) * float(world_index.zone_h) + float(oy)
                abs_size = game._size_for_render(obj)
                if not _intersects_tile(abs_x, abs_y, abs_size):
                    continue
                delta = _lod_delta(abs_size)
                if delta is None:
                    continue
                candidates.append((obj, float(delta)))

        attn_store = getattr(game, "attn_store", None)
        if attn_store is not None:
            for obj, abs_x, abs_y in attn_store.query_abs_rect((ax, ay, ax + 1, ay + 1), zz=zz):
                abs_size = game._size_for_render(obj)
                if not _intersects_tile(float(abs_x), float(abs_y), abs_size):
                    continue
                delta = _lod_delta(abs_size)
                if delta is None:
                    continue
                candidates.append((obj, float(delta)))

        if candidates:
            # Prefer entities whose LOD best matches the camera.
            tol = float(getattr(game, "look_lod_tolerance", 0.75))
            min_delta = min(abs(d) for _, d in candidates)
            filtered = [obj for obj, d in candidates if abs(d) <= min_delta + tol]

            if filtered:
                try:
                    from edgecaster.systems.actions import describe_entity_for_look
                except Exception:
                    describe_entity_for_look = None  # type: ignore[assignment]
                ent = filtered[0]
                if describe_entity_for_look is not None:
                    info = describe_entity_for_look(ent)
                    glyph = info.get("glyph", "?")
                    desc = info.get("description", "") or "You see nothing remarkable about it."
                    lines = [str(glyph), "", str(desc)] if glyph else [str(desc)]
                    hp_txt = info.get("hp_text")
                    if hp_txt:
                        lines.extend(["", str(hp_txt)])
                    return "\n".join(lines)
    except Exception:
        pass

    return f"You peer into the distance at ({ax}, {ay}) in zone ({zx}, {zy}). The terrain is too far to make out."


def describe_tile_at(
    game,
    pos: Tuple[int, int],
    *,
    level=None,
    zone_coord: Optional[Tuple[int, int, int]] = None,
) -> str:
    if level is None:
        level = game._level()
    if zone_coord is None:
        zone_coord = getattr(level, "coord", None) or getattr(game, "zone_coord", (0, 0, 0))
    tile = level.world.get_tile(*pos)
    if tile is None:
        return "You see nothing but void."

    _god_vision = bool(getattr(game, "god_vision", False))
    _explored = bool(getattr(tile, "explored", True))

    # --- Biome label (default: realized tile biome_id) ---
    biome_label = "Unknown"
    stored_biome_id = None
    try:
        stored_biome_id = int(getattr(tile, "biome_id", 0) or 0)
        from edgecaster.climate import BIOME_SHORT_NAMES, Biome

        b = Biome(stored_biome_id)
        biome_label = BIOME_SHORT_NAMES.get(b, b.name.replace("_", " ").title())
    except Exception:
        biome_label = "Unknown"

    passable = "Yes" if getattr(tile, "walkable", True) else "No (Blocked)"

    relief_label_map = {
        "\u2248": "Deep Ocean",
        "~": "Shallow Water",
        ",": "Coast",
        ".": "Plains",
        '"': "Hills",
        "#": "Mountains",
        "^": "Peak",
    }

    # Canonical relief ladder used by ascii.py's overmap LOD glyph ladder.
    relief_glyphs_by_cat = ["\u2248", "~", ",", ".", '"', "#", "^"]

    relief_glyph = "?"
    relief_label = "Unknown"

    # ---------------------------------------------------------------------
    # Preferred: ask the renderer what it drew (LOD cache).
    # This is the only correct answer when zoomed-out (LOD cells != tiles).
    # ---------------------------------------------------------------------
    try:
        cfg = getattr(game, "cfg", None)
        zone_w = int(getattr(cfg, "world_width", getattr(level.world, "width", 60) or 60))
        zone_h = int(getattr(cfg, "world_height", getattr(level.world, "height", 40) or 40))

        # IMPORTANT: renderer uses the tile's zone_coord, not necessarily game.zone_coord.
        zx, zy, _zz = zone_coord
        tx, ty = int(pos[0]), int(pos[1])

        abs_wx = float(int(zx) * zone_w + tx)
        abs_wy = float(int(zy) * zone_h + ty)

        # Find the Ascii renderer instance (best-effort; depends on wiring)
        ascii_renderer = None
        mgr = getattr(game, "scene_manager", None)
        candidates = []

        if mgr is not None:
            candidates.extend([
                getattr(mgr, "renderer", None),
                getattr(mgr, "ascii_renderer", None),
                getattr(mgr, "ascii", None),
            ])
            r = getattr(mgr, "renderer", None)
            if r is not None:
                candidates.extend([
                    getattr(r, "ascii_renderer", None),
                    getattr(r, "ascii", None),
                    getattr(r, "ascii_render", None),
                ])

        for c in candidates:
            if c is None:
                continue
            if hasattr(c, "_lod_cell_cache") and hasattr(c, "_overmap_signature") and hasattr(c, "_render_lod_grid"):
                ascii_renderer = c
                break

        cache = getattr(ascii_renderer, "_lod_cell_cache", None) if ascii_renderer is not None else None
        if isinstance(cache, dict) and ascii_renderer is not None:
            # Recompute LOD the same way ascii.py draw_world_zoomed() does.
            world_scale = float(
                getattr(
                    ascii_renderer,
                    "tile_px",
                    float(getattr(ascii_renderer, "base_tile", 18)) * float(getattr(ascii_renderer, "zoom", 1.0)),
                )
            )
            world_scale = max(1e-6, world_scale)

            target_glyph_px = float(
                getattr(ascii_renderer, "lod_target_glyph_px", getattr(ascii_renderer, "base_tile", 18))
                or getattr(ascii_renderer, "base_tile", 18)
            )
            raw = target_glyph_px / world_scale

            lod_radix = getattr(ascii_renderer, "lod_radix", 2)
            lod_f = math.log(max(1e-12, raw), lod_radix)

            lod0 = int(math.floor(lod_f))
            lod1 = lod0 + 1
            frac = float(lod_f - lod0)

            lod0 = max(-12, min(lod0, 12))
            lod1 = max(-12, min(lod1, 12))
            if lod1 == lod0:
                frac = 0.0

            cell0 = float(lod_radix**lod0)
            cell1 = float(lod_radix**lod1)

            # Same smoothstep + deadband as ascii.py
            blend = smoothstep_range(0.0, 1.0, frac)
            eps = 0.06
            if blend <= eps:
                a0, a1 = 1.0, 0.0
            elif blend >= 1.0 - eps:
                a0, a1 = 0.0, 1.0
            else:
                a0, a1 = 1.0 - blend, blend

            # Choose the dominant layer (what you mostly see)
            if a1 > a0 and abs(cell1 - cell0) > 1e-12:
                lod_id = int(lod1)
                cell_tiles = float(cell1)
            else:
                lod_id = int(lod0)
                cell_tiles = float(cell0)

            cell_tiles = max(1e-6, cell_tiles)

            # Cache keys use absolute cell coords (cx,cy) in world tile space.
            cx = int(math.floor(abs_wx / cell_tiles))
            cy = int(math.floor(abs_wy / cell_tiles))

            sig = ascii_renderer._overmap_signature(game)
            key = (lod_id, cell_tiles, cell_tiles, cx, cy, sig)
            val = cache.get(key, None)

            if val is not None:
                # Cache entries can be either:
                #   (ch, rgb)  [older]
                #   (ch, rgb, biome_id, elev_cat)  [new]
                ch = val[0] if len(val) > 0 else ""
                cached_biome_id = None
                cached_elev_cat = None

                try:
                    if len(val) > 2:
                        cached_biome_id = int(val[2])
                except Exception:
                    cached_biome_id = None

                try:
                    if len(val) > 3:
                        cached_elev_cat = int(val[3])
                except Exception:
                    cached_elev_cat = None

                # Relief: prefer cached elev category (most stable), else use cached glyph char.
                if cached_elev_cat is not None and 0 <= cached_elev_cat < len(relief_glyphs_by_cat):
                    relief_glyph = relief_glyphs_by_cat[cached_elev_cat]
                    relief_label = relief_label_map.get(relief_glyph, "Unknown")
                elif isinstance(ch, str) and ch:
                    relief_glyph = ch[0]
                    relief_label = relief_label_map.get(relief_glyph, "Unknown")

                # Prefer cached biome_id if available.
                if cached_biome_id is not None:
                    try:
                        stored_biome_id = int(cached_biome_id)
                        from edgecaster.climate import BIOME_SHORT_NAMES, Biome

                        b = Biome(stored_biome_id)
                        biome_label = BIOME_SHORT_NAMES.get(b, b.name.replace("_", " ").title())
                    except Exception:
                        pass

    except Exception:
        # If cache plumbing fails, fall through to fallback.
        pass

    # ---------------------------------------------------------------------
    # Fallback: only trust tile.glyph if it is one of the NEW relief glyphs.
    # (Never resurrect legacy '%' etc.)
    # ---------------------------------------------------------------------
    if relief_glyph == "?":
        try:
            g = str(getattr(tile, "glyph", "") or "")
            if g and g[0] in relief_label_map:
                relief_glyph = g[0]
                relief_label = relief_label_map.get(relief_glyph, "Unknown")
        except Exception:
            pass

    if passable != "Yes" and relief_glyph == "\u2588":
        relief_label = "Wall"

    return "\n".join(
        [
            f"Biome: {biome_label} [tile:{stored_biome_id}]",
            f"Relief: {relief_label} ({relief_glyph})",
            f"Passable: {passable}",
        ]
    )


def describe_tile_log(
    game,
    level,
    pos: Tuple[int, int],
    observer_id: Optional[str] = None,
    auto: bool = False,
) -> None:
    """Log a description of entities at the given tile, if any."""
    from edgecaster.systems.inventory import get_quantity

    # Get all items at this position
    items = game._items_at(level, pos)
    if not items:
        return

    # Filter out observer from auto-observe
    if auto and observer_id is not None:
        items = [i for i in items if getattr(i, "id", None) != observer_id]
        if not items:
            return

    # Check for bismuth currency first (special handling)
    bismuth_items = []
    regular_items = []
    for item in items:
        tags = getattr(item, "tags", {}) or {}
        if tags.get("currency") == "bismuth":
            bismuth_items.append(item)
        else:
            regular_items.append(item)

    # Describe bismuth separately
    for bitem in bismuth_items:
        tags = getattr(bitem, "tags", {}) or {}
        amt = 0
        try:
            amt = int(tags.get("amount", 0))
        except Exception:
            amt = 0
        if amt <= 0:
            size = "tiny"
        elif amt <= 3:
            size = "tiny"
        elif amt <= 10:
            size = "small"
        elif amt <= 25:
            size = "medium"
        elif amt <= 50:
            size = "large"
        elif amt <= 100:
            size = "huge"
        else:
            size = "enormous"
        article = "an" if size[0].lower() in "aeiou" else "a"
        game.log.add(f"You see {article} {size} bismuth crystal.")

    # Describe regular items
    if not regular_items:
        return

    if len(regular_items) == 1:
        # Single item - original behavior
        item = regular_items[0]
        name = getattr(item, "name", None) or "thing"
        qty = get_quantity(item)
        if qty > 1:
            game.log.add(f"You see here {qty} {name.lower()}s.")
        else:
            article = "an" if name and name[0].lower() in "aeiou" else "a"
            game.log.add(f"You see here {article} {name.lower()}.")
    else:
        # Multiple items - list them
        game.log.add(f"You see here {len(regular_items)} items:")
        for item in regular_items[:5]:
            name = getattr(item, "name", "something")
            qty = get_quantity(item)
            qty_suffix = f" ({qty})" if qty > 1 else ""
            game.log.add(f"  - {name}{qty_suffix}")
        if len(regular_items) > 5:
            game.log.add(f"  ... and {len(regular_items) - 5} more.")
