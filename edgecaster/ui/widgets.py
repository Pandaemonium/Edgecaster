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


# ---------------------------------------------------------------------------
# Font selection helpers
# ---------------------------------------------------------------------------

def _menu_font(ctx: WidgetContext) -> pygame.font.Font:
    """
    Preferred font for menus/popups.
    If renderer has menu_font, use it. Otherwise fall back to small_font/font.
    """
    r = ctx.renderer
    return getattr(
        r,
        "menu_font",
        getattr(r, "small_font", getattr(r, "font")),
    )


def _menu_title_font(ctx: WidgetContext) -> pygame.font.Font:
    """
    Preferred font for menu titles/banners.
    If renderer has menu_title_font, use it; else fall back to menu_font.
    """
    r = ctx.renderer
    return getattr(
        r,
        "menu_title_font",
        getattr(r, "menu_font", getattr(r, "font", getattr(r, "small_font"))),
    )


def _generic_font(ctx: WidgetContext) -> pygame.font.Font:
    """
    Generic UI font fallback (non-menu widgets may call this).
    """
    r = ctx.renderer
    return getattr(r, "font", getattr(r, "small_font"))



# ---------------------------------------------------------------------------
# Auto font sizing + caching
# ---------------------------------------------------------------------------

# Widgets sometimes need to "fill the space" (especially menus) so that as the
# panel shrinks (recursion / transforms) we still use the biggest readable font
# that fits the allocated rect.
#
# We do this purely in the widget layer: choose a pygame SysFont size that fits
# within the widget's rect constraints, and cache the resulting Font objects.

_FONT_CACHE: dict[tuple[str, int, bool], pygame.font.Font] = {}


def _get_sys_font(name: str, size: int, *, bold: bool = False) -> pygame.font.Font:
    key = (name, int(size), bool(bold))
    f = _FONT_CACHE.get(key)
    if f is None:
        f = pygame.font.SysFont(name, int(size), bold=bool(bold))
        _FONT_CACHE[key] = f
    return f


def _fit_font_size(
    *,
    font_name: str,
    bold: bool,
    lines: list[str],
    max_width_px: int,
    max_height_px: int,
    line_spacing: int,
    padding: int,
    min_size: int,
    max_size: int,
    # If provided, we only require fitting THIS many lines vertically.
    # (Useful for scrollable lists: keep text big even if there are 80 items.)
    target_lines: int | None = None,
    prefix_px: int = 0,
) -> pygame.font.Font:
    max_width_px = max(1, int(max_width_px))
    max_height_px = max(1, int(max_height_px))
    padding = max(0, int(padding))
    line_spacing = max(0, int(line_spacing))
    min_size = max(6, int(min_size))
    max_size = max(min_size, int(max_size))

    usable_w = max(1, max_width_px - 2 * padding - int(prefix_px))
    usable_h = max(1, max_height_px - 2 * padding)

    want_lines = len(lines) if target_lines is None else max(1, int(target_lines))

    def fits(sz: int) -> bool:
        font = _get_sys_font(font_name, sz, bold=bold)
        h_line = font.get_height()
        total_h = want_lines * h_line + max(0, want_lines - 1) * line_spacing
        if total_h > usable_h:
            return False
        if lines:
            # Worst-case width across sample lines.
            max_w = 0
            for ln in lines:
                w = font.size(ln)[0]
                if w > max_w:
                    max_w = w
            if max_w > usable_w:
                return False
        return True

    lo, hi = min_size, max_size
    best = lo
    while lo <= hi:
        mid = (lo + hi) // 2
        if fits(mid):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    return _get_sys_font(font_name, best, bold=bold)



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
        font = self.font or _menu_font(ctx)
        w, h = font.size(self.text)
        self.rect.width = w + 2 * self.padding
        self.rect.height = h + 2 * self.padding
        super().layout(ctx)

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        font = self.font or _menu_font(ctx)
        color = self.color or getattr(ctx.renderer, "fg", (255, 255, 255))
        text_surf = font.render(self.text, True, color)

        x = self.rect.x + self.padding
        if self.align == "center":
            x = self.rect.x + (self.rect.width - text_surf.get_width()) // 2
        elif self.align == "right":
            x = self.rect.right - self.padding - text_surf.get_width()
        y = self.rect.y + self.padding

        ctx.surface.blit(text_surf, (x, y))
        super().draw(ctx)


