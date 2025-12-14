# edgecaster/ui/widgets.py

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Callable, Optional, Any
import pygame


@dataclass
class WidgetContext:
    """
    Lightweight context passed into widget methods.

    - surface: the logical surface the widget should draw into
    - game:    the current Game object (for state)
    - scene:   the owning Scene (or None if not relevant)
    - renderer: the active renderer (AsciiRenderer or some future variant)
    """
    surface: pygame.Surface
    game: object
    scene: object | None
    renderer: object


class Widget:
    """
    Minimal base class for UI widgets.

    Responsibilities:
    - Keep a rect in local coordinates (for layout and hit-testing).
    - Optionally have children.
    - Provide overridable hooks: layout / draw / handle_event / update.

    This is intentionally small so it can be reused in any scene
    without dragging in pygame event or Scene-specific logic.
    """

    def __init__(self) -> None:
        # Panel-local rectangle; subclasses or layout managers set this.
        self.rect: pygame.Rect = pygame.Rect(0, 0, 0, 0)
        self.visible: bool = True
        self.enabled: bool = True
        self.children: List[Widget] = []

    # ---- composition helpers -------------------------------------------------

    def add_child(self, child: "Widget") -> None:
        self.children.append(child)

    # ---- lifecycle hooks -----------------------------------------------------

    def layout(self, ctx: WidgetContext) -> None:
        """
        Compute or update self.rect (and children) based on the current surface.
        Default implementation just forwards to children.
        """
        for child in self.children:
            child.layout(ctx)

    def draw(self, ctx: WidgetContext) -> None:
        """
        Draw this widget (and children) into ctx.surface.
        Default implementation only draws children if visible.
        """
        if not self.visible:
            return
        for child in self.children:
            child.draw(ctx)

    def handle_event(self, event, ctx: WidgetContext) -> bool:
        """
        Give this widget a chance to consume an event.
        Return True if the event is handled and should not propagate further.

        Default behaviour: give children a chance, from topmost to bottom.
        """
        # Iterate reversed so later-added children are treated as “on top”.
        for child in reversed(self.children):
            if child.handle_event(event, ctx):
                return True
        return False

    def update(self, dt_ms: int, ctx: WidgetContext) -> None:
        """
        Optional per-frame update hook (for animations, timers, etc.).
        Default: forward to children.
        """
        for child in self.children:
            child.update(dt_ms, ctx)


class HUDWidget(Widget):
    """Thin adapter between the renderer's existing HUD methods and the
    generic Widget API.

    For now this just forwards to:

        renderer.draw_status(game)
        renderer.draw_log(game)
        renderer.draw_ability_bar(game)

    so behaviour is unchanged. Later we can break these out into separate
    widgets, or swap in a different HUD layout entirely.
    """

    def layout(self, ctx: WidgetContext) -> None:
        # HUD always spans the full logical surface for now.
        self.rect = ctx.surface.get_rect()
        # No children yet, but call base implementation for future-proofing.
        super().layout(ctx)

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        renderer = ctx.renderer
        game = ctx.game
        if game is None:
            return

        # These are methods on AsciiRenderer; we defensively check for them
        # so the widget type can in principle be reused in tests.
        if hasattr(renderer, "draw_status"):
            renderer.draw_status(game)  # type: ignore[call-arg]
        if hasattr(renderer, "draw_log"):
            renderer.draw_log(game)  # type: ignore[call-arg]
        if hasattr(renderer, "draw_ability_bar"):
            renderer.draw_ability_bar(game)  # type: ignore[call-arg]

        # Children (if any) draw on top.
        super().draw(ctx)


