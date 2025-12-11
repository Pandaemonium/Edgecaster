from __future__ import annotations

import threading
import pygame
from dataclasses import dataclass
from typing import Literal, Optional

from .base import Scene
from edgecaster.game import Game
from .urgent_message_scene import UrgentMessageScene
from .game_input import GameInput, GameCommand
from edgecaster.systems.abilities import trigger_ability_effect
from edgecaster.systems.targeting import predict_aim_preview
from edgecaster.patterns import motion as pattern_motion
from edgecaster.ui.ability_bar import AbilityBarState
from edgecaster.systems.actions import get_action, describe_entity_for_look


TargetKind = Literal["tile", "vertex", "look", "position"]


@dataclass
class TargetConstraints:
    # Tile-geometry constraints
    max_range: Optional[int] = None
    allowed_offsets: list[tuple[int, int]] | None = None
    require_passable: bool = False
    require_visible: bool = False

    # Graph-geometry constraints (for fractal / vertex modes)
    neighbor_depth_param: str | None = None   # e.g. "neighbor_depth" for activate_seed
    use_param_radius: str | None = None       # e.g. "radius" for activate_all


@dataclass
class TargetState:
    action: str
    kind: TargetKind
    origin_actor_id: str | None
    cursor_tile: tuple[int, int] | None = None
    cursor_vertex: int | None = None
    constraints: TargetConstraints | None = None
    mode: str | None = None  # "terminus" or "aim" or None

    def __post_init__(self) -> None:
        if self.constraints is None:
            self.constraints = TargetConstraints()


@dataclass
class DungeonUIState:
    """Scene-owned view-model for UI state previously held by the renderer."""
    target: TargetState | None = None
    target_cursor: tuple[int, int] = (0, 0)
    aim_action: str | None = None
    hover_vertex: int | None = None
    hover_neighbors: list[int] | None = None
    config_open: bool = False
    config_action: str | None = None
    config_selection: int = 0
    aim_prediction: object | None = None  # computed preview info for aim overlays
    push_target: tuple[float, float] | None = None
    push_rotation: float = 0.0
    push_preview: object | None = None

    def __post_init__(self) -> None:
        if self.hover_neighbors is None:
            self.hover_neighbors = []


