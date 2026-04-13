from __future__ import annotations

import threading
import math
import pygame
from dataclasses import dataclass
from typing import Literal, Optional

from .base import Scene
from edgecaster.game import Game
from .urgent_message_scene import UrgentMessageScene
from .game_input import GameInput, GameCommand
from edgecaster.systems.abilities import trigger_ability_effect
from edgecaster.systems.targeting import predict_aim_preview
from edgecaster.systems.previews import build_action_preview, is_previewable_action
from edgecaster.patterns import motion as pattern_motion
from edgecaster.ui.ability_bar import AbilityBarState
from edgecaster.systems.actions import get_action, describe_entity_for_look
from edgecaster.visuals import VisualProfile, apply_visual_panel  
from edgecaster.ui.widgets import WidgetContext, VBox, HBox, LabelWidget, ButtonWidget, ListWidget
from edgecaster.systems import rune_audio as rune_audio_system



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
    """Scene-owned view-model for UI state previously held by the renderer.

    YOGA REFACTOR NOTE:
    - target_cursor_abs should be the CANONICAL source of truth for cursor position.
    - target_cursor (zone-local) should be DERIVED from target_cursor_abs.
    - DONE: target_cursor_abs is canonical; target_cursor derived via _set_cursor_abs()
    - See vision_documents/the_yoga.txt "UI & Targeting Yoga: One Spatial Contract"
    """
    target: TargetState | None = None
    target_cursor: tuple[int, int] = (0, 0)  # Derived from target_cursor_abs via _set_cursor_abs
    target_cursor_abs: tuple[int, int] | None = None  # YOGA: Canonical (ABS) cursor position
    aim_action: str | None = None
    hover_vertex: int | None = None
    hover_neighbors: list[int] | None = None
    config_open: bool = False
    config_action: str | None = None
    config_selection: int = 0
    aim_prediction: object | None = None  # computed preview info for aim overlays
    action_preview: object | None = None  # action outcome preview (Alt+click)
    push_target: tuple[float, float] | None = None
    push_rotation: float = 0.0
    push_preview: object | None = None
    hovered_action: str | None = None  # current action hovered in ability bar
    seal_snap_active: bool = False
    seal_root_hint: tuple[int, int] | None = None
    # --- debug widget PoC state (safe, pure data) ---
    debug_widget_visible: bool = False
    debug_clicks: int = 0
    debug_selected_index: int = 0


    def __post_init__(self) -> None:
        if self.hover_neighbors is None:
            self.hover_neighbors = []


class DungeonScene(Scene):
    """The main roguelike dungeon scene."""

    uses_live_loop = True
    # --- Music declaration ---
    music_playlist = ["harmonic", "majesty", "aire", "kemet", "john_bismuth", "warp_whistle", "hornpipe", "real_boy", "creche", "ice_cave", "dignity", "tick_tock", "dwarf_train"]
    music_loop = True
    music_fade_out_ms = 1200
    music_fade_in_ms = 1200
    
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
        # --- Widget PoC: scene-owned widget tree (view objects) ---
        self._debug_widget_root = HBox(spacing=12, padding=10, valign="top")
        self._debug_list = ListWidget(
            ["alpha", "beta", "gamma", "delta", "epsilon"],
            selected_index=self.ui_state.debug_selected_index,
            on_activate=self._on_debug_list_activate,
        )
        self._debug_right = VBox(spacing=6, padding=6, align="left")
        self._debug_title = LabelWidget("Debug Widget PoC", align="left")
        self._debug_selected = LabelWidget("Selected: alpha", align="left")
        self._debug_button = ButtonWidget("Click me", on_click=self._on_debug_click)
        self._debug_hint = LabelWidget("Tip: F8 toggles this panel", align="left")

        self._debug_right.add_child(self._debug_title)
        self._debug_right.add_child(self._debug_selected)
        self._debug_right.add_child(self._debug_button)
        self._debug_right.add_child(self._debug_hint)

        self._debug_widget_root.add_child(self._debug_list)
        self._debug_widget_root.add_child(self._debug_right)

        # Position and size (a simple overlay box) -- Debut widget clean up later
        self._debug_widget_root.rect = pygame.Rect(12, 120, 420, 170)

        self._pending_door_toggle: dict[tuple[int, int], object] | None = None
        # Scene-level input mapper for "pure game" actions
        # refactor: migrate to a shared input layer; DungeonScene should consume a GameCommand queue only.
        self.input = GameInput()
        self._started = False
        self._old_urgent_cb = None
        # Right-mouse drag camera panning state
        self._rmb_dragging: bool = False
        self._rmb_last_pos: tuple[int, int] | None = None