class LabelWidget(Widget):
    def __init__(
        self,
        text: str,
        *,
        color: Optional[tuple[int, int, int]] = None,
        font: Optional[pygame.font.Font] = None,
        padding: int = 0,
        align: str = "left",  # "left" | "center" | "right"
    ) -> None:
        super().__init__()
        self.text = text
        self.color = color
        self.font = font
        self.padding = padding
        self.align = align

    def layout(self, ctx: WidgetContext) -> None:
        font = self.font or getattr(
            ctx.renderer,
            "small_font",
            getattr(ctx.renderer, "font"),
        )
        w, h = font.size(self.text)
        # If rect.x/rect.y were already chosen by a container, we leave them.
        self.rect.width = w + 2 * self.padding
        self.rect.height = h + 2 * self.padding
        super().layout(ctx)

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        font = self.font or getattr(
            ctx.renderer,
            "small_font",
            getattr(ctx.renderer, "font"),
        )
        color = self.color or getattr(ctx.renderer, "fg", (255, 255, 255))
        text_surf = font.render(self.text, True, color)

        x = self.rect.x + self.padding
        if self.align == "center":
            x = self.rect.x + (self.rect.width - text_surf.get_width()) // 2
        elif self.align == "right":
            x = self.rect.right - self.padding - text_surf.get_width()
        y = self.rect.y + self.padding

        ctx.surface.blit(text_surf, (x, y))
        # Children (if any) draw on top.
        super().draw(ctx)


class MultiLineLabelWidget(Widget):
    """A label that supports '\\n' newlines by drawing one line at a time.

    Used for legacy menu 'ASCII art' banners and popup text blocks (death/level-up/dialogue)
    that were previously rendered line-by-line.
    """

    def __init__(
        self,
        text: str,
        *,
        color: Optional[tuple[int, int, int]] = None,
        font: Optional[pygame.font.Font] = None,
        padding: int = 0,
        align: str = "left",  # "left" | "center" | "right"
        line_spacing: int = 0,
    ) -> None:
        super().__init__()
        self.text = text
        self.color = color
        self.font = font
        self.padding = padding
        self.align = align
        self.line_spacing = line_spacing

    def layout(self, ctx: WidgetContext) -> None:
        font = self.font or getattr(
            ctx.renderer,
            "small_font",
            getattr(ctx.renderer, "font"),
        )
        lines = self.text.splitlines() or [""]
        widths = [font.size(line)[0] for line in lines]
        w = max(widths) if widths else 0
        h_line = font.get_height()
        h = len(lines) * h_line + max(0, len(lines) - 1) * self.line_spacing
        self.rect.width = w + 2 * self.padding
        self.rect.height = h + 2 * self.padding

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        font = self.font or getattr(
            ctx.renderer,
            "small_font",
            getattr(ctx.renderer, "font"),
        )
        col = self.color or getattr(ctx.renderer, "fg", (220, 220, 220))
        lines = self.text.splitlines() or [""]

        x0 = self.rect.x + self.padding
        y = self.rect.y + self.padding
        h_line = font.get_height()

        for line in lines:
            surf = font.render(line, True, col)
            if self.align == "center":
                x = x0 + (self.rect.width - 2 * self.padding - surf.get_width()) // 2
            elif self.align == "right":
                x = x0 + (self.rect.width - 2 * self.padding - surf.get_width())
            else:
                x = x0
            ctx.surface.blit(surf, (x, y))
            y += h_line + self.line_spacing

        super().draw(ctx)


class ButtonWidget(Widget):
    def __init__(
        self,
        text: str,
        *,
        on_click: Optional[Callable[["ButtonWidget"], None]] = None,
        hotkey: Optional[int] = None,
        padding_x: int = 12,
        padding_y: int = 4,
    ) -> None:
        super().__init__()
        self.text = text
        self.on_click = on_click
        self.hotkey = hotkey
        self.padding_x = padding_x
        self.padding_y = padding_y
        self.hovered = False
        self.pressed = False

    def layout(self, ctx: WidgetContext) -> None:
        font = getattr(
            ctx.renderer,
            "font",
            getattr(ctx.renderer, "small_font"),
        )
        w, h = font.size(self.text)
        self.rect.width = w + 2 * self.padding_x
        self.rect.height = h + 2 * self.padding_y
        super().layout(ctx)

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        font = getattr(
            ctx.renderer,
            "font",
            getattr(ctx.renderer, "small_font"),
        )
        fg = getattr(ctx.renderer, "fg", (255, 255, 255))
        sel = getattr(ctx.renderer, "sel", (255, 255, 0))
        dim = getattr(ctx.renderer, "dim", (150, 150, 150))

        bg_col = (30, 30, 50)
        border_col = sel if (self.hovered or self.pressed) else dim

        pygame.draw.rect(ctx.surface, bg_col, self.rect)
        pygame.draw.rect(ctx.surface, border_col, self.rect, 1)

        text_surf = font.render(self.text, True, fg)
        tx = self.rect.x + (self.rect.width - text_surf.get_width()) // 2
        ty = self.rect.y + (self.rect.height - text_surf.get_height()) // 2
        ctx.surface.blit(text_surf, (tx, ty))

        super().draw(ctx)

    def handle_event(self, event, ctx: WidgetContext) -> bool:
        if not (self.visible and self.enabled):
            return False

        if event.type == pygame.MOUSEMOTION:
            self.hovered = self.rect.collidepoint(event.pos)

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.pressed = True
                return True  # consume click-down

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            was_pressed = self.pressed
            self.pressed = False
            if was_pressed and self.rect.collidepoint(event.pos):
                if self.on_click:
                    self.on_click(self)
                return True  # consume click-up

        # Optional: hotkey (keyboard) support later.
        return super().handle_event(event, ctx)