class DungeonScene(Scene):
    """The main roguelike dungeon scene."""

    uses_live_loop = True

    def _refresh_aim_prediction(self, game: Game) -> None:
        """Compute aim preview data in logic layer so renderer only draws."""
        ui = self.ui_state
        action_name = ui.aim_action
        if not action_name or ui.hover_vertex is None:
            ui.aim_prediction = None
            return

        try:
            action_def = get_action(action_name)
        except KeyError:
            ui.aim_prediction = None
            return

        spec = getattr(action_def, "targeting", None)
        if not spec or spec.kind != "vertex" or spec.mode != "aim":
            ui.aim_prediction = None
            return

        try:
            ui.aim_prediction = predict_aim_preview(
                game,
                action_name,
                ui.hover_vertex,
                neighbors=ui.hover_neighbors or [],
            )
        except Exception:
            ui.aim_prediction = None

    def __init__(self) -> None:
        # Keep the Game instance across pauses/inventory.
        self.game: Game | None = None
        self.ui_state = DungeonUIState()
        # Scene-level input mapper for "pure game" actions
        # refactor: migrate to a shared input layer; DungeonScene should consume a GameCommand queue only.
        self.input = GameInput()
        self._started = False
        self._old_urgent_cb = None

    # ------------------------------------------------------------------ #
    # Live-loop hooks
    def handle_event(self, event, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        # Keep keybindings in sync with manager settings.
        if hasattr(manager, "keybindings"):
            kb = manager.keybindings
            self.input.set_bindings(kb.get("bindings", {}))
            self.input.set_move_bindings(kb.get("move_bindings", {}))
        game, renderer = self._ensure_game(manager)
        if game is None:
            manager.set_scene(None)
            return

        if event.type == pygame.QUIT:
            manager.set_scene(None)
            return

        if event.type == pygame.KEYDOWN:
            cmds = self.input.handle_keydown(event)
            for cmd in cmds:
                self._handle_command(game, renderer, cmd, manager)

        elif event.type == pygame.MOUSEBUTTONDOWN:
            cmds = self.input.handle_mousebutton(event)
            for cmd in cmds:
                self._handle_command(game, renderer, cmd, manager)

        elif event.type == pygame.MOUSEMOTION:
            cmds = self.input.handle_mousemotion(event)
            for cmd in cmds:
                self._handle_command(game, renderer, cmd, manager)

        elif event.type == pygame.MOUSEWHEEL:
            cmds = self.input.handle_mousewheel(event)
            for cmd in cmds:
                self._handle_command(game, renderer, cmd, manager)

    def update(self, dt_ms: int, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        game, renderer = self._ensure_game(manager)
        if game is None:
            manager.set_scene(None)
            return

        # Legacy flags still respected
        if getattr(renderer, "quit_requested", False) or getattr(renderer, "pause_requested", False):
            renderer.quit_requested = False

        # Process any transitions (death, map, inventory, etc.)
        self._process_transitions(game, renderer, manager)

        # Keep aim preview in sync with any param/hover changes.
        self._refresh_aim_prediction(game)

    def render(self, renderer, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        if self.game is None:
            return
        renderer.draw_dungeon_frame(self.game)

    # Legacy compatibility: if SceneManager falls back to run()
    def run(self, manager: "SceneManager") -> None:  # pragma: no cover - legacy path
        clock = pygame.time.Clock()
        while manager.scene_stack and manager.scene_stack[-1] is self:
            for event in pygame.event.get():
                self.handle_event(event, manager)
            self.update(clock.tick(60), manager)
            self.render(manager.renderer, manager)

    # ------------------------------------------------------------------ #
    # Helpers
    def _ensure_game(self, manager: "SceneManager"):
        """Lazily build game + renderer state and attach callbacks."""
        from .main_menu import MainMenuScene

        cfg = manager.cfg
        renderer = manager.renderer
        char = manager.character

        if self.game is None:
            seed = None
            if hasattr(char, "use_random_seed") and char.use_random_seed:
                seed = None  # random
            else:
                seed = getattr(char, "seed", None) or getattr(cfg, "seed", None)
            rng = manager.rng_factory(seed)
            self.game = Game(cfg, rng, character=char)
            # ability bar view-model
            self.game.ability_bar_state = AbilityBarState()

            # Precompute world map cache in the background
            if not getattr(self.game, "world_map_thread_started", False):
                self.game.world_map_thread_started = True
                self.game.world_map_rendering = True

                def worker(game_ref: Game, width: int, height: int) -> None:
                    try:
                        from .world_map_scene import WorldMapScene

                        wm = WorldMapScene(game_ref, span=16)

                        class Stub:
                            def __init__(self, w, h) -> None:
                                self.width = w
                                self.height = h

                        stub = Stub(width, height)
                        surf, view = wm._render_overmap(stub)
                        game_ref.world_map_cache = {
                            "surface": surf,
                            "view": view,
                            "key": (width, height, wm.span),
                        }
                        game_ref.world_map_ready = True
                    except Exception:
                        game_ref.world_map_ready = False
                    finally:
                        game_ref.world_map_rendering = False

                threading.Thread(
                    target=worker,
                    args=(self.game, renderer.width, renderer.height),
                    daemon=True,
                ).start()

        game = self.game
        if game is None:
            return None, renderer

        # expose to manager for options display
        manager.current_game = game
        setattr(game, "scene_manager", manager)

        # If a fractal edit result is waiting, absorb it into custom patterns
        if getattr(manager, "fractal_edit_result", None):
            res = manager.fractal_edit_result
            manager.fractal_edit_result = None
            verts = res.get("vertices") if isinstance(res, dict) else None
            edges = res.get("edges") if isinstance(res, dict) else []
            if verts and len(verts) >= 2:
                pattern = {"vertices": verts, "edges": edges}
                game.custom_patterns.append(pattern)
                game.character.custom_pattern = pattern
                # New custom pattern can change available abilities; force resync.
                if hasattr(game, "ability_bar_state"):
                    game.ability_bar_state.invalidate()
                # Grant the corresponding custom ability (custom, custom_1, ...)
                new_idx = len(game.custom_patterns) - 1
                ab_name = "custom" if new_idx == 0 else f"custom_{new_idx}"
                if hasattr(game, "grant_ability"):
                    game.grant_ability(ab_name)
                # Reset editor state after applying result so '+' uses defaults next time.
                setattr(game, "fractal_editor_state", None)

        # Sync ability bar state with current game abilities
        if not hasattr(game, "ability_bar_state"):
            game.ability_bar_state = AbilityBarState()
        game.ability_bar_state.sync_from_game(game)

        # Attach and sync UI state to renderer (temporary bridge while moving state out of renderer).
        if renderer is not None:
            renderer.ui_state = self.ui_state  # type: ignore[attr-defined]
            # pull renderer-local fields into scene ui_state for compatibility
            for attr in (
                "target_cursor",
                "aim_action",
                "hover_vertex",
                "hover_neighbors",
                "config_open",
                "config_action",
                "config_selection",
            ):
                if hasattr(renderer, attr):
                    setattr(self.ui_state, attr, getattr(renderer, attr))

        # Save any previous hook (in case we ever call DungeonScene from another scene)
        if self._old_urgent_cb is None:
            self._old_urgent_cb = getattr(game, "urgent_callback", None)

            def show_urgent(text: str) -> None:
                # Build popup from game's structured urgent fields.
                title = getattr(game, "urgent_title", "") or ""
                body = getattr(game, "urgent_body", text)
                choices = getattr(game, "urgent_choices", None) or ["Continue..."]

                def handle_choice(idx, _manager) -> None:
                    # Look up any effect the Game attached to this urgent event.
                    effect = getattr(game, "urgent_choice_effect", None)
                    if effect is not None:
                        effect(idx, game)
                        # Clear it so it doesn't leak to the next popup.
                        game.urgent_choice_effect = None

                manager.push_scene(
                    UrgentMessageScene(
                        game,
                        body,
                        title=title,
                        choices=choices,
                        on_choice=handle_choice,
                    )
                )

            game.urgent_callback = show_urgent

        # Clear flags before rendering
        if not self._started:
            renderer.quit_requested = False
            if hasattr(renderer, "pause_requested"):
                renderer.pause_requested = False
            if hasattr(game, "inventory_requested"):
                game.inventory_requested = False
            renderer.start_dungeon(game)
            self._started = True

        return game, renderer

    def _process_transitions(self, game: Game, renderer, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        from .main_menu import MainMenuScene
        from .pause_menu_scene import PauseMenuScene
        from .inventory_scene import InventoryScene

        # 0) If an UrgentMessageScene was pushed from inside the game logic,
        #    it will already be on top of the stack. In that case, just yield.
        if manager.scene_stack and isinstance(manager.scene_stack[-1], UrgentMessageScene):
            return

        # 1) Fallback / generalisation for legacy urgent_message flag:
        if getattr(game, "urgent_message", None) and not getattr(game, "urgent_resolved", True):
            body = getattr(game, "urgent_body", None) or game.urgent_message or ""
            title = getattr(game, "urgent_title", "") or ""
            choices = getattr(game, "urgent_choices", None) or ["Continue..."]

            manager.push_scene(
                UrgentMessageScene(
                    game,
                    body,
                    title=title,
                    choices=choices,
                )
            )
            return

        # 2) Death -> go back to main menu, discard the run
        if not getattr(game, "player_alive", True):
            self.game = None
            manager.current_game = None
            manager.set_scene(MainMenuScene())
            return

        # 3) Inventory requested -> push overlay, keep dungeon scene on stack
        if getattr(game, "inventory_requested", False):
            game.inventory_requested = False
            manager.push_scene(InventoryScene(game))
            return

        # 4) Fractal editor requested -> open editor scene
        if getattr(game, "fractal_editor_requested", False):
            game.fractal_editor_requested = False
            from .fractal_editor_scene import FractalEditorScene, FractalEditorState

            state = getattr(game, "fractal_editor_state", None) or FractalEditorState()
            manager.push_scene(FractalEditorScene(state=state, window_rect=None))
            return

        # 5) Pause requested -> push pause menu overlay
        if getattr(renderer, "pause_requested", False):
            renderer.pause_requested = False
            manager.push_scene(PauseMenuScene())
            return

        # 6) World map requested -> push world map scene (keep game instance)
        if getattr(game, "map_requested", False):
            game.map_requested = False
            from .world_map_scene import WorldMapScene

            manager.push_scene(WorldMapScene(game, span=16))
            return

    # ------------------------------------------------------------------ #
    # Unified TargetMode helpers
    # ------------------------------------------------------------------ #
    def begin_target_mode(
        self,
        game: Game,
        *,
        action: str,
        kind: TargetKind,
        mode: str | None = None,
        origin_actor_id: str | None = None,
        constraints: TargetConstraints | None = None,
    ) -> None:
        if constraints is None:
            constraints = TargetConstraints()
        actor_id = origin_actor_id or getattr(game, "player_id", None)
        origin_tile = None
        if actor_id is not None and getattr(game, "actors", None):
            origin_tile = game.actors[actor_id].pos

        tstate = TargetState(
            action=action,
            kind=kind,
            origin_actor_id=actor_id,
            cursor_tile=origin_tile,
            constraints=constraints,
            mode=mode,
        )
        self.ui_state.target = tstate

        # Look-style generic tile cursor (no legacy flags, no game.awaiting_terminus).
        if kind == "look":
            if origin_tile is not None:
                tstate.cursor_tile = origin_tile
                self.ui_state.target_cursor = origin_tile
            return

        # Backwards-compat bridge for rune terminus targeting:
        if kind == "tile" and mode == "terminus":
            if hasattr(game, "begin_place_mode"):
                # Ensure game-side place state (e.g. place_range) is initialized.
                game.begin_place_mode()
            game.awaiting_terminus = True
            self.ui_state.target_cursor = origin_tile or (0, 0)

        if kind == "vertex":
            # Enter generic vertex targeting (e.g. activate_all / activate_seed).
            self.ui_state.aim_action = action

            # Seed hover at nearest vertex to the origin tile (usually the player).
            idx = None
            if origin_tile is not None:
                tx, ty = origin_tile
                wx = tx + 0.5
                wy = ty + 0.5
                idx = game.nearest_vertex((wx, wy))

            tstate.cursor_vertex = idx
            self.ui_state.hover_vertex = idx

            # Seed the neighbor set if this action has a neighbor-depth constraint.
            if idx is not None and tstate.constraints and tstate.constraints.neighbor_depth_param:
                depth = game.get_param_value(action, tstate.constraints.neighbor_depth_param)
                self.ui_state.hover_neighbors = game.neighbor_set_depth(idx, depth)
            else:
                self.ui_state.hover_neighbors = []

            self._refresh_aim_prediction(game)

        if kind == "position":
            # Push pattern targeting: seed at pattern COM (or player tile).
            self.ui_state.aim_action = action
            lvl = game._level()
            pattern = getattr(lvl, "pattern", None)
            anchor = getattr(lvl, "pattern_anchor", None)
            max_range = constraints.max_range or 5.0
            if pattern and anchor and pattern.vertices:
                com = pattern_motion.center_of_mass(pattern)
                com_world = (com[0] + anchor[0], com[1] + anchor[1])
                tstate.cursor_tile = (int(round(com_world[0])), int(round(com_world[1])))
                self.ui_state.target_cursor = tstate.cursor_tile
                self.ui_state.push_target = com_world
                self.ui_state.push_rotation = 0.0
                self.ui_state.push_preview = pattern_motion.build_push_preview(
                    pattern, anchor, com_world, 0.0, max_range
                )
            elif origin_tile is not None:
                tstate.cursor_tile = origin_tile
                self.ui_state.target_cursor = origin_tile
                self.ui_state.push_target = (origin_tile[0], origin_tile[1])
                self.ui_state.push_rotation = 0.0
                self.ui_state.push_preview = None

    def cancel_target_mode(self, game: Game) -> None:
        t = self.ui_state.target
        self.ui_state.target = None
        self.ui_state.aim_action = None
        self.ui_state.hover_vertex = None
        self.ui_state.hover_neighbors = []
        self.ui_state.aim_prediction = None
        self.ui_state.push_target = None
        self.ui_state.push_rotation = 0.0
        self.ui_state.push_preview = None
        # Clear legacy terminus flag for any tile/terminus targeting.
        if t and t.kind == "tile" and getattr(t, "mode", None) == "terminus":
            game.awaiting_terminus = False

    def confirm_target(self, game: Game) -> None:
        """Apply the currently selected target to the action that requested it."""
        t = self.ui_state.target
        if not t:
            return

        # TILE TARGETING (e.g. Kochbender 'place' / rune terminus)
        if t.kind == "tile":
            if t.cursor_tile is None:
                return

            if getattr(t, "mode", None) == "terminus":
                # Generic "terminus" semantics: place a rune terminus at the tile.
                if hasattr(game, "try_place_terminus"):
                    game.try_place_terminus(t.cursor_tile)
            else:
                # Future: tile-based ranged attacks, teleports, etc.
                trigger_ability_effect(
                    game,
                    t.action,
                    target_tile=t.cursor_tile,
                )

        # VERTEX TARGETING (e.g. activate_all / activate_seed)
        elif t.kind == "vertex":
            if t.cursor_vertex is None:
                return

            # Pass a generic vertex target; the action implementation decides how to use it.
            trigger_ability_effect(
                game,
                t.action,
                hover_vertex=t.cursor_vertex,
            )

        # POSITION TARGETING (push_pattern)
        elif t.kind == "position":
            tgt = self.ui_state.push_target or t.cursor_tile
            if tgt is None:
                return
            rot = self.ui_state.push_rotation
            trigger_ability_effect(game, t.action, target_pos=tgt, rotation_deg=rot)

        # Clear target + legacy flags
        self.cancel_target_mode(game)


    def _confirm_look(self, game: Game, renderer, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """Resolve a 'look' target into an inspect popup.

        Uses the new inheritance-aware description system:
        - Prefer any entity on the targeted tile (actors, items, features).
        - Resolve its description via prototype parents (entity -> actor -> humanoid, etc.).
        - Fall back to a tile description if no entity is present.
        """
        t = self.ui_state.target
        if not t or t.cursor_tile is None:
            self.cancel_target_mode(game)
            return

        tx, ty = t.cursor_tile
        level = game._level() if hasattr(game, "_level") else None

        title = "You look around..."
        body: str | None = None

        if level is not None:
            # Prefer any renderable entities on this tile (actors, items, features, etc.).
            try:
                renderables = game.renderables_current()
            except Exception:
                renderables = []

            entities_here = [
                e for e in renderables
                if getattr(e, "pos", None) == (tx, ty)
            ]

            if entities_here:
                # For now just inspect the first visible entity on this tile.
                primary = entities_here[0]
                info = describe_entity_for_look(primary)

                title = info.get("name", title) or title
                glyph = info.get("glyph", "?")
                desc = info.get("description", "") or "You see nothing remarkable about it."

                # Layout:
                #  [glyph]
                #  (blank line)
                #  [description]
                lines: list[str] = []
                if glyph:
                    # Later we could teach UrgentMessageScene to draw this big & colored,
                    # using info["color"]; for now it's just a plain line.
                    lines.append(str(glyph))
                    lines.append("")
                lines.append(str(desc))
                body = "\n".join(lines)
            else:
                # No entities here: fall back to a tile description if available.
                if hasattr(game, "describe_tile_at"):
                    body = game.describe_tile_at((tx, ty))
                else:
                    body = f"You look at the tile at {tx}, {ty}."
        else:
            body = f"You look at the tile at {tx}, {ty}."

        if not body:
            body = "You see nothing of interest."

        manager.push_scene(
            UrgentMessageScene(
                game,
                body,
                title=title,
                choices=["OK"],
            )
        )

        # Note: we deliberately DO NOT cancel target mode here.
        # Look targeting stays active underneath the popup, so when
        # the UrgentMessageScene is dismissed, you're still in look
        # mode. Press ESC again to exit look mode back to normal.



    def _update_hover_from_mouse(
        self,
        game: Game,
        renderer,
        surface_pos: tuple[int, int],
    ) -> None:
        t = self.ui_state.target
        if not t:
            # no targeting, also clear old vertex preview
            self.ui_state.hover_vertex = None
            self.ui_state.hover_neighbors = []
            self._refresh_aim_prediction(game)
            return

        mx, my = surface_pos
        wx = (mx - renderer.origin_x) / renderer.tile
        wy = (my - renderer.origin_y) / renderer.tile

        if t.kind in ("tile", "look", "position"):
            tx = int((mx - renderer.origin_x) // renderer.tile)
            ty = int((my - renderer.origin_y) // renderer.tile)
            if game.world.in_bounds(tx, ty):
                t.cursor_tile = (tx, ty)
                self.ui_state.target_cursor = (tx, ty)
                if getattr(t, "action", None) == "push_pattern":
                    lvl = game._level()
                    pattern = getattr(lvl, "pattern", None)
                    anchor = getattr(lvl, "pattern_anchor", None)
                    if pattern and anchor and pattern.vertices:
                        com = pattern_motion.center_of_mass(pattern)
                        com_world = (com[0] + anchor[0], com[1] + anchor[1])
                        dx = tx - com_world[0]
                        dy = ty - com_world[1]
                        dist = (dx * dx + dy * dy) ** 0.5
                        max_range = getattr(t.constraints, "max_range", None) if t.constraints else None
                        if max_range is None:
                            max_range = 5.0
                        if dist > max_range and dist > 0:
                            scale = max_range / dist
                            dx *= scale
                            dy *= scale
                        tgt = (com_world[0] + dx, com_world[1] + dy)
                        self.ui_state.push_target = tgt
                        self.ui_state.push_preview = pattern_motion.build_push_preview(
                            pattern, anchor, tgt, self.ui_state.push_rotation, max_range
                        )

        elif t.kind == "vertex":
            idx = game.nearest_vertex((wx, wy))
            self.ui_state.hover_vertex = idx
            t.cursor_vertex = idx

            if idx is not None and t.constraints and t.constraints.neighbor_depth_param:
                depth = game.get_param_value(t.action, t.constraints.neighbor_depth_param)
                self.ui_state.hover_neighbors = game.neighbor_set_depth(idx, depth)
            else:
                self.ui_state.hover_neighbors = []

            self._refresh_aim_prediction(game)

    # ------------------------------------------------------------------ #
    # Command handling
    # ------------------------------------------------------------------ #
    def _handle_command(
        self,
        game: Game,
        renderer,
        cmd: GameCommand,
        manager: "SceneManager",  # type: ignore[name-defined]
    ) -> None:
        """
        Apply a high-level GameCommand to the current game + renderer.

        This is where scene-level logic lives: we can query game/renderer
        state (e.g. awaiting_terminus, dialogs, targeting) and decide
        whether to act or ignore the command.
        """
        import pygame  # local to avoid circulars in some environments

        ui = self.ui_state

        def _set_ui(attr: str, value) -> None:
            setattr(ui, attr, value)
            if renderer is not None and hasattr(renderer, attr):
                setattr(renderer, attr, value)

        def _set_aim_action(value: str | None) -> None:
            _set_ui("aim_action", value)
            self._refresh_aim_prediction(game)

        def _update_hover(surface_pos: tuple[int, int]) -> None:
            """Scene-side hover resolver; updates ui_state (and renderer for compatibility)."""
            aim = ui.aim_action
            if not aim:
                _set_ui("hover_vertex", None)
                _set_ui("hover_neighbors", [])
                self._refresh_aim_prediction(game)
                return

            try:
                action_def = get_action(aim)
            except KeyError:
                _set_ui("hover_vertex", None)
                _set_ui("hover_neighbors", [])
                self._refresh_aim_prediction(game)
                return

            spec = getattr(action_def, "targeting", None)
            if not spec or spec.kind != "vertex":
                _set_ui("hover_vertex", None)
                _set_ui("hover_neighbors", [])
                self._refresh_aim_prediction(game)
                return

            mx, my = surface_pos
            wx = (mx - renderer.origin_x) / renderer.tile
            wy = (my - renderer.origin_y) / renderer.tile
            idx = game.nearest_vertex((wx, wy))
            _set_ui("hover_vertex", idx)
            if idx is not None and spec.neighbor_depth_param:
                depth = game.get_param_value(aim, spec.neighbor_depth_param)
                _set_ui("hover_neighbors", game.neighbor_set_depth(idx, depth))
            else:
                _set_ui("hover_neighbors", [])
            self._refresh_aim_prediction(game)

        kind = cmd.kind
        key = cmd.raw_key
        vec = cmd.vector

        # AbilityBarState is the single source of truth for ability ordering
        # and selection; renderer only draws via AbilityBarRenderer.
        bar = getattr(game, "ability_bar_state", None)
        if bar is None:
            bar = AbilityBarState()
            game.ability_bar_state = bar
        bar.sync_from_game(game)

        t = getattr(self.ui_state, "target", None)
        in_target_mode = t is not None
        in_terminus_mode = bool(
            in_target_mode and t.kind == "tile" and getattr(t, "mode", None) == "terminus"
        )
        in_aim_mode = bool(
            in_target_mode and t.kind == "vertex" and getattr(t, "mode", None) == "aim"
        )
        aim_action = ui.aim_action
        push_mode = bool(in_target_mode and t and getattr(t, "action", "") == "push_pattern")

        in_look_mode = bool(in_target_mode and t and t.kind == "look")

        # If we're in unified TargetMode and the user presses confirm, resolve it here.
        if in_target_mode and kind == "confirm":
            if t and t.kind == "look":
                self._confirm_look(game, renderer, manager)
            else:
                self.confirm_target(game)
            return

        # ------------------------------------------------------------
        # Ability reordering overlay (when open, swallow most commands)
        # ------------------------------------------------------------
        if getattr(game, "ability_reorder_open", False):
            if kind == "escape":
                game.ability_reorder_open = False
                return
            if kind == "confirm":
                game.ability_reorder_open = False
                # keep active action aligned with selected item
                sel_act = bar.action_at_index(bar.selected_index)
                if sel_act:
                    bar.set_active(sel_act)
                return
            if kind == "move" and vec is not None:
                dx, dy = vec
                if dy:
                    bar.move_selection(dy)
                if dx:
                    bar.move_selected_item(dx)
                # keep page in view of selection
                if bar.selected_index // bar.page_size != bar.page:
                    bar.page = bar.selected_index // bar.page_size
                return

            if kind == "ability_page_prev":
                bar.prev_page()
                # Snap selection to first slot on the new page
                bar.selected_index = bar.page * bar.page_size
                return
            if kind == "ability_page_next":
                bar.next_page()
                bar.selected_index = bar.page * bar.page_size
                return
            # ignore other commands while reorder UI is active
            return


        # ------------------------------------------------------------
        # 0) Global-ish keys: Escape, fullscreen, help
        # ------------------------------------------------------------
        if kind == "escape":
            # First: cancel unified target mode if active.
            if in_target_mode:
                self.cancel_target_mode(game)
                return

            # Next: close config overlay if open.
            if self.ui_state.config_open:
                _set_ui("config_open", False)
                return

            # Otherwise: normal ESC in the dungeon → request pause.
            renderer.pause_requested = True
            renderer.quit_requested = True
            return

        if kind == "open_abilities":
            game.ability_reorder_open = True
            # select current active ability if possible
            if bar.active_action and bar.active_action in bar.order:
                bar.selected_index = bar.order.index(bar.active_action)
                bar.page = bar.selected_index // bar.page_size
            return

        # ------------------------------------------------------------
        # 2) Config overlay (always takes precedence while open)
        # ------------------------------------------------------------

        if self.ui_state.config_open and self.ui_state.config_action:
            params = game.param_view(self.ui_state.config_action)

            if key in (pygame.K_RETURN, pygame.K_SPACE):
                _set_ui("config_open", False)
                return

            if key == pygame.K_UP:
                _set_ui(
                    "config_selection",
                    (self.ui_state.config_selection - 1) % max(1, len(params)),
                )
                return

            if key == pygame.K_DOWN:
                _set_ui(
                    "config_selection",
                    (self.ui_state.config_selection + 1) % max(1, len(params)),
                )
                return

            if key in (pygame.K_LEFT, pygame.K_RIGHT):
                if params:
                    param_key = params[self.ui_state.config_selection]["key"]
                    delta = 1 if key == pygame.K_RIGHT else -1
                    changed, msg = game.adjust_param(
                        self.ui_state.config_action,
                        param_key,
                        delta,
                    )
                    # msg is available if you want to surface it later
                return

            # Other commands do nothing while config overlay is open
            return

        # ------------------------------------------------------------
        # 3) Terminus targeting mode
        # ------------------------------------------------------------
        if in_terminus_mode:
            if kind == "move" and vec is not None and in_target_mode and t.kind == "tile":
                tx, ty = t.cursor_tile or game.actors[game.player_id].pos
                dx, dy = vec
                nt = (tx + dx, ty + dy)
                if game.world.in_bounds(*nt):
                    t.cursor_tile = nt
                    self.ui_state.target_cursor = nt
                return

            if kind == "confirm" and in_target_mode:
                self.confirm_target(game)
                return

            # Let mouse_* commands pass through to the mouse handler.
            if kind not in ("mouse_click", "mouse_move", "mouse_wheel"):
                # Everything else (examine, pickup, etc.) is ignored
                # while we're choosing a terminus.
                return

        # ------------------------------------------------------------
        # 4) Vertex targeting mode (activate_all / activate_seed)
        # ------------------------------------------------------------
        if in_target_mode and t and t.kind == "vertex":
            # Arrow / WASD: move a logical tile cursor and pick nearest vertex.
            if kind == "move" and vec is not None:
                tx, ty = t.cursor_tile or game.actors[game.player_id].pos
                dx, dy = vec
                nt = (tx + dx, ty + dy)
                if game.world.in_bounds(*nt):
                    t.cursor_tile = nt

                    # Aim at the vertex nearest to the center of this tile.
                    wx = nt[0] + 0.5
                    wy = nt[1] + 0.5
                    idx = game.nearest_vertex((wx, wy))
                    t.cursor_vertex = idx
                    ui.hover_vertex = idx

                    # Update neighbor halo if this action has depth-based neighbors.
                    if idx is not None and t.constraints and t.constraints.neighbor_depth_param:
                        depth = game.get_param_value(
                            t.action,
                            t.constraints.neighbor_depth_param,
                        )
                        ui.hover_neighbors = game.neighbor_set_depth(idx, depth)
                    else:
                        ui.hover_neighbors = []

                    self._refresh_aim_prediction(game)

                # Always swallow movement while targeting, even if we hit a boundary.
                return

            # Swallow any other 'move' events so they never reach player movement.
            if kind == "move":
                return

        # ------------------------------------------------------------
        # 4b) Position targeting mode (push_pattern)
        #      - Arrow/WASD/numpad move the push target
        #      - Q/E rotate the push direction
        #      While active, player movement is frozen.
        # ------------------------------------------------------------
        if push_mode and t and t.kind == "position":
            # Keyboard rotation with Q/E
            if key in (pygame.K_q, pygame.K_e):
                delta_deg = 15 if key == pygame.K_e else -15
                ui.push_rotation = (ui.push_rotation + delta_deg) % 360

                if self.ui_state.push_target and t and t.constraints:
                    lvl = game._level()
                    pattern = getattr(lvl, "pattern", None)
                    anchor = getattr(lvl, "pattern_anchor", None)
                    max_range = getattr(t.constraints, "max_range", 5.0)
                    if pattern and anchor and getattr(pattern, "vertices", None):
                        self.ui_state.push_preview = pattern_motion.build_push_preview(
                            pattern,
                            anchor,
                            self.ui_state.push_target,
                            self.ui_state.push_rotation,
                            max_range,
                        )

                # Swallow Q/E so they don’t do anything else while targeting
                return

            # Keyboard translation of the push target
            if kind == "move" and vec is not None:
                dx, dy = vec

                # Get current pattern center-of-mass in world coords
                lvl = game._level()
                pattern = getattr(lvl, "pattern", None)
                anchor = getattr(lvl, "pattern_anchor", None)
                if pattern and anchor and getattr(pattern, "vertices", None):
                    com = pattern_motion.center_of_mass(pattern)
                    com_world = (com[0] + anchor[0], com[1] + anchor[1])

                    # Current displacement from COM → target
                    cur_tgt = self.ui_state.push_target or com_world
                    cur_dx = cur_tgt[0] - com_world[0]
                    cur_dy = cur_tgt[1] - com_world[1]

                    # Step by 1 tile in the requested direction
                    new_dx = cur_dx + dx
                    new_dy = cur_dy + dy

                    # Clamp to max_range if needed
                    max_range = getattr(t.constraints, "max_range", None) if t.constraints else None
                    if max_range is None:
                        max_range = 5.0
                    dist = (new_dx * new_dx + new_dy * new_dy) ** 0.5
                    if dist > max_range and dist > 0:
                        scale = max_range / dist
                        new_dx *= scale
                        new_dy *= scale

                    tgt = (com_world[0] + new_dx, com_world[1] + new_dy)
                    self.ui_state.push_target = tgt

                    # Update preview geometry
                    self.ui_state.push_preview = pattern_motion.build_push_preview(
                        pattern,
                        anchor,
                        tgt,
                        self.ui_state.push_rotation,
                        max_range,
                    )

                    # Keep the tile highlight roughly on the target
                    tx = int(round(tgt[0]))
                    ty = int(round(tgt[1]))
                    if game.world.in_bounds(tx, ty):
                        t.cursor_tile = (tx, ty)
                        self.ui_state.target_cursor = (tx, ty)

                # Always swallow movement while in push-mode targeting
                return


        # ------------------------------------------------------------
        # Look targeting mode (tile-based inspect cursor)
        # ------------------------------------------------------------
        if in_look_mode and t:
            if kind == "move" and vec is not None:
                tx, ty = t.cursor_tile or game.actors[game.player_id].pos
                dx, dy = vec
                nt = (tx + dx, ty + dy)
                if game.world.in_bounds(*nt):
                    t.cursor_tile = nt
                    self.ui_state.target_cursor = nt
                # Swallow movement so the player never walks in look mode.
                return

            # Swallow any 'move' commands even if we didn’t step (e.g. boundary).
            if kind == "move":
                return

        # ------------------------------------------------------------
        # 5) Ability bar: page cycling + hotkeys + quick 'f'
        # ------------------------------------------------------------

        def _page_bar(bar, forward: bool) -> None:
            """Cycle ability bar page and snap selection to first slot on that page."""
            if forward:
                bar.next_page()
            else:
                bar.prev_page()
            start = bar.page * bar.page_size
            if 0 <= start < len(bar.order):
                bar.selected_index = start
                act = bar.action_at_index(start)
                if act:
                    bar.active_action = act

        # Page cycling: PgUp/PgDn/Tab switch ability bar pages
        if kind == "ability_page_prev":
            _page_bar(bar, forward=False)
            return

        if kind == "ability_page_next":
            _page_bar(bar, forward=True)
            return

        if kind == "ability_hotkey" and cmd.hotkey is not None:
            hk = cmd.hotkey
            vis = bar.visible_abilities()

            # Dynamic page-local hotkeys: 1..N for the current page.
            for idx, ability in enumerate(vis):
                # Keep the model's hotkey in sync with row number, so
                # the renderer's labels match this logic.
                if hasattr(ability, "hotkey"):
                    ability.hotkey = idx + 1

                if idx + 1 == hk:
                    bar.set_active(ability.action)
                    self._begin_action_from_def(game, ability)
                    return
            return

        # ------------------------------------------------------------
        # 6 1/2) Mouse input (click / move / wheel)
        # ------------------------------------------------------------

        # Mouse hover: update tile/vertex cursor & aim preview.
        if kind == "mouse_move" and cmd.mouse_pos is not None:
            self._update_hover_from_mouse(
                game,
                renderer,
                renderer._to_surface(cmd.mouse_pos),
            )
            return

        # Mouse wheel controls zoom or activate_all radius.
        if kind == "mouse_wheel":
            if cmd.wheel_y:
                # If hovering over log panel, scroll log instead of zoom.
                sx, sy = renderer._to_surface(pygame.mouse.get_pos())
                log_x0 = renderer.width - renderer.log_panel_width
                log_y0 = renderer.top_bar_height
                log_y1 = renderer.height - renderer.ability_bar_height
                if sx >= log_x0 and log_y0 <= sy < log_y1:
                    try:
                        renderer.scroll_log(game, delta_lines=cmd.wheel_y)
                    except Exception:
                        pass
                    return
                if push_mode:
                    delta_deg = 15 if cmd.wheel_y > 0 else -15
                    ui.push_rotation = (ui.push_rotation + delta_deg) % 360
                    if ui.push_target and t and t.constraints:
                        lvl = game._level()
                        pattern = getattr(lvl, "pattern", None)
                        anchor = getattr(lvl, "pattern_anchor", None)
                        max_range = getattr(t.constraints, "max_range", 5.0)
                        if pattern and anchor and pattern.vertices:
                            ui.push_preview = pattern_motion.build_push_preview(
                                pattern,
                                anchor,
                                ui.push_target,
                                ui.push_rotation,
                                max_range,
                            )
                else:
                    active_name = bar.active_action
                    spec = None
                    if active_name:
                        try:
                            action_def = get_action(active_name)
                            spec = getattr(action_def, "targeting", None)
                        except KeyError:
                            spec = None

                    if spec and spec.radius_param:
                        delta = 1 if cmd.wheel_y > 0 else -1
                        changed, msg = game.adjust_param(
                            active_name,
                            spec.radius_param,
                            delta,
                        )
                        if not changed and delta > 0 and msg:
                            renderer._set_flash(msg)
                        self._refresh_aim_prediction(game)
                    else:
                        renderer._change_zoom(
                            cmd.wheel_y,
                            renderer._to_surface(pygame.mouse.get_pos()),
                        )
            return

        # Mouse click drives target confirm, ability bar, config, placement, and click-to-move.
        if kind == "mouse_click" and cmd.mouse_pos is not None and cmd.mouse_button == 1:
            # If we’re in target mode, treat click as confirm after updating hover.
            if in_target_mode:
                self._update_hover_from_mouse(
                    game,
                    renderer,
                    renderer._to_surface(cmd.mouse_pos),
                )
                if t and t.kind == "look":
                    self._confirm_look(game, renderer, manager)
                else:
                    self.confirm_target(game)
                return

            mx, my = renderer._to_surface(cmd.mouse_pos)

            # Ability bar page arrows.
            bar_view = getattr(renderer, "ability_bar_view", None)
            if bar_view is not None:
                prev_rects = []
                next_rects = []
                # Support multiple arrow hitboxes (above/below on both sides).
                if hasattr(bar_view, "page_prev_rects"):
                    prev_rects.extend(bar_view.page_prev_rects)
                if hasattr(bar_view, "page_next_rects"):
                    next_rects.extend(bar_view.page_next_rects)
                if bar_view.page_prev_rect:
                    prev_rects.append(bar_view.page_prev_rect)
                if bar_view.page_next_rect:
                    next_rects.append(bar_view.page_next_rect)

                if any(r.collidepoint(mx, my) for r in prev_rects):
                    _page_bar(bar, forward=False)
                    return
                if any(r.collidepoint(mx, my) for r in next_rects):
                    _page_bar(bar, forward=True)
                    return

            # Open ability reorder manager.
            if (
                bar_view is not None
                and bar_view.abilities_button_rect
                and bar_view.abilities_button_rect.collidepoint(mx, my)
            ):
                game.ability_reorder_open = True
                if bar.active_action and bar.active_action in bar.order:
                    bar.selected_index = bar.order.index(bar.active_action)
                    bar.page = bar.selected_index // bar.page_size if bar.page_size else 0
                return

            # Ability bar buttons: rects are attached to Ability instances by the AbilityBarRenderer.
            for ability in bar.visible_abilities():
                rect = getattr(ability, "rect", None)
                if rect and rect.collidepoint(mx, my):
                    bar.set_active(ability.action)

                    plus_rect = getattr(ability, "plus_rect", None)
                    minus_rect = getattr(ability, "minus_rect", None)
                    gear_rect = getattr(ability, "gear_rect", None)

                    # +/- param tweak using sub-button metadata.
                    if plus_rect and plus_rect.collidepoint(mx, my):
                        from edgecaster.systems.actions import action_sub_buttons

                        for meta in action_sub_buttons(ability.action):
                            if (
                                meta.kind == "param_delta"
                                and (meta.delta or 0) > 0
                                and meta.param_key
                            ):
                                changed, msg = game.adjust_param(
                                    ability.action,
                                    meta.param_key,
                                    meta.delta,
                                )
                                if not changed and msg:
                                    renderer._set_flash(msg)
                                self._refresh_aim_prediction(game)
                                break
                        return

                    if minus_rect and minus_rect.collidepoint(mx, my):
                        from edgecaster.systems.actions import action_sub_buttons

                        for meta in action_sub_buttons(ability.action):
                            if (
                                meta.kind == "param_delta"
                                and (meta.delta or 0) < 0
                                and meta.param_key
                            ):
                                changed, _ = game.adjust_param(
                                    ability.action,
                                    meta.param_key,
                                    meta.delta,
                                )
                                self._refresh_aim_prediction(game)
                                break
                        return

                    # Gear opens config overlay (still generic).
                    if gear_rect and gear_rect.collidepoint(mx, my):
                        _set_ui("config_open", True)
                        _set_ui("config_action", ability.action)
                        _set_ui("config_selection", 0)
                        return

                    # Main ability click: delegate to Action metadata.
                    self._begin_action_from_def(game, ability)
                    return

            # Map / world clicks.
            tx = int((mx - renderer.origin_x) // renderer.tile)
            ty = int((my - renderer.origin_y) // renderer.tile)
            if not game.world.in_bounds(tx, ty):
                return

            # Terminus placement via click (legacy).
            if getattr(game, "awaiting_terminus", False):
                _set_ui("target_cursor", (tx, ty))
                game.try_place_terminus((tx, ty))
                return

            # Default: click-to-move / stairs / wait.
            player = game.actors[game.player_id]
            px, py = player.pos
            dx = tx - px
            dy = ty - py

            if tx == px and ty == py:
                # Clicked on the player: use stairs if present, otherwise wait.
                tile = game.world.get_tile(tx, ty) if hasattr(game, "world") else None
                glyph = getattr(tile, "glyph", None) if tile is not None else None

                if glyph == ">":
                    # Stairs down
                    if hasattr(game, "use_stairs_down"):
                        game.use_stairs_down()
                elif glyph == "<":
                    # Stairs up
                    if hasattr(game, "use_stairs_up"):
                        game.use_stairs_up()
                else:
                    # No stairs here: treat click-on-self as a wait.
                    if hasattr(game, "queue_player_wait"):
                        game.queue_player_wait()
            elif max(abs(dx), abs(dy)) == 1:
                # Clicked on an adjacent tile: move there.
                game.queue_player_move((int(dx), int(dy)))
            return


        # ------------------------------------------------------------
        # 6) High-level game actions (non-movement)
        # ------------------------------------------------------------

        if kind == "examine":
            if not in_aim_mode:
                if hasattr(game, "describe_current_tile"):
                    game.describe_current_tile()
            return

        if kind == "look_action":
            # Trigger the 'look' Action via the central Action entry point.
            # This will read the Action's TargetingSpec (kind="look", mode="look")
            # and enter look-style TargetMode.
            self._begin_action_from_def(game, "look")
            return

        if kind == "pickup":
            if not in_aim_mode:
                if hasattr(game, "player_pick_up"):
                    game.player_pick_up()
            return

        if kind == "possess_nearest":
            level = game._level()
            player = level.actors.get(game.player_id)
            if player is not None:
                px, py = player.pos
                best_id = None
                best_d2 = 1e18
                for actor in level.actors.values():
                    if not actor.alive or actor.id == game.player_id:
                        continue
                    ax, ay = actor.pos
                    dx = ax - px
                    dy = ay - py
                    d2 = dx * dx + dy * dy
                    if d2 < best_d2:
                        best_d2 = d2
                        best_id = actor.id
                if best_id is not None:
                    game.possess_actor(best_id)
            return

        if kind == "open_inventory":
            if not in_aim_mode:
                setattr(game, "inventory_requested", True)
                renderer.quit_requested = True
            return

        if kind == "yawp":
            if hasattr(game, "queue_player_action"):
                game.queue_player_action("yawp")
            else:
                game.log.add("You yawp! 'Yawp!'")
            return

        if kind == "wait":
            if hasattr(game, "queue_player_wait"):
                game.queue_player_wait()
            return

        if kind == "stairs_down":
            if hasattr(game, "use_stairs_down"):
                game.use_stairs_down()
            return

        if kind == "stairs_up_or_map":
            tile = game.world.get_tile(*game.actors[game.player_id].pos)
            zone = getattr(game, "zone_coord", getattr(game, "zone", (0, 0, game.level_index)))
            depth = zone[2] if len(zone) > 2 else getattr(game, "level_index", 0)
            if depth == 0 and (not tile or tile.glyph != "<"):
                game.map_requested = True
                renderer.quit_requested = True
                return
            if hasattr(game, "use_stairs_up"):
                game.use_stairs_up()
            return

        if kind == "open_fractal_editor":
            from .fractal_editor_scene import FractalEditorState

            game.fractal_editor_state = FractalEditorState()  # default rect grid
            setattr(game, "fractal_editor_requested", True)
            renderer.quit_requested = True
            return

        if kind == "talk":
            convo = game.talk_start() if hasattr(game, "talk_start") else None
            if convo:
                title = convo.get("name", "Conversation") if isinstance(convo, dict) else "Conversation"
                lines = []
                if isinstance(convo, dict):
                    lines = convo.get("lines", [])
                body = "\n".join(lines) if lines else ""
                choices = convo.get("choices", ["Continue..."]) if isinstance(convo, dict) else ["Continue..."]
                npc_id = convo.get("npc_id") if isinstance(convo, dict) else None

                def on_choice(idx: int, mgr) -> None:
                    choice = choices[idx] if 0 <= idx < len(choices) else None
                    if hasattr(game, "talk_complete"):
                        summary = game.talk_complete(npc_id, choice)
                        if summary:
                            game.log.add(summary)

                manager.push_scene(
                    UrgentMessageScene(
                        game,
                        body or title,
                        title=title,
                        choices=choices,
                        on_choice=on_choice,
                    )
                )
            else:
                game.log.add("No one nearby to talk to.")
            return

        # ------------------------------------------------------------
        # 7) Movement (no special modes active)
        # ------------------------------------------------------------

        if kind == "move" and vec is not None:
            if hasattr(game, "queue_player_move"):
                game.queue_player_move(vec)
            return

        # ------------------------------------------------------------
        # 8) Default confirm: trigger current ability
        # ------------------------------------------------------------

        if kind == "confirm":
            vis = bar.visible_abilities()
            if not vis:
                return

            ability = None
            if bar.active_action:
                for ab in vis:
                    if ab.action == bar.active_action:
                        ability = ab
                        break
            if ability is None:
                ability = vis[0]

            self._begin_action_from_def(game, ability)
            return

        # Any other kinds are currently ignored.

    # ------------------------------------------------------------------ #
    # Central ability entry point
    # ------------------------------------------------------------------ #
    def _begin_action_from_def(self, game: Game, ability) -> None:
        """
        Central entry point for invoking an ability from the UI.

        - Looks up the ActionDef.
        - If the action is non-targeted, fires immediately.
        - If the action has targeting metadata, enters unified TargetMode.
        """
        action_name = getattr(ability, "action", ability)
        try:
            action_def = get_action(action_name)
        except KeyError:
            trigger_ability_effect(game, action_name)
            return

        spec = getattr(action_def, "targeting", None)

        # No targeting metadata: fire immediately.
        if not spec or not spec.kind:
            self.ui_state.aim_action = None
            self._refresh_aim_prediction(game)
            trigger_ability_effect(game, action_name)
            return

        constraints = TargetConstraints(
            max_range=spec.max_range,
            neighbor_depth_param=spec.neighbor_depth_param,
            use_param_radius=getattr(spec, "radius_param", spec.use_param_radius if hasattr(spec, "use_param_radius") else None),
        )

        # Enter unified TargetMode for this action.
        self.begin_target_mode(
            game,
            action=action_name,
            kind=spec.kind,           # "tile" or "vertex" or "look" or "position"
            mode=spec.mode,           # "terminus", "aim", etc.
            constraints=constraints,
        )
