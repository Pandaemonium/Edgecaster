# edgecaster/ui/status_header.py

from __future__ import annotations

import pygame

from edgecaster.ui.widgets import Widget, WidgetContext


class StatusHeaderWidget(Widget):
    """Top-left dungeon header HUD: name + bars + basic stats.

    This used to live in AsciiRenderer.draw_status(). It is now a widget so HUD
    becomes composable and the renderer stays closer to draw-only plumbing.
    """

    @staticmethod
    def _format_meters(dist_m: float) -> str:
        """Always format in meters (no cm/km). Precision adapts smoothly."""
        d = max(0.0, float(dist_m))

        # Choose precision that feels "fluid" without jittering too much.
        # You can tweak these thresholds easily.
        if d >= 10000:
            return f"{d:,.0f} m"
        if d >= 1000:
            return f"{d:,.1f} m"
        if d >= 100:
            return f"{d:.1f} m"
        if d >= 10:
            return f"{d:.2f} m"
        if d >= 1:
            return f"{d:.3f} m"
        # Below 1 meter, keep a bit more precision.
        if d >= 0.1:
            return f"{d:.4f} m"
        if d >= 0.01:
            return f"{d:.5f} m"
        return f"{d:.6f} m"

    def layout(self, ctx: WidgetContext) -> None:
        # Default to the renderer's top bar region if caller didn't set a size.
        if self.rect.w == 0 and self.rect.h == 0:
            w = getattr(ctx.renderer, "width", ctx.surface.get_width())
            h = getattr(ctx.renderer, "top_bar_height", 64)
            self.rect = pygame.Rect(0, 0, w, h)
        super().layout(ctx)

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        game = ctx.game
        renderer = ctx.renderer
        if game is None or renderer is None:
            return

        small_font = getattr(renderer, "small_font", None)
        if small_font is None:
            return

        # Reuse renderer colors/fonts so visuals stay consistent.
        fg = getattr(renderer, "fg", (220, 230, 240))
        bar_bg = getattr(renderer, "bar_bg", (40, 40, 60))
        hp_color = getattr(renderer, "hp_color", (200, 80, 80))
        mana_color = getattr(renderer, "mana_color", (90, 160, 255))

        xp_color = (120, 200, 120)
        coh_color = (200, 180, 100)

        player = game.actors[game.player_id]
        x = self.rect.x + 12
        y = self.rect.y + 12

        bar_w = 220
        bar_h = 12

        # --- Header line: name / host / level ---
        char = getattr(game, "character", None)
        if char:
            name = getattr(char, "name", None) or "Edgecaster"
        else:
            name = getattr(player, "name", "Edgecaster")

        lvl = getattr(getattr(player, "stats", None), "level", 1)

        label = getattr(game, "current_host_label", None)
        header_line = f"{name} the {label}" if label else str(name)
        header_line += f" (Lv {lvl})"

        header_text = small_font.render(header_line, True, fg)
        ctx.surface.blit(header_text, (x, y))
        y += header_text.get_height() + 10

        # --- HP bar ---
        pygame.draw.rect(ctx.surface, bar_bg, pygame.Rect(x, y, bar_w, bar_h))
        max_hp = max(0, getattr(player.stats, "max_hp", 0))
        hp = max(0, getattr(player.stats, "hp", 0))
        hp_ratio = 0 if max_hp == 0 else hp / max_hp
        pygame.draw.rect(ctx.surface, hp_color, pygame.Rect(x, y, int(bar_w * hp_ratio), bar_h))
        hp_text = small_font.render(f"HP {hp}/{max_hp}", True, fg)
        ctx.surface.blit(hp_text, (x + 4, y - 18))

        # --- XP bar to the right of HP ---
        xp_needed = max(1, getattr(player.stats, "xp_to_next", 1))
        xp_cur = getattr(player.stats, "xp", 0)
        xp_text = small_font.render(f"XP {xp_cur}/{xp_needed}   (Lv {lvl})", True, fg)
        ctx.surface.blit(xp_text, (x + bar_w + 20, y - 18))

        xp_x = x + bar_w + 20
        xp_y = y
        xp_ratio = max(0.0, min(1.0, xp_cur / xp_needed))
        xp_w = 180
        pygame.draw.rect(ctx.surface, bar_bg, pygame.Rect(xp_x, xp_y, xp_w, bar_h))
        pygame.draw.rect(ctx.surface, xp_color, pygame.Rect(xp_x, xp_y, int(xp_w * xp_ratio), bar_h))

        # --- Mana bar below HP ---
        y += bar_h + 16
        pygame.draw.rect(ctx.surface, bar_bg, pygame.Rect(x, y, bar_w, bar_h))
        max_mana = max(0, getattr(player.stats, "max_mana", 0))
        mana = max(0, getattr(player.stats, "mana", 0))
        mp_ratio = 0 if max_mana == 0 else mana / max_mana
        pygame.draw.rect(ctx.surface, mana_color, pygame.Rect(x, y, int(bar_w * mp_ratio), bar_h))
        mp_text = small_font.render(f"Mana {mana}/{max_mana}", True, fg)
        ctx.surface.blit(mp_text, (x + 4, y - 18))

        # --- Coherence bar to the right of mana ---
        coh_x = x + bar_w + 20
        coh_y = y
        coh = max(0, getattr(player.stats, "coherence", 0))
        coh_max = max(1, getattr(player.stats, "max_coherence", 1))
        coh_ratio = max(0.0, min(1.0, coh / coh_max))
        coh_w = 200
        pygame.draw.rect(ctx.surface, bar_bg, pygame.Rect(coh_x, coh_y, coh_w, bar_h))
        pygame.draw.rect(ctx.surface, coh_color, pygame.Rect(coh_x, coh_y, int(coh_w * coh_ratio), bar_h))
        coh_text = small_font.render(f"Coherence {coh}/{coh_max}", True, fg)
        ctx.surface.blit(coh_text, (coh_x + 4, coh_y - 18))

        # --- Character stats & vertices under bars ---
        y += bar_h + 12
        if hasattr(game, "character") and game.character:
            if hasattr(game, "effective_character_stats"):
                stats = game.effective_character_stats()
            else:
                stats = game.character.stats
            line = (
                f"CON {stats.get('con',0)}  "
                f"AGI {stats.get('agi',0)}  "
                f"INT {stats.get('int',0)}  "
                f"RES {stats.get('res',0)}"
            )
            stats_text = small_font.render(line, True, fg)
            ctx.surface.blit(stats_text, (x, y))

            verts_count = len(game.pattern.vertices) if hasattr(game, "pattern") else 0
            coh_limit = game._coherence_limit()
            y += 18
            label2 = f"Vertices {verts_count}/{coh_limit}"
            coh_text2 = small_font.render(label2, True, fg)
            ctx.surface.blit(coh_text2, (x, y))

        # --- Tick / zone info top-right ---
        tick_text = small_font.render(f"Tick: {game.current_tick}", True, fg)
        zx, zy, zz = getattr(game, "zone", (0, 0, getattr(game, "level_index", 0)))
        level_text = small_font.render(f"Zone ({zx},{zy}) Depth {zz}", True, fg)

        surface_w = getattr(renderer, "width", ctx.surface.get_width())
        tick_x = surface_w - tick_text.get_width() - 8
        level_x = surface_w - level_text.get_width() - 8

        # --- Currency (bismuth) icon + amount, left of tick/zone block ---
        try:
            balance = getattr(game, "bismuth", 0)
        except Exception:
            balance = 0

        icon = getattr(renderer, "bismuth_icon_hud", None) or getattr(renderer, "bismuth_icon", None)
        bal_text = small_font.render(f"{balance}", True, fg)

        pad = 8
        icon_w = icon.get_width() if icon is not None else 0
        icon_h = icon.get_height() if icon is not None else 0

        # Place currency immediately to the left of whichever of tick/zone is farther left.
        currency_right = min(tick_x, level_x) - pad
        bx = currency_right - icon_w - bal_text.get_width() - pad
        by = self.rect.y + 12
        if bx < pad:
            bx = pad

        # --- Zoom scale bar: fixed pixel length, label changes fluidly ---
        # Assumption: LoD 0 tiles correspond to 1 meter in-world.
        # tile_px should represent pixels per world-tile at current zoom.
        tile_px = float(getattr(renderer, "tile_px", getattr(renderer, "tile", 16) or 16))
        tile_px = max(1e-6, tile_px)

        # Fixed on-screen length for the bar (does NOT change with zoom).
        SCALE_BAR_PX = 110

        # Put the scale bar to the *left of the bismuth indicator*.
        # We reserve room for the bismuth icon+amount; bar ends a little before bx.
        bar_gap = 12
        bar_right = max(pad + SCALE_BAR_PX, bx - bar_gap)
        bar_left = bar_right - SCALE_BAR_PX

        # Convert bar pixels -> world tiles -> meters (LoD0: 1 tile = 1 m)
        meters_per_px = 1.0 / tile_px
        bar_meters = float(SCALE_BAR_PX) * meters_per_px
        bar_label = self._format_meters(bar_meters)

        bar_y = self.rect.y + 50
        line_y = bar_y + 3

        # Draw line with end caps
        pygame.draw.line(ctx.surface, fg, (bar_left, line_y), (bar_right, line_y), 2)
        pygame.draw.line(ctx.surface, fg, (bar_left, line_y - 4), (bar_left, line_y + 4), 2)
        pygame.draw.line(ctx.surface, fg, (bar_right, line_y - 4), (bar_right, line_y + 4), 2)

        label_surf = small_font.render(bar_label, True, fg)
        lx = bar_right - label_surf.get_width()
        ly = bar_y - label_surf.get_height() - 2
        if ly < self.rect.y + 2:
            ly = bar_y + 10
        ctx.surface.blit(label_surf, (lx, ly))

        # --- Draw bismuth after scale bar so it stays visually on top / unambiguous ---
        if icon is not None:
            ctx.surface.blit(icon, (bx, by))
            bx2 = bx + icon_w + 6
        else:
            bx2 = bx

        ctx.surface.blit(
            bal_text,
            (bx2, by + max(0, (icon_h - bal_text.get_height()) // 2)),
        )

        # --- Draw tick/zone text (far right) ---
        ctx.surface.blit(tick_text, (tick_x, 12))
        ctx.surface.blit(level_text, (level_x, 30))

        super().draw(ctx)