class MultiLineLabelWidget(Widget):
    """A label that supports '\\n' newlines by drawing one line at a time."""

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
        font = self.font or _menu_font(ctx)
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

        font = self.font or _menu_font(ctx)
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
        # Buttons are menu UI: prefer menu_font.
        font = _menu_font(ctx)
        w, h = font.size(self.text)
        self.rect.width = w + 2 * self.padding_x
        self.rect.height = h + 2 * self.padding_y
        super().layout(ctx)

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        font = _menu_font(ctx)
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
                return True

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:

            was_pressed = self.pressed

            self.pressed = False

            if was_pressed:

                if self.on_click:
                    self.on_click(self)

                return True

                return super().handle_event(event, ctx)


class ListWidget(Widget):
    """
    Simple vertical list for left-hand “choices” panes.
    """

    def __init__(
        self,
        items: List[Any],
        *,
        selected_index: int = 0,
        on_activate: Optional[Callable[[int, Any], None]] = None,
        line_spacing: int = 2,
        padding: int = 4,
        auto_font: bool = True,
        font_name: str = "consolas",
        font_bold: bool = True,
        min_font_size: int = 12,
        max_font_size: int = 30,
        target_visible_items: int = 18,
    ) -> None:
        super().__init__()
        self.items = items
        self.selected_index = selected_index
        self.on_activate = on_activate
        self.line_spacing = line_spacing
        self.padding = padding
        self._line_height: int = 0


        self.auto_font = bool(auto_font)
        self.font_name = str(font_name)
        self.font_bold = bool(font_bold)
        self.min_font_size = int(min_font_size)
        self.max_font_size = int(max_font_size)
        self.target_visible_items = int(target_visible_items)

        self._font: pygame.font.Font | None = None

        self.scroll_offset: int = 0

    def _item_label(self, item: Any) -> str:
        if isinstance(item, str):
            return item
        return getattr(item, "label", getattr(item, "name", str(item)))


    def _pick_font(self, ctx: WidgetContext) -> pygame.font.Font:
        # If we already picked a fitted font this frame, reuse it.
        if self._font is not None:
            return self._font

        # Default: renderer-provided menu font.
        base = _menu_font(ctx)

        if not self.auto_font:
            self._font = base
            return base

        # If we have no rect constraints yet, fall back.
        if self.rect.width <= 0 or self.rect.height <= 0:
            self._font = base
            return base

        # Sample some labels to bound width. (Use the longest N labels; cheap and stable.)
        labels = [self._item_label(it) for it in (self.items or [])]
        labels.sort(key=lambda s: len(s), reverse=True)
        samples = labels[: min(12, len(labels))] or [""]

        # Scrollable list: don't require fitting ALL items. Fit a "reasonable" number.
        want_items = min(len(self.items), max(1, self.target_visible_items)) if self.items else 1

        # Account for the "▶ " prefix width so we don't clip.
        prefix_px = base.size("▶ ")[0]

        self._font = _fit_font_size(
            font_name=self.font_name,
            bold=self.font_bold,
            lines=samples,
            max_width_px=self.rect.width,
            max_height_px=self.rect.height,
            line_spacing=self.line_spacing,
            padding=self.padding,
            min_size=self.min_font_size,
            max_size=self.max_font_size,
            target_lines=want_items,
            prefix_px=prefix_px,
        )
        return self._font

    def set_items(self, items: List[Any]) -> None:
        self.items = items
        self.selected_index = max(0, min(self.selected_index, max(0, len(items) - 1)))
        self.scroll_offset = max(0, min(self.scroll_offset, max(0, len(items) - 1)))
        self._font = None

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
        self._font = None
        font = self._pick_font(ctx)

        base_h = font.get_height()
        self._line_height = base_h + self.line_spacing

        max_w = 0
        for item in self.items:
            label = self._item_label(item)
            w, _ = font.size(label)
            max_w = max(max_w, w)

        if self.rect.width == 0:
            self.rect.width = max_w + 2 * self.padding

        if self.rect.height == 0:
            total_h = len(self.items) * self._line_height + 2 * self.padding
            self.rect.height = total_h

        self.ensure_visible(self.selected_index)
        super().layout(ctx)

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
                            self.on_activate(self.selected_index, self.items[self.selected_index])
                        return True

        return super().handle_event(event, ctx)


