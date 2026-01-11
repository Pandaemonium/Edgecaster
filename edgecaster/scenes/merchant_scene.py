from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pygame

from .base import PanelScene
from .quantity_prompt_scene import QuantityPromptScene

from edgecaster.systems import trade
from edgecaster.systems.inventory import get_quantity
from edgecaster.systems import equipment as equipment_system
from edgecaster.ui.widgets import (
    ButtonWidget,
    HBox,
    LabelWidget,
    ScaledLabelWidget,
    TwoColumnListWidget,
    VBox,
    WidgetContext,
)


@dataclass
class _TradeRow:
    label: str
    value: str
    index: int
    ent_id: str

    def __str__(self) -> str:
        return self.label


class MerchantScene(PanelScene):
    """Popup trade UI (buy/sell) for a single merchant actor.

    Trades are staged until the player clicks Accept:
    - Click/Enter toggles an item in the proposal
    - Accept commits the whole proposal atomically and exits
    - Esc cancels (discard proposal) and exits
    """

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

        # Proposed trades (staged until Accept)
        # Maps entity_id -> quantity to trade
        self.pending_buy_qtys: dict[str, int] = {}
        self.pending_sell_qtys: dict[str, int] = {}

        self._close_requested: str | None = None  # "accept" | "cancel" | None
        self._current_manager: Any = None  # Set during event handling for quantity prompts

        self._header: ScaledLabelWidget | None = None
        self._buy_list: TwoColumnListWidget | None = None
        self._sell_list: TwoColumnListWidget | None = None
        self._summary: LabelWidget | None = None
        self._status: LabelWidget | None = None

        # Keep merchant theme playing while the trade UI is open
        self.music_override_key = "shop"


        self._build_widgets()

    def _sell_id_is_equipped(self, ent_id: str) -> bool:
        ent_id = str(ent_id)
        if not ent_id:
            return False
        try:
            pinv = list(getattr(self.game, "player_inventory", []) or [])
        except Exception:
            pinv = []
        for ent in pinv:
            if str(getattr(ent, "id", "")) == ent_id:
                return equipment_system.is_equipped(ent)
        return False

    # ------------------------------------------------------------------ #
    # Layout / widgets
    # ------------------------------------------------------------------ #

    def _build_widgets(self) -> None:
        self._header = ScaledLabelWidget("Trade", align="left", scale=2)

        left = VBox(spacing=8, padding=0, align="left")
        left.add_child(LabelWidget("Merchant Stock", align="left"))
        self._buy_list = TwoColumnListWidget(
            [],
            selected_index=0,
            on_activate=self._on_buy_click,
            padding=6,
            line_spacing=4,
            value_gap=18,
        )
        left.add_child(self._buy_list)

        right = VBox(spacing=8, padding=0, align="left")
        right.add_child(LabelWidget("Your Inventory", align="left"))
        self._sell_list = TwoColumnListWidget(
            [],
            selected_index=0,
            on_activate=self._on_sell_click,
            padding=6,
            line_spacing=4,
            value_gap=18,
        )
        right.add_child(self._sell_list)

        cols = HBox(spacing=18, padding=0, valign="top")
        cols.add_child(left)
        cols.add_child(right)

        self._summary = LabelWidget("", align="left")
        self._status = LabelWidget("", align="left", color=(255, 120, 120))

        buttons = HBox(spacing=12, padding=0, valign="top")
        buttons.add_child(ButtonWidget("Accept", on_click=lambda _b: self._request_close("accept")))
        buttons.add_child(ButtonWidget("Cancel", on_click=lambda _b: self._request_close("cancel")))

        footer = LabelWidget(
            "Tab/Left/Right: switch  |  Enter/Click: propose  |  Accept: commit & exit  |  Esc: cancel",
            align="left",
        )

        root = VBox(spacing=12, padding=14, align="left")
        root.add_child(self._header)
        root.add_child(cols)
        root.add_child(self._summary)
        root.add_child(self._status)
        root.add_child(buttons)
        root.add_child(footer)
        self.root = root

    def _request_close(self, kind: str) -> None:
        self._close_requested = str(kind)

    def _toggle_pending_buy(self, ent_id: str, ent: Any = None, manager: Any = None) -> None:
        """Toggle or adjust quantity for a buy proposal."""
        ent_id = str(ent_id)
        if not ent_id:
            return

        # If already in proposal, remove it
        if ent_id in self.pending_buy_qtys:
            del self.pending_buy_qtys[ent_id]
            if self._status is not None:
                self._status.text = ""
            return

        # Check quantity of the item
        qty = get_quantity(ent) if ent else 1

        if qty > 1 and manager is not None:
            # Show quantity prompt
            def on_qty(amount: int) -> None:
                if amount > 0:
                    self.pending_buy_qtys[ent_id] = amount
                if self._status is not None:
                    self._status.text = ""

            manager.push_scene(QuantityPromptScene(
                self.game,
                qty,
                on_qty,
                title="Buy how many?",
            ))
        else:
            # Single item - add directly
            self.pending_buy_qtys[ent_id] = qty
            if self._status is not None:
                self._status.text = ""

    def _toggle_pending_sell(self, ent_id: str, ent: Any = None, manager: Any = None) -> None:
        """Toggle or adjust quantity for a sell proposal."""
        ent_id = str(ent_id)
        if not ent_id:
            return

        # If already in proposal, remove it
        if ent_id in self.pending_sell_qtys:
            del self.pending_sell_qtys[ent_id]
            if self._status is not None:
                self._status.text = ""
            return

        # Check quantity of the item
        qty = get_quantity(ent) if ent else 1

        if qty > 1 and manager is not None:
            # Show quantity prompt
            def on_qty(amount: int) -> None:
                if amount > 0:
                    self.pending_sell_qtys[ent_id] = amount
                if self._status is not None:
                    self._status.text = ""

            manager.push_scene(QuantityPromptScene(
                self.game,
                qty,
                on_qty,
                title="Sell how many?",
            ))
        else:
            # Single item - add directly
            self.pending_sell_qtys[ent_id] = qty
            if self._status is not None:
                self._status.text = ""

    def _refresh_rows(self) -> None:
        level = self.game._level()
        merchant = getattr(level, "actors", {}).get(self.merchant_actor_id)
        if merchant is None:
            return

        trade.ensure_merchant_initialized(self.game, level, merchant)

        buy_rows: list[_TradeRow] = []
        minv = list(self.game.get_inventory(self.merchant_actor_id))
        valid_buy = {str(getattr(ent, "id", "")) for ent in minv}
        # Remove invalid entries from pending
        for eid in list(self.pending_buy_qtys.keys()):
            if eid not in valid_buy:
                del self.pending_buy_qtys[eid]
        for i, ent in enumerate(minv):
            ent_id = str(getattr(ent, "id", ""))
            q = trade.quote_prices(merchant, ent)
            price = int(q.buy_price) if q else 0
            name = str(getattr(ent, "name", "Item"))
            qty = get_quantity(ent)
            qty_suffix = f" ({qty})" if qty > 1 else ""
            pending_qty = self.pending_buy_qtys.get(ent_id, 0)
            if pending_qty > 0:
                prefix = f"+{pending_qty} "
            else:
                prefix = ""
            buy_rows.append(
                _TradeRow(
                    label=f"{prefix}{name}{qty_suffix}",
                    value=f"{price}b" if price else "",
                    index=i,
                    ent_id=ent_id,
                )
            )

        sell_rows: list[_TradeRow] = []
        pinv = list(getattr(self.game, "player_inventory", []) or [])
        valid_sell = {str(getattr(ent, "id", "")) for ent in pinv}
        # Remove invalid entries from pending
        for eid in list(self.pending_sell_qtys.keys()):
            if eid not in valid_sell:
                del self.pending_sell_qtys[eid]
        for i, ent in enumerate(pinv):
            ent_id = str(getattr(ent, "id", ""))
            equipped = equipment_system.is_equipped(ent)
            if equipped and ent_id:
                # Equipped items can't be sold; don't allow them in the proposal.
                self.pending_sell_qtys.pop(ent_id, None)
            q = trade.quote_prices(merchant, ent)
            price = int(q.sell_price) if q else 0
            name = str(getattr(ent, "name", "Item"))
            qty = get_quantity(ent)
            qty_suffix = f" ({qty})" if qty > 1 else ""
            pending_qty = self.pending_sell_qtys.get(ent_id, 0)
            if equipped:
                prefix = "[E] "
            elif pending_qty > 0:
                prefix = f"-{pending_qty} "
            else:
                prefix = ""
            sell_rows.append(
                _TradeRow(
                    label=f"{prefix}{name}{qty_suffix}",
                    value=f"{price}b" if price else "",
                    index=i,
                    ent_id=ent_id,
                )
            )

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

        if self._header is not None:
            try:
                p = int(getattr(self.game, "bismuth", 0))
            except Exception:
                p = 0
            m = int(trade.merchant_funds(merchant))
            mname = str(getattr(merchant, "name", "Merchant"))
            self._header.text = f"Trade - {mname} ({m}b)  |  You ({p}b)"

        if self._summary is not None:
            summary = trade.proposal_summary_with_qty(
                self.game,
                self.merchant_actor_id,
                self.pending_buy_qtys,
                self.pending_sell_qtys,
            )
            buy_count = sum(self.pending_buy_qtys.values())
            sell_count = sum(self.pending_sell_qtys.values())
            if not self.pending_buy_qtys and not self.pending_sell_qtys:
                self._summary.text = "No proposed trades."
            else:
                self._summary.text = (
                    f"Proposed: buy {buy_count} (cost {summary.buy_total}b)  |  "
                    f"sell {sell_count} (earn {summary.sell_total}b)  |  "
                    f"net {summary.net_player:+d}b  |  "
                    f"after: you {summary.player_bismuth_after}b, merchant {summary.merchant_funds_after}b"
                )
                if not summary.ok and summary.reason:
                    self._summary.text += f"  |  {summary.reason}"

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #

    def _on_buy_click(self, idx: int, item: Any) -> None:
        self.focus = "buy"
        self.buy_index = int(idx)
        if isinstance(item, _TradeRow):
            # Get the entity for quantity check
            minv = list(self.game.get_inventory(self.merchant_actor_id))
            ent = minv[item.index] if 0 <= item.index < len(minv) else None
            self._toggle_pending_buy(item.ent_id, ent=ent, manager=self._current_manager)

    def _on_sell_click(self, idx: int, item: Any) -> None:
        self.focus = "sell"
        self.sell_index = int(idx)
        if isinstance(item, _TradeRow):
            if self._sell_id_is_equipped(item.ent_id):
                if self._status is not None:
                    self._status.text = "Unequip that item before selling."
                return
            # Get the entity for quantity check
            pinv = list(getattr(self.game, "player_inventory", []) or [])
            ent = pinv[item.index] if 0 <= item.index < len(pinv) else None
            self._toggle_pending_sell(item.ent_id, ent=ent, manager=self._current_manager)

    def _activate_focused(self) -> None:
        if self.focus == "sell":
            if self._sell_list is None:
                return
            if not (0 <= int(self.sell_index) < len(self._sell_list.items)):
                return
            row = self._sell_list.items[int(self.sell_index)]
            if isinstance(row, _TradeRow):
                if self._sell_id_is_equipped(row.ent_id):
                    if self._status is not None:
                        self._status.text = "Unequip that item before selling."
                    return
                # Get entity for quantity check
                pinv = list(getattr(self.game, "player_inventory", []) or [])
                ent = pinv[row.index] if 0 <= row.index < len(pinv) else None
                self._toggle_pending_sell(row.ent_id, ent=ent, manager=self._current_manager)
            return

        if self._buy_list is None:
            return
        if not (0 <= int(self.buy_index) < len(self._buy_list.items)):
            return
        row = self._buy_list.items[int(self.buy_index)]
        if isinstance(row, _TradeRow):
            # Get entity for quantity check
            minv = list(self.game.get_inventory(self.merchant_actor_id))
            ent = minv[row.index] if 0 <= row.index < len(minv) else None
            self._toggle_pending_buy(row.ent_id, ent=ent, manager=self._current_manager)

    def _panel_event(self, event, manager) -> None:
        # Store manager for quantity prompts
        self._current_manager = manager

        if event.type != pygame.KEYDOWN:
            return

        if event.key == pygame.K_ESCAPE:
            self.pending_buy_qtys.clear()
            self.pending_sell_qtys.clear()
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

    def update(self, dt_ms: int, manager) -> None:
        super().update(dt_ms, manager)
        # Store manager for quantity prompts (in case update is called before event)
        self._current_manager = manager

        if self._close_requested == "cancel":
            self._close_requested = None
            self.pending_buy_qtys.clear()
            self.pending_sell_qtys.clear()
            manager.pop_scene()
            return

        if self._close_requested == "accept":
            self._close_requested = None

            if not self.pending_buy_qtys and not self.pending_sell_qtys:
                manager.pop_scene()
                return

            ok, msg = trade.apply_proposal_with_qty(
                self.game,
                self.merchant_actor_id,
                self.pending_buy_qtys,
                self.pending_sell_qtys,
            )
            if ok:
                self.pending_buy_qtys.clear()
                self.pending_sell_qtys.clear()
                manager.pop_scene()
            else:
                if self._status is not None:
                    self._status.text = msg

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
        try:
            self._refresh_rows()
        except Exception:
            pass

        panel.fill((10, 10, 20, 240))
        pygame.draw.rect(panel, (220, 220, 240, 255), panel.get_rect(), 2)

        ctx = WidgetContext(surface=panel, game=self.game, scene=self, renderer=renderer)
        self.root.rect = panel.get_rect()

        # Allocate list geometry so it fills the popup and scroll behaves sensibly.
        pad = 14
        inner_w = max(1, panel.get_width() - 2 * pad)
        col_w = max(140, (inner_w - 18) // 2)
        list_h = max(160, panel.get_height() - 240)
        if self._buy_list is not None:
            self._buy_list.rect.width = int(col_w)
            self._buy_list.rect.height = int(list_h)
        if self._sell_list is not None:
            self._sell_list.rect.width = int(col_w)
            self._sell_list.rect.height = int(list_h)

        self.root.layout(ctx)
        self.root.draw(ctx)

        # Light framing around the list panes; highlight the focused side.
        try:
            focus_col = (220, 220, 240, 255)
            dim_col = (110, 110, 130, 255)
            if self._buy_list is not None:
                pygame.draw.rect(panel, focus_col if self.focus == "buy" else dim_col, self._buy_list.rect, 1)
            if self._sell_list is not None:
                pygame.draw.rect(panel, focus_col if self.focus == "sell" else dim_col, self._sell_list.rect, 1)
        except Exception:
            pass
