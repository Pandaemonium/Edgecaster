"""Gardener Branch Editor — constrained fractal pattern designer.

Two tiers:
  Tier 1 (level 1+): horizontal baseline + 1 branch edge, auto-mirrored.
  Tier 2 (level 5+): horizontal baseline + N independent edges (no mirror).

The output is a ``{vertices, edges}`` dict compatible with
``CustomGraphGenerator``, stored on the game as ``gardener_branch_pattern``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pygame

from .base import Scene
from edgecaster.math_utils import lerp_rgb
from edgecaster.patterns import builder

Color = Tuple[int, int, int]
Vec2 = Tuple[float, float]

# Palette -------------------------------------------------------------------

YELLOW = (240, 210, 80)
GREEN = (100, 200, 100)
DIM_GREEN = (60, 130, 60)
WHITE = (230, 230, 240)
RED = (240, 120, 120)
CYAN = (120, 200, 240)
BASELINE_COL = (200, 190, 140)
PENDING_COL = (255, 255, 120)

# Grid constants (matches FractalEditorScene baseline convention) -----------

GRID_X_MIN = 0
GRID_X_MAX = 10
GRID_Y_MIN = -5
GRID_Y_MAX = 5


def branch_edge_budget(player_level: int) -> Tuple[int, int]:
    """Return ``(tier, max_branch_edges)`` for the player's level."""
    if player_level < 5:
        return 1, 1
    extra = (player_level - 5) // 4
    return 2, 3 + extra


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class BranchEditorState:
    tier: int = 1
    max_branch_edges: int = 1
    # vertices[0] = (0,0), vertices[-1] = (10,0); baseline always present.
    vertices: List[Vec2] = field(default_factory=lambda: [(0.0, 0.0), (10.0, 0.0)])
    edges: List[Tuple[int, int]] = field(default_factory=lambda: [(0, 1)])
    # Track which edges are "branch" vs "baseline" for rendering / removal.
    branch_edge_indices: List[int] = field(default_factory=list)
    # Two-click flow: first click picks origin on baseline.
    pending_origin_idx: Optional[int] = None


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------