class VBox(Widget):
    """
    Vertical layout container.
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
        for child in self.children:
            child.layout(ctx)

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
            else:
                child_x = self.rect.right - self.padding - child.rect.width

            child.rect.topleft = (child_x, y)
            y += child.rect.height + self.spacing

        if self.rect.height == 0:
            self.rect.height = (y - self.rect.y) + self.padding - self.spacing

        for child in self.children:
            child.layout(ctx)


class HBox(Widget):
    """
    Horizontal layout container.
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
            else:
                child_y = self.rect.bottom - self.padding - child.rect.height

            child.rect.topleft = (x, child_y)
            x += child.rect.width + self.spacing

        if self.rect.width == 0:
            self.rect.width = (x - self.rect.x) + self.padding - self.spacing

        for child in self.children:
            child.layout(ctx)


class ScaledLabelWidget(Widget):
    """
    Label drawn using a base font but scaled up.
    """

    def __init__(
        self,
        text: str,
        *,
        scale: int = 2,
        color: Optional[tuple[int, int, int]] = None,
        font: Optional[pygame.font.Font] = None,
        padding: int = 0,
        align: str = "left",
    ) -> None:
        super().__init__()
        self.text = text
        self.scale = max(1, int(scale))
        self.color = color
        self.font = font
        self.padding = padding
        self.align = align

    def _base_font(self, ctx: WidgetContext) -> pygame.font.Font:
        return self.font or _menu_title_font(ctx)

    def layout(self, ctx: WidgetContext) -> None:
        font = self._base_font(ctx)
        w, h = font.size(self.text)
        self.rect.width = w * self.scale + 2 * self.padding
        self.rect.height = h * self.scale + 2 * self.padding

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return
        font = self._base_font(ctx)
        color = self.color or getattr(ctx.renderer, "fg", (255, 255, 255))

        base = font.render(self.text, True, color)
        surf = pygame.transform.scale(
            base,
            (base.get_width() * self.scale, base.get_height() * self.scale),
        )

        x = self.rect.x + self.padding
        if self.align == "center":
            x = self.rect.x + (self.rect.width - surf.get_width()) // 2
        elif self.align == "right":
            x = self.rect.right - self.padding - surf.get_width()
        y = self.rect.y + self.padding

        ctx.surface.blit(surf, (x, y))
        super().draw(ctx)


class ScaledMultiLineLabelWidget(Widget):
    """
    Multi-line label drawn at a base font size, then scaled.
    Great for ASCII banners.
    """

    def __init__(
        self,
        text: str,
        *,
        scale: int = 2,
        color: Optional[tuple[int, int, int]] = None,
        font: Optional[pygame.font.Font] = None,
        padding: int = 0,
        align: str = "left",
        line_spacing: int = 0,
    ) -> None:
        super().__init__()
        self.text = text
        self.scale = max(1, int(scale))
        self.color = color
        self.font = font
        self.padding = padding
        self.align = align
        self.line_spacing = line_spacing

    def _base_font(self, ctx: WidgetContext) -> pygame.font.Font:
        return self.font or _menu_title_font(ctx)

    def layout(self, ctx: WidgetContext) -> None:
        font = self._base_font(ctx)
        lines = self.text.splitlines() or [""]
        widths = [font.size(line)[0] for line in lines]
        w = max(widths) if widths else 0
        h_line = font.get_height()
        h = len(lines) * h_line + max(0, len(lines) - 1) * self.line_spacing
        self.rect.width = w * self.scale + 2 * self.padding
        self.rect.height = h * self.scale + 2 * self.padding

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        font = self._base_font(ctx)
        col = self.color or getattr(ctx.renderer, "fg", (220, 220, 220))
        lines = self.text.splitlines() or [""]

        widths = [font.size(line)[0] for line in lines] or [0]
        w0 = max(widths)
        h_line = font.get_height()
        h0 = len(lines) * h_line + max(0, len(lines) - 1) * self.line_spacing

        temp = pygame.Surface((max(1, w0), max(1, h0)), pygame.SRCALPHA)
        y = 0
        for line in lines:
            s = font.render(line, True, col)
            if self.align == "center":
                x = (w0 - s.get_width()) // 2
            elif self.align == "right":
                x = (w0 - s.get_width())
            else:
                x = 0
            temp.blit(s, (x, y))
            y += h_line + self.line_spacing

        surf = pygame.transform.scale(
            temp,
            (temp.get_width() * self.scale, temp.get_height() * self.scale),
        )

        x = self.rect.x + self.padding
        y = self.rect.y + self.padding
        ctx.surface.blit(surf, (x, y))
        super().draw(ctx)


# ---------------------------------------------------------------------------
# Wrapping helpers + wrapped widgets
# ---------------------------------------------------------------------------

