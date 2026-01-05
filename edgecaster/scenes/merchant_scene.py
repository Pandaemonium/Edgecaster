from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pygame

from .base import PanelScene

from edgecaster.systems import trade
from edgecaster.ui.widgets import WidgetContext, VBox, HBox, LabelWidget, ScaledLabelWidget, TwoColumnListWidget


@dataclass
class _TradeRow:
    label: str
    value: str
    index: int

    def __str__(self) -> str:
        return self.label


class MerchantScene(PanelScene):
    """Popup trade UI (buy/sell) for a single merchant actor."""

    def __init__(
        self,
        game: Any,
        *,
        merchant_actor_id: str,
        window_rect: Optional[pygame.Rect] = None,
        dim_background: bool = True,
        scale: float = 0.82,
    ) -> None:
        super().__init__(window_rect=window_rect)
        self.game = game
        self.merchant_actor_id = str(merchant_actor_id)
        self.dim_background = bool(dim_background)
        self.popup_scale = float(scale)

        self._background: Optional[pygame.Surface] = None
        self._dim_surf: Optional[pygame.Surface] = None

        self.focus: str = "buy"  # "buy" or "sell"
        self.buy_index: int = 0
        self.sell_index: int = 0

        self._header: ScaledLabelWidget | None = None
        self._buy_list: TwoColumnListWidget | None = None
        self._sell_list: TwoColumnListWidget | None = None

        self._build_widgets()

    # ------------------------------------------------------------------ #
    # Layout / widgets
    # ------------------------------------------------------------------ #

    def _build_widgets(self) -> None:
        self._header = ScaledLabelWidget("Trade", align="left", scale=2)

        left = VBox(spacing=8, padding=0, align="left")
        left.add_child(LabelWidget("Merchant Stock", align="left"))
        self._buy_list = TwoColumnListWidget([], selected_index=0, on_activate=self._on_buy_click, padding=6, line_spacing=4)
        left.add_child(self._buy_list)

        right = VBox(spacing=8, padding=0, align="left")
        right.add_child(LabelWidget("Your Inventory", align="left"))
        self._sell_list = TwoColumnListWidget([], selected_index=0, on_activate=self._on_sell_click, padding=6, line_spacing=4)
        right.add_child(self._sell_list)

        cols = HBox(spacing=18, padding=0, valign="top")
        cols.add_child(left)
        cols.add_child(right)

        footer = LabelWidget("Left/Right or Tab to switch  •  Enter/Click to trade  •  Esc to return", align="left")

        root = VBox(spacing=14, padding=14, align="left")
        root.add_child(self._header)
        root.add_child(cols)
        root.add_child(footer)
        self.root = root

    def _refresh_rows(self) -> None:
        level = self.game._level()
        merchant = getattr(level, "actors", {}).get(self.merchant_actor_id)
        if merchant is None:
            return

        trade.ensure_merchant_initialized(self.game, level, merchant)

        buy_rows: list[_TradeRow] = []
        minv = self.game.get_inventory(self.merchant_actor_id)
        for i, ent in enumerate(list(minv)):
            q = trade.quote_prices(merchant, ent)
            price = q.buy_price if q else 0
            buy_rows.append(_TradeRow(label=str(getattr(ent, "name", "Item")), value=str(price), index=i))

        sell_rows: list[_TradeRow] = []
        pinv = list(getattr(self.game, "player_inventory", []) or [])
        for i, ent in enumerate(pinv):
            q = trade.quote_prices(merchant, ent)
            price = q.sell_price if q else 0
            sell_rows.append(_TradeRow(label=str(getattr(ent, "name", "Item")), value=str(price), index=i))

        if self._buy_list is not None:
            self._buy_list.set_items(buy_rows)
            if buy_rows:
                self.buy_index = max(0, min(self.buy_index, len(buy_rows) - 1))
                self._buy_list.selected_index = int(self.buy_index)
            else:
                self.buy_index = 0
                self._buy_list.selected_index = 0

        if self._sell_list is not None:
            self._sell_list.set_items(sell_rows)
            if sell_rows:
                self.sell_index = max(0, min(self.sell_index, len(sell_rows) - 1))
                self._sell_list.selected_index = int(self.sell_index)
            else:
                self.sell_index = 0
                self._sell_list.selected_index = 0

        # Header text
        if self._header is not None:
            try:
                p = int(getattr(self.game, "bismuth", 0))
            except Exception:
                p = 0
            m = int(trade.merchant_funds(merchant))
            mname = str(getattr(merchant, "name", "Merchant"))
            self._header.text = f"Trade — {mname} ({m} bismuth)  |  You ({p} bismuth)"

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #

    def _on_buy_click(self, idx: int, _item: Any) -> None:
        self.focus = "buy"
        self.buy_index = int(idx)
        trade.try_buy(self.game, self.merchant_actor_id, int(idx))

    def _on_sell_click(self, idx: int, _item: Any) -> None:
        self.focus = "sell"
        self.sell_index = int(idx)
        trade.try_sell(self.game, self.merchant_actor_id, int(idx))

    def _activate_focused(self) -> None:
        if self.focus == "sell":
            trade.try_sell(self.game, self.merchant_actor_id, int(self.sell_index))
        else:
            trade.try_buy(self.game, self.merchant_actor_id, int(self.buy_index))

    def _panel_event(self, event, manager) -> None:
        if event.type != pygame.KEYDOWN:
            return

        if event.key == pygame.K_ESCAPE:
            manager.pop_scene()
            return

        if event.key in (pygame.K_TAB, pygame.K_LEFT, pygame.K_RIGHT):
            self.focus = "sell" if self.focus == "buy" else "buy"
            return

        if event.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_KP_ENTER):
            self._activate_focused()
            return

        if event.key in (pygame.K_UP, pygame.K_w, pygame.K_KP8):
            if self.focus == "sell":
                self.sell_index = max(0, int(self.sell_index) - 1)
                if self._sell_list is not None:
                    self._sell_list.selected_index = int(self.sell_index)
                    self._sell_list.ensure_visible(int(self.sell_index))
            else:
                self.buy_index = max(0, int(self.buy_index) - 1)
                if self._buy_list is not None:
                    self._buy_list.selected_index = int(self.buy_index)
                    self._buy_list.ensure_visible(int(self.buy_index))
            return

        if event.key in (pygame.K_DOWN, pygame.K_s, pygame.K_KP2):
            if self.focus == "sell":
                if self._sell_list is not None:
                    n = len(self._sell_list.items)
                    self.sell_index = min(max(0, n - 1), int(self.sell_index) + 1)
                    self._sell_list.selected_index = int(self.sell_index)
                    self._sell_list.ensure_visible(int(self.sell_index))
            else:
                if self._buy_list is not None:
                    n = len(self._buy_list.items)
                    self.buy_index = min(max(0, n - 1), int(self.buy_index) + 1)
                    self._buy_list.selected_index = int(self.buy_index)
                    self._buy_list.ensure_visible(int(self.buy_index))
            return

    # ------------------------------------------------------------------ #
    # Popup sizing / underlay
    # ------------------------------------------------------------------ #

    def _ensure_window_rect(self, manager) -> None:
        if self.window_rect is not None:
            return
        r = manager.renderer
        w = int(r.width * float(self.popup_scale))
        h = int(r.height * float(self.popup_scale))
        x = (r.width - w) // 2
        y = (r.height - h) // 2
        self.window_rect = pygame.Rect(x, y, w, h)

    def draw_underlay(self, renderer, manager) -> None:
        # Snapshot beneath the popup once (build it from a fresh render of stack below).
        if self._background is None:
            stack = getattr(manager, "scene_stack", None) or []
            try:
                idx = stack.index(self)
            except ValueError:
                idx = len(stack) - 1

            renderer.surface.fill(renderer.bg)

            prev_suspend = getattr(renderer, "suspend_present", False)
            prev_present = getattr(renderer, "present", None)
            prev__present = getattr(renderer, "_present", None)

            renderer.suspend_present = True

            if callable(prev_present):
                renderer.present = (lambda: None)  # type: ignore[assignment]
            if callable(prev__present):
                renderer._present = (lambda: None)  # type: ignore[assignment]

            try:
                for sc in stack[:idx]:
                    if getattr(sc, "uses_live_loop", False):
                        try:
                            sc.render(renderer, manager)
                        except Exception:
                            pass
            finally:
                renderer.suspend_present = prev_suspend
                if callable(prev_present):
                    renderer.present = prev_present  # type: ignore[assignment]
                if callable(prev__present):
                    renderer._present = prev__present  # type: ignore[assignment]

            self._background = renderer.surface.copy()

        # Draw cached background
        renderer.surface.blit(self._background, (0, 0))

        # Dim overlay
        if self.dim_background:
            if self._dim_surf is None or self._dim_surf.get_size() != renderer.surface.get_size():
                self._dim_surf = pygame.Surface(renderer.surface.get_size(), pygame.SRCALPHA)
            self._dim_surf.fill((0, 0, 0, 140))
            renderer.surface.blit(self._dim_surf, (0, 0))

    def draw_panel(self, panel: pygame.Surface, renderer, manager) -> None:
        # Refresh list contents before layout.
        try:
            self._refresh_rows()
        except Exception:
            pass

        panel.fill((10, 10, 20, 240))
        pygame.draw.rect(panel, (220, 220, 240, 255), panel.get_rect(), 2)

        ctx = WidgetContext(surface=panel, game=self.game, scene=self, renderer=renderer)
        self.root.rect = panel.get_rect()
        self.root.layout(ctx)
        self.root.draw(ctx)
