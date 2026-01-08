from __future__ import annotations

from typing import Optional, Callable, List

import pygame

from edgecaster.ui.widgets import (
    Widget,
    WidgetContext,
    LabelWidget,
    ScaledLabelWidget,
    WrappedMultiLineLabelWidget,
    WrappedListWidget,
    SpacerWidget,
    _wrap_text_px,
)

from .base import PopupMenuScene

if False:  # type checking only
    from .manager import SceneManager  # pragma: no cover


class UrgentFrameWidget(Widget):
    """
    Urgent-message layout:
      title (big)
      art spacer (blank for now)
      body (wrapped)
      choices list (wrapped)
      (optional footer pinned to bottom)
    """

    def __init__(
        self,
        *,
        banner: Widget | None,
        art: Widget | None,
        body: Widget | None,
        list_widget: Widget,
        footer: Widget | None,
        top_pad: int = 18,
        bottom_pad: int = 14,
        gap_after_banner: int = 10,
        gap_after_art: int = 10,
        gap_after_body: int = 14,
        max_body_width: int = 640,
        max_list_width: int = 560,
        min_list_height: int = 90,
        fill_list_width: bool = True,
    ) -> None:
        super().__init__()
        self.banner = banner
        self.art = art
        self.body = body
        self.list_widget = list_widget
        self.footer = footer

        self.top_pad = int(top_pad)
        self.bottom_pad = int(bottom_pad)
        self.gap_after_banner = int(gap_after_banner)
        self.gap_after_art = int(gap_after_art)
        self.gap_after_body = int(gap_after_body)
        self.max_body_width = int(max_body_width)
        self.max_list_width = int(max_list_width)
        self.min_list_height = int(min_list_height)
        self.fill_list_width = bool(fill_list_width)

        if self.banner is not None:
            self.add_child(self.banner)
        if self.art is not None:
            self.add_child(self.art)
        if self.body is not None:
            self.add_child(self.body)
        self.add_child(self.list_widget)
        if self.footer is not None:
            self.add_child(self.footer)

    def layout(self, ctx: WidgetContext) -> None:
        if self.rect.width == 0 or self.rect.height == 0:
            self.rect = ctx.surface.get_rect()

        # Pre-layout
        if self.banner:
            self.banner.layout(ctx)
        if self.art:
            self.art.layout(ctx)
        if self.body:
            self.body.layout(ctx)
        self.list_widget.layout(ctx)
        if self.footer:
            self.footer.layout(ctx)

        inner_w = max(1, self.rect.width - 2 * self.top_pad)

        y = self.rect.y + self.top_pad

        # Banner (center)
        if self.banner and self.banner.visible:
            bx = self.rect.x + (self.rect.width - self.banner.rect.width) // 2
            self.banner.rect.topleft = (bx, y)
            y = self.banner.rect.bottom + self.gap_after_banner

        # Art spacer (centered, optional fixed width clamp)
        if self.art and self.art.visible:
            # Make the art block match the content column width.
            aw = min(inner_w, self.max_body_width, self.max_list_width)
            if aw > 0:
                self.art.rect.width = aw
            self.art.layout(ctx)

            ax = self.rect.x + (self.rect.width - self.art.rect.width) // 2
            self.art.rect.topleft = (ax, y)
            y = self.art.rect.bottom + self.gap_after_art

        # Body (centered, clamped width)
        if self.body and self.body.visible:
            bw = min(inner_w, self.max_body_width)
            if bw > 0:
                self.body.rect.width = bw
            self.body.layout(ctx)

            bx = self.rect.x + (self.rect.width - self.body.rect.width) // 2
            self.body.rect.topleft = (bx, y)
            y = self.body.rect.bottom + self.gap_after_body

        # Footer pinned bottom
        footer_h = 0
        if self.footer and self.footer.visible:
            footer_h = self.footer.rect.height
            fx = self.rect.x + (self.rect.width - self.footer.rect.width) // 2
            fy = self.rect.bottom - self.bottom_pad - footer_h
            self.footer.rect.topleft = (fx, fy)

        # List fills remaining height
        avail_h = (self.rect.bottom - self.bottom_pad - footer_h) - y
        self.list_widget.rect.height = max(self.min_list_height, avail_h)

        # Width policy (fill column, but clamp)
        if self.fill_list_width:
            desired_w = min(inner_w, self.max_list_width)
            self.list_widget.rect.width = max(1, desired_w)
        else:
            self.list_widget.rect.width = min(max(1, self.list_widget.rect.width), self.max_list_width)

        lx = self.rect.x + (self.rect.width - self.list_widget.rect.width) // 2
        self.list_widget.rect.topleft = (lx, y)
        self.list_widget.layout(ctx)