class ListWidget(Widget):
    """
    Simple vertical list for left-hand “choices” panes.

    Items can be strings, or any object with .label or .name; falls
    back to str(item). Selection is tracked internally; activation is
    reported via on_activate(index, item).

    Now supports:
      - scroll_offset (for long lists)
      - mousewheel scrolling
      - keeping selected item visible
    """

    def __init__(
        self,
        items: List[Any],
        *,
        selected_index: int = 0,
        on_activate: Optional[Callable[[int, Any], None]] = None,
        line_spacing: int = 2,
        padding: int = 4,
    ) -> None:
        super().__init__()
        self.items = items
        self.selected_index = selected_index
        self.on_activate = on_activate
        self.line_spacing = line_spacing
        self.padding = padding
        self._line_height: int = 0

        # NEW
        self.scroll_offset: int = 0

    def _item_label(self, item: Any) -> str:
        if isinstance(item, str):
            return item
        return getattr(item, "label", getattr(item, "name", str(item)))

    def set_items(self, items: List[Any]) -> None:
        self.items = items
        self.selected_index = max(0, min(self.selected_index, max(0, len(items) - 1)))
        self.scroll_offset = max(0, min(self.scroll_offset, max(0, len(items) - 1)))

    def _visible_capacity(self) -> int:
        if self._line_height <= 0:
            return max(1, len(self.items))
        usable_h = max(0, self.rect.height - 2 * self.padding)
        return max(1, usable_h // self._line_height)

    def ensure_visible(self, index: int) -> None:
        cap = self._visible_capacity()
        if index < self.scroll_offset:
            self.scroll_offset = index
        elif index >= self.scroll_offset + cap:
            self.scroll_offset = max(0, index - cap + 1)

        max_off = max(0, len(self.items) - cap)
        self.scroll_offset = max(0, min(self.scroll_offset, max_off))

    def layout(self, ctx: WidgetContext) -> None:
        font = getattr(
            ctx.renderer,
            "font",
            getattr(ctx.renderer, "small_font"),
        )

        # Compute line height based on a representative item or font height.
        base_h = font.get_height()
        self._line_height = base_h + self.line_spacing

        # Determine a reasonable width from item labels
        max_w = 0
        for item in self.items:
            label = self._item_label(item)
            w, _ = font.size(label)
            max_w = max(max_w, w)

        # If rect.width/height are pre-set by container, respect them.
        if self.rect.width == 0:
            self.rect.width = max_w + 2 * self.padding

        # If height not preset, compute height to show all items (legacy behavior).
        if self.rect.height == 0:
            total_h = len(self.items) * self._line_height + 2 * self.padding
            self.rect.height = total_h

        # Clamp scroll if needed
        self.ensure_visible(self.selected_index)

        super().layout(ctx)

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        font = getattr(
            ctx.renderer,
            "font",
            getattr(ctx.renderer, "small_font"),
        )
        fg = getattr(ctx.renderer, "fg", (255, 255, 255))
        sel = getattr(
            ctx.renderer,
            "player_color",
            getattr(ctx.renderer, "sel", (255, 255, 0)),
        )

        cap = self._visible_capacity()
        start = self.scroll_offset
        end = min(len(self.items), start + cap)

        x = self.rect.x + self.padding
        y = self.rect.y + self.padding

        for idx in range(start, end):
            item = self.items[idx]
            label = self._item_label(item)
            selected = (idx == self.selected_index)
            color = sel if selected else fg
            prefix = "▶ " if selected else "  "
            surf = font.render(prefix + label, True, color)
            ctx.surface.blit(surf, (x, y))
            y += self._line_height

        super().draw(ctx)

    def handle_event(self, event, ctx: WidgetContext) -> bool:
        if not (self.visible and self.enabled):
            return False

        if event.type == pygame.MOUSEWHEEL:
            # wheel up => y=1, wheel down => y=-1 (pygame convention)
            if self.rect.collidepoint(getattr(pygame.mouse, "get_pos", lambda: (0, 0))()):
                cap = self._visible_capacity()
                max_off = max(0, len(self.items) - cap)
                self.scroll_offset = max(0, min(self.scroll_offset - int(event.y), max_off))
                return True

        if event.type == pygame.MOUSEMOTION:
            if self.rect.collidepoint(event.pos):
                rel_y = event.pos[1] - self.rect.y - self.padding
                if self._line_height > 0:
                    idx = self.scroll_offset + (rel_y // self._line_height)
                    if 0 <= idx < len(self.items):
                        self.selected_index = int(idx)
                        self.ensure_visible(self.selected_index)

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                rel_y = event.pos[1] - self.rect.y - self.padding
                if self._line_height > 0:
                    idx = self.scroll_offset + (rel_y // self._line_height)
                    if 0 <= idx < len(self.items):
                        self.selected_index = int(idx)
                        self.ensure_visible(self.selected_index)
                        if self.on_activate:
                            self.on_activate(
                                self.selected_index,
                                self.items[self.selected_index],
                            )
                        return True

        return super().handle_event(event, ctx)



class VBox(Widget):
    """
    Vertical layout container.

    - Positions children top → bottom
    - Uses spacing and padding
    - Aligns children horizontally: "left" | "center" | "right"
    """

    def __init__(
        self,
        *,
        spacing: int = 4,
        padding: int = 0,
        align: str = "left",
    ) -> None:
        super().__init__()
        self.spacing = spacing
        self.padding = padding
        self.align = align

    def layout(self, ctx: WidgetContext) -> None:
        # Let children figure out their preferred sizes first
        for child in self.children:
            child.layout(ctx)

        # Determine container width if not preset
        max_w = max((child.rect.width for child in self.children), default=0)
        if self.rect.width == 0:
            self.rect.width = max_w + 2 * self.padding

        x_left = self.rect.x + self.padding
        y = self.rect.y + self.padding

        for child in self.children:
            if self.align == "left":
                child_x = x_left
            elif self.align == "center":
                child_x = self.rect.x + (self.rect.width - child.rect.width) // 2
            else:  # "right"
                child_x = self.rect.right - self.padding - child.rect.width

            child.rect.topleft = (child_x, y)
            y += child.rect.height + self.spacing

        # If height not preset, infer from children
        if self.rect.height == 0:
            self.rect.height = (y - self.rect.y) + self.padding - self.spacing

        # Children may have their own children; propagate layout
        for child in self.children:
            child.layout(ctx)


class HBox(Widget):
    """
    Horizontal layout container.

    - Positions children left → right
    - Uses spacing and padding
    - Aligns children vertically: "top" | "center" | "bottom"
    """

    def __init__(
        self,
        *,
        spacing: int = 4,
        padding: int = 0,
        valign: str = "top",
    ) -> None:
        super().__init__()
        self.spacing = spacing
        self.padding = padding
        self.valign = valign

    def layout(self, ctx: WidgetContext) -> None:
        for child in self.children:
            child.layout(ctx)

        max_h = max((child.rect.height for child in self.children), default=0)
        if self.rect.height == 0:
            self.rect.height = max_h + 2 * self.padding

        x = self.rect.x + self.padding
        y_top = self.rect.y + self.padding

        for child in self.children:
            if self.valign == "top":
                child_y = y_top
            elif self.valign == "center":
                child_y = self.rect.y + (self.rect.height - child.rect.height) // 2
            else:  # "bottom"
                child_y = self.rect.bottom - self.padding - child.rect.height

            child.rect.topleft = (x, child_y)
            x += child.rect.width + self.spacing

        if self.rect.width == 0:
            self.rect.width = (x - self.rect.x) + self.padding - self.spacing

        for child in self.children:
            child.layout(ctx)
