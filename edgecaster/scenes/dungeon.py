from __future__ import annotations

import threading
import pygame

from .base import Scene
from edgecaster.game import Game
from .urgent_message_scene import UrgentMessageScene
from .game_input import GameInput, GameCommand
from edgecaster.systems.abilities import (
    build_abilities,
    compute_abilities_signature,
    trigger_ability_effect,
)


class DungeonScene(Scene):
    """The main roguelike dungeon scene."""

    uses_live_loop = True

    def __init__(self) -> None:
        # Keep the Game instance across pauses/inventory.
        self.game: Game | None = None
        # Scene-level input mapper for "pure game" actions
        self.input = GameInput()
        self._started = False
        self._old_urgent_cb = None

    # ------------------------------------------------------------------ #
    # Live-loop hooks
    def handle_event(self, event, manager: "SceneManager") -> None:  # type: ignore[name-defined]
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
                        game_ref.world_map_cache = {"surface": surf, "view": view, "key": (width, height, wm.span)}
                        game_ref.world_map_ready = True
                    except Exception:
                        game_ref.world_map_ready = False
                    finally:
                        game_ref.world_map_rendering = False

                threading.Thread(target=worker, args=(self.game, renderer.width, renderer.height), daemon=True).start()

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
                renderer.abilities_signature = None

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
    # Command handling (unchanged from legacy loop)
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

        kind = cmd.kind
        key = cmd.raw_key
        vec = cmd.vector

        in_terminus_mode = bool(getattr(game, "awaiting_terminus", False))
        in_aim_mode = renderer.aim_action in ("activate_all", "activate_seed")

        # ------------------------------------------------------------
        # 0) Global-ish keys: Escape, fullscreen, help
        # ------------------------------------------------------------

        if kind == "escape":
            # Mirror old renderer logic, minus legacy dialog:
            if in_terminus_mode:
                game.awaiting_terminus = False
            elif renderer.config_open:
                renderer.config_open = False
            else:
                # Normal ESC in the dungeon: request pause
                renderer.pause_requested = True
                renderer.quit_requested = True
            return


        if kind == "toggle_fullscreen":
            renderer.toggle_fullscreen()
            return

        if kind == "show_help":
            # Only if we're not in some special modal state
            if (
                not renderer.config_open
                and not in_terminus_mode
                and not in_aim_mode
            ):
                if hasattr(game, "show_help"):
                    game.show_help()
            return


        # ------------------------------------------------------------
        # 1) Dialog overlay (always takes precedence while open) !! LEGACY, REMOVED !!
        # ------------------------------------------------------------



        # ------------------------------------------------------------
        # 2) Config overlay (always takes precedence while open)
        # ------------------------------------------------------------

        if renderer.config_open and renderer.config_action:
            params = game.param_view(renderer.config_action)

            if key in (pygame.K_RETURN, pygame.K_SPACE):
                renderer.config_open = False
                return

            if key == pygame.K_UP:
                renderer.config_selection = (renderer.config_selection - 1) % max(1, len(params))
                return

            if key == pygame.K_DOWN:
                renderer.config_selection = (renderer.config_selection + 1) % max(1, len(params))
                return

            if key in (pygame.K_LEFT, pygame.K_RIGHT):
                if params:
                    param_key = params[renderer.config_selection]["key"]
                    delta = 1 if key == pygame.K_RIGHT else -1
                    changed, msg = game.adjust_param(renderer.config_action, param_key, delta)
                    # msg is available if you want to surface it later
                return

            # Other commands do nothing while config overlay is open
            return

        # ------------------------------------------------------------
        # 3) Terminus targeting mode
        # ------------------------------------------------------------
        #
        # While in this mode:
        # - arrow/WASD/etc ("move") moves the target cursor
        # - ENTER / SPACE ("confirm") places at the current cursor
        # - mouse commands are *not* swallowed here; they fall through
        #   to the generic mouse handler below so clicks can place the
        #   terminus directly on the map.

        if in_terminus_mode:
            if kind == "move" and vec is not None:
                tx, ty = renderer.target_cursor
                dx, dy = vec
                nt = (tx + dx, ty + dy)
                if game.world.in_bounds(*nt):
                    renderer.target_cursor = nt
                return

            if kind == "confirm":
                game.try_place_terminus(renderer.target_cursor)
                return

            # Let mouse_* commands pass through to the mouse handler.
            if kind not in ("mouse_click", "mouse_move", "mouse_wheel"):
                # Everything else (examine, pickup, etc.) is ignored
                # while we're choosing a terminus.
                return


        # 4) Aiming mode (activate_all / activate_seed) confirm

        if in_aim_mode and kind == "confirm":
            target_idx = renderer._current_hover_vertex(game)
            if target_idx is not None and renderer.aim_action in ("activate_all", "activate_seed"):
                trigger_ability_effect(
                    game,
                    renderer.aim_action,
                    hover_vertex=target_idx,
                )
            renderer.aim_action = None
            return

        # ------------------------------------------------------------
        # 5) Ability bar: hotkeys + quick 'f'
        # ------------------------------------------------------------

        if kind == "ability_hotkey" and cmd.hotkey is not None:
            hk = cmd.hotkey
            for idx, ability in enumerate(renderer.abilities):
                if ability.hotkey == hk:
                    renderer.current_ability_index = idx

                    if ability.action == "place":
                        renderer.target_cursor = game.actors[game.player_id].pos
                        if hasattr(game, "begin_place_mode"):
                            game.begin_place_mode()
                        renderer.aim_action = None

                    else:
                        # Aim-style abilities set aim_action and wait for confirm / click
                        if ability.action in ("activate_all", "activate_seed"):
                            renderer.aim_action = ability.action
                        else:
                            renderer.aim_action = None
                            # Immediate abilities: go straight to the shared effect function
                            trigger_ability_effect(game, ability.action)

                    return
            return

        if kind == "quick_activate_all":
            # If we're already in activate_all aim mode, treat this as confirm
            if renderer.aim_action == "activate_all":
                target_idx = renderer._current_hover_vertex(game)
                if target_idx is not None:
                    trigger_ability_effect(
                        game,
                        "activate_all",
                        hover_vertex=target_idx,
                    )
                renderer.aim_action = None
            else:
                # Start aiming from current mouse position
                renderer.aim_action = "activate_all"
                renderer._update_hover(game, renderer._to_surface(pygame.mouse.get_pos()))
            return



        # ------------------------------------------------------------
        # 6 1/2) Mouse input (click / move / wheel)
        # ------------------------------------------------------------

        # Mouse hover updates the fractal aim hover.
        if kind == "mouse_move" and cmd.mouse_pos is not None:
            renderer._update_hover(game, renderer._to_surface(cmd.mouse_pos))
            return

        # Mouse wheel controls zoom around current cursor.
        if kind == "mouse_wheel":
            if cmd.wheel_y:
                renderer._change_zoom(cmd.wheel_y, renderer._to_surface(pygame.mouse.get_pos()))
            return

        # Mouse click drives ability bar, config overlay, placement, and click-to-move.
        if kind == "mouse_click" and cmd.mouse_pos is not None and cmd.mouse_button == 1:
            mx, my = renderer._to_surface(cmd.mouse_pos)

            # Config overlay: click anywhere closes it for now.
            if renderer.config_open and renderer.config_action:
                renderer.config_open = False
                return

            # Ability bar page arrows.
            if getattr(renderer, "page_prev_rect", None) and renderer.page_prev_rect and renderer.page_prev_rect.collidepoint(mx, my):
                if renderer.ability_page > 0:
                    renderer.ability_page -= 1
                return

            if getattr(renderer, "page_next_rect", None) and renderer.page_next_rect and renderer.page_next_rect.collidepoint(mx, my):
                items_per_page = 12
                total_pages = max(1, (len(renderer.abilities) + items_per_page - 1) // items_per_page)
                if renderer.ability_page < total_pages - 1:
                    renderer.ability_page += 1
                return

            # Ability bar buttons.
            for idx, ability in enumerate(renderer.abilities):
                if ability.rect and ability.rect.collidepoint(mx, my):
                    renderer.current_ability_index = idx

                    # +/- radius tweak for activate_all
                    if ability.plus_rect and ability.plus_rect.collidepoint(mx, my):
                        changed, msg = game.adjust_param("activate_all", "radius", 1)
                        if not changed and msg:
                            renderer._set_flash(msg)
                        renderer.abilities_signature = None
                        return

                    if ability.minus_rect and ability.minus_rect.collidepoint(mx, my):
                        changed, _ = game.adjust_param("activate_all", "radius", -1)
                        renderer.abilities_signature = None
                        return

                    # Gear opens config overlay.
                    if ability.gear_rect and ability.gear_rect.collidepoint(mx, my):
                        renderer.config_open = True
                        renderer.config_action = ability.action
                        renderer.config_selection = 0

                    # Placement ability: enter terminus mode at player.
                    elif ability.action == "place":
                        renderer.target_cursor = game.actors[game.player_id].pos
                        if hasattr(game, "begin_place_mode"):
                            game.begin_place_mode()
                        renderer.aim_action = None

                    else:
                        # Aim-style abilities set aim_action and wait for confirm/click.
                        if ability.action in ("activate_all", "activate_seed"):
                            renderer.aim_action = ability.action
                        else:
                            renderer.aim_action = None
                            # Immediate abilities fire immediately.
                            trigger_ability_effect(game, ability.action)

                    return

            # Map / world clicks.
            tx = int((mx - renderer.origin_x) // renderer.tile)
            ty = int((my - renderer.origin_y) // renderer.tile)
            if not game.world.in_bounds(tx, ty):
                return

            # Terminus placement via click.
            if getattr(game, "awaiting_terminus", False):
                renderer.target_cursor = (tx, ty)
                game.try_place_terminus((tx, ty))
                return

            # Aim-mode click to fire activate_all / activate_seed.
            if renderer.aim_action in ("activate_all", "activate_seed"):
                # Convert click to world-coordinates (in pattern space) and pick nearest vertex
                wx = (mx - renderer.origin_x) / renderer.tile
                wy = (my - renderer.origin_y) / renderer.tile
                target_idx = game.nearest_vertex((wx, wy))
                if target_idx is not None:
                    trigger_ability_effect(
                        game,
                        renderer.aim_action,
                        hover_vertex=target_idx,
                    )
                renderer.aim_action = None
                return


            # Default: click-to-move / use stairs.
            player = game.actors[game.player_id]
            px, py = player.pos
            dx = tx - px
            dy = ty - py
            if tx == px and ty == py:
                game.use_stairs()
            elif max(abs(dx), abs(dy)) == 1:
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
            setattr(game, "fractal_editor_requested", True)
            renderer.quit_requested = True
            return

        if kind == "talk":
            # Temporary: reuse yawp behaviour until scene-based dialogue is wired in.
            if hasattr(game, "queue_player_action"):
                game.queue_player_action("yawp")
            else:
                game.log.add("You yawp, but in a civilized manner.")
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
            ability = renderer.abilities[renderer.current_ability_index]

            if ability.action == "place":
                renderer.target_cursor = game.actors[game.player_id].pos
                game.begin_place_mode()
                renderer.aim_action = None
            else:
                if ability.action in ("activate_all", "activate_seed"):
                    renderer.aim_action = ability.action
                else:
                    renderer.aim_action = None
                    trigger_ability_effect(game, ability.action)

            return

        # Any other kinds are currently ignored.