class BranchEditorScene(Scene):
    """Constrained branch editor for the Gardener class."""

    def __init__(
        self,
        tier: int = 1,
        max_edges: int = 1,
        existing: Optional[dict] = None,
        window_rect: Optional[pygame.Rect] = None,
    ) -> None:
        self.tier = max(1, tier)
        self.max_edges = max(1, max_edges)
        self.window_rect = window_rect
        self.panel_rect: Optional[pygame.Rect] = None
        self._font: Optional[pygame.font.Font] = None
        self._small_font: Optional[pygame.font.Font] = None
        self.margin = 40
        self.cell_size = 0
        self.status_msg = ""

        # Build initial state from existing pattern or blank.
        if existing and isinstance(existing, dict):
            self.state = self._load_existing(existing)
        else:
            self.state = BranchEditorState(tier=self.tier, max_branch_edges=self.max_edges)
        self.state.tier = self.tier
        self.state.max_branch_edges = self.max_edges

        self.undo_stack: List[Tuple[List[Vec2], List[Tuple[int, int]], List[int], Optional[int]]] = []

    # ------------------------------------------------------------------
    # Existing pattern import
    # ------------------------------------------------------------------

    @staticmethod
    def _load_existing(data: dict) -> BranchEditorState:
        verts = list(data.get("vertices") or [(0.0, 0.0), (10.0, 0.0)])
        edges = list(data.get("edges") or [(0, 1)])
        # Identify branch edges (any edge where at least one vertex is off the baseline).
        branch_idxs: List[int] = []
        for i, (a, b) in enumerate(edges):
            ay = verts[a][1] if a < len(verts) else 0
            by = verts[b][1] if b < len(verts) else 0
            if abs(ay) > 0.01 or abs(by) > 0.01:
                branch_idxs.append(i)
        st = BranchEditorState(vertices=verts, edges=edges, branch_edge_indices=branch_idxs)
        return st

    # ------------------------------------------------------------------
    # Grid math (borrowed from FractalEditorScene)
    # ------------------------------------------------------------------

    def _compute_cell_size(self, panel: pygame.Rect) -> None:
        w = GRID_X_MAX - GRID_X_MIN
        h = GRID_Y_MAX - GRID_Y_MIN
        self.cell_size = min(
            max(8, (panel.width - 2 * self.margin) // max(1, w)),
            max(8, (panel.height - 2 * self.margin - 80) // max(1, h)),
        )

    def _grid_to_screen(self, gx: float, gy: float, panel: pygame.Rect) -> Tuple[int, int]:
        if self.cell_size <= 0:
            self._compute_cell_size(panel)
        sx = panel.left + self.margin + int((gx - GRID_X_MIN) * self.cell_size)
        sy = panel.top + self.margin + int((GRID_Y_MAX - gy) * self.cell_size)
        return sx, sy

    def _screen_to_grid(self, sx: int, sy: int, panel: pygame.Rect) -> Vec2:
        if self.cell_size <= 0:
            self._compute_cell_size(panel)
        gx = GRID_X_MIN + (sx - (panel.left + self.margin)) / float(self.cell_size)
        gy = GRID_Y_MAX - (sy - (panel.top + self.margin)) / float(self.cell_size)
        return gx, gy

    def _nearest_grid_point(self, gx: float, gy: float) -> Vec2:
        return (round(gx), round(gy))

    def _in_grid(self, gx: float, gy: float) -> bool:
        return GRID_X_MIN <= gx <= GRID_X_MAX and GRID_Y_MIN <= gy <= GRID_Y_MAX

    def _vertex_at_screen(self, sx: int, sy: int, panel: pygame.Rect) -> Optional[int]:
        hit_r = max(6, self.cell_size // 2)
        for idx, (vx, vy) in enumerate(self.state.vertices):
            px, py = self._grid_to_screen(vx, vy, panel)
            if (sx - px) ** 2 + (sy - py) ** 2 <= hit_r ** 2:
                return idx
        return None

    def _is_on_baseline(self, gy: float) -> bool:
        return abs(gy) < 0.01

    # ------------------------------------------------------------------
    # Vertex / edge helpers
    # ------------------------------------------------------------------

    def _find_or_add_vertex(self, pos: Vec2) -> int:
        for i, v in enumerate(self.state.vertices):
            if abs(v[0] - pos[0]) < 0.01 and abs(v[1] - pos[1]) < 0.01:
                return i
        self.state.vertices.append(pos)
        return len(self.state.vertices) - 1

    def _split_baseline_at(self, x: float) -> int:
        """Ensure a vertex exists at (x, 0) and the baseline edges are split there."""
        pos = (float(x), 0.0)
        idx = self._find_or_add_vertex(pos)

        # Check if baseline edges already reference this vertex.
        # If not, find the baseline edge that spans this x and split it.
        # Baseline edges connect vertices on y=0.
        baseline_edges = []
        for ei, (a, b) in enumerate(self.state.edges):
            if ei in self.state.branch_edge_indices:
                continue
            va = self.state.vertices[a]
            vb = self.state.vertices[b]
            if abs(va[1]) < 0.01 and abs(vb[1]) < 0.01:
                baseline_edges.append(ei)

        for ei in baseline_edges:
            a, b = self.state.edges[ei]
            va = self.state.vertices[a]
            vb = self.state.vertices[b]
            lo_x = min(va[0], vb[0])
            hi_x = max(va[0], vb[0])
            if lo_x < x < hi_x and a != idx and b != idx:
                # Split this edge: remove it, add two edges through idx.
                self.state.edges[ei] = (a, idx)
                new_edge = (idx, b)
                self.state.edges.append(new_edge)
                # Adjust branch_edge_indices for the new edge count.
                break

        return idx

    def _branch_count(self) -> int:
        return len(self.state.branch_edge_indices)

    def _clear_branches(self) -> None:
        """Remove all branch edges and their off-baseline vertices."""
        # Remove branch edges (iterate in reverse to keep indices stable).
        for ei in sorted(self.state.branch_edge_indices, reverse=True):
            if ei < len(self.state.edges):
                self.state.edges.pop(ei)
        self.state.branch_edge_indices.clear()

        # Remove vertices not on the baseline and not referenced by remaining edges.
        referenced: set[int] = set()
        for a, b in self.state.edges:
            referenced.add(a)
            referenced.add(b)
        to_remove = []
        for i, (vx, vy) in enumerate(self.state.vertices):
            if abs(vy) > 0.01 and i not in referenced:
                to_remove.append(i)
        # Remove in reverse order and remap edges.
        for ri in sorted(to_remove, reverse=True):
            self.state.vertices.pop(ri)
            # Remap edge indices.
            new_edges = []
            for a, b in self.state.edges:
                na = a - 1 if a > ri else a
                nb = b - 1 if b > ri else b
                new_edges.append((na, nb))
            self.state.edges = new_edges

        # Rebuild a clean baseline if needed.
        self._ensure_baseline()

    def _ensure_baseline(self) -> None:
        """Ensure (0,0) and (10,0) exist and are connected."""
        idx0 = self._find_or_add_vertex((0.0, 0.0))
        idx10 = self._find_or_add_vertex((10.0, 0.0))
        # Check if there's at least one baseline edge chain.
        has_edge = any(
            (a == idx0 or b == idx0 or a == idx10 or b == idx10)
            for a, b in self.state.edges
        )
        if not has_edge:
            self.state.edges = [(idx0, idx10)]
            self.state.branch_edge_indices.clear()

    def _remove_last_branch(self) -> None:
        """Remove the most recently added branch edge."""
        if not self.state.branch_edge_indices:
            return
        ei = self.state.branch_edge_indices.pop()
        if ei < len(self.state.edges):
            a, b = self.state.edges[ei]
            self.state.edges.pop(ei)
            # Adjust remaining branch indices.
            self.state.branch_edge_indices = [
                x - 1 if x > ei else x for x in self.state.branch_edge_indices
            ]
            # Remove orphan off-baseline vertices.
            referenced: set[int] = set()
            for ea, eb in self.state.edges:
                referenced.add(ea)
                referenced.add(eb)
            for vi in (a, b):
                if vi >= len(self.state.vertices):
                    continue
                vx, vy = self.state.vertices[vi]
                if abs(vy) > 0.01 and vi not in referenced:
                    self.state.vertices.pop(vi)
                    # Remap.
                    new_edges = []
                    for ea, eb in self.state.edges:
                        na = ea - 1 if ea > vi else ea
                        nb = eb - 1 if eb > vi else eb
                        new_edges.append((na, nb))
                    self.state.edges = new_edges
                    self.state.branch_edge_indices = [
                        x if x < vi else x for x in self.state.branch_edge_indices
                    ]

    # ------------------------------------------------------------------
    # Click handling
    # ------------------------------------------------------------------

    def _handle_click(self, mx: int, my: int) -> None:
        panel = self.panel_rect
        assert panel is not None
        gx, gy = self._screen_to_grid(mx, my, panel)
        gx, gy = self._nearest_grid_point(gx, gy)
        if not self._in_grid(gx, gy):
            return

        pending = self.state.pending_origin_idx

        if pending is None:
            # First click: must be on the baseline.
            if not self._is_on_baseline(gy):
                self.status_msg = "First click must be on the baseline (y=0)."
                return
            if gx <= GRID_X_MIN or gx >= GRID_X_MAX:
                self.status_msg = "Pick a point between the endpoints."
                return
            # Split baseline at this x and set as pending origin.
            origin_idx = self._split_baseline_at(gx)
            self.state.pending_origin_idx = origin_idx
            self.status_msg = f"Origin set at ({int(gx)}, 0). Click a branch tip."
        else:
            # Second click: the branch tip.
            if self._is_on_baseline(gy):
                self.status_msg = "Branch tip must be off the baseline."
                return

            # Check budget.
            if self.state.tier == 1:
                # Tier 1: clear any existing branch first (only 1 allowed).
                if self._branch_count() > 0:
                    self._clear_branches()
                    # Re-find origin after clear.
                    origin_pos = self.state.vertices[pending] if pending < len(self.state.vertices) else None
                    if origin_pos:
                        pending = self._split_baseline_at(origin_pos[0])
                    else:
                        self.state.pending_origin_idx = None
                        self.status_msg = "Origin lost. Click baseline again."
                        return
            else:
                # Tier 2: check budget.
                if self._branch_count() >= self.state.max_branch_edges:
                    self.status_msg = f"Edge budget reached ({self.state.max_branch_edges}). Remove one first."
                    self.state.pending_origin_idx = None
                    return

            tip_pos = (float(gx), float(gy))
            tip_idx = self._find_or_add_vertex(tip_pos)
            edge = (pending, tip_idx)
            self.state.edges.append(edge)
            self.state.branch_edge_indices.append(len(self.state.edges) - 1)

            # Tier 1: auto-mirror.
            if self.state.tier == 1 and abs(gy) > 0.01:
                mirror_pos = (float(gx), -float(gy))
                mirror_idx = self._find_or_add_vertex(mirror_pos)
                mirror_edge = (pending, mirror_idx)
                self.state.edges.append(mirror_edge)
                self.state.branch_edge_indices.append(len(self.state.edges) - 1)

            self.state.pending_origin_idx = None
            n = self._branch_count()
            if self.state.tier == 1:
                self.status_msg = "Branch placed (mirrored). Enter to accept."
            else:
                self.status_msg = f"Branch {n}/{self.state.max_branch_edges} placed."

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _build_preview_segments(self) -> List[Tuple[Vec2, Vec2]]:
        """Apply the current design as a CustomGraphGenerator to a sample segment."""
        verts = list(self.state.vertices)
        edges = list(self.state.edges)
        if len(verts) < 2 or not edges:
            return []
        try:
            gen = builder.CustomGraphGenerator(verts, edges, amplitude=1.0)
            segs = builder.line_pattern((0.0, 0.0), (8.0, 0.0)).to_segments()
            segs = gen.apply_segments(segs, max_segments=500)
            segs = builder.cleanup_duplicates(segs)
            return [(s.a, s.b) for s in segs]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Accept / reorder
    # ------------------------------------------------------------------

    def _build_output(self) -> dict:
        """Produce the output dict with (0,0) first and (10,0) last."""
        verts = list(self.state.vertices)
        edges = list(self.state.edges)

        # Ensure (0,0) is at index 0.
        origin = (0.0, 0.0)
        if origin in verts and verts.index(origin) != 0:
            old = verts.index(origin)
            verts[0], verts[old] = verts[old], verts[0]
            edges = [
                (old if a == 0 else (0 if a == old else a),
                 old if b == 0 else (0 if b == old else b))
                for a, b in edges
            ]

        # Ensure (10,0) is at the last index.
        terminus = (10.0, 0.0)
        last = len(verts) - 1
        if terminus in verts and verts.index(terminus) != last:
            old = verts.index(terminus)
            verts[last], verts[old] = verts[old], verts[last]
            edges = [
                (old if a == last else (last if a == old else a),
                 old if b == last else (last if b == old else b))
                for a, b in edges
            ]

        return {
            "vertices": verts,
            "edges": edges,
            "tier": self.state.tier,
        }

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self, surface: pygame.Surface) -> None:
        panel = self.panel_rect
        assert panel is not None

        overlay = pygame.Surface((panel.width, panel.height), pygame.SRCALPHA)
        pygame.draw.rect(overlay, (10, 10, 20, 230), overlay.get_rect())
        pygame.draw.rect(overlay, (200, 200, 220, 255), overlay.get_rect(), 2)

        self._compute_cell_size(panel)

        # Grid dots.
        for gx in range(GRID_X_MIN, GRID_X_MAX + 1):
            for gy in range(GRID_Y_MIN, GRID_Y_MAX + 1):
                px, py = self._grid_to_screen(gx, gy, panel)
                col = (50, 60, 80) if gy != 0 else (80, 80, 60)
                pygame.draw.circle(overlay, col, (px, py), 2)

        # Baseline highlight.
        p0 = self._grid_to_screen(GRID_X_MIN, 0, panel)
        p10 = self._grid_to_screen(GRID_X_MAX, 0, panel)
        pygame.draw.line(overlay, BASELINE_COL, p0, p10, 3)

        # Edges.
        branch_set = set(self.state.branch_edge_indices)
        for i, (a, b) in enumerate(self.state.edges):
            if a >= len(self.state.vertices) or b >= len(self.state.vertices):
                continue
            va = self.state.vertices[a]
            vb = self.state.vertices[b]
            pa = self._grid_to_screen(va[0], va[1], panel)
            pb = self._grid_to_screen(vb[0], vb[1], panel)
            if i in branch_set:
                # Check if this is a mirror edge (tier 1: second branch edge for same origin).
                col = GREEN
                width = max(2, self.cell_size // 4)
                # In tier 1, the second branch_edge_index is the mirror.
                if self.state.tier == 1 and len(self.state.branch_edge_indices) >= 2:
                    if i == self.state.branch_edge_indices[-1]:
                        col = DIM_GREEN
                pygame.draw.line(overlay, col, pa, pb, width)
            else:
                # Baseline edge.
                pygame.draw.line(overlay, BASELINE_COL, pa, pb, 2)

        # Vertices.
        for idx, (vx, vy) in enumerate(self.state.vertices):
            px, py = self._grid_to_screen(vx, vy, panel)
            is_baseline = abs(vy) < 0.01
            radius = max(3, self.cell_size // 4)
            col = YELLOW if is_baseline else GREEN
            pygame.draw.circle(overlay, col, (px, py), radius)
            if idx == self.state.pending_origin_idx:
                pygame.draw.circle(overlay, PENDING_COL, (px, py), radius + 4, 2)

        # Root / terminus markers.
        root_px, root_py = self._grid_to_screen(0, 0, panel)
        term_px, term_py = self._grid_to_screen(10, 0, panel)
        pygame.draw.circle(overlay, YELLOW, (root_px, root_py), max(5, self.cell_size // 2), 2)
        pygame.draw.circle(overlay, YELLOW, (term_px, term_py), max(5, self.cell_size // 2), 2)

        # Preview panel (right third of the screen).
        preview_x = panel.left + int(panel.width * 0.68)
        preview_y = panel.top + 20
        preview_w = int(panel.width * 0.28)
        preview_h = int(panel.height * 0.4)
        preview_rect = pygame.Rect(preview_x, preview_y, preview_w, preview_h)
        pygame.draw.rect(overlay, (20, 20, 30, 200), preview_rect)
        pygame.draw.rect(overlay, (100, 100, 120), preview_rect, 1)

        preview_segs = self._build_preview_segments()
        if preview_segs:
            # Find bounds.
            all_x = [p[0] for s in preview_segs for p in s]
            all_y = [p[1] for s in preview_segs for p in s]
            min_x, max_x = min(all_x), max(all_x)
            min_y, max_y = min(all_y), max(all_y)
            span_x = max(0.01, max_x - min_x)
            span_y = max(0.01, max_y - min_y)
            scale = min((preview_w - 20) / span_x, (preview_h - 20) / span_y)
            cx = preview_rect.centerx
            cy = preview_rect.centery
            mid_x = (min_x + max_x) / 2
            mid_y = (min_y + max_y) / 2
            for sa, sb in preview_segs:
                px1 = int(cx + (sa[0] - mid_x) * scale)
                py1 = int(cy + (sa[1] - mid_y) * scale)
                px2 = int(cx + (sb[0] - mid_x) * scale)
                py2 = int(cy + (sb[1] - mid_y) * scale)
                pygame.draw.line(overlay, GREEN, (px1, py1), (px2, py2), 1)

        if self._small_font:
            pt = self._small_font.render("Preview (1 iteration)", True, WHITE)
            overlay.blit(pt, (preview_rect.left + 4, preview_rect.bottom + 4))

        # Instructions.
        if self._small_font:
            tier_name = "Tier 1 (Mirrored)" if self.state.tier == 1 else f"Tier 2 ({self._branch_count()}/{self.state.max_branch_edges} edges)"
            lines = [
                f"Branch Editor — {tier_name}",
                "",
                "1) Click a point on the baseline to set branch origin",
                "2) Click off the baseline to place the branch tip",
            ]
            if self.state.tier == 1:
                lines.append("   (branch is auto-mirrored across the baseline)")
            else:
                lines.append(f"   Repeat up to {self.state.max_branch_edges} times")
            lines += [
                "",
                "Ctrl+Z: undo  |  Backspace: remove last branch",
                "Enter: accept  |  Esc: cancel",
            ]
            if self.status_msg:
                lines.append("")
                lines.append(self.status_msg)

            y = panel.height - 20 * len(lines) - 10
            for ln in lines:
                col = RED if ln == self.status_msg and self.status_msg else WHITE
                txt = self._small_font.render(ln, True, col)
                overlay.blit(txt, (16, y))
                y += txt.get_height() + 2

        surface.blit(overlay, panel.topleft)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _push_undo(self) -> None:
        self.undo_stack.append((
            list(self.state.vertices),
            list(self.state.edges),
            list(self.state.branch_edge_indices),
            self.state.pending_origin_idx,
        ))

    def _pop_undo(self) -> None:
        if not self.undo_stack:
            return
        verts, edges, branch_idxs, pending = self.undo_stack.pop()
        self.state.vertices = verts
        self.state.edges = edges
        self.state.branch_edge_indices = branch_idxs
        self.state.pending_origin_idx = pending
        self.status_msg = ""

    def run(self, manager) -> None:  # type: ignore[override]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()
        running = True

        if not hasattr(manager, "branch_edit_result"):
            manager.branch_edit_result = None

        if self._font is None:
            base_size = max(16, renderer.base_tile)
            self._font = pygame.font.SysFont("consolas", base_size)
            self._small_font = pygame.font.SysFont("consolas", max(12, int(base_size * 0.7)))

        if self.window_rect is None:
            self.panel_rect = pygame.Rect(0, 0, renderer.width, renderer.height)
        else:
            self.panel_rect = self.window_rect.copy()

        def accept() -> None:
            if self._branch_count() == 0:
                self.status_msg = "Place at least one branch before accepting."
                return
            manager.branch_edit_result = self._build_output()
            manager.pop_scene()

        def cancel() -> None:
            manager.branch_edit_result = None
            manager.pop_scene()

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return
                if hasattr(manager, "renderer") and hasattr(manager.renderer, "_to_surface"):
                    to_surface = manager.renderer._to_surface
                else:
                    to_surface = None

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        cancel()
                        running = False
                        break
                    if event.key == pygame.K_F11:
                        renderer.toggle_fullscreen()
                        if self.window_rect is None:
                            self.panel_rect = pygame.Rect(0, 0, renderer.width, renderer.height)
                        continue
                    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        accept()
                        if getattr(manager, "branch_edit_result", None) is not None:
                            running = False
                        break
                    if event.key == pygame.K_z and (event.mod & pygame.KMOD_CTRL):
                        self._pop_undo()
                    if event.key == pygame.K_BACKSPACE:
                        self._push_undo()
                        self._remove_last_branch()
                        self.status_msg = ""

                if event.type == pygame.MOUSEBUTTONDOWN:
                    if to_surface:
                        mx, my = to_surface(event.pos)
                    else:
                        mx, my = event.pos
                    if event.button == 1:
                        self._push_undo()
                        self.status_msg = ""
                        self._handle_click(mx, my)
                    elif event.button == 3:
                        # Right-click: remove last branch.
                        self._push_undo()
                        self._remove_last_branch()
                        self.status_msg = ""

            if self.window_rect is None:
                self.panel_rect = pygame.Rect(0, 0, renderer.width, renderer.height)

            surface.fill(renderer.bg)
            self._draw(surface)
            renderer.present()
            clock.tick(60)
