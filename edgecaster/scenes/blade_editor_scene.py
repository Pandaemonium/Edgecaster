from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Sequence, Tuple

import pygame

from edgecaster.patterns import builder
from .base import Scene
from edgecaster.systems import blade_runtime as blade_runtime_system


Vec2 = Tuple[float, float]

def _log_debug(msg: str) -> None:
    """Small local debug helper for diagnosing scene-open stalls."""
    try:
        with open("C:/Games/Edgecaster/debug.log", "a", encoding="utf-8") as f:
            f.write(f"[blade_editor] {msg}\n")
    except Exception:
        pass


@dataclass
class BladePreview:
    verts: List[Vec2]
    segs: List[Tuple[int, int]]


def _is_custom_generator(kind: str) -> bool:
    return kind == "custom" or kind.startswith("custom_")


def _available_generators(game: Any) -> List[str]:
    """Return selectable blade generators for V1.

    We keep this explicit so the editor stays stable even as new actions are added.
    """
    base = ["subdivide", "extend", "koch", "branch", "zigzag"]

    # Custom generators are enabled if the run has at least one saved custom pattern.
    try:
        custom_count = len(getattr(game, "custom_patterns", []) or [])
    except Exception:
        custom_count = 0

    if custom_count > 0:
        base.append("custom")
        # Expose explicit extra custom slots (custom_1, custom_2, ...)
        for idx in range(1, custom_count):
            base.append(f"custom_{idx}")

    return base


def _label_for_generator(kind: str) -> str:
    if kind.startswith("custom_"):
        return "Custom " + kind.split("_", 1)[1]
    return kind.replace("_", " ").title()


def _build_preview(game: Any, generators: Sequence[str]) -> BladePreview:
    """Build a blade preview using the same fractal pipeline as rune operators.

    We intentionally use the same generator classes and parameter lookups as
    runtime rune edits, but with a preview segment cap to keep the editor smooth.
    """
    # Safety cap for editor responsiveness. This keeps branching-heavy stacks
    # from exploding into very large previews that would hitch the UI.
    preview_max_segments = 5000
    segs = builder.line_pattern((0.0, 0.0), (1.0, 0.0)).to_segments()

    for kind in list(generators):
        name = str(kind).strip()
        if not name:
            continue

        gen = None
        if name == "subdivide":
            parts = 3
            try:
                parts = int(getattr(game, "_param_value", lambda *_: 3)("subdivide", "parts"))
            except Exception:
                parts = 3
            gen = builder.SubdivideGenerator(parts=parts)
        elif name == "koch":
            height = 0.25
            flip = False
            try:
                height = float(getattr(game, "_param_value", lambda *_: 0.25)("koch", "height"))
                flip = bool(getattr(game, "_param_value", lambda *_: False)("koch", "flip"))
            except Exception:
                pass
            gen = builder.KochGenerator(height_factor=height, flip=flip)
        elif name == "branch":
            angle = 30
            count = 2
            try:
                angle = int(getattr(game, "_param_value", lambda *_: 30)("branch", "angle"))
                count = int(getattr(game, "_param_value", lambda *_: 2)("branch", "count"))
            except Exception:
                pass
            gen = builder.BranchGenerator(angle_deg=angle, length_factor=0.45, branch_count=count)
        elif name == "extend":
            gen = builder.ExtendGenerator()
        elif name == "zigzag":
            parts = 6
            amp = 0.2
            try:
                parts = int(getattr(game, "_param_value", lambda *_: 6)("zigzag", "parts"))
                amp = float(getattr(game, "_param_value", lambda *_: 0.2)("zigzag", "amp"))
            except Exception:
                pass
            gen = builder.ZigzagGenerator(parts=parts, amplitude_factor=amp)
        elif _is_custom_generator(name):
            idx = 0
            if name != "custom":
                try:
                    idx = int(name.split("_", 1)[1])
                except Exception:
                    idx = 0
            custom_patterns = list(getattr(game, "custom_patterns", []) or [])
            if 0 <= idx < len(custom_patterns):
                pattern = custom_patterns[idx]
                verts = None
                edges = []
                if isinstance(pattern, dict):
                    verts = pattern.get("vertices")
                    edges = list(pattern.get("edges", []) or [])
                else:
                    verts = pattern
                if verts and len(verts) >= 2:
                    amp = 1.0
                    try:
                        amp = float(getattr(game, "_param_value", lambda *_: 1.0)("custom", "amplitude"))
                    except Exception:
                        amp = 1.0
                    if edges:
                        gen = builder.CustomGraphGenerator(verts, edges, amplitude=amp)
                    else:
                        gen = builder.CustomPolyGenerator(verts, amplitude=amp)

        if gen is None:
            continue

        segs = gen.apply_segments(segs, max_segments=preview_max_segments)
        segs = builder.cleanup_duplicates(segs)
        if len(segs) > preview_max_segments:
            segs = segs[:preview_max_segments]
            break

    pattern = builder.Pattern.from_segments(segs)
    out_verts: List[Vec2] = []
    for v in list(pattern.vertices or []):
        # state.patterns.Pattern stores Vertex objects (with .pos), but keep
        # tuple fallback for any legacy/custom pattern objects.
        if hasattr(v, "pos"):
            try:
                px, py = v.pos  # type: ignore[attr-defined]
                out_verts.append((float(px), float(py)))
                continue
            except Exception:
                pass
        try:
            px, py = v  # type: ignore[misc]
            out_verts.append((float(px), float(py)))
        except Exception:
            continue
    out_segs: List[Tuple[int, int]] = []
    for edge in list(pattern.edges or []):
        try:
            out_segs.append((int(edge.a), int(edge.b)))
        except Exception:
            continue
    return BladePreview(verts=out_verts, segs=out_segs)


