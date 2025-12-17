from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

import pygame
import math

from .base import PopupMenuScene
from .urgent_message_scene import UrgentMessageScene

from edgecaster.visuals import VisualProfile, apply_visual_panel
from edgecaster.visual_effects import (
    effect_names_from_obj,
    concat_effect_names,
    apply_entity_color_effects,
    apply_surface_overlays,
    compute_overlay_union_rect,
    build_visual_profile,
)

from edgecaster.ui.widgets import (
    Widget,
    WidgetContext,
    VBox,
    LabelWidget,
    ScaledLabelWidget,
    ListWidget,
)

if TYPE_CHECKING:
    from .manager import SceneManager


# ---------------------------------------------------------------------------
# Small helpers: smooth animation
# ---------------------------------------------------------------------------

def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _smoothstep(x: float) -> float:
    x = _clamp01(x)
    return x * x * (3.0 - 2.0 * x)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _render_entity_glyph_canvas(
    renderer,
    ent: Any,
    *,
    font: pygame.font.Font,
    base_px: int,
    scene_effects: list[str] | None = None,
) -> pygame.Surface:
    """
    Render a single entity glyph into a small RGBA canvas, preserving:
      - entity base color (player stays yellow, etc.)
      - scene + entity visual effects (fiery, syrupy, etc.) as much as possible
    """
    glyph = str(getattr(ent, "glyph", "@"))[:1]

    base_color = getattr(renderer, "fg", (240, 240, 255))
    if hasattr(renderer, "_entity_visual"):
        try:
            _, base_color = renderer._entity_visual(ent)  # type: ignore[attr-defined]
        except Exception:
            pass

    eff = concat_effect_names(scene_effects or [], effect_names_from_obj(ent))
    color = apply_entity_color_effects(ent, base_color, eff)

    base_rect = pygame.Rect(0, 0, base_px, base_px)
    union_rect, rect_by_name = compute_overlay_union_rect(ent, base_rect, eff)

    canvas = pygame.Surface((union_rect.w, union_rect.h), pygame.SRCALPHA)
    ox, oy = -union_rect.left, -union_rect.top

    gsurf = font.render(glyph, True, color)
    gx = ox + (base_px - gsurf.get_width()) // 2
    gy = oy + (base_px - gsurf.get_height()) // 2
    canvas.blit(gsurf, (gx, gy))

    if eff:
        shifted = {name: r.move(ox, oy) for name, r in rect_by_name.items()}
        apply_surface_overlays(ent, canvas, canvas.get_rect(), eff, rect_by_name=shifted)

    # Some effects include geometry transforms; apply them to the glyph canvas only.
    if eff:
        try:
            visual = build_visual_profile(VisualProfile(), eff)
            out = pygame.Surface(canvas.get_size(), pygame.SRCALPHA)
            apply_visual_panel(out, canvas, out.get_rect(), visual)
            return out
        except Exception:
            return canvas

    return canvas


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class InventoryListWidget(ListWidget):
    """ListWidget that draws entity glyphs with per-entity color/effects."""

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        font = self._pick_font(ctx)
        fg = getattr(ctx.renderer, "fg", (255, 255, 255))
        sel = getattr(
            ctx.renderer,
            "player_color",
            getattr(ctx.renderer, "sel", (255, 255, 0)),
        )

        cap = self._visible_capacity()
        start = self.scroll_offset
        end = min(len(self.items), start + cap)

        x0 = self.rect.x + self.padding
        y = self.rect.y + self.padding

        scene = ctx.scene
        scene_effects = list(getattr(scene, "visual_effects", []) or [])

        base_px = max(14, int(font.get_height() * 1.15))

        for idx in range(start, end):
            row = self.items[idx]
            ent = getattr(row, "ent", None)
            selected = (idx == self.selected_index)

            prefix = "▶ " if selected else "  "
            prefix_col = sel if selected else fg
            prefix_surf = font.render(prefix, True, prefix_col)
            ctx.surface.blit(prefix_surf, (x0, y))

            x = x0 + prefix_surf.get_width()

            if ent is not None:
                glyph_canvas = _render_entity_glyph_canvas(
                    ctx.renderer,
                    ent,
                    font=font,
                    base_px=base_px,
                    scene_effects=scene_effects,
                )
                ctx.surface.blit(
                    glyph_canvas,
                    (x, y - (glyph_canvas.get_height() - font.get_height()) // 2),
                )
                x += glyph_canvas.get_width() + int(font.size("  ")[0] * 0.5)

                name = getattr(ent, "name", None) or "(unnamed item)"
                name_col = sel if selected else fg
                name_surf = font.render(str(name), True, name_col)
                ctx.surface.blit(name_surf, (x, y))
            else:
                label = getattr(row, "label", str(row))
                label_col = sel if selected else fg
                surf = font.render(label, True, label_col)
                ctx.surface.blit(surf, (x, y))

            y += self._line_height

        Widget.draw(self, ctx)


class EntityPreviewWidget(Widget):
    """
    Right pane: magnified glyph preview.

    Diagrammatic zoom happens at the scene level (VisualProfile anchored on this
    pane's center), so the preview itself should be stable.

    During the zoom, the panel fades in but the glyph should remain opaque;
    InventoryScene re-draws the glyph as an opaque overlay layer, so this widget
    can skip drawing the glyph to avoid double brightening.
    """

    def __init__(self) -> None:
        super().__init__()
        self._font_cache: dict[int, pygame.font.Font] = {}

    def _get_font(self, size: int) -> pygame.font.Font:
        size = int(max(10, min(256, size)))
        f = self._font_cache.get(size)
        if f is None:
            f = pygame.font.SysFont("consolas", size, bold=True)
            self._font_cache[size] = f
        return f

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible or self.rect.width <= 0 or self.rect.height <= 0:
            return

        scene = ctx.scene
        renderer = ctx.renderer
        surf = ctx.surface

        owner = getattr(scene, "_find_owner_entity", lambda: None)()
        name = getattr(owner, "name", None) or getattr(scene, "explicit_title", None) or "Entity"
        glyph = str(getattr(owner, "glyph", "@"))[:1]

        r = self.rect
        card = pygame.Surface((r.w, r.h), pygame.SRCALPHA)

        bg = getattr(renderer, "bg", (10, 10, 20))
        fg = getattr(renderer, "fg", (240, 240, 255))

        fill = (min(255, bg[0] + 12), min(255, bg[1] + 12), min(255, bg[2] + 18), 235)
        card.fill(fill)
        pygame.draw.rect(card, (*fg, 120), card.get_rect(), 2, border_radius=10)

        title_font = getattr(renderer, "menu_font", None)
        if title_font is None:
            title_font = pygame.font.SysFont("consolas", 18, bold=True)
        ts = title_font.render("Inhabiting", True, fg)
        card.blit(ts, (14, 12))

        # Stable center in pane coords.
        cx = r.w * 0.50
        cy = r.h * 0.50

        # Optional label.
        nfont = pygame.font.SysFont("consolas", 16, bold=True)
        ns = nfont.render(str(name), True, fg)
        card.blit(ns, (14, 36))

        # Only draw the glyph here if the scene is NOT doing the external opaque overlay.
        if not bool(getattr(scene, "_external_opaque_glyph", False)):
            base_px = max(12, int(min(r.w, r.h) * 0.50))
            font = self._get_font(max(10, int(base_px)))
            gcanvas = _render_entity_glyph_canvas(
                renderer,
                owner if owner is not None else type("X", (), {"glyph": glyph})(),
                font=font,
                base_px=base_px,
                scene_effects=list(getattr(scene, "visual_effects", []) or []),
            )
            gx = int(cx - gcanvas.get_width() // 2)
            gy = int(cy - gcanvas.get_height() // 2)
            card.blit(gcanvas, (gx, gy))

        surf.blit(card, r.topleft)


class TwoPaneInventoryRoot(Widget):
    def __init__(
        self,
        *,
        header: Widget,
        left: Widget,
        right: Widget,
        footer: Widget,
        padding: int = 14,
        spacing: int = 12,
        col_spacing: int = 14,
        left_frac: float = 0.46,
        min_right_w: int = 220,
    ) -> None:
        super().__init__()
        self.header = header
        self.left = left
        self.right = right
        self.footer = footer

        self.padding = int(padding)
        self.spacing = int(spacing)
        self.col_spacing = int(col_spacing)
        self.left_frac = float(left_frac)
        self.min_right_w = int(min_right_w)

        self.add_child(self.header)
        self.add_child(self.left)
        self.add_child(self.right)
        self.add_child(self.footer)

    def layout(self, ctx: WidgetContext) -> None:
        for c in self.children:
            c.layout(ctx)

        if self.rect.width == 0 or self.rect.height == 0:
            self.rect = ctx.surface.get_rect()

        r = self.rect
        pad = self.padding

        # header at top
        self.header.rect.topleft = (r.x + pad, r.y + pad)

        # footer at bottom
        self.footer.rect.topleft = (r.x + pad, r.bottom - pad - self.footer.rect.height)

        # body region
        top = self.header.rect.bottom + self.spacing
        bottom = self.footer.rect.top - self.spacing
        avail_h = max(1, bottom - top)

        inner_w = max(1, r.width - 2 * pad)
        left_w = int(inner_w * self.left_frac)
        right_w = inner_w - left_w - self.col_spacing
        if right_w < self.min_right_w:
            right_w = self.min_right_w
            left_w = max(1, inner_w - right_w - self.col_spacing)

        # left column
        self.left.rect.topleft = (r.x + pad, top)
        self.left.rect.width = max(1, left_w)
        self.left.rect.height = avail_h
        self.left.layout(ctx)

        # right column
        rx = self.left.rect.right + self.col_spacing
        self.right.rect.topleft = (rx, top)
        self.right.rect.width = max(1, right_w)
        self.right.rect.height = avail_h
        self.right.layout(ctx)

    def draw(self, ctx: WidgetContext) -> None:
        for child in self.children:
            child.draw(ctx)


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------

@dataclass
class _InvRow:
    label: str
    ent: Any | None = None  # None for Back / Empty rows

    def __str__(self) -> str:
        return self.label


class InventoryScene(PopupMenuScene):
    """
    Two-pane inventory prototype (popup PanelScene):
      - Left: list of items (glyph + name), keyboard + mouse
      - Right: magnified “inhabited entity” preview
      - Activate: context menu (UrgentMessageScene)

    Diagrammatic zoom:
      - The panel transform is anchored on the preview glyph center.
      - That anchor point travels from the owner's *exact* map pixel to its final location.
      - Panel alpha fades in; glyph stays solid (drawn as an extra overlay pass).

    This version also scales proportionately:
      - Start scale is chosen so the *final preview glyph*, after transform,
        starts at the same on-screen size as the map glyph (respecting mousewheel zoom).
      - Then it lerps up to full panel size.
    """

    FOOTER_TEXT = "↑/↓ or W/S to move • Enter/Space to select • Esc/i to go back"

    ZOOM_MS: int = 900

    CLOSE_MS: int = 700

    # Artistic tweak multipliers (applied on top of the physically-derived scale).
    PANEL_SCALE_START: float = 1.00
    PANEL_SCALE_END: float = 1.00

    PANEL_ALPHA_START: float = 0.00
    PANEL_ALPHA_END: float = 1.00


    # Each nested inventory is multiplied by this factor (recursion CRT effect).
    DEPTH_SCALE: float = 0.90
    def __init__(
        self,
        game,
        *,
        owner_id: Optional[str] = None,
        window_rect: Optional[pygame.Rect] = None,
        parent_owner_id: Optional[str] = None,
        title: Optional[str] = None,
        base_effects: Optional[list[str]] = None,
        source_px: tuple[int, int] | None = None,
        stack_depth: int = 0,
        animate_affine: bool = False,
        ) -> None:
        self.game = game
        self.owner_id = owner_id
        self.parent_owner_id = parent_owner_id
        self.explicit_title = title

        self.visual_effects: list[str] = list(base_effects or [])

        self.stack_depth = int(stack_depth)

        # If True, we *animate* rotation/flips during the zoom.
        # Default is False: the panel starts already transformed (less distracting).
        self.animate_affine = bool(animate_affine)

        # Depth-based “CRT recursion” scaling that actually scales rendered text.
        self._depth_visual_scale = float(self.DEPTH_SCALE ** max(0, self.stack_depth))

        self._zoom_elapsed = 0
        self._zoom_progress = 0.0

        # Closing animation (reverse of the diagrammatic zoom).
        self._closing: bool = False
        self._close_elapsed: int = 0

        # Source in renderer.surface coords.
        self._source_from_parent_panel: bool = False

        self._zoom_source_px: tuple[int, int] | None = None
        self._zoom_owner_world: tuple[int, int] | None = None

        # Optional override: when opening a nested inventory, the source glyph
        # is often a glyph in the *parent* inventory list (not a world tile).
        if source_px is not None:
            self._source_from_parent_panel = True
            try:
                self._zoom_source_px = (int(source_px[0]), int(source_px[1]))
            except Exception:
                self._zoom_source_px = None

        # Cached map tile pixel size (respects mousewheel zoom).
        self._zoom_map_tile_px: int = 32

        # Panel-local anchor (center of the preview glyph) we keep glued to the source.
        self._zoom_anchor_panel: tuple[float, float] | None = None

        # Base pixel size of the *final* glyph inside the preview pane (panel space).
        self._zoom_glyph_base_px: int = 48

        # If we can derive the initial panel scale from glyph sizes, store it here.
        self._zoom_start_scale: float | None = None

        # Preview will skip glyph; we redraw it as an opaque overlay.
        self._external_opaque_glyph: bool = False

        self._rows: list[_InvRow] = []

        self._list: Optional[ListWidget] = None
        self._preview: Optional[EntityPreviewWidget] = None

        super().__init__(window_rect=window_rect, dim_background=True,
                         scale=0.78)
        self.overlay_layers = {"hud"}

        self._inherit_owner_visual_effects()

        # Cache owner's world position for source pixel calc.
        _owner = self._find_owner_entity()
        _pos = getattr(_owner, "pos", None)
        if _pos is not None:
            try:
                self._zoom_owner_world = (int(_pos[0]), int(_pos[1]))
            except Exception:
                self._zoom_owner_world = None

        self._refresh_rows()
        if self._list:
            self._list.set_items(self._rows)

    # ---------------------------------------------------------------------
    # Effects inheritance (names only)
    # ---------------------------------------------------------------------

    def _owner_id(self) -> str:
        return self.owner_id or self.game.player_id


    def _find_container_targets(self, exclude_id: Optional[str] = None) -> list[tuple[str, str]]:
        """
        Return a list of (owner_id, label) container inventories in the *same
        inventory space* as the currently viewed items.
        """
        space_owner_id = self._owner_id()
        inv = self.game.get_inventory(space_owner_id)
        candidates: list[tuple[str, str]] = []

        for ent in inv:
            tags = getattr(ent, "tags", {}) or {}
            if not tags.get("container"):
                continue
            ent_id = getattr(ent, "id", None)
            if exclude_id is not None and ent_id == exclude_id:
                continue
            name = getattr(ent, "name", None) or "Container"
            if ent_id is not None:
                candidates.append((str(ent_id), str(name)))

        return candidates

    @staticmethod
    def _is_berry_from_tags(tags: dict) -> bool:
        return bool(tags.get("test_berry")) or tags.get("item_type") in {
            "blueberry",
            "raspberry",
            "strawberry",
        }

    def _find_owner_entity(self):
        owner_id = self._owner_id()

        level = self.game._level()
        if level is not None:
            ent = level.entities.get(owner_id) or level.actors.get(owner_id)
            if ent is not None:
                return ent

        for cand in getattr(self.game, "player_inventory", []):
            if getattr(cand, "id", None) == owner_id:
                return cand

        for inv_list in getattr(self.game, "inventories", {}).values():
            for cand in inv_list:
                if getattr(cand, "id", None) == owner_id:
                    return cand

        return None

    def _inherit_owner_visual_effects(self) -> None:
        ent = self._find_owner_entity()
        if ent is None:
            return
        self.visual_effects = concat_effect_names(self.visual_effects, effect_names_from_obj(ent))

    # ---------------------------------------------------------------------
    # Inventory rows
    # ---------------------------------------------------------------------

    def _refresh_rows(self) -> None:
        owner_id = self._owner_id()
        inv = self.game.get_inventory(owner_id)

        rows: list[_InvRow] = []
        if inv:
            for ent in inv:
                name = getattr(ent, "name", None) or "(unnamed item)"
                glyph = getattr(ent, "glyph", None) or "?"
                rows.append(_InvRow(f"{str(glyph)[:1]}  {name}", ent=ent))
        else:
            rows.append(_InvRow("(Empty)", ent=None))

        rows.append(_InvRow("Back", ent=None))
        self._rows = rows

    # ---------------------------------------------------------------------
    # GeneralMenuScene hooks
    # ---------------------------------------------------------------------

    def get_ascii_art(self) -> str:
        return ""

    def get_body_text(self) -> Optional[str]:
        return None

    def get_menu_items(self) -> list[Any]:
        self._refresh_rows()
        return list(self._rows)

    def wants_wrapped_choices(self) -> bool:
        return False
    # -----------------------------------------------------------------
    # Reverse-zoom pop interception (SceneManager.pop_scene hook)
    # -----------------------------------------------------------------

    def begin_pop(self, manager: "SceneManager") -> bool:
        """Start the closing (reverse) zoom animation instead of popping immediately."""
        if self._closing:
            return True
        self._closing = True
        self._close_elapsed = 0
        return True

    def handle_event(self, event, manager: "SceneManager") -> None:
        # While closing, swallow inputs so the selection doesn't jitter mid-collapse.
        if self._closing:
            return
        return super().handle_event(event, manager)


    def on_back(self, manager: "SceneManager") -> None:
        if hasattr(manager, "pop_scene"):
            manager.pop_scene()
        else:
            manager.set_scene(None)

    def on_activate(self, index: int, manager: "SceneManager") -> bool:
        """
        Selecting an item opens a context submenu:
          - Player inventory: Drop / Eat / Put into...
          - Other inventories: Take / Eat / Put into...
          - If item is a container: Open
        """
        self._refresh_rows()
        rows = self._rows
        if index < 0 or index >= len(rows):
            return False

        row = rows[index]

        # 'Back' (or the '(Empty)' row) → close inventory
        if row.ent is None:
            return self.on_back(manager)

        ent = row.ent
        tags = getattr(ent, "tags", {}) or {}

        is_container = bool(tags.get("container"))
        is_berry = self._is_berry_from_tags(tags)

        choices: list[str] = []

        owner_id = self._owner_id()
        container_targets = self._find_container_targets(exclude_id=getattr(ent, "id", None))

        if owner_id == self.game.player_id:
            choices.append("Drop")
            if is_berry or is_container:
                choices.append("Eat")
            if container_targets:
                choices.append("Put into...")
        else:
            choices.append("Take")
            if is_berry:
                choices.append("Eat")
            if container_targets:
                choices.append("Put into...")

        if is_container:
            choices.append("Open")

        if not choices:
            return False

        def _handle_choice(choice_idx: int, mgr: "SceneManager") -> None:
            if choice_idx < 0 or choice_idx >= len(choices):
                return
            choice = choices[choice_idx]

            # Re-fetch inventory in case it changed while popup was open.
            current_owner_id = self._owner_id()
            cur_inv = self.game.get_inventory(current_owner_id)
            if not cur_inv:
                return

            # Re-find this entity by identity if possible (more robust than index when list changes).
            try:
                cur_index = cur_inv.index(ent)
            except Exception:
                # Fallback: trust the original index if it still maps to an entity.
                cur_index = index

            if cur_index < 0 or cur_index >= len(cur_inv):
                return

            cur_ent = cur_inv[cur_index]
            cur_tags = getattr(cur_ent, "tags", {}) or {}
            cur_is_container = bool(cur_tags.get("container"))

            if choice == "Drop" and current_owner_id == self.game.player_id:
                if hasattr(self.game, "drop_inventory_item"):
                    self.game.drop_inventory_item(cur_index)
                return

            if choice == "Take" and current_owner_id != self.game.player_id:
                dest_owner_id = self.parent_owner_id or self.game.player_id
                if hasattr(self.game, "move_item_between_inventories"):
                    self.game.move_item_between_inventories(
                        current_owner_id,
                        cur_index,
                        dest_owner_id,
                    )
                return

            if choice == "Put into...":
                targets = self._find_container_targets(exclude_id=getattr(cur_ent, "id", None))
                if not targets:
                    return

                target_labels = [label for (_oid, label) in targets]

                def on_target_choice(target_idx: int, mgr2: "SceneManager") -> None:
                    if target_idx < 0 or target_idx >= len(targets):
                        return

                    dest_owner_id, _dest_label = targets[target_idx]

                    src_owner_id = self._owner_id()
                    src_inv = self.game.get_inventory(src_owner_id)
                    if not src_inv:
                        return

                    try:
                        src_index = src_inv.index(cur_ent)
                    except Exception:
                        src_index = cur_index

                    if not (0 <= src_index < len(src_inv)):
                        return

                    if hasattr(self.game, "move_item_between_inventories"):
                        self.game.move_item_between_inventories(
                            src_owner_id,
                            src_index,
                            dest_owner_id,
                        )

                mgr.push_scene(
                    UrgentMessageScene(
                        self.game,
                        "",
                        title="Put into which container?",
                        choices=target_labels,
                        on_choice=on_target_choice,
                        back_confirms=False,
                    )
                )
                return

            if choice == "Open" and cur_is_container:
                nested_owner_id = getattr(cur_ent, "id", None)
                if nested_owner_id is None:
                    return

                # When opening a nested inventory, we want the new panel to "emerge"
                # from the glyph that represents this item in the *current* list.
                src_px = self._row_glyph_screen_px(index, mgr)

                mgr.push_scene(
                    InventoryScene(
                        self.game,
                        owner_id=str(nested_owner_id),
                        parent_owner_id=self._owner_id(),
                        title=getattr(cur_ent, "name", None) or "Container",
                        base_effects=list(self.visual_effects),
                        source_px=src_px,
                        stack_depth=self.stack_depth + 1,
                    )
                )
                return

            if choice == "Eat":
                # Recompute berry/container flags (inventory may have changed)
                cur_is_berry = self._is_berry_from_tags(cur_tags)

                if cur_is_container and not cur_is_berry:
                    # --- Eat the inventory (container), recursively -----------------------

                    # 1) Remove the container item itself from the current inventory
                    eaten_ent = cur_inv.pop(cur_index)
                    eaten_id = getattr(eaten_ent, "id", None)

                    # 2) Walk the inventory tree, collecting effects from:
                    #    - the container itself
                    #    - every item inside it
                    #    - every nested container and its contents, recursively
                    all_effects: list[str] = []
                    all_effects = concat_effect_names(all_effects, effect_names_from_obj(eaten_ent))

                    def _consume_inventory_tree(owner_id2: str, visited: set[str]) -> None:
                        if not owner_id2 or owner_id2 in visited:
                            return
                        visited.add(owner_id2)

                        inv_map = getattr(self.game, "inventories", None)
                        if not isinstance(inv_map, dict):
                            return

                        inv_list = inv_map.get(owner_id2)
                        if not inv_list:
                            inv_map.pop(owner_id2, None)
                            return

                        for child in list(inv_list):
                            nonlocal all_effects
                            all_effects = concat_effect_names(all_effects, effect_names_from_obj(child))

                            child_id = getattr(child, "id", None)
                            child_tags = getattr(child, "tags", {}) or {}
                            child_is_container = bool(child_tags.get("container"))

                            # If a berry is inside, try to "eat" it so HP/log happen.
                            child_is_berry = self._is_berry_from_tags(child_tags)
                            if child_is_berry:
                                try:
                                    if hasattr(self.game, "eat_item_from_inventory"):
                                        idx2 = inv_list.index(child)
                                        self.game.eat_item_from_inventory(owner_id2, idx2)
                                    elif owner_id2 == self.game.player_id and hasattr(self.game, "eat_inventory_item"):
                                        idx2 = inv_list.index(child)
                                        self.game.eat_inventory_item(idx2)
                                except Exception:
                                    pass

                            if child_is_container and child_id and child_id in inv_map:
                                _consume_inventory_tree(str(child_id), visited)

                        inv_map.pop(owner_id2, None)

                    if eaten_id and hasattr(self.game, "inventories"):
                        _consume_inventory_tree(str(eaten_id), set())

                    # 3) Apply ALL collected effects globally (stacking)
                    if all_effects:
                        try:
                            existing = list(getattr(mgr.renderer.visual_fx, "global_effects", []) or [])
                        except Exception:
                            existing = []
                        if hasattr(mgr, "set_global_visual_effects"):
                            mgr.set_global_visual_effects(concat_effect_names(existing, all_effects))

                    # 4) Log
                    if hasattr(self.game, "log") and hasattr(self.game.log, "add"):
                        self.game.log.add("You're not sure if you should have eaten that inventory...")
                        seen: set[str] = set()
                        for eff in all_effects:
                            if eff in seen:
                                continue
                            seen.add(eff)
                            self.game.log.add(f"You feel {eff}.")
                    return

                # Otherwise: eating a berry directly from this inventory menu
                if cur_is_berry:
                    if hasattr(self.game, "eat_item_from_inventory"):
                        self.game.eat_item_from_inventory(current_owner_id, cur_index)
                    elif current_owner_id == self.game.player_id and hasattr(self.game, "eat_inventory_item"):
                        self.game.eat_inventory_item(cur_index)
                return

        manager.push_scene(
            UrgentMessageScene(
                self.game,
                "",
                title=getattr(ent, "name", None) or "",
                choices=choices,
                on_choice=_handle_choice,
                back_confirms=False,
            )
        )

        return True

    # ---------------------------------------------------------------------
    # Widget tree
    # ---------------------------------------------------------------------

    def _header_title(self) -> str:
        if self.explicit_title:
            return str(self.explicit_title)
        owner = self._find_owner_entity()
        if owner is not None:
            return str(getattr(owner, "name", "Inventory"))
        return "Inventory"

    def _build_widgets(self, items: list[Any]) -> None:
        title = self._header_title()
        header = ScaledLabelWidget(title, align="left", scale=2)

        left_col = VBox(spacing=8, padding=0, align="left")
        left_col.add_child(LabelWidget("Contents", align="left"))

        self._refresh_rows()
        self._list = InventoryListWidget(
            self._rows,
            selected_index=self.selected_idx,
            on_activate=self._on_list_activate,  # provided by GeneralMenuScene
            line_spacing=6,
            padding=6,
            auto_font=True,
            min_font_size=18,
            max_font_size=48,
            target_visible_items=16,
        )
        left_col.add_child(self._list)

        self._preview = EntityPreviewWidget()
        footer = LabelWidget(self.FOOTER_TEXT, align="left")

        self.root = TwoPaneInventoryRoot(
            header=header,
            left=left_col,
            right=self._preview,
            footer=footer,
            padding=14,
            spacing=12,
            col_spacing=14,
            left_frac=0.48,
        )
        self.root.rect = pygame.Rect(0, 0, 0, 0)

    # ---------------------------------------------------------------------
    # Animation
    # ---------------------------------------------------------------------

    # ---------------------------------------------------------------------
    # Source glyph utilities (for nested inventory zooms)
    # ---------------------------------------------------------------------

    def _project_point_window_to_screen(self, pt: tuple[float, float], visual: VisualProfile) -> tuple[int, int]:
        """Project a point in window_rect-local coords through a VisualProfile into renderer.surface coords."""
        assert self.window_rect is not None
        x, y = float(pt[0]), float(pt[1])
        cx, cy = float(self.window_rect.w) * 0.5, float(self.window_rect.h) * 0.5
        # to center
        dx, dy = x - cx, y - cy
        # scale
        sx, sy = float(getattr(visual, "scale_x", 1.0)), float(getattr(visual, "scale_y", 1.0))
        dx *= sx
        dy *= sy
        # flips
        if getattr(visual, "flip_x", False):
            dx = -dx
        if getattr(visual, "flip_y", False):
            dy = -dy
        # rotate
        ang = float(getattr(visual, "angle", 0.0))
        if ang:
            rad = math.radians(ang)
            c = math.cos(rad)
            s = math.sin(rad)
            dx, dy = (dx * c - dy * s, dx * s + dy * c)
        # translate
        ox = float(self.window_rect.centerx) + float(getattr(visual, "offset_x", 0.0))
        oy = float(self.window_rect.centery) + float(getattr(visual, "offset_y", 0.0))
        return (int(round(ox + dx)), int(round(oy + dy)))

    def _row_glyph_screen_px(self, row_index: int, manager: "SceneManager") -> tuple[int, int] | None:
        """Return the screen pixel where the given inventory row's glyph is drawn (approx)."""
        try:
            renderer = manager.renderer
        except Exception:
            return None

        # Ensure we have a window rect and a freshly-laid-out widget tree.
        self._ensure_window_rect(manager)
        if self.window_rect is None or self._list is None:
            return None

        panel = self._get_panel(manager)
        # Layout widgets into the panel surface (no present).
        try:
            self.draw_panel(panel, renderer, manager)
        except Exception:
            pass

        lst = self._list
        try:
            ctx = WidgetContext(surface=panel, game=getattr(self, "game", None), scene=self, renderer=renderer)
            font = lst._pick_font(ctx)  # type: ignore[attr-defined]
            line_h = int(getattr(lst, "_line_height", font.get_height()))
        except Exception:
            font = getattr(renderer, "menu_font", getattr(renderer, "font", None))
            if font is None:
                return None
            line_h = font.get_height()

        cap = 0
        try:
            cap = int(lst._visible_capacity())  # type: ignore[attr-defined]
        except Exception:
            cap = max(1, (lst.rect.h - 2 * int(getattr(lst, "padding", 0))) // max(1, line_h))

        start = int(getattr(lst, "scroll_offset", 0))
        # If the requested row isn't visible, approximate it at the closest visible slot.
        vis_i = row_index
        if vis_i < start:
            vis_i = start
        if vis_i >= start + max(1, cap):
            vis_i = start + max(1, cap) - 1

        pad = int(getattr(lst, "padding", 0))
        x0 = float(lst.rect.x + pad)
        y0 = float(lst.rect.y + pad)
        y = y0 + float((vis_i - start) * line_h)

        # Prefix width ("▶ " or "  ")
        prefix_w = 0
        try:
            prefix_w = int(font.size("▶ ")[0])
        except Exception:
            prefix_w = 0

        base_px = max(14, int(font.get_height() * 1.15))
        # Glyph center in PANEL coords.
        gx = x0 + float(prefix_w) + float(base_px) * 0.5
        gy = y + float(font.get_height()) * 0.5

        # Convert PANEL coords -> WINDOW coords (if logical panel != window size).
        pw, ph = panel.get_size()
        sx = float(self.window_rect.w) / float(max(1, pw))
        sy = float(self.window_rect.h) / float(max(1, ph))
        win_pt = (gx * sx, gy * sy)

        # With the menu fully open, use the current VisualProfile at p=1.0.
        visual = self._current_visual_profile(logical_to_window_scale=min(sx, sy))
        return self._project_point_window_to_screen(win_pt, visual)

    def update(self, dt_ms: int, manager: "SceneManager") -> None:
        self._refresh_rows()
        if self._list:
            self._list.set_items(self._rows)

        # Opening (zoom-in) vs closing (zoom-out) animation.
        if not self._closing:
            self._zoom_elapsed = min(self.ZOOM_MS, self._zoom_elapsed + int(dt_ms))
            self._zoom_progress = _clamp01(self._zoom_elapsed / float(max(1, self.ZOOM_MS)))
        else:
            self._close_elapsed = min(self.CLOSE_MS, self._close_elapsed + int(dt_ms))
            t = _clamp01(self._close_elapsed / float(max(1, self.CLOSE_MS)))
            self._zoom_progress = _clamp01(1.0 - t)
            if t >= 1.0:
                # Finish: pop ourselves for real (without re-triggering begin_pop).
                if hasattr(manager, "_force_pop_scene"):
                    manager._force_pop_scene()  # type: ignore[attr-defined]
                else:
                    try:
                        manager.pop_scene(force=True)  # type: ignore[call-arg]
                    except Exception:
                        manager.pop_scene()
                return

        # Cache map tile pixel size (tracks mousewheel zoom).
        try:
            renderer = getattr(manager, "renderer", None)
            if renderer is not None and hasattr(renderer, "tile"):
                self._zoom_map_tile_px = int(getattr(renderer, "tile"))
        except Exception:
            pass

        # Compute the pixel position of the owner entity in screen space (renderer surface coords).
        if self._zoom_source_px is None and self._zoom_owner_world is not None:
            try:
                renderer = getattr(manager, "renderer", None)
                if renderer is not None and hasattr(renderer, "tile"):
                    tx, ty = self._zoom_owner_world
                    px = int(tx * int(renderer.tile) + int(getattr(renderer, "origin_x", 0)) + int(renderer.tile) // 2)
                    py = int(ty * int(renderer.tile) + int(getattr(renderer, "origin_y", 0)) + int(renderer.tile) // 2)
                    self._zoom_source_px = (px, py)
            except Exception:
                self._zoom_source_px = None

        super().update(dt_ms, manager)

    # ---------------------------------------------------------------------
    # Diagrammatic zoom transform
    # ---------------------------------------------------------------------

    def _current_visual_profile(self, *, logical_to_window_scale: float = 1.0) -> VisualProfile:
        """Compute the *current* VisualProfile for the panel.

        Goals:
        - successive recursion scaling (handled via PopupMenuScene scale + extra profile scaling here)
        - zoom-in/out that respects *all* affine bits from accumulated visual effects:
          scale, rotation, flips, offsets (e.g. clockwise, mirror_x), plus time-varying effects.
        - keep the preview glyph anchor glued to the source point during the transition.
        """
        # Start with inherited scene effects (time-based too).
        base = build_visual_profile(VisualProfile(), self.visual_effects)

        p = _smoothstep(_clamp01(float(self._zoom_progress)))

        # --- Affine animation (optional) -------------------------------------
        # By default we *do not* animate rotation/flips during the zoom.
        # The panel starts already transformed, matching the list glyph.
        if self.animate_affine:
            # Rotation ramps in.
            try:
                base.angle = _lerp(0.0, float(base.angle), p)
            except Exception:
                pass

            # Flip can't be smoothly interpolated (boolean), but we can time it.
            flip_gate = 0.35
            if getattr(base, "flip_x", False):
                base.flip_x = bool(p >= flip_gate)
            if getattr(base, "flip_y", False):
                base.flip_y = bool(p >= flip_gate)

        # --- Proportional scale ----------------------------------------------
        # We want: (panel scale) * (final glyph px) ~= (map tile px) at p=0.
        glyph_full_px = max(1.0, float(self._zoom_glyph_base_px) * float(max(0.01, logical_to_window_scale)))
        want_px = float(max(1, int(self._zoom_map_tile_px)))
        if self._source_from_parent_panel:
            # When the zoom source is a glyph inside a *parent* inventory panel,
            # that glyph is already scaled down by the parent’s recursion factor.
            parent_scale = float(self._depth_visual_scale) / float(max(0.0001, self.DEPTH_SCALE))
            want_px *= parent_scale
            want_px = max(6.0, want_px)


        if self._zoom_start_scale is None:
            s0 = want_px / glyph_full_px
            # Clamp so the whole menu doesn't become astronomically tiny on extreme zoom-out.
            s0 = max(0.04, min(1.0, float(s0)))
            self._zoom_start_scale = float(s0)

        start_scale = float(self._zoom_start_scale)
        panel_scale = _lerp(start_scale, 1.0, p)

        # Apply recursion depth scaling at all times (scales text, glyphs, spacing).
        panel_scale *= float(self._depth_visual_scale)

        panel_scale *= _lerp(self.PANEL_SCALE_START, self.PANEL_SCALE_END, p)

        # Fade the PANEL only.
        zoom_alpha = _lerp(self.PANEL_ALPHA_START, self.PANEL_ALPHA_END, p)
        base.alpha = float(base.alpha) * float(zoom_alpha)

        # Apply zoom scale multiplicatively (preserve other effect scales).
        base.scale_x = float(base.scale_x) * float(panel_scale)
        base.scale_y = float(base.scale_y) * float(panel_scale)

        # --- Anchor glue (full affine) ---------------------------------------
        # Solve for (delta_offset_x, delta_offset_y) so that the panel-local anchor
        # lands on the desired screen point, *after* scale/flip/rotate.
        if self.window_rect is not None:
            ax, ay = (self._zoom_anchor_panel or (self.window_rect.width * 0.5, self.window_rect.height * 0.5))
            cx, cy = (self.window_rect.width * 0.5, self.window_rect.height * 0.5)

            # Vector from panel center to anchor, in panel-local coords.
            dx = float(ax) - float(cx)
            dy = float(ay) - float(cy)

            # Apply flip then scale.
            if getattr(base, "flip_x", False):
                dx = -dx
            if getattr(base, "flip_y", False):
                dy = -dy

            dx *= float(base.scale_x)
            dy *= float(base.scale_y)

            # Rotate about the panel center.
            ang = float(getattr(base, "angle", 0.0))
            if ang:
                th = math.radians(ang)
                cth = math.cos(th)
                sth = math.sin(th)
                rdx = dx * cth - dy * sth
                rdy = dx * sth + dy * cth
            else:
                rdx, rdy = dx, dy

            # Where should the anchor end up when the zoom is finished?
            # Use the *un-glued* base offsets so orbiting/jittery offsets remain part of the final position.
            final_anchor_x = float(self.window_rect.centerx) + float(base.offset_x) + float(rdx)
            final_anchor_y = float(self.window_rect.centery) + float(base.offset_y) + float(rdy)

            if self._zoom_source_px is not None:
                sx, sy = self._zoom_source_px
                desired_x = _lerp(float(sx), float(final_anchor_x), p)
                desired_y = _lerp(float(sy), float(final_anchor_y), p)
            else:
                desired_x, desired_y = float(final_anchor_x), float(final_anchor_y)

            # Current anchor position without glue:
            cur_anchor_x = float(self.window_rect.centerx) + float(base.offset_x) + float(rdx)
            cur_anchor_y = float(self.window_rect.centery) + float(base.offset_y) + float(rdy)

            # Add just the delta needed to move anchor onto desired.
            base.offset_x = float(base.offset_x) + (float(desired_x) - float(cur_anchor_x))
            base.offset_y = float(base.offset_y) + (float(desired_y) - float(cur_anchor_y))

        return base

    # ---------------------------------------------------------------------
    # Panel drawing + opaque glyph overlay
    # ---------------------------------------------------------------------

    def draw_panel(self, panel: pygame.Surface, renderer, manager: "SceneManager") -> None:
        # Force external glyph overlay mode (panel fades, glyph stays solid).
        self._external_opaque_glyph = True

        super().draw_panel(panel, renderer, manager)

        # Cache anchor + glyph size from preview pane after layout.
        try:
            if self._preview is not None:
                self._zoom_anchor_panel = (float(self._preview.rect.centerx), float(self._preview.rect.centery))
                self._zoom_glyph_base_px = max(12, int(min(self._preview.rect.w, self._preview.rect.h) * 0.50))
        except Exception:
            pass

    def render(self, renderer, manager: "SceneManager") -> None:
        """
        Override render so we can:
          1) draw the panel with a fading/scaling VisualProfile
          2) then redraw the anchor glyph as a separate *opaque* overlay using the same transform
        """
        self._ensure_window_rect(manager)
        assert self.window_rect is not None

        self.draw_underlay(renderer, manager)

        panel = self._get_panel(manager)
        self.draw_panel(panel, renderer, manager)

        # If logical != window_rect, scale panel to window_rect.size BEFORE VisualProfile.
        logical = self.get_logical_panel_size(manager)
        panel_to_blit = panel
        logical_to_window = 1.0
        if logical is not None and panel.get_size() != self.window_rect.size:
            try:
                panel_to_blit = pygame.transform.smoothscale(panel, self.window_rect.size)
                logical_w, logical_h = panel.get_size()
                logical_to_window = min(
                    self.window_rect.w / max(1, logical_w),
                    self.window_rect.h / max(1, logical_h),
                )
            except Exception:
                panel_to_blit = panel
                logical_to_window = 1.0

        # Apply the diagrammatic zoom transform (fading + proportional scaling + anchor glue).
        visual = self._current_visual_profile(logical_to_window_scale=float(logical_to_window))
        apply_visual_panel(renderer.surface, panel_to_blit, self.window_rect, visual)

        # Redraw the glyph as an opaque overlay at the anchor point using the SAME transform.
        owner = self._find_owner_entity()
        if owner is not None and self._zoom_anchor_panel is not None:
            try:
                glyph_layer = pygame.Surface(self.window_rect.size, pygame.SRCALPHA)

                # Anchor location in the pre-transform panel space.
                ax, ay = self._zoom_anchor_panel

                # If we scaled the logical surface to window size, the anchor point lives in
                # window_rect coords already (because we blit a window-sized panel_to_blit).
                # So we do NOT rescale ax/ay here: they're panel_to_blit coords.
                base_px = max(8, int(self._zoom_glyph_base_px * float(max(0.25, logical_to_window))))
                font = pygame.font.SysFont("consolas", max(10, int(base_px)), bold=True)

                gcanvas = _render_entity_glyph_canvas(
                    renderer,
                    owner,
                    font=font,
                    base_px=base_px,
                    scene_effects=list(getattr(self, "visual_effects", []) or []),
                )

                gx = int(ax - gcanvas.get_width() // 2)
                gy = int(ay - gcanvas.get_height() // 2)
                glyph_layer.blit(gcanvas, (gx, gy))

                # Apply the exact same transform but force alpha to 1.0.
                visual_g = VisualProfile(
                    scale_x=visual.scale_x,
                    scale_y=visual.scale_y,
                    offset_x=visual.offset_x,
                    offset_y=visual.offset_y,
                    angle=visual.angle,
                    alpha=1.0,
                    flip_x=visual.flip_x,
                    flip_y=visual.flip_y,
                )
                apply_visual_panel(renderer.surface, glyph_layer, self.window_rect, visual_g)
            except Exception:
                pass

        if getattr(renderer, "suspend_present", False):
            return

        if hasattr(renderer, "present"):
            renderer.present()
        else:
            pygame.display.flip()