def _wrap_text_px(font: pygame.font.Font, text: str, max_width_px: int) -> List[str]:
    """
    Word-wrap in pixel space using `font.size()`.
    Preserves explicit newlines.
    """
    max_width_px = max(1, int(max_width_px))
    out: List[str] = []

    for raw_line in (text.splitlines() or [""]):
        words = raw_line.split()
        if not words:
            out.append("")
            continue

        cur = words[0]
        for w in words[1:]:
            test = cur + " " + w
            if font.size(test)[0] <= max_width_px:
                cur = test
            else:
                out.append(cur)
                cur = w
        out.append(cur)

    return out


class WrappedMultiLineLabelWidget(Widget):
    """
    Like MultiLineLabelWidget, but wraps automatically to a max pixel width.
    """

    def __init__(
        self,
        text: str,
        *,
        max_width_px: int = 520,
        color: Optional[tuple[int, int, int]] = None,
        font: Optional[pygame.font.Font] = None,
        padding: int = 0,
        align: str = "left",
        line_spacing: int = 0,
    ) -> None:
        super().__init__()
        self.text = text
        self.max_width_px = int(max_width_px)
        self.color = color
        self.font = font
        self.padding = padding
        self.align = align
        self.line_spacing = line_spacing
        self._wrapped_lines: List[str] = [""]

    def _base_font(self, ctx: WidgetContext) -> pygame.font.Font:
        return self.font or _menu_font(ctx)

    def layout(self, ctx: WidgetContext) -> None:
        font = self._base_font(ctx)

        inner_max = self.max_width_px
        if self.rect.width > 0:
            inner_max = max(1, self.rect.width - 2 * self.padding)

        self._wrapped_lines = _wrap_text_px(font, self.text, inner_max)

        widths = [font.size(line)[0] for line in self._wrapped_lines] or [0]
        w = max(widths)
        h_line = font.get_height()
        h = len(self._wrapped_lines) * h_line + max(0, len(self._wrapped_lines) - 1) * self.line_spacing

        self.rect.width = w + 2 * self.padding
        self.rect.height = h + 2 * self.padding

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        font = self._base_font(ctx)
        col = self.color or getattr(ctx.renderer, "fg", (220, 220, 220))

        x0 = self.rect.x + self.padding
        y = self.rect.y + self.padding
        h_line = font.get_height()

        for line in (self._wrapped_lines or [""]):
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