##debug widget
    def _on_debug_click(self, _btn: ButtonWidget) -> None:
        self.ui_state.debug_clicks += 1
        self._debug_title.text = f"Debug Widget PoC ({self.ui_state.debug_clicks} clicks)"
        # Optional: also log into MessageLog if you want visible proof
        log = getattr(self.game, "log", None)
        if log is not None and hasattr(log, "add"):
            log.add("[debug] ButtonWidget click registered!")

    def _on_debug_list_activate(self, idx: int, item) -> None:
        self.ui_state.debug_selected_index = idx
        self._debug_selected.text = f"Selected: {item}"

    # ------------------------------------------------------------------ #
    # Live-loop hooks

    def _entity_source_px_from_world(
        self,
        renderer,
        world_pos: tuple[int, int] | None,
    ) -> tuple[int, int] | None:
        """Screen pixel for center of an entity at renderer tile coords.

        This matches the same coordinate convention used by the dungeon renderer:
        (tx, ty) are in the *current drawn tile space* (currently the loaded chunk's
        local tile coordinates). This is the same convention InventoryScene uses
        when no explicit source_px is provided (owner.pos).
        """
        if renderer is None or world_pos is None:
            return None
        try:
            tx, ty = int(world_pos[0]), int(world_pos[1])
        except Exception:
            return None
        try:
            ox = float(getattr(renderer, "origin_x", 0.0))
            oy = float(getattr(renderer, "origin_y", 0.0))
        except Exception:
            ox = oy = 0.0
        try:
            tile_px = float(getattr(renderer, "tile_px", float(getattr(renderer, "tile", 1) or 1)))
        except Exception:
            tile_px = 1.0
        if tile_px <= 0:
            tile_px = 1.0
        sx = int(round(ox + (float(tx) + 0.5) * tile_px))
        sy = int(round(oy + (float(ty) + 0.5) * tile_px))
        return (sx, sy)


    def _sync_attention_stage(self, game, renderer) -> None:
        """Stage/unstage micro entities based on current camera view (no time advance)."""
        try:
            if not hasattr(game, "sync_attention_instantiation"):
                return
            if not hasattr(renderer, "get_camera_abs_rect_and_lod"):
                return
            abs_rect, cam_lod = renderer.get_camera_abs_rect_and_lod(game)
            game.sync_attention_instantiation(abs_rect, cam_lod=float(cam_lod))
        except Exception:
            pass





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
            if event.key == pygame.K_F8:
                self.ui_state.debug_widget_visible = not self.ui_state.debug_widget_visible
                return


        elif event.type == pygame.MOUSEBUTTONDOWN:
            # Right mouse: click-and-drag pan (camera)
            if getattr(event, "button", None) == 3:
                self._rmb_dragging = True
                self._rmb_last_pos = getattr(event, "pos", None)
                return

            cmds = self.input.handle_mousebutton(event)
            for cmd in cmds:
                self._handle_command(game, renderer, cmd, manager)

        elif event.type == pygame.MOUSEBUTTONUP:
            if getattr(event, "button", None) == 3:
                self._rmb_dragging = False
                self._rmb_last_pos = None
                return

        elif event.type == pygame.MOUSEMOTION:
            # While right-dragging, pan camera and swallow hover/aim updates.
            if self._rmb_dragging:
                # Convert display-space rel -> logical surface rel
                scale = float(getattr(renderer, "lb_scale", 1.0) or 1.0)
                rx, ry = getattr(event, "rel", (0, 0))
                dx = float(rx) / scale
                dy = float(ry) / scale
                try:
                    renderer.pan_by_px(dx, dy)
                except Exception:
                    # Fallback: direct pan fields if present
                    if hasattr(renderer, "pan_x"):
                        renderer.pan_x += dx
                    if hasattr(renderer, "pan_y"):
                        renderer.pan_y += dy
                    if getattr(renderer, "camera_follow", False):
                        try:
                            ox, oy = renderer.camera_follow_offset_px
                        except Exception:
                            ox, oy = 0.0, 0.0
                        renderer.camera_follow_offset_px = (float(ox) + dx, float(oy) + dy)

                self._sync_attention_stage(game, renderer)

                return

            cmds = self.input.handle_mousemotion(event)
            for cmd in cmds:
                self._handle_command(game, renderer, cmd, manager)

        elif event.type == pygame.MOUSEWHEEL:
            cmds = self.input.handle_mousewheel(event)
            for cmd in cmds:
                self._handle_command(game, renderer, cmd, manager)

        if self.ui_state.debug_widget_visible:
            # Convert display-space mouse coords -> logical surface coords.
            ev = event
            if hasattr(event, "pos"):
                ox, oy = getattr(renderer, "lb_off", (0, 0))
                scale = float(getattr(renderer, "lb_scale", 1.0) or 1.0)
                sx = int((event.pos[0] - ox) / scale)
                sy = int((event.pos[1] - oy) / scale)

                # Make a shallow event with transformed pos
                ev = pygame.event.Event(event.type, {**event.dict, "pos": (sx, sy)})

            ctx = WidgetContext(surface=renderer.surface, game=game, scene=self, renderer=renderer)

            # Keep list selection synced from UIState (state is canonical)
            self._debug_list.selected_index = self.ui_state.debug_selected_index

            # Layout (hitboxes) then allow widget tree to consume the event.
            self._debug_widget_root.layout(ctx)
            if self._debug_widget_root.handle_event(ev, ctx):
                # If list selection changed via hover/click, persist to UIState:
                self.ui_state.debug_selected_index = self._debug_list.selected_index
                return

    def update(self, dt_ms: int, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        game, renderer = self._ensure_game(manager)
        if game is None:
            manager.set_scene(None)
            return

        # Rune audio: keep the current rune pattern expressed as a looped drone.
        # Cheap signature compare; only resynthesizes on change.
        try:
            rune_audio_system.sync_rune_drone(game, manager.audio)
        except Exception:
            pass


        # Legacy flags still respected
        if getattr(renderer, "quit_requested", False) or getattr(renderer, "pause_requested", False):
            renderer.quit_requested = False

        # Process any transitions (death, map, inventory, etc.)
        self._process_transitions(game, renderer, manager)

        # Keep hover previews responsive even when no mouse_move event is emitted.
        try:
            bar_widget = getattr(renderer, "ability_bar_widget", None)
            if bar_widget is not None:
                mx, my = renderer._to_surface(pygame.mouse.get_pos())
                ctx = WidgetContext(surface=renderer.surface, game=game, scene=self, renderer=renderer)
                hovered_action = bar_widget.hover_action((mx, my), ctx)
                self.ui_state.hovered_action = str(hovered_action) if hovered_action else None
                hover_preview_actions = {"energy_kick", "palm_burst", "knife_rune"}
                if hovered_action in hover_preview_actions:
                    self.ui_state.action_preview = build_action_preview(game, str(hovered_action), game.player_id)
                else:
                    current_preview = getattr(self.ui_state, "action_preview", None)
                    if getattr(current_preview, "action", None) in hover_preview_actions:
                        self.ui_state.action_preview = None
            else:
                self.ui_state.hovered_action = None
        except Exception:
            self.ui_state.hovered_action = None

        # Clear Alt-gated previews when Alt is released.
        # Hover-driven previews are not Alt-gated and are cleared by hover logic.
        if self.ui_state.action_preview is not None:
            try:
                preview_action = getattr(self.ui_state.action_preview, "action", None)
                if preview_action not in {"energy_kick", "palm_burst"} and (pygame.key.get_mods() & pygame.KMOD_ALT) == 0:
                    self.ui_state.action_preview = None
            except Exception:
                self.ui_state.action_preview = None



        # Keep aim preview in sync with any param/hover changes.
        self._refresh_aim_prediction(game)

        # Camera follow: keep player anchored each frame (respects manual pan offset).
        if getattr(renderer, "camera_follow", False):
            try:
                renderer.center_camera_on_player(
                    game,
                    snap_zoom=False,
                    target_offset_px=getattr(renderer, "camera_follow_offset_px", (0.0, 0.0)),
                )
            except Exception:
                pass

    def render(self, renderer, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        if self.game is None:
            return

        # Use the per-scene visual profile if present.
        visual = getattr(self, "visual_profile", None) or VisualProfile()

        # If the profile is “identity”, just render normally.
        if (
            visual.scale_x == 1.0 and visual.scale_y == 1.0
            and visual.offset_x == 0.0 and visual.offset_y == 0.0
            and visual.angle == 0.0
            and visual.alpha == 1.0
            and not visual.flip_x and not visual.flip_y
        ):
            # Normal path: let the renderer draw and present as usual.
            old_present = getattr(renderer, "_present", None)
            try:
                if old_present is not None:
                    renderer._present = lambda: None  # type: ignore[assignment]

                renderer.draw_dungeon_frame(self.game)

                if self.ui_state.debug_widget_visible:
                    ctx = WidgetContext(surface=renderer.surface, game=self.game, scene=self, renderer=renderer)
                    self._debug_list.selected_index = self.ui_state.debug_selected_index
                    self._debug_widget_root.layout(ctx)
                    self._debug_widget_root.draw(ctx)

            finally:
                if old_present is not None:
                    renderer._present = old_present  # type: ignore[assignment]

            # Present exactly once.
            if hasattr(renderer, "_present"):
                renderer._present()
            else:
                renderer.present()
            return

        # Otherwise: draw the dungeon into an off-screen panel and apply the transform.
        width, height = renderer.width, renderer.height
        panel = pygame.Surface((width, height), pygame.SRCALPHA)

        # Temporarily redirect the renderer to draw into the panel, and
        # *suppress* its own _present() so we don't present the unrotated frame.
        old_surface = renderer.surface
        old_present = getattr(renderer, "_present", None)

        try:
            renderer.surface = panel

            if old_present is not None:
                # Monkey-patch _present to a no-op while we render into the panel.
                renderer._present = lambda: None  # type: ignore[assignment]

            renderer.draw_dungeon_frame(self.game)
            if self.ui_state.debug_widget_visible:
                ctx = WidgetContext(surface=panel, game=self.game, scene=self, renderer=renderer)
                self._debug_list.selected_index = self.ui_state.debug_selected_index
                self._debug_widget_root.layout(ctx)
                self._debug_widget_root.draw(ctx)

        finally:
            # Restore renderer surface and _present implementation.
            renderer.surface = old_surface
            if old_present is not None:
                renderer._present = old_present  # type: ignore[assignment]

        # Treat the entire logical screen as the “window rect” for this scene.
        window_rect = pygame.Rect(0, 0, width, height)

        # Apply the scene's visual profile, blitting the transformed panel
        # into the real logical surface.
        apply_visual_panel(
            base_surface=renderer.surface,
            logical_surface=panel,
            window_rect=window_rect,
            visual=visual,
        )

        # Finally, present the transformed frame to the actual display.
        if hasattr(renderer, "_present"):
            renderer._present()
        else:
            renderer.present()



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
            # Ensure any trial zone grants/layout are applied now that UI exists.
            try:
                from edgecaster.systems import seal_trials
                seal_trials.sync_zone_trial(self.game, self.game._level(), self.game.zone_coord)
            except Exception:
                pass
            try:
                from edgecaster.systems import rune_anchor_sieges

                rune_anchor_sieges.sync_zone_siege(
                    self.game,
                    self.game._level(),
                    self.game.zone_coord,
                )
            except Exception:
                pass

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

        # If a branch editor result is waiting, absorb it into the gardener pattern.
        if getattr(manager, "branch_edit_result", None):
            res = manager.branch_edit_result
            manager.branch_edit_result = None
            if isinstance(res, dict) and res.get("vertices") and len(res["vertices"]) >= 2:
                game.gardener_branch_pattern = res
                game.character.gardener_branch = res
                if hasattr(game, "ability_bar_state"):
                    game.ability_bar_state.invalidate()

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
                        # Support chained prompts: if the effect installs a new
                        # urgent_choice_effect (e.g. by calling Game.set_urgent),
                        # don't clear it out from under the follow-up popup.
                        before = effect
                        effect(idx, game)
                        if getattr(game, "urgent_choice_effect", None) is before:
                            # Clear it so it doesn't leak to the next popup.
                            game.urgent_choice_effect = None
                stinger_key = None
                stinger_scary = False
                t = (title or "").strip().lower()

                if t == "imps aplenty":
                    stinger_key = "imp_cackle"
                    stinger_scary = True
                elif t == "berry glut":
                    stinger_key = "slot_machine"
                elif t == "holy christ an alligator!":
                    stinger_key = "alligator_sting"
                    stinger_scary = True
                elif t == "beggarly vagrant":
                    stinger_key = "beggarly_vagrant"
                elif t == "level up!":
                    stinger_key = "arpeggio"
                elif t.startswith("you die, by way of"):
                    stinger_key = "cascade"
                    stinger_scary = True



                manager.push_scene(
                    UrgentMessageScene(
                        game,
                        body,
                        title=title,
                        choices=choices,
                        on_choice=handle_choice,
                        stinger_music_key=stinger_key,
                        stinger_scary=stinger_scary,
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
            if hasattr(game, "chakra_requested"):
                game.chakra_requested = False
            if hasattr(game, "blade_editor_requested"):
                game.blade_editor_requested = False
            if hasattr(game, "gods_menu_requested"):
                game.gods_menu_requested = False
            renderer.start_dungeon(game)
            self._started = True

        return game, renderer

    def _process_transitions(self, game: Game, renderer, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        from .main_menu import MainMenuScene
        from .pause_menu_scene import PauseMenuScene
        from .inventory_scene import InventoryScene
        from .quest_scene import QuestScene

        # 0) If an UrgentMessageScene was pushed from inside the game logic,
        #    it will already be on top of the stack. In that case, just yield.
        if manager.scene_stack and isinstance(manager.scene_stack[-1], UrgentMessageScene):
            return

        # 1) Fallback / generalisation for legacy urgent_message flag:
        if getattr(game, "urgent_message", None) and not getattr(game, "urgent_resolved", True):
            body = getattr(game, "urgent_body", None) or game.urgent_message or ""
            title = getattr(game, "urgent_title", "") or ""
            choices = getattr(game, "urgent_choices", None) or ["Continue..."]

            stinger_key = None
            stinger_scary = False
            t = (title or "").strip().lower()

            if t == "imps aplenty":
                stinger_key = "imp_cackle"
            elif t == "berry glut":
                stinger_key = "slot_machine"
            elif t == "holy christ an alligator!":
                stinger_key = "alligator_sting"
                stinger_scary = True

            manager.push_scene(
                UrgentMessageScene(
                    game,
                    body,
                    title=title,
                    choices=choices,
                    stinger_music_key=stinger_key,
                    stinger_scary=stinger_scary,
                )
            )
            return


        # 2) Death -> go back to main menu after the death popup is dismissed.
        #
        # The death popup (UrgentMessageScene) is pushed synchronously during
        # handle_event via the urgent_callback path (combat.py:handle_player_death ->
        # game.set_urgent -> show_urgent).  Without this guard, _process_transitions
        # would immediately call set_scene(MainMenuScene) in the same frame,
        # clearing the popup before the player ever saw it.  We wait here while
        # the popup is on the stack, then proceed to main menu once it is dismissed.
        if not getattr(game, "player_alive", True):
            if manager.scene_stack and isinstance(manager.scene_stack[-1], UrgentMessageScene):
                return
            self.game = None
            manager.current_game = None
            manager.set_scene(MainMenuScene())
            return

        # 3) Inventory requested -> push overlay, keep dungeon scene on stack
        if getattr(game, "inventory_requested", False):
            game.inventory_requested = False
            manager.push_scene(InventoryScene(game))
            return

        # 3a) Chakra management requested -> push chakra selection scene
        if getattr(game, "chakra_requested", False):
            game.chakra_requested = False
            from .chakra_scene import ChakraSelectionScene
            manager.push_scene(ChakraSelectionScene(game=game))
            return

        # 3b) Merchant requested -> push trade overlay
        merchant_id = getattr(game, "merchant_requested", None)
        if merchant_id:
            try:
                game.merchant_requested = None
            except Exception:
                pass

            # We’ve arrived at the merchant UI; clear the transition override.
            try:
                game.pending_music_override_key = None
                game.pending_music_override_playlist = None
            except Exception:
                pass


            from .merchant_scene import MerchantScene

            manager.open_window_scene(
                MerchantScene,
                scale=0.82,
                game=game,
                merchant_actor_id=str(merchant_id),
            )
            return

        # 4) Quest journal requested -> push overlay
        if getattr(game, "quest_journal_requested", False):
            game.quest_journal_requested = False
            manager.push_scene(QuestScene(game))
            return

        # 4b) Factions requested -> push factions overlay
        if getattr(game, "factions_requested", False):
            game.factions_requested = False
            from .factions_scene import FactionsScene
            manager.push_scene(FactionsScene(game))
            return

        # 4c) Gods menu requested -> push gods overlay
        if getattr(game, "gods_menu_requested", False):
            game.gods_menu_requested = False
            from .gods_scene import GodsScene
            manager.push_scene(GodsScene(game))
            return

        # 5) Fractal editor requested -> open editor scene
        if getattr(game, "fractal_editor_requested", False):
            game.fractal_editor_requested = False
            from .fractal_editor_scene import FractalEditorScene, FractalEditorState

            state = getattr(game, "fractal_editor_state", None) or FractalEditorState()
            manager.push_scene(FractalEditorScene(state=state, window_rect=None))
            return

        # 5b) Blade editor requested -> open blade editor scene
        if getattr(game, "blade_editor_requested", False):
            game.blade_editor_requested = False
            from .blade_editor_scene import BladeEditorScene
            try:
                with open("C:/Games/Edgecaster/debug.log", "a", encoding="utf-8") as f:
                    f.write("[dungeon] transition -> BladeEditorScene push\n")
            except Exception:
                pass
            manager.push_scene(BladeEditorScene(game))
            return

        # 5c) Branch editor requested -> open branch editor scene
        if getattr(game, "branch_editor_requested", False):
            game.branch_editor_requested = False
            from .branch_editor_scene import BranchEditorScene, branch_edge_budget

            player = getattr(game, "_player", lambda: None)()
            level = int(getattr(getattr(player, "stats", None), "level", 1) or 1)
            tier, budget = branch_edge_budget(level)
            existing = getattr(game, "gardener_branch_pattern", None)
            manager.push_scene(BranchEditorScene(tier=tier, max_edges=budget, existing=existing))
            return

        # 6) Pause requested -> push pause menu overlay
        if getattr(renderer, "pause_requested", False):
            renderer.pause_requested = False
            manager.push_scene(PauseMenuScene())
            return

        # 7) World map requested -> push world map scene (keep game instance)
        if getattr(game, "map_requested", False):
            game.map_requested = False
            from .world_map_scene import WorldMapScene

            manager.push_scene(WorldMapScene(game, span=16))
            return

        # 8) Camera recenter requested (after zone transition / fast travel)
        if getattr(game, "camera_needs_recenter", False):
            
            game.camera_needs_recenter = False
            try:
                with open("C:/Games/Edgecaster/debug.log", "a") as f:
                    player = game.actors.get(game.player_id)
                    abs_pos = getattr(player, "abs_pos", None) if player else None
                    old_pan = (getattr(renderer, "pan_x", 0), getattr(renderer, "pan_y", 0))
                    f.write(f"[dungeon] Camera recenter triggered, player abs_pos={abs_pos}, zone={game.zone_coord}, old_pan={old_pan}\n")
            except Exception:
                pass
            try:
                renderer.center_camera_on_player(
                    game,
                    snap_zoom=False,
                    target_offset_px=getattr(renderer, "camera_follow_offset_px", (0.0, 0.0)),
                )  # Don't snap zoom, just recenter
                try:
                    with open("C:/Games/Edgecaster/debug.log", "a") as f:
                        new_pan = (getattr(renderer, "pan_x", 0), getattr(renderer, "pan_y", 0))
                        f.write(f"[dungeon] Camera recenter done, new_pan={new_pan}\n")
                except Exception:
                    pass
            except Exception as e:
                try:
                    with open("C:/Games/Edgecaster/debug.log", "a") as f:
                        f.write(f"[dungeon] Camera recenter FAILED: {e}\n")
                except Exception:
                    pass

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

        # Look-style generic tile cursor (ABS-canonical; LOCAL is derived).
        if kind == "look":
            if origin_tile is not None:
                # Compute canonical ABS cursor from the origin actor if possible.
                abs_origin = None
                if actor_id is not None and getattr(game, "actors", None):
                    a = game.actors.get(actor_id)
                    abs_origin = getattr(a, "abs_pos", None)
                if abs_origin is None:
                    abs_origin = game.abs_from_zone_local(game.zone_coord, origin_tile)
                # YOGA: ABS is canonical; local is derived
                self._set_cursor_abs(game, abs_origin)
                tstate.cursor_tile = self.ui_state.target_cursor
            return


        # Backwards-compat bridge for rune terminus targeting:
        if kind == "tile" and mode == "terminus":
            if hasattr(game, "begin_place_mode"):
                game.begin_place_mode()
            game.awaiting_terminus = True
            # YOGA: Compute ABS from origin, derive local
            if origin_tile is not None:
                abs_origin = game.abs_from_zone_local(game.zone_coord, origin_tile)
                self._set_cursor_abs(game, abs_origin)
            else:
                self._set_cursor_abs(game, None)

        if kind == "vertex":
            # Enter generic vertex targeting (e.g. activate_all / activate_seed).
            self.ui_state.aim_action = action

            # Seed hover at nearest vertex to the origin tile (usually the player).
            # NOTE: origin_tile is in current-zone local coords, which matches nearest_vertex() space.
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
                local_tile = (int(round(com_world[0])), int(round(com_world[1])))
                # YOGA: Compute ABS from zone-local COM
                abs_tile = game.abs_from_zone_local(game.zone_coord, local_tile)
                self._set_cursor_abs(game, abs_tile)
                tstate.cursor_tile = self.ui_state.target_cursor
                self.ui_state.push_target = com_world
                self.ui_state.push_rotation = 0.0
                self.ui_state.push_preview = pattern_motion.build_push_preview(
                    pattern, anchor, com_world, 0.0, max_range
                )
            elif origin_tile is not None:
                # YOGA: Compute ABS from origin
                abs_origin = game.abs_from_zone_local(game.zone_coord, origin_tile)
                self._set_cursor_abs(game, abs_origin)
                tstate.cursor_tile = self.ui_state.target_cursor
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
        self.ui_state.seal_snap_active = False
        self.ui_state.seal_root_hint = None
        self.ui_state.target_cursor_abs = None

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
            if getattr(t, "mode", None) == "terminus":
                # Terminus placement is ABS-canonical; allow off-zone cursor.
                abs_tgt = getattr(self.ui_state, "target_cursor_abs", None)

                if abs_tgt is None and t.cursor_tile is not None:
                    try:
                        abs_tgt = game.abs_from_zone_local(game.zone_coord, t.cursor_tile)
                    except Exception:
                        abs_tgt = None

                if abs_tgt is None:
                    return

                if hasattr(game, "try_place_terminus"):
                    game.try_place_terminus((int(abs_tgt[0]), int(abs_tgt[1])))
                # Exit targeting mode after successful placement
                self.cancel_target_mode(game)
                return

            # Non-terminus tile targeting still requires a concrete local tile for now.
            if t.cursor_tile is None:
                return

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
        if not t:
            self.cancel_target_mode(game)
            return

        # Prefer canonical ABS cursor; fall back to deriving from local if needed.
        abs_tile = getattr(self.ui_state, "target_cursor_abs", None)
        if abs_tile is None and t.cursor_tile is not None:
            try:
                abs_tile = game.abs_from_zone_local(getattr(game, "zone_coord", (0, 0, 0)), t.cursor_tile)
            except Exception:
                abs_tile = None

        if abs_tile is None:
            # As an absolute last resort, look at the player.
            a = game.actors[game.player_id]
            abs_tile = getattr(a, "abs_pos", None)
            if abs_tile is None:
                self.cancel_target_mode(game)
                return

        # Resolve ABS -> zone/local coordinates.
        tx_ty: tuple[int, int] | None = None
        zone_coord: tuple[int, int, int] | None = None
        try:
            zone_coord, local = game.zone_local_from_abs(
                abs_tile,
                depth=getattr(game, "zone_coord", (0, 0, 0))[2],
                clamp_to_world=True,
            )
            if local is not None:
                tx_ty = (int(local[0]), int(local[1]))
        except Exception:
            zone_coord = None
            tx_ty = None

        # If the zone is loaded, we can inspect its concrete instances.
        level_for_tile = None
        if zone_coord is not None:
            if zone_coord == getattr(game, "zone_coord", None):
                try:
                    level_for_tile = game._level()
                except Exception:
                    level_for_tile = None
            else:
                try:
                    level_for_tile = game.get_zone_for_render(zone_coord)
                except Exception:
                    level_for_tile = None

        # LOD context (used to pick which world-index entities to report).
        cam_lod = 0.0
        dmin = -5.0
        dmax = 3.0
        fade_w = 0.45
        if renderer is not None:
            try:
                _abs_rect, cam_lod = renderer.get_camera_abs_rect_and_lod(game)
            except Exception:
                try:
                    world_scale = float(getattr(renderer, "tile_px", float(getattr(renderer, "base_tile", 18)) * float(getattr(renderer, "zoom", 1.0))))
                    world_scale = max(1e-6, world_scale)
                    cam_lod = math.log2(float(getattr(renderer, "base_tile", 18)) / world_scale)
                except Exception:
                    cam_lod = 0.0
            dmin = float(getattr(renderer, "entity_lod_delta_min", dmin))
            dmax = float(getattr(renderer, "entity_lod_delta_max", dmax))
            fade_w = float(getattr(renderer, "entity_lod_fade_width", fade_w))

        ax = int(abs_tile[0])
        ay = int(abs_tile[1])

        def _lod_delta(abs_size: float) -> float | None:
            ent_lod = math.log2(max(1e-12, float(abs_size)))
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

        # Gather entity candidates across local zone + world index + attention store.
        seen: dict[str | int, tuple[object, float]] = {}

        def _add_candidate(ent: object, abs_x: float | None = None, abs_y: float | None = None) -> None:
            key = getattr(ent, "id", None) or id(ent)
            if abs_x is None or abs_y is None:
                ap = getattr(ent, "abs_pos", None)
                if ap:
                    abs_x, abs_y = float(ap[0]), float(ap[1])
                elif zone_coord is not None and getattr(ent, "pos", None) is not None:
                    try:
                        zx, zy, _zz = zone_coord
                        ox, oy = getattr(ent, "pos", (0, 0))
                        zw = int(getattr(game, "cfg", None).world_width) if getattr(game, "cfg", None) else 60
                        zh = int(getattr(game, "cfg", None).world_height) if getattr(game, "cfg", None) else 40
                        abs_x = float(int(zx) * zw + int(ox))
                        abs_y = float(int(zy) * zh + int(oy))
                    except Exception:
                        abs_x = abs_y = None
            if abs_x is None or abs_y is None:
                return

            abs_size = game._size_for_render(ent)
            if not _intersects_tile(float(abs_x), float(abs_y), abs_size):
                return
            delta = _lod_delta(abs_size)
            if delta is None:
                return

            prev = seen.get(key)
            if prev is not None:
                if abs(delta) >= abs(prev[1]):
                    return
            seen[key] = (ent, float(delta))

        # 1) Concrete entities from loaded zone.
        if level_for_tile is not None and tx_ty is not None:
            tx, ty = tx_ty
            for ent in list(level_for_tile.actors.values()) + list(level_for_tile.entities.values()):
                if getattr(ent, "pos", None) != (tx, ty):
                    continue
                _add_candidate(ent)

        # 2) World index entities (POIs, structures, macro).
        try:
            world_index = getattr(game, "world_entity_index", None)
            if world_index is not None:
                zz = int(getattr(game, "zone_coord", (0, 0, 0))[2])
                for ref in world_index.query_abs_rect((ax, ay, ax + 1, ay + 1), z=zz, zone_span_cap=1):
                    obj = ref.ent
                    zx0, zy0, _z = ref.zone_coord
                    ox, oy = ref.local_pos
                    abs_x = float(zx0) * float(world_index.zone_w) + float(ox)
                    abs_y = float(zy0) * float(world_index.zone_h) + float(oy)
                    _add_candidate(obj, abs_x=abs_x, abs_y=abs_y)
        except Exception:
            pass

        # 3) Attention-staged entities (derived, lightweight).
        try:
            attn_store = getattr(game, "attn_store", None)
            if attn_store is not None:
                zz = int(getattr(game, "zone_coord", (0, 0, 0))[2])
                for obj, abs_x, abs_y in attn_store.query_abs_rect((ax, ay, ax + 1, ay + 1), zz=zz):
                    _add_candidate(obj, abs_x=float(abs_x), abs_y=float(abs_y))
        except Exception:
            pass

        # LOD-best filtering: keep entities closest to the camera LOD.
        candidates = list(seen.values())
        if candidates:
            tol = float(getattr(game, "look_lod_tolerance", 0.75))
            min_delta = min(abs(delta) for _, delta in candidates)
            entities_here = [ent for ent, delta in candidates if abs(delta) <= min_delta + tol]
        else:
            entities_here = []

        title = "You look around..."
        body: str | None = None

        if entities_here:
            if len(entities_here) > 1:
                # Prompt for which entity to inspect (terrain is not included here).
                choices = []
                infos = []
                for ent in entities_here:
                    info = describe_entity_for_look(ent)
                    infos.append(info)
                    choices.append(info.get("name", "Something") or "Something")

                def _inspect_choice(idx: int, mgr) -> None:  # type: ignore[no-redef]
                    if idx < 0 or idx >= len(entities_here):
                        return
                    ent = entities_here[idx]
                    info = infos[idx]
                    try:
                        from .inventory_scene import LookScene
                    except Exception:
                        LookScene = None  # type: ignore[assignment]

                    ent_id = getattr(ent, "id", None)
                    if LookScene is not None and ent_id is not None:
                        mgr.push_scene(
                            LookScene(
                                game,
                                owner_id=str(ent_id),
                                title=info.get("name", "You inspect...") or "You inspect...",
                                source_px=self._entity_source_px_from_world(
                                    getattr(mgr, "renderer", None),
                                    getattr(ent, "abs_pos", None) or getattr(ent, "pos", None),
                                ),
                                source_glyph_px=int(getattr(getattr(mgr, "renderer", None), "glyph_px", getattr(getattr(mgr, "renderer", None), "tile", 1) or 1)),
                            )
                        )
                        return

                    # Fallback to a text popup.
                    glyph = info.get("glyph", "?")
                    desc = info.get("description", "") or "You see nothing remarkable about it."
                    lines = [str(glyph), "", str(desc)] if glyph else [str(desc)]
                    hp_txt = info.get("hp_text")
                    if hp_txt:
                        lines.extend(["", str(hp_txt)])
                    mgr.push_scene(
                        UrgentMessageScene(
                            game,
                            "\n".join(lines),
                            title=info.get("name", title) or title,
                            choices=["OK"],
                        )
                    )

                manager.push_scene(
                    UrgentMessageScene(
                        game,
                        "Multiple things are here. Which do you inspect?",
                        title="Inspect",
                        choices=choices,
                        on_choice=_inspect_choice,
                    )
                )
                return

            # Single entity: open a read-only inspect popup (InventoryScene in look mode)
            primary = entities_here[0]
            info = describe_entity_for_look(primary)

            try:
                from .inventory_scene import LookScene
            except Exception:
                LookScene = None  # type: ignore[assignment]

            ent_id = getattr(primary, "id", None)
            if LookScene is not None and ent_id is not None:
                rend = getattr(manager, "renderer", None)
                world_pos = getattr(primary, "abs_pos", None) or getattr(primary, "pos", None)
                manager.push_scene(
                    LookScene(
                        game,
                        owner_id=str(ent_id),
                        title=info.get("name", "You inspect...") or "You inspect...",
                        source_px=self._entity_source_px_from_world(rend, world_pos),
                        source_glyph_px=int(getattr(rend, "glyph_px", getattr(rend, "tile", 1) or 1)),
                    )
                )
                return


            # Fallback if LookScene can't be imported / entity has no id
            title = info.get("name", title) or title
            glyph = info.get("glyph", "?")
            desc = info.get("description", "") or "You see nothing remarkable about it."

            lines: list[str] = []
            if glyph:
                lines.append(str(glyph))
                lines.append("")
            lines.append(str(desc))
            hp_txt = info.get("hp_text")
            if hp_txt:
                lines.append("")
                lines.append(str(hp_txt))
            # Faction standings are already included in description, but add
            # explicitly if passed separately (for fallback completeness)
            faction_lines = info.get("faction_standings")
            if faction_lines and "\nFaction Standings:" not in desc:
                lines.append("")
                lines.append("Faction Standings:")
                lines.extend(faction_lines)
            body = "\n".join(lines)
        else:
            # No entities here: fall back to a tile description if available.
            if tx_ty is not None and level_for_tile is not None and hasattr(game, "describe_tile_at"):
                body = game.describe_tile_at(tx_ty, level=level_for_tile, zone_coord=zone_coord)
            elif hasattr(game, "describe_abs_tile_at"):
                body = game.describe_abs_tile_at((int(abs_tile[0]), int(abs_tile[1])), cam_lod=cam_lod)
            else:
                body = f"You look at a distant tile at ABS {abs_tile}."
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



    def _apply_seal_snap(
        self,
        game: Game,
        t: TargetState | None,
        cursor_tile: tuple[int, int],
    ) -> tuple[int, int]:
        """Snap terminus targeting to a sealing rune's canonical terminus."""
        ui = self.ui_state
        trial = getattr(game._level(), "seal_trial", None)

        # Only snap in terminus targeting when a live trial exists.
        if (
            t is None
            or t.kind != "tile"
            or getattr(t, "mode", None) != "terminus"
            or trial is None
            or trial.sealed
        ):
            ui.seal_snap_active = False
            ui.seal_root_hint = None
            return cursor_tile

        tx, ty = cursor_tile
        sx, sy = trial.terminus_tile
        dx = tx - sx
        dy = ty - sy
        if dx * dx + dy * dy <= trial.snap_radius * trial.snap_radius:
            ui.seal_snap_active = True
            ui.seal_root_hint = trial.root_tile
            return trial.terminus_tile

        ui.seal_snap_active = False
        ui.seal_root_hint = None
        return cursor_tile


    def _abs_tile_to_local_current_zone(
        self,
        game: Game,
        abs_tile: tuple[int, int],
    ) -> tuple[int, int] | None:
        """Convert an ABS world-tile to LOCAL coords in the *current* loaded zone.

        Post-abs_pos refactor: renderer.origin is anchored to ABS (0,0), so mouse-derived
        tiles are ABS. Most dungeon gameplay systems (movement, runes, seal trials) still
        operate in the current LevelState's local coordinate frame.
        """
        try:
            ax, ay = int(abs_tile[0]), int(abs_tile[1])
        except Exception:
            return None

        try:
            zx, zy, _zz = getattr(game, "zone_coord", (0, 0, 0))
            zx = int(zx)
            zy = int(zy)
        except Exception:
            zx = zy = 0

        try:
            zw = int(getattr(game.cfg, "world_width", 0) or 0)
            zh = int(getattr(game.cfg, "world_height", 0) or 0)
        except Exception:
            zw = zh = 0

        if zw <= 0 or zh <= 0:
            # Fallback: assume abs==local (legacy single-zone worlds)
            return (ax, ay)

        lx = ax - zx * zw
        ly = ay - zy * zh

        if 0 <= lx < zw and 0 <= ly < zh:
            return (int(lx), int(ly))
        return None

    def _set_cursor_abs(
        self,
        game: Game,
        abs_pos: tuple[int, int] | None,
    ) -> None:
        """Set cursor position from ABS coordinates (canonical source of truth).

        YOGA: target_cursor_abs is the single source of truth for cursor position.
        target_cursor (zone-local) is derived from it for backward compatibility.
        """
        self.ui_state.target_cursor_abs = abs_pos
        if abs_pos is None:
            self.ui_state.target_cursor = (0, 0)
            return
        local = self._abs_tile_to_local_current_zone(game, abs_pos)
        if local is not None:
            self.ui_state.target_cursor = local
        else:
            # Off-zone: keep local at last known or origin
            self.ui_state.target_cursor = (0, 0)

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
        # YOGA Stage 2: Use centralized coordinate helpers instead of ad-hoc math.
        wx, wy = renderer.screen_to_abs_tile((mx, my))

        # NOTE (Yoga): screen->tile is currently in ABS tile-space (camera anchored in abs).
        # Most gameplay targeting (runes/look/terminus) still has local-zone legacy, but the
        # canonical cursor state should be ABS so we can traverse vestigial zone boundaries.
        if t.kind in ("tile", "look", "position"):
            abs_tx, abs_ty = renderer.screen_to_abs_tile_int((mx, my))
            abs_fx, abs_fy = renderer.screen_to_abs_tile((mx, my))

            # ABS cursor is canonical for ALL tile-ish targeting modes.
            self.ui_state.target_cursor_abs = (abs_tx, abs_ty)

            # Try to also derive local cursor when the hovered tile lies in the current zone.
            local = self._abs_tile_to_local_current_zone(game, (abs_tx, abs_ty))

            if local is not None and game.world.in_bounds(*local):
                tx, ty = local
                cursor = (tx, ty)

                if t.kind == "tile" and getattr(t, "mode", None) == "terminus":
                    cursor = self._apply_seal_snap(game, t, cursor)
                else:
                    self.ui_state.seal_snap_active = False
                    self.ui_state.seal_root_hint = None

                t.cursor_tile = cursor
                self.ui_state.target_cursor = cursor

                # Push preview: prefer ABS target tile (world) to avoid zone-boundary clamp/desync.
                if getattr(t, "action", None) == "push_pattern":
                    lvl = game._level()
                    pattern = getattr(lvl, "pattern", None)
                    anchor = getattr(lvl, "pattern_anchor", None)
                    if pattern and anchor and pattern.vertices:
                        com = pattern_motion.center_of_mass(pattern)
                        com_world = (com[0] + anchor[0], com[1] + anchor[1])

                        # Mouse target in continuous world coords.
                        #
                        # Default behavior: free continuous aiming (no grid snap).
                        # Hold Ctrl to force grid snapping for precision/repeatability.
                        ox, oy = renderer._zone_abs_offset(game)
                        mods = pygame.key.get_mods()
                        snap_to_grid = bool(mods & pygame.KMOD_CTRL)
                        if snap_to_grid:
                            mouse_world = (float(abs_tx - ox), float(abs_ty - oy))
                        else:
                            mouse_world = (float(abs_fx - ox), float(abs_fy - oy))

                        dx = mouse_world[0] - com_world[0]
                        dy = mouse_world[1] - com_world[1]
                        dist = (dx * dx + dy * dy) ** 0.5
                        max_range = getattr(t.constraints, "max_range", None) if t.constraints else None
                        if max_range is None:
                            max_range = 5.0
                        if dist > max_range and dist > 0:
                            scale = max_range / dist
                            dx *= scale
                            dy *= scale
                        tgt = (com_world[0] + dx, com_world[1] + dy)
                        if snap_to_grid:
                            tgt = (float(round(tgt[0])), float(round(tgt[1])))
                        self.ui_state.push_target = tgt
                        self.ui_state.push_preview = pattern_motion.build_push_preview(
                            pattern, anchor, tgt, self.ui_state.push_rotation, max_range
                        )
            else:
                # Out-of-zone: keep ABS cursor, but clear local cursor so nothing "sticks".
                t.cursor_tile = None
                self.ui_state.target_cursor = None

                # If we are in push mode, we can still compute a preview using ABS.
                if getattr(t, "action", None) == "push_pattern":
                    lvl = game._level()
                    pattern = getattr(lvl, "pattern", None)
                    anchor = getattr(lvl, "pattern_anchor", None)
                    if pattern and anchor and pattern.vertices:
                        com = pattern_motion.center_of_mass(pattern)
                        com_world = (com[0] + anchor[0], com[1] + anchor[1])

                        ox, oy = renderer._zone_abs_offset(game)
                        mods = pygame.key.get_mods()
                        snap_to_grid = bool(mods & pygame.KMOD_CTRL)
                        if snap_to_grid:
                            mouse_world = (float(abs_tx - ox), float(abs_ty - oy))
                        else:
                            mouse_world = (float(abs_fx - ox), float(abs_fy - oy))

                        dx = mouse_world[0] - com_world[0]
                        dy = mouse_world[1] - com_world[1]
                        dist = (dx * dx + dy * dy) ** 0.5
                        max_range = getattr(t.constraints, "max_range", None) if t.constraints else None
                        if max_range is None:
                            max_range = 5.0
                        if dist > max_range and dist > 0:
                            scale = max_range / dist
                            dx *= scale
                            dy *= scale
                        tgt = (com_world[0] + dx, com_world[1] + dy)
                        if snap_to_grid:
                            tgt = (float(round(tgt[0])), float(round(tgt[1])))
                        self.ui_state.push_target = tgt
                        self.ui_state.push_preview = pattern_motion.build_push_preview(
                            pattern, anchor, tgt, self.ui_state.push_rotation, max_range
                        )

            return

        elif t.kind == "vertex":
            ox, oy = renderer._zone_abs_offset(game)
            idx = game.nearest_vertex((wx - ox, wy - oy))
            self.ui_state.hover_vertex = idx
            t.cursor_vertex = idx

            if idx is not None and t.constraints and t.constraints.neighbor_depth_param:
                depth = game.get_param_value(t.action, t.constraints.neighbor_depth_param)
                self.ui_state.hover_neighbors = game.neighbor_set_depth(idx, depth)
            else:
                self.ui_state.hover_neighbors = []

            self._refresh_aim_prediction(game)
            return

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
            # YOGA Stage 2: Use centralized coordinate helpers instead of ad-hoc math.
            wx, wy = renderer.screen_to_abs_tile((mx, my))
            ox, oy = renderer._zone_abs_offset(game)
            idx = game.nearest_vertex((wx - ox, wy - oy))
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
            # Group editing sub-mode: edit membership for one group.
            if getattr(bar, "overlay_mode", "order") == "group_edit":
                # Prefer raw-key handling so this works even if the input layer maps keys oddly.
                if key == pygame.K_SPACE:
                    bar.group_edit_toggle_current()
                    return
                if key == pygame.K_a:
                    bar.group_edit_set_active()
                    return
                if kind in ("escape", "confirm"):
                    bar.end_group_edit()
                    return
                if kind == "move" and vec is not None:
                    _, dy = vec
                    if dy:
                        bar.group_edit_move_cursor(dy)
                    return
                return

            # Slot ordering mode
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

            if key == pygame.K_g:
                bar.begin_group_edit_for_selected()
                return

            if key == pygame.K_u:
                bar.dissolve_selected_group()
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

            # ignore other commands while Abilities menu is active
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
            if bar.active_action:
                idx = bar.slot_index_for_action(bar.active_action)
                if idx is not None:
                    bar.selected_index = idx
                    bar.page = bar.selected_index // bar.page_size
            return

        if kind == "wish_prompt":
            try:
                from .wish_scene import WishScene

                manager.push_scene(WishScene(game))
            except Exception:
                pass
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
                # ABS cursor so we can target across zone boundaries.
                # Store abs cursor on ui_state to avoid overloading cursor_tile.
                cur_abs = getattr(self.ui_state, "target_cursor_abs", None)
                if cur_abs is None:
                    # Seed from current tile cursor if available, else from player ABS
                    if t.cursor_tile is not None:
                        cur_abs = game.abs_from_zone_local(game.zone_coord, t.cursor_tile)
                    else:
                        cur_abs = game._get_player_abs()

                dx, dy = vec
                new_abs = (int(cur_abs[0] + dx), int(cur_abs[1] + dy))
                self.ui_state.target_cursor_abs = new_abs

                # If the ABS cursor lies in the current zone, expose a local tile cursor for UI.
                dest_coord, dest_local = game.zone_local_from_abs(
                    new_abs, depth=game.zone_coord[2], clamp_to_world=True
                )
                if dest_coord == game.zone_coord:
                    cursor = self._apply_seal_snap(game, t, dest_local)
                    t.cursor_tile = cursor
                    self.ui_state.target_cursor = cursor
                else:
                    # Cursor is off-zone; keep tile cursor unset so we don't clamp it.
                    t.cursor_tile = None
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
                    ox, oy = renderer._zone_abs_offset(game)
                    idx = game.nearest_vertex((wx - ox, wy - oy))

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
                        # YOGA: Set ABS cursor as canonical, local is derived
                        abs_tile = game.abs_from_zone_local(game.zone_coord, (tx, ty))
                        self.ui_state.target_cursor_abs = abs_tile
                        t.cursor_tile = (tx, ty)
                        self.ui_state.target_cursor = (tx, ty)

                # Always swallow movement while in push-mode targeting
                return


        # ------------------------------------------------------------
        # Look targeting mode (tile-based inspect cursor)
        # ------------------------------------------------------------
        if in_look_mode and t:
            if kind == "move" and vec is not None:
                dx, dy = vec

                # Start from canonical ABS cursor if we have it; otherwise derive from local.
                cur_abs = getattr(self.ui_state, "target_cursor_abs", None)
                if cur_abs is None:
                    base_local = t.cursor_tile
                    if base_local is None:
                        base_local = game.actors[game.player_id].pos
                    try:
                        cur_abs = game.abs_from_zone_local(getattr(game, "zone_coord", (0, 0, 0)), base_local)
                    except Exception:
                        cur_abs = (0, 0)

                new_abs = (int(cur_abs[0] + dx), int(cur_abs[1] + dy))
                self.ui_state.target_cursor_abs = new_abs

                # Derive LOCAL cursor only if the ABS tile lies in the currently loaded zone.
                try:
                    zone, local = game.zone_local_from_abs(new_abs, depth=getattr(game, "zone_coord", (0, 0, 0))[2], clamp_to_world=True)
                except Exception:
                    zone, local = None, None

                if zone == getattr(game, "zone_coord", None) and local is not None:
                    t.cursor_tile = (int(local[0]), int(local[1]))
                    self.ui_state.target_cursor = t.cursor_tile
                else:
                    # Out of current zone: LOCAL cursor becomes undefined, but ABS remains authoritative.
                    t.cursor_tile = None

                # Swallow movement so the player never walks in look mode.
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
            if 0 <= start < len(getattr(bar, "slots", [])):
                bar.selected_index = start
                act = bar.action_at_index(start)
                if act:
                    bar.set_active(act)

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
            mx, my = renderer._to_surface(cmd.mouse_pos)

            # Ability-hover preview for radial chakra activators.
            bar_widget = getattr(renderer, "ability_bar_widget", None)
            if bar_widget is not None:
                try:
                    ctx = WidgetContext(surface=renderer.surface, game=game, scene=self, renderer=renderer)
                    hovered_action = bar_widget.hover_action((mx, my), ctx)
                except Exception:
                    hovered_action = None
                self.ui_state.hovered_action = str(hovered_action) if hovered_action else None

                hover_preview_actions = {"energy_kick", "palm_burst", "knife_rune"}
                if hovered_action in hover_preview_actions:
                    self.ui_state.action_preview = build_action_preview(game, str(hovered_action), game.player_id)
                    return

                current_preview = getattr(self.ui_state, "action_preview", None)
                if getattr(current_preview, "action", None) in hover_preview_actions:
                    self.ui_state.action_preview = None
            else:
                self.ui_state.hovered_action = None

            self._update_hover_from_mouse(
                game,
                renderer,
                (mx, my),
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

                    # Only adjust radius with the wheel when actively aiming an action that has a radius param.
                    if (
                        spec
                        and spec.radius_param
                        and in_target_mode
                        and t
                        and getattr(t, "action", None) == active_name
                    ):
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
                            renderer.map_center_surface_px(),
                        )

                        self._sync_attention_stage(game, renderer)

            return


        if kind == "center_camera":
            # Safety valve: recenter on the player if the camera gets lost.
            try:
                snap = bool(getattr(renderer, "zoom", 1.0) < 0.35)
                renderer.reset_camera(game)
                self._sync_attention_stage(game, renderer)

            except Exception:
                pass
            return

        if kind == "toggle_door":
            level = game._level()
            player = game.actors[game.player_id]
            px, py = player.pos
            # Check current + cardinal neighbors for doors.
            offsets = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]
            doors: list[tuple[tuple[int, int], object]] = []
            for dx, dy in offsets:
                tx, ty = px + dx, py + dy
                for ent in game._all_entities(level):
                    if ent.pos == (tx, ty) and getattr(ent, "tags", {}).get("door"):
                        doors.append(((dx, dy), ent))
                        break

            if not doors:
                game.log.add("No door nearby.")
                self._pending_door_toggle = None
                return

            if len(doors) == 1:
                (_, ent) = doors[0]
                game._toggle_door(ent, level, notify=True)
                game._advance_time(level, 5)
                self._pending_door_toggle = None
                return

            # Multiple doors: ask for direction and store candidates.
            self._pending_door_toggle = {offset: ent for offset, ent in doors}
            game.log.add("Multiple doors nearby. Press a direction to choose.")
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
            alt_held = bool(pygame.key.get_mods() & pygame.KMOD_ALT)

            # Ability bar interaction is handled via the AbilityBarWidget.
            bar_widget = getattr(renderer, "ability_bar_widget", None)
            if bar_widget is not None:
                ctx = WidgetContext(surface=renderer.surface, game=game, scene=self, renderer=renderer)
                hit = bar_widget.click((mx, my), ctx)
                if hit is not None:
                    if hit.kind == "page_prev":
                        _page_bar(bar, forward=False)
                        return
                    if hit.kind == "page_next":
                        _page_bar(bar, forward=True)
                        return
                    if hit.kind == "open_reorder":
                        game.ability_reorder_open = True
                        if bar.active_action:
                            idx = bar.slot_index_for_action(bar.active_action)
                            if idx is not None:
                                bar.selected_index = idx
                                bar.page = bar.selected_index // bar.page_size if bar.page_size else 0
                        return

                    ability = hit.ability
                    if ability is None:
                        return

                    # Group interactions
                    if hit.kind == "group_arrow":
                        bar.set_active(ability.action)
                        slot_index = getattr(ability, "_bar_slot_index", None)
                        if isinstance(slot_index, int):
                            bar.toggle_group_expanded(slot_index)
                        return

                    if hit.kind == "group_pick":
                        action = getattr(hit, "group_action", None)
                        if isinstance(action, str) and action:
                            bar.set_active(action)
                            if alt_held and is_previewable_action(action):
                                self.ui_state.action_preview = build_action_preview(game, action, game.player_id)
                                return
                            self.ui_state.action_preview = None
                            self._begin_action_from_def(game, action)
                        return

                    # Any click on an ability slot selects it.
                    bar.set_active(ability.action)

                    # Sub-button intents (param tweak / open config).
                    if hit.kind == "sub_button" and hit.sub_meta is not None:
                        meta = hit.sub_meta
                        if meta.kind == "param_delta" and meta.param_key and meta.delta:
                            changed, msg = game.adjust_param(ability.action, meta.param_key, meta.delta)
                            if not changed and msg:
                                renderer._set_flash(msg)
                            self._refresh_aim_prediction(game)
                            return
                        if meta.kind == "open_config":
                            _set_ui("config_open", True)
                            _set_ui("config_action", ability.action)
                            _set_ui("config_selection", 0)
                            return

                    # Main ability click: delegate to Action metadata.
                    if hit.kind == "ability":
                        action_name = getattr(ability, "action", None)
                        if alt_held and isinstance(action_name, str) and is_previewable_action(action_name):
                            self.ui_state.action_preview = build_action_preview(game, action_name, game.player_id)
                            return
                        self.ui_state.action_preview = None
                        self._begin_action_from_def(game, ability)
                        return

            # Map / world clicks.
            # YOGA Stage 2: Use centralized coordinate helpers instead of ad-hoc math.
            tx, ty = renderer.screen_to_abs_tile_int((mx, my))
            if not game.world.in_bounds(tx, ty):
                return

            # Terminus placement via click (legacy).
            # NOTE (YOGA): tx, ty are ABS coords (from mouse relative to ABS origin).
            # The in_bounds check above is suspect for multi-zone worlds.
            if getattr(game, "awaiting_terminus", False):
                # ABS cursor is canonical; try_place_terminus expects ABS
                self._set_cursor_abs(game, (tx, ty))
                game.try_place_terminus((tx, ty))
                # Clear any target mode that might be lingering
                self.cancel_target_mode(game)
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
                level = game._level()
                player = level.actors.get(game.player_id)
                if player:
                    items = game._items_at(level, player.pos)
                    if len(items) == 0:
                        game.log.add("There's nothing here to pick up.")
                    elif len(items) == 1:
                        # We already have the right entity — pass it directly to
                        # avoid entity_at returning a non-item entity at the same tile.
                        from edgecaster.systems import inventory as inv_system
                        inv_system.player_pick_up_item(game, items[0])
                    else:
                        # Multiple items - show selection scene
                        from edgecaster.scenes.cache_items_scene import CacheItemsScene
                        manager.push_scene(CacheItemsScene(game, items))
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

        if kind == "open_chakra_menu":
            if not in_aim_mode:
                setattr(game, "chakra_requested", True)
                renderer.quit_requested = True
            return

        if kind == "open_blade_editor":
            if in_aim_mode:
                return
            pclass = str(
                getattr(getattr(game, "character", None), "player_class", "")
                or getattr(getattr(game, "character", None), "char_class", "")
                or ""
            )
            if pclass != "Blade":
                game.log.add("Only Blade adepts can edit an intrinsic blade right now.")
                return
            try:
                with open("C:/Games/Edgecaster/debug.log", "a", encoding="utf-8") as f:
                    f.write("[dungeon] open_blade_editor command accepted\n")
            except Exception:
                pass
            setattr(game, "blade_editor_requested", True)
            renderer.quit_requested = True
            return

        if kind == "open_quest_journal":
            if not in_aim_mode:
                setattr(game, "quest_journal_requested", True)
                renderer.quit_requested = True
            return

        if kind == "open_factions":
            if not in_aim_mode:
                setattr(game, "factions_requested", True)
                renderer.quit_requested = True
            return

        if kind == "open_gods_menu":
            if not in_aim_mode:
                setattr(game, "gods_menu_requested", True)
                renderer.quit_requested = True
            return

        if kind == "yawp":
            # Defer to the central Action definition for yawp.
            # This lets _debug_yawp in actions.py handle both the log
            # message and the visual rotation test.
            trigger_ability_effect(game, "yawp")
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
            # Gardener uses the branch editor instead of the generic fractal editor.
            player_class = getattr(getattr(game, "character", None), "player_class", None)
            if player_class == "Gardener":
                setattr(game, "branch_editor_requested", True)
                renderer.quit_requested = True
                return

            from .fractal_editor_scene import FractalEditorState

            game.fractal_editor_state = FractalEditorState()  # default rect grid
            setattr(game, "fractal_editor_requested", True)
            renderer.quit_requested = True
            return

        if kind == "talk":
            npc = game._adjacent_npc() if hasattr(game, "_adjacent_npc") else None
            if not npc:
                game.log.add("No one nearby to talk to.")
                return

            from edgecaster import events
            from edgecaster.content.dialogues import build_npc_dialogue_tree

            tree = build_npc_dialogue_tree(game, npc)
            events.start_dialogue(game, tree)
            return

        # ------------------------------------------------------------
        # 7) Movement (no special modes active)
        # ------------------------------------------------------------

        if kind == "move" and vec is not None:
            if self._pending_door_toggle:
                dx, dy = vec
                ent = self._pending_door_toggle.get((dx, dy))
                if ent:
                    game._toggle_door(ent, game._level(), notify=True)
                    game._advance_time(game._level(), 5)
                else:
                    game.log.add("No door in that direction.")
                self._pending_door_toggle = None
                return
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
        import logging
        action_name = getattr(ability, "action", ability)
        logging.debug(f"[_begin_action_from_def] action_name={action_name}")

        # If we're already in targeting mode for this action, confirm the target
        if self.ui_state.target and self.ui_state.target.action == action_name:
            self.confirm_target(game)
            return

        try:
            action_def = get_action(action_name)
            logging.debug(f"[_begin_action_from_def] Found action_def for {action_name}")
        except KeyError:
            logging.debug(f"[_begin_action_from_def] KeyError for {action_name}, using trigger_ability_effect")
            trigger_ability_effect(game, action_name)
            return

        spec = getattr(action_def, "targeting", None)
        logging.debug(f"[_begin_action_from_def] spec={spec}")

        # No targeting metadata: fire immediately.
        if not spec or not spec.kind:
            logging.debug(f"[_begin_action_from_def] No targeting spec, firing immediately")
            self.ui_state.aim_action = None
            self._refresh_aim_prediction(game)
            trigger_ability_effect(game, action_name)
            return

        logging.debug(f"[_begin_action_from_def] Entering target mode: kind={spec.kind}, mode={spec.mode}")

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
        logging.debug(f"[_begin_action_from_def] begin_target_mode called")