class UrgentMessageScene(PopupMenuScene):
    """
    Popup-style urgent message dialog.

    This scene is intentionally *fixed-font* (no dynamic list font fitting).
    Layout:
      - big title header
      - reserved art area (blank for now)
      - wrapped message body
      - wrapped choice list
      - snug popup rect around content
    """

    FOOTER_TEXT = ""
    WRAP_SELECTION: bool = True

    # Reserved vertical space for future artwork (blank block for now).
    ART_HEIGHT_PX: int = 96

    def get_logical_panel_size(self, manager):
        # Urgent popups should NOT use popup logical-surface scaling.
        return None

    def __init__(
        self,
        game,
        message: str,
        *,
        title: str = "",
        choices: Optional[List[str]] = None,
        on_choice: Optional[Callable[[int, "SceneManager"], None]] = None,
        window_rect: Optional[pygame.Rect] = None,
        back_confirms: bool = True,
        stinger_music_key: str | None = None,
        stinger_scary: bool = False,
    ) -> None:

        # Must exist BEFORE super().__init__ (widget tree build happens early).
        self.game = game
        self.message = message
        self.title = title

        self.stinger_music_key = stinger_music_key
        self.stinger_scary = stinger_scary

        # Play stinger immediately when the popup is created (if any).
        if self.stinger_music_key:
            try:
                manager = getattr(self.game, "scene_manager", None)
                if manager is not None and hasattr(manager, "audio"):
                    from edgecaster.scenes.audio_manager import MusicRequest

                    resume_to = None
                    if hasattr(manager, "current_music_request"):
                        resume_to = manager.current_music_request()

                    if self.stinger_scary:
                        req = MusicRequest(
                            key=self.stinger_music_key,
                            loop=False,
                            hard_cut=True,
                            fade_out_ms=0,
                            fade_in_ms=0,
                        )
                    else:
                        req = MusicRequest(
                            key=self.stinger_music_key,
                            loop=False,
                            hard_cut=False,
                            fade_out_ms=700,
                            fade_in_ms=50,
                        )

                    manager.audio.interrupt_then_resume(req, resume_to=resume_to)
            except Exception:
                pass




        self.choices = choices or ["Continue..."]
        self.on_choice = on_choice
        self.back_confirms = back_confirms

        # Visual knobs some menus expect to exist
        self.visual_effects: list[str] = []




        super().__init__(window_rect=window_rect, dim_background=True, scale=0.7)

    def _ensure_window_rect(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        if self.window_rect is not None and self.window_rect.width > 0 and self.window_rect.height > 0:
            return

        try:
            self.window_rect = self._compute_snug_rect(manager)
        except Exception:
            self.window_rect = manager.compute_child_window_rect(scale=self.popup_scale)

        # Window rect affects wrapping; rebuild widgets once sized.
        try:
            self._build_widgets(self.get_menu_items())
        except Exception:
            pass

    # ---- GeneralMenuScene hooks ----------------------------------------

    def get_ascii_art(self) -> str:
        return (self.title or "").strip()

    def get_body_text(self) -> Optional[str]:
        return (self.message or "").strip()

    def wants_wrapped_choices(self) -> bool:
        return True

    # ---- Fixed-font widget tree ----------------------------------------

    def _build_widgets(self, items: list[str]) -> None:
        title = (self.get_ascii_art() or "").strip()
        body_text = (self.get_body_text() or "").strip()

        # Big chunky title (fixed behavior: draw base title font, then scale pixels).
        banner = ScaledLabelWidget(title, align="center", scale=2) if title else None

        # Reserved art space (blank for now).
        art = None
        if getattr(self, "art_surface", None) is not None:
            art = SpacerWidget(height=self.ART_HEIGHT_PX)

        # Medium body text (wrapped, fixed font: uses renderer.menu_font/_menu_font, not SysFont fitting).
        body = (
            WrappedMultiLineLabelWidget(
                body_text,
                align="left",
                line_spacing=2,
                max_width_px=640,
            )
            if body_text
            else None
        )

        # Choices list: WRAPPED but crucially NOT auto-font sized.
        # This is the main fix for “dynamic text size fuckery”.
        choice_list = WrappedListWidget(
            items,
            selected_index=self.selected_idx,
            on_activate=self._on_list_activate,
            padding=6,
            line_spacing=2,
            wrap_width_px=560,
            auto_font=False,  # <<< FIXED FONT
            scrollable=False,

        )
        choice_list.rect = pygame.Rect(0, 0, 0, 0)

        footer = None  # urgent popups usually don’t want the big help footer

        self.root = UrgentFrameWidget(
            banner=banner,
            art=art,
            body=body,
            list_widget=choice_list,
            footer=footer,
            top_pad=18,
            bottom_pad=14,
            gap_after_banner=10,
            gap_after_art=10,
            gap_after_body=14,
            max_body_width=640,
            max_list_width=560,
            min_list_height=90,
            fill_list_width=True,
        )
        self.root.rect = pygame.Rect(0, 0, 0, 0)

        # Keep GeneralMenuScene internals in sync
        self._banner = banner  # type: ignore[attr-defined]
        self._body = body  # type: ignore[attr-defined]
        self._list = choice_list  # type: ignore[attr-defined]
        self._footer = footer  # type: ignore[attr-defined]
        self._last_banner_text = title  # type: ignore[attr-defined]
        self._last_body_text = body_text  # type: ignore[attr-defined]

    # ---- Window sizing --------------------------------------------------

    def _compute_snug_rect(self, manager: "SceneManager") -> pygame.Rect:  # type: ignore[name-defined]
        r = manager.renderer
        screen_w, screen_h = r.width, r.height

        pad_x = 28
        pad_y = 22
        border = 2
        gap_title_art = 10
        gap_art_body = 10
        gap_body_choices = 14

        # Use renderer menu fonts for consistent sizing.
        font = getattr(r, "menu_font", getattr(r, "small_font", getattr(r, "font")))
        title_font = getattr(r, "menu_title_font", font)

        title_scale = 2
        title = (self.title or "").strip()
        body = (self.message or "").strip()
        choices = list(self.choices or ["Continue..."])

        max_text_w = min(760, int(screen_w * 0.78))
        max_text_w = max(360, max_text_w)

        # Title (scaled)
        t_w0, t_h0 = title_font.size(title or " ")
        title_w = t_w0 * title_scale
        title_h = t_h0 * title_scale

        # Art block
        art_h = int(self.ART_HEIGHT_PX) if getattr(self, "art_surface", None) is not None else 0

        # Body (wrapped)
        body_lines = _wrap_text_px(font, body, max_text_w) if body else []
        body_w = max((font.size(line)[0] for line in body_lines), default=0)
        body_h = len(body_lines) * font.get_height() + max(0, len(body_lines) - 1) * 2

        # Choices (wrapped, fixed font)
        prefix_w = font.size("▶ ")[0]
        choice_lines_total = 0
        choice_w = 0
        for c in choices:
            lines = _wrap_text_px(font, str(c), max(1, max_text_w - prefix_w))
            choice_lines_total += max(1, len(lines))
            for line in lines:
                choice_w = max(choice_w, font.size(line)[0] + prefix_w)
        choice_h = choice_lines_total * (font.get_height() + 2)

        content_w = max(title_w, body_w, choice_w, 320)
        content_h = 0

        if title:
            content_h += title_h
            content_h += gap_title_art

        content_h += art_h

        if body_lines:
            content_h += gap_art_body
            content_h += body_h

        if choices:
            if body_lines:
                content_h += gap_body_choices
            else:
                content_h += gap_body_choices
            content_h += choice_h

        win_w = content_w + pad_x * 2 + border * 2
        win_h = content_h + pad_y * 2 + border * 2

        win_w = min(win_w, int(screen_w * 0.92))
        win_h = min(win_h, int(screen_h * 0.92))

        rect = pygame.Rect(0, 0, int(win_w), int(win_h))
        rect.center = (screen_w // 2, screen_h // 2)
        return rect

    # ---- Closing / choice semantics (unchanged) ------------------------

    def _close_self(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        stack = getattr(manager, "scene_stack", None)

        if stack is not None and stack:
            if stack[-1] is self and hasattr(manager, "pop_scene"):
                manager.pop_scene()
            else:
                if self in stack:
                    stack.remove(self)

                    win_stack = getattr(manager, "window_stack", None)
                    if (
                        win_stack is not None
                        and hasattr(self, "window_rect")
                        and self.window_rect in win_stack
                    ):
                        try:
                            win_stack.remove(self.window_rect)
                        except ValueError:
                            pass
            return

        if hasattr(manager, "set_scene"):
            manager.set_scene(None)

    def get_menu_items(self) -> list[str]:
        return self.choices

    def on_activate(self, index: int, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        if hasattr(self.game, "urgent_resolved"):
            self.game.urgent_resolved = True
        if hasattr(self.game, "urgent_message"):
            self.game.urgent_message = None
        if hasattr(self.game, "urgent_title"):
            self.game.urgent_title = None
        if hasattr(self.game, "urgent_body"):
            self.game.urgent_body = None
        if hasattr(self.game, "urgent_choices"):
            self.game.urgent_choices = None

        if hasattr(manager, "renderer") and getattr(self, "_background", None) is not None:
            manager.renderer.surface.blit(self._background, (0, 0))

        self._close_self(manager)

        if self.on_choice is not None:
            self.on_choice(index, manager)

        return True

    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        if getattr(self, "back_confirms", True):
            return self.on_activate(self.selected_idx, manager)
        else:
            self._close_self(manager)
            return True