class WrappedListWidget(Widget):
    """
    ListWidget variant that wraps long item labels to multiple lines (pixel wrap).
    """

    def __init__(
        self,
        items: List[Any],
        *,
        selected_index: int = 0,
        on_activate: Optional[Callable[[int, Any], None]] = None,
        padding: int = 4,
        line_spacing: int = 2,
        wrap_width_px: int = 520,
        auto_font: bool = True,
        font_name: str = "consolas",
        font_bold: bool = True,
        min_font_size: int = 12,
        max_font_size: int = 38,
        target_visible_items: int = 14,
        scrollable: bool = True,

    ) -> None:
        super().__init__()
        self.items = items
        self.selected_index = selected_index
        self.on_activate = on_activate
        self.padding = padding
        self.line_spacing = line_spacing
        self.wrap_width_px = int(wrap_width_px)
        self.scrollable = bool(scrollable)

        self.auto_font = bool(auto_font)
        self.font_name = str(font_name)
        self.font_bold = bool(font_bold)
        self.min_font_size = int(min_font_size)
        self.max_font_size = int(max_font_size)
        self.target_visible_items = int(target_visible_items)

        self._font_override: pygame.font.Font | None = None

        self.scroll_offset: int = 0
        self._font_h: int = 0
        self._line_h: int = 0

        self._wrapped: List[List[str]] = []

    def _item_label(self, item: Any) -> str:
        if isinstance(item, str):
            return item
        return getattr(item, "label", getattr(item, "name", str(item)))

    def set_items(self, items: List[Any]) -> None:
        self.items = items
        self.selected_index = max(0, min(self.selected_index, max(0, len(items) - 1)))
        self.scroll_offset = max(0, min(self.scroll_offset, max(0, len(items) - 1)))

        # IMPORTANT:
        # Do NOT assign self._font = None here — that would shadow the _font(ctx) method
        # and cause "NoneType is not callable" in layout().
        self._font_override = None
        self._wrapped = []


    def _font(self, ctx: WidgetContext) -> pygame.font.Font:
        if self._font_override is not None:
            return self._font_override

        base = _menu_font(ctx)
        if not self.auto_font:
            self._font_override = base
            return base

        if self.rect.width <= 0 or self.rect.height <= 0:
            self._font_override = base
            return base

        labels = [self._item_label(it) for it in (self.items or [])]
        labels.sort(key=lambda s: len(s), reverse=True)
        samples = labels[: min(10, len(labels))] or [""]

        want_items = min(len(self.items), max(1, self.target_visible_items)) if self.items else 1
        prefix_px = base.size("▶ ")[0]

        self._font_override = _fit_font_size(
            font_name=self.font_name,
            bold=self.font_bold,
            lines=samples,
            max_width_px=self.rect.width,
            max_height_px=self.rect.height,
            line_spacing=self.line_spacing,
            padding=self.padding,
            min_size=self.min_font_size,
            max_size=self.max_font_size,
            target_lines=want_items,
            prefix_px=prefix_px,
        )
        return self._font_override


    def _rebuild_wrap_cache(self, ctx: WidgetContext) -> None:
        font = self._font(ctx)
        self._font_h = font.get_height()
        self._line_h = self._font_h + self.line_spacing

        inner_w = self.wrap_width_px
        if self.rect.width > 0:
            inner_w = max(1, self.rect.width - 2 * self.padding)

        prefix_w = font.size("▶ ")[0]
        inner_w = max(1, inner_w - prefix_w)

        self._wrapped = []
        for it in self.items:
            label = self._item_label(it)
            lines = _wrap_text_px(font, label, inner_w)
            self._wrapped.append(lines or [""])

    def _visible_capacity(self) -> int:
        if self._line_h <= 0:
            return max(1, len(self.items))
        usable_h = max(0, self.rect.height - 2 * self.padding)
        return max(1, usable_h // self._line_h)

    def ensure_visible(self, index: int) -> None:
        if not self.scrollable:
            self.scroll_offset = 0
            return

        cap_items = max(1, self._visible_items_capacity())
        if index < self.scroll_offset:
            self.scroll_offset = index
        elif index >= self.scroll_offset + cap_items:
            self.scroll_offset = max(0, index - cap_items + 1)

        max_off = max(0, len(self.items) - cap_items)
        self.scroll_offset = max(0, min(self.scroll_offset, max_off))

    def _visible_items_capacity(self) -> int:
        if self._line_h <= 0:
            return max(1, len(self.items))
        usable_h = max(0, self.rect.height - 2 * self.padding)
        approx_lines = max(1, usable_h // self._line_h)
        return max(1, approx_lines // 2)

    def layout(self, ctx: WidgetContext) -> None:
        self._font_override = None
        font = self._font(ctx)
        self._font_h = font.get_height()
        self._line_h = self._font_h + self.line_spacing

        if self.rect.width == 0:
            self.rect.width = self.wrap_width_px + 2 * self.padding

        if self.rect.height == 0:
            est_lines = max(1, len(self.items) * 2)
            self.rect.height = est_lines * self._line_h + 2 * self.padding

        self._rebuild_wrap_cache(ctx)
        self.ensure_visible(self.selected_index)

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        font = self._font(ctx)
        fg = getattr(ctx.renderer, "fg", (255, 255, 255))
        sel = getattr(
            ctx.renderer,
            "player_color",
            getattr(ctx.renderer, "sel", (255, 255, 0)),
        )

        x = self.rect.x + self.padding
        y = self.rect.y + self.padding

        cap_lines = self._visible_capacity()
        lines_drawn = 0

        idx = self.scroll_offset
        while idx < len(self.items) and lines_drawn < cap_lines:
            wrapped = self._wrapped[idx] if idx < len(self._wrapped) else [self._item_label(self.items[idx])]
            selected = (idx == self.selected_index)
            color = sel if selected else fg

            prefix = "▶ " if selected else "  "
            for li, line in enumerate(wrapped):
                if lines_drawn >= cap_lines:
                    break
                text = (prefix if li == 0 else "  ") + line
                surf = font.render(text, True, color)
                ctx.surface.blit(surf, (x, y))
                y += self._line_h
                lines_drawn += 1

            idx += 1

        super().draw(ctx)

    def handle_event(self, event, ctx: WidgetContext) -> bool:
        if not (self.visible and self.enabled):
            return False

        # Ensure we have an up-to-date wrap cache for accurate hit testing.
        if not self._wrapped or len(self._wrapped) != len(self.items):
            self._rebuild_wrap_cache(ctx)

        # Mouse wheel only affects scrollable lists.
        if event.type == pygame.MOUSEWHEEL:
            if not self.scrollable:
                return False
            if self.rect.collidepoint(getattr(pygame.mouse, "get_pos", lambda: (0, 0))()):
                cap_items = max(1, self._visible_items_capacity())
                max_off = max(0, len(self.items) - cap_items)
                self.scroll_offset = max(0, min(self.scroll_offset - int(event.y), max_off))
                return True
            return False

        def _pick_index_from_mouse_y(mouse_y: int) -> int | None:
            rel_y = mouse_y - self.rect.y - self.padding
            if rel_y < 0 or self._line_h <= 0:
                return None
            line_idx = int(rel_y // self._line_h)

            idx = int(self.scroll_offset)
            while idx < len(self.items):
                wrapped = self._wrapped[idx] if idx < len(self._wrapped) else [self._item_label(self.items[idx])]
                n_lines = max(1, len(wrapped))
                if line_idx < n_lines:
                    return idx
                line_idx -= n_lines
                idx += 1
            return None

        if event.type == pygame.MOUSEMOTION:
            if self.rect.collidepoint(event.pos):
                idx = _pick_index_from_mouse_y(event.pos[1])
                if idx is not None:
                    self.selected_index = int(idx)
                    self.ensure_visible(self.selected_index)
                    return True
            return False

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                idx = _pick_index_from_mouse_y(event.pos[1])
                if idx is not None:
                    self.selected_index = int(idx)
                    self.ensure_visible(self.selected_index)
                    if self.on_activate:
                        self.on_activate(self.selected_index, self.items[self.selected_index])
                    return True
            return False

        return super().handle_event(event, ctx)



class TwoColumnListWidget(ListWidget):
    """
    ListWidget variant that renders a right-aligned "value" column.
    Used by OptionsScene.
    """

    def __init__(
        self,
        items: List[Any],
        *,
        selected_index: int = 0,
        on_activate: Optional[Callable[[int, Any], None]] = None,
        line_spacing: int = 2,
        padding: int = 4,
        value_gap: int = 24,
    ) -> None:
        super().__init__(
            items,
            selected_index=selected_index,
            on_activate=on_activate,
            line_spacing=line_spacing,
            padding=padding,
        )
        self.value_gap = int(value_gap)

    def _item_value(self, item: Any) -> str:
        v = getattr(item, "value", "")
        return "" if v is None else str(v)

    def layout(self, ctx: WidgetContext) -> None:
        font = self._pick_font(ctx)

        base_h = font.get_height()
        self._line_height = base_h + self.line_spacing

        max_label_w = 0
        max_value_w = 0

        for item in self.items:
            label = self._item_label(item)
            value = self._item_value(item)
            max_label_w = max(max_label_w, font.size("▶ " + label)[0])
            if value:
                max_value_w = max(max_value_w, font.size(value)[0])

        if self.rect.width == 0:
            self.rect.width = (
                max_label_w
                + (self.value_gap if max_value_w > 0 else 0)
                + max_value_w
                + 2 * self.padding
            )

        if self.rect.height == 0:
            total_h = len(self.items) * self._line_height + 2 * self.padding
            self.rect.height = total_h

        self.ensure_visible(self.selected_index)
        super().layout(ctx)

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

        x_left = self.rect.x + self.padding
        x_right = self.rect.right - self.padding
        y = self.rect.y + self.padding

        for idx in range(start, end):
            item = self.items[idx]
            label = self._item_label(item)
            value = self._item_value(item)

            selected = (idx == self.selected_index)
            color = sel if selected else fg
            prefix = "▶ " if selected else "  "

            left_surf = font.render(prefix + label, True, color)
            ctx.surface.blit(left_surf, (x_left, y))

            if value:
                val_surf = font.render(value, True, color)
                vx = x_right - val_surf.get_width()
                ctx.surface.blit(val_surf, (vx, y))

            y += self._line_height

        super().draw(ctx)



class SpacerWidget(Widget):
    """
    A blank fixed-size widget used to reserve layout space (e.g. art area).
    """
    def __init__(self, *, width: int = 0, height: int = 0) -> None:
        super().__init__()
        self._w = max(0, int(width))
        self._h = max(0, int(height))

    def layout(self, ctx: WidgetContext) -> None:
        # If width/height are 0, we keep whatever the parent layout assigned.
        if self._w > 0:
            self.rect.width = self._w
        if self._h > 0:
            self.rect.height = self._h

    def draw(self, ctx: WidgetContext) -> None:
        # Intentionally draws nothing.
        return