class BladeEditorScene(Scene):
    """V1 blade editor for the Blade class intrinsic blade profile.

    Behavior:
    - Every edit applies immediately to the player blade state.
    - Esc/Cancel restores the snapshot captured on entry.
    - Enter/Done exits while keeping current applied state.
    """

    def __init__(self, game: Any) -> None:
        _log_debug("BladeEditorScene __init__ start")
        self.game = game
        self._close = False
        self._accepted = False

        self._available = _available_generators(game)
        self._list_index = 0
        self._selected_slot = 0
        self._focus = "generators"  # generators | slots
        self._status = ""

        self._snapshot = self._current_generators()
        self._working = list(self._snapshot)

        # Shared button hitboxes
        self._btn_done = pygame.Rect(0, 0, 0, 0)
        self._btn_cancel = pygame.Rect(0, 0, 0, 0)
        self._btn_remove = pygame.Rect(0, 0, 0, 0)
        self._btn_up = pygame.Rect(0, 0, 0, 0)
        self._btn_down = pygame.Rect(0, 0, 0, 0)
        self._slot_rects: list[pygame.Rect] = []
        self._gen_rects: list[pygame.Rect] = []
        self._preview_cache: BladePreview = BladePreview([], [])
        self._preview_dirty = True

        self._apply_now()
        _log_debug(f"BladeEditorScene __init__ done (available={len(self._available)} working={len(self._working)})")

    def _current_generators(self) -> List[str]:
        state = self.game.ensure_actor_blade_state(self.game.player_id)
        return list(getattr(state, "generators", []) or [])

    def _slots(self) -> int:
        return int(blade_runtime_system.blade_slots(self.game, self.game.player_id))

    def _apply_now(self) -> None:
        self.game.set_actor_blade_generators(self.game.player_id, list(self._working))
        self._working = self._current_generators()
        self._preview_dirty = True

    def _restore_snapshot(self) -> None:
        self.game.set_actor_blade_generators(self.game.player_id, list(self._snapshot))
        self._working = self._current_generators()
        self._preview_dirty = True

    def _ensure_preview(self) -> None:
        if not self._preview_dirty:
            return
        _log_debug(f"preview rebuild start (generators={len(self._working)})")
        self._preview_cache = _build_preview(self.game, self._working)
        self._preview_dirty = False
        _log_debug(
            f"preview rebuild done (verts={len(self._preview_cache.verts)} segs={len(self._preview_cache.segs)})"
        )

    def _stats_lines(self) -> List[str]:
        snap = blade_runtime_system.blade_stat_snapshot(self.game, self.game.player_id)
        return [
            f"Slots: {snap.slots}",
            f"Generators: {len(self._working)}",
            f"Slash: {snap.slash_damage} dmg, {snap.slash_reach:.2f} reach",
            f"Thrust: {snap.thrust_damage} dmg, {snap.thrust_reach:.2f} reach",
            f"Cleave: {snap.cleave_damage_min}-{snap.cleave_damage_max} dmg, {snap.cleave_reach:.2f} reach",
        ]

    def _add_generator(self, kind: str) -> None:
        if len(self._working) >= self._slots():
            self._status = f"No open slots (INT cap: {self._slots()})."
            return
        self._working.append(kind)
        self._apply_now()
        self._selected_slot = max(0, len(self._working) - 1)
        self._status = f"Added {_label_for_generator(kind)}."

    def _remove_selected(self) -> None:
        if not self._working:
            return
        idx = max(0, min(self._selected_slot, len(self._working) - 1))
        removed = self._working.pop(idx)
        self._apply_now()
        self._selected_slot = max(0, min(idx, len(self._working) - 1))
        self._status = f"Removed {_label_for_generator(removed)}."

    def _move_selected(self, delta: int) -> None:
        if not self._working:
            return
        idx = max(0, min(self._selected_slot, len(self._working) - 1))
        j = idx + int(delta)
        if j < 0 or j >= len(self._working):
            return
        self._working[idx], self._working[j] = self._working[j], self._working[idx]
        self._selected_slot = j
        self._apply_now()
        self._status = "Reordered generators."

    def _handle_keydown(self, event: pygame.event.Event) -> None:
        key = int(event.key)
        if key == pygame.K_ESCAPE:
            self._accepted = False
            self._close = True
            return
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._accepted = True
            self._close = True
            return
        if key == pygame.K_TAB:
            self._focus = "slots" if self._focus == "generators" else "generators"
            return
        if key in (pygame.K_UP, pygame.K_w):
            if self._focus == "generators":
                self._list_index = max(0, self._list_index - 1)
            else:
                self._selected_slot = max(0, self._selected_slot - 1)
            return
        if key in (pygame.K_DOWN, pygame.K_s):
            if self._focus == "generators":
                self._list_index = min(len(self._available) - 1, self._list_index + 1)
            else:
                self._selected_slot = min(max(0, len(self._working) - 1), self._selected_slot + 1)
            return
        if key in (pygame.K_LEFT, pygame.K_a):
            if self._focus == "slots":
                self._move_selected(-1)
            return
        if key in (pygame.K_RIGHT, pygame.K_d):
            if self._focus == "slots":
                self._move_selected(+1)
            return
        if key in (pygame.K_BACKSPACE, pygame.K_DELETE):
            self._remove_selected()
            return
        if key == pygame.K_SPACE:
            if self._focus == "generators" and self._available:
                self._add_generator(self._available[self._list_index])
            else:
                self._remove_selected()
            return

    def _button(self, surface: pygame.Surface, rect: pygame.Rect, label: str, *, active: bool = False) -> None:
        bg = (26, 30, 44) if not active else (42, 52, 78)
        fg = (210, 220, 240) if not active else (255, 235, 160)
        pygame.draw.rect(surface, bg, rect)
        pygame.draw.rect(surface, (88, 98, 130), rect, 1)
        font = pygame.font.SysFont("consolas", 22)
        txt = font.render(label, True, fg)
        surface.blit(txt, txt.get_rect(center=rect.center))

    def _draw_preview(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        self._ensure_preview()
        preview = self._preview_cache
        pygame.draw.rect(surface, (8, 12, 24), rect)
        pygame.draw.rect(surface, (44, 52, 86), rect, 1)

        if len(preview.verts) < 2:
            return

        xs = [p[0] for p in preview.verts]
        ys = [p[1] for p in preview.verts]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        spanx = max(1e-6, maxx - minx)
        spany = max(1e-6, maxy - miny)
        margin = 24
        inner = rect.inflate(-2 * margin, -2 * margin)
        scale = min(inner.w / spanx, inner.h / spany)
        cx = (minx + maxx) * 0.5
        cy = (miny + maxy) * 0.5
        ox = inner.centerx
        oy = inner.centery

        pts = []
        for vx, vy in preview.verts:
            px = int(round(ox + (vx - cx) * scale))
            py = int(round(oy + (vy - cy) * scale))
            pts.append((px, py))

        for a, b in preview.segs:
            if a < 0 or b < 0 or a >= len(pts) or b >= len(pts):
                continue
            pygame.draw.line(surface, (120, 205, 248), pts[a], pts[b], 2)

        for idx, p in enumerate(pts):
            col = (255, 220, 100) if idx in (0, len(pts) - 1) else (170, 220, 255)
            pygame.draw.circle(surface, col, p, 3)

    def _draw(self, surface: pygame.Surface) -> None:
        w, h = surface.get_width(), surface.get_height()
        surface.fill((4, 8, 22))

        left = pygame.Rect(20, 20, int(w * 0.34), h - 40)
        mid = pygame.Rect(left.right + 14, 20, int(w * 0.28), h - 40)
        right = pygame.Rect(mid.right + 14, 20, w - (mid.right + 34), h - 40)

        pygame.draw.rect(surface, (10, 14, 30), left)
        pygame.draw.rect(surface, (44, 52, 86), left, 1)
        pygame.draw.rect(surface, (10, 14, 30), mid)
        pygame.draw.rect(surface, (44, 52, 86), mid, 1)

        font_title = pygame.font.SysFont("consolas", 30)
        font_ui = pygame.font.SysFont("consolas", 22)
        font_small = pygame.font.SysFont("consolas", 18)
        title = font_title.render("Fractal Blade Editor", True, (226, 232, 246))
        surface.blit(title, (26, 24))

        subtitle = font_small.render(
            "Instant apply; Enter = keep, Esc = revert snapshot",
            True,
            (150, 166, 196),
        )
        surface.blit(subtitle, (30, 62))

        # Generator list (left)
        surface.blit(font_ui.render("Generators", True, (200, 214, 242)), (left.x + 12, left.y + 12))
        self._gen_rects = []
        gy = left.y + 48
        for i, g in enumerate(self._available):
            rr = pygame.Rect(left.x + 12, gy, left.w - 24, 30)
            self._gen_rects.append(rr)
            active = self._focus == "generators" and i == self._list_index
            bg = (34, 44, 76) if active else (16, 22, 40)
            fg = (255, 234, 160) if active else (194, 208, 236)
            pygame.draw.rect(surface, bg, rr)
            pygame.draw.rect(surface, (66, 78, 114), rr, 1)
            lbl = font_small.render(_label_for_generator(g), True, fg)
            surface.blit(lbl, (rr.x + 10, rr.y + 6))
            gy += 34

        # Slots / current order (middle)
        surface.blit(font_ui.render(f"Blade Sequence ({len(self._working)}/{self._slots()})", True, (200, 214, 242)), (mid.x + 12, mid.y + 12))
        self._slot_rects = []
        sy = mid.y + 48
        for i in range(max(self._slots(), 1)):
            rr = pygame.Rect(mid.x + 12, sy, mid.w - 24, 30)
            self._slot_rects.append(rr)
            active = self._focus == "slots" and i == self._selected_slot
            bg = (34, 44, 76) if active else (16, 22, 40)
            pygame.draw.rect(surface, bg, rr)
            pygame.draw.rect(surface, (66, 78, 114), rr, 1)
            if i < len(self._working):
                name = _label_for_generator(self._working[i])
                txt = f"{i + 1}. {name}"
                fg = (255, 234, 160) if active else (194, 208, 236)
            else:
                txt = f"{i + 1}. [empty]"
                fg = (120, 132, 164)
            surface.blit(font_small.render(txt, True, fg), (rr.x + 10, rr.y + 6))
            sy += 34

        # Right column: stats + preview + buttons
        stats_y = right.y + 10
        surface.blit(font_ui.render("Stats", True, (200, 214, 242)), (right.x + 8, stats_y))
        stats_y += 30
        for line in self._stats_lines():
            surface.blit(font_small.render(line, True, (190, 206, 230)), (right.x + 8, stats_y))
            stats_y += 24

        preview_rect = pygame.Rect(right.x + 8, stats_y + 10, right.w - 16, int(right.h * 0.48))
        self._draw_preview(surface, preview_rect)

        by = right.bottom - 98
        bw = int((right.w - 28) / 2)
        self._btn_done = pygame.Rect(right.x + 8, by, bw, 36)
        self._btn_cancel = pygame.Rect(right.x + 16 + bw, by, bw, 36)
        self._btn_remove = pygame.Rect(right.x + 8, by + 44, int((right.w - 24) / 3), 32)
        self._btn_up = pygame.Rect(self._btn_remove.right + 4, by + 44, int((right.w - 24) / 3), 32)
        self._btn_down = pygame.Rect(self._btn_up.right + 4, by + 44, int((right.w - 24) / 3), 32)

        self._button(surface, self._btn_done, "Done", active=self._accepted)
        self._button(surface, self._btn_cancel, "Cancel")
        self._button(surface, self._btn_remove, "Remove")
        self._button(surface, self._btn_up, "Move Up")
        self._button(surface, self._btn_down, "Move Down")

        if self._status:
            st = font_small.render(self._status, True, (255, 208, 120))
            surface.blit(st, (right.x + 8, right.bottom - 16 - st.get_height()))

    def _handle_mouse(self, event: pygame.event.Event, mouse_pos: tuple[int, int] | None = None) -> None:
        if mouse_pos is None:
            mx, my = event.pos
        else:
            mx, my = mouse_pos
        if self._btn_done.collidepoint(mx, my):
            self._accepted = True
            self._close = True
            return
        if self._btn_cancel.collidepoint(mx, my):
            self._accepted = False
            self._close = True
            return
        if self._btn_remove.collidepoint(mx, my):
            self._remove_selected()
            return
        if self._btn_up.collidepoint(mx, my):
            self._move_selected(-1)
            return
        if self._btn_down.collidepoint(mx, my):
            self._move_selected(+1)
            return
        for i, rr in enumerate(self._gen_rects):
            if rr.collidepoint(mx, my):
                self._focus = "generators"
                self._list_index = i
                if event.button == 1:
                    self._add_generator(self._available[i])
                return
        for i, rr in enumerate(self._slot_rects):
            if rr.collidepoint(mx, my):
                self._focus = "slots"
                self._selected_slot = i
                if event.button == 1:
                    if i < len(self._working):
                        self._remove_selected()
                elif event.button == 3:
                    self._remove_selected()
                return

    def run(self, manager) -> None:  # type: ignore[override]
        _log_debug("BladeEditorScene run() entered")
        renderer = manager.renderer
        clock = pygame.time.Clock()
        running = True

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    renderer.quit_requested = True
                    self._accepted = False
                    self._close = True
                    running = False
                    break
                if event.type == pygame.VIDEORESIZE:
                    if hasattr(renderer, "handle_resize"):
                        renderer.handle_resize(event.w, event.h)
                    else:
                        pygame.display.set_mode((event.w, event.h), renderer.surface_flags)
                    continue
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_F11:
                        renderer.toggle_fullscreen()
                    else:
                        self._handle_keydown(event)
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button in (1, 3):
                    # Map window/display coords back to the logical renderer surface
                    # so button hitboxes stay correct under letterboxing/scaling.
                    if hasattr(renderer, "_to_surface"):
                        try:
                            mx, my = renderer._to_surface(event.pos)  # type: ignore[attr-defined]
                            self._handle_mouse(event, (int(mx), int(my)))
                        except Exception:
                            self._handle_mouse(event)
                    else:
                        self._handle_mouse(event)

            surface = renderer.surface
            self._draw(surface)
            renderer.present()
            clock.tick(60)

            if self._close:
                running = False

        if not self._accepted:
            self._restore_snapshot()
        # IMPORTANT: this scene is pushed as an overlay. If we return from run()
        # without popping ourselves, SceneManager will immediately call run() again
        # on the same top-of-stack scene, causing an apparent hard hang loop.
        try:
            if manager.scene_stack and manager.scene_stack[-1] is self:
                manager.pop_scene(force=True)
        except Exception:
            pass
        _log_debug(f"BladeEditorScene run() exit accepted={self._accepted}")
