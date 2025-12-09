from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import pygame

from .base import Scene


Color = Tuple[int, int, int]
Vec2 = Tuple[float, float]


YELLOW = (240, 210, 80)
PURPLE = (150, 90, 190)
WHITE = (230, 230, 240)
RED = (240, 120, 120)
CYAN = (120, 200, 240)


@dataclass
class FractalEditorState:
    grid_x_min: int = 0
    grid_x_max: int = 10
    grid_y_min: int = -5
    grid_y_max: int = 5
    grid_kind: str = "rect"  # "rect" | "hex"

    vertices: List[Vec2] | None = None
    edges: List[Tuple[int, int]] | None = None

    # Soft constraints: enforced on accept() / Enter, not during drawing.
    # Defaults match old behavior (no real limits).
    min_vertices: int = 1
    max_vertices: Optional[int] = None
    min_edges: int = 0
    max_edges: Optional[int] = None

    def __post_init__(self) -> None:
        if self.vertices is None:
            self.vertices = []
        if self.edges is None:
            self.edges = []


class FractalEditorScene(Scene):
    """
    Freeform fractal editor:
    - Left click empty (Draw mode): add vertex (snap to nearest grid intersection) and auto-edge from current root
    - Left click vertex (Draw mode): select as current root anchor
    - Left click vertex (Edge mode): first click selects start, second click selects end and adds edge start->end
    - Left click near edge: select edge (for flip/delete)
    - Right click vertex: delete vertex and reattach incident edges where possible (A-B-C -> A-C)
    - Right click edge: delete edge
    - F: flip selected edge direction
    - Del: delete selected vertex/edge
    - Enter: accept; Esc: cancel

    Grid can be larger than the "baseline" segment from (0,0) to (10,0).
    That baseline is still used to normalize the custom graph when building
    the generator, so you can draw vertices beyond it (e.g. -5..15) and
    get patterns that extend beyond each original segment.
    """

    def __init__(
        self,
        state: Optional[FractalEditorState] = None,
        window_rect: Optional[pygame.Rect] = None,
    ) -> None:
        self.state = state or FractalEditorState()
        # Ensure a starting root at (0,0) so the generated pattern aligns with expected anchors
        if (0, 0) not in self.state.vertices:
            self.state.vertices.insert(0, (0, 0))
        self.window_rect = window_rect
        self.panel_rect: Optional[pygame.Rect] = None
        self.selected_vertex: Optional[int] = None
        self.selected_edge: Optional[int] = None
        self.current_root: Optional[int] = None  # None uses implicit root at (0,0)
        self.mode: str = "draw"  # draw | delete | flip | edge
        self.undo_stack: List[Tuple[List[Vec2], List[Tuple[int, int]], Optional[int], str]] = []
        self._background: Optional[pygame.Surface] = None
        self._font: Optional[pygame.font.Font] = None
        self._small_font: Optional[pygame.font.Font] = None
        self.margin = 40
        self.cell_size = 0  # computed per render
        self._hex_min_x: float = 0.0
        self._hex_min_y: float = 0.0

        # Status line (e.g. for constraint failures on accept)
        self.status_msg: str = ""

    # ------------------------------------------------------------
    # Utility helpers

    def _lerp(self, a: Color, b: Color, t: float) -> Color:
        t = max(0.0, min(1.0, t))
        return (
            int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t),
        )

    def _color_for_x(self, x: float) -> Color:
        denom = max(1e-6, (self.state.grid_x_max - self.state.grid_x_min))
        t = (x - self.state.grid_x_min) / denom
        return self._lerp(YELLOW, PURPLE, t)

    def _grid_to_screen(self, gx: float, gy: float, panel: pygame.Rect) -> Tuple[int, int]:
        if self.cell_size <= 0:
            self._compute_cell_size(panel)
        if self.state.grid_kind == "hex":
            size = self.cell_size
            root_x = panel.left + self.margin
            root_y = panel.top + self.margin
            sqrt3 = math.sqrt(3)
            px = (sqrt3 * gx + (sqrt3 / 2.0) * gy - self._hex_min_x) * size
            py = ((1.5) * gy - self._hex_min_y) * size
            return int(root_x + px), int(root_y + py)
        else:
            sx = panel.left + self.margin + int((gx - self.state.grid_x_min) * self.cell_size)
            # invert y for screen
            sy = panel.top + self.margin + int((self.state.grid_y_max - gy) * self.cell_size)
            return sx, sy

    def _screen_to_grid(self, sx: int, sy: int, panel: pygame.Rect) -> Vec2:
        if self.cell_size <= 0:
            self._compute_cell_size(panel)
        if self.state.grid_kind == "hex":
            size = float(self.cell_size)
            root_x = panel.left + self.margin
            root_y = panel.top + self.margin
            sqrt3 = math.sqrt(3)
            x = (sx - root_x) / size + self._hex_min_x
            y = (sy - root_y) / size + self._hex_min_y
            r = y / 1.5
            q = (x - (sqrt3 / 2.0) * r) / sqrt3
            return (q, r)
        else:
            gx = self.state.grid_x_min + (sx - (panel.left + self.margin)) / float(self.cell_size)
            gy = self.state.grid_y_max - (sy - (panel.top + self.margin)) / float(self.cell_size)
            return gx, gy

    def _nearest_grid_point(self, gx: float, gy: float) -> Vec2:
        if self.state.grid_kind == "hex":
            return self._nearest_hex_point(gx, gy)
        # rectangular grid snapping
        return (round(gx), round(gy))

    def _compute_cell_size(self, panel: pygame.Rect) -> None:
        if self.state.grid_kind == "hex":
            qmin, qmax = self.state.grid_x_min, self.state.grid_x_max
            rmin, rmax = self.state.grid_y_min, self.state.grid_y_max
            sqrt3 = math.sqrt(3)
            corners = [(qmin, rmin), (qmin, rmax), (qmax, rmin), (qmax, rmax)]
            xs = [sqrt3 * q + (sqrt3 / 2.0) * r for q, r in corners]
            ys = [1.5 * r for _, r in corners]
            min_x = min(xs) - sqrt3
            max_x = max(xs) + sqrt3
            min_y = min(ys) - 2.0
            max_y = max(ys) + 2.0
            span_x = max_x - min_x
            span_y = max_y - min_y
            size_by_w = (panel.width - 2 * self.margin) / max(1e-6, span_x)
            size_by_h = (panel.height - 2 * self.margin) / max(1e-6, span_y)
            self.cell_size = int(max(8, min(size_by_w, size_by_h)))
            self._hex_min_x = min_x
            self._hex_min_y = min_y
        else:
            w = self.state.grid_x_max - self.state.grid_x_min
            h = self.state.grid_y_max - self.state.grid_y_min
            self.cell_size = min(
                max(8, (panel.width - 2 * self.margin) // max(1, w)),
                max(8, (panel.height - 2 * self.margin) // max(1, h)),
            )

    def _vertex_at_screen(self, sx: int, sy: int, panel: pygame.Rect) -> Optional[int]:
        hit_radius = max(6, self.cell_size // 2)
        for idx, (vx, vy) in enumerate(self.state.vertices):
            px, py = self._grid_to_screen(vx, vy, panel)
            if (sx - px) ** 2 + (sy - py) ** 2 <= hit_radius ** 2:
                return idx
        return None

    def _edge_at_screen(self, sx: int, sy: int, panel: pygame.Rect) -> Optional[int]:
        # simple distance to segment test
        thresh = max(6, self.cell_size // 2)
        for i, (a_idx, b_idx) in enumerate(self.state.edges):
            ax, ay = self._edge_point(a_idx)
            bx, by = self._edge_point(b_idx)
            pax, pay = self._grid_to_screen(ax, ay, panel)
            pbx, pby = self._grid_to_screen(bx, by, panel)
            if self._point_seg_dist(sx, sy, pax, pay, pbx, pby) <= thresh:
                return i
        return None

    # --- hex helpers ---------------------------------------------------

    def _nearest_hex_point(self, q: float, r: float) -> Vec2:
        """Round axial coordinates to the nearest hex center."""
        x = q
        z = r
        y = -x - z

        rx, ry, rz = round(x), round(y), round(z)

        dx = abs(rx - x)
        dy = abs(ry - y)
        dz = abs(rz - z)

        if dx > dy and dx > dz:
            rx = -ry - rz
        elif dy > dz:
            ry = -rx - rz
        else:
            rz = -rx - ry

        return (rx, rz)

    def _hex_corners(self, q: float, r: float, panel: pygame.Rect) -> List[Tuple[int, int]]:
        """Return pixel coords for the corners of a hex at axial q,r."""
        cx, cy = self._grid_to_screen(q, r, panel)
        size = self.cell_size
        sqrt3 = math.sqrt(3)
        corners = []
        for i in range(6):
            angle = math.radians(30 + 60 * i)  # pointy-top orientation
            x = cx + size * math.cos(angle)
            y = cy + size * math.sin(angle)
            corners.append((int(x), int(y)))
        return corners

    def _point_seg_dist(self, px, py, x1, y1, x2, y2) -> float:
        # distance from point to segment
        dx, dy = x2 - x1, y2 - y1
        if dx == dy == 0:
            return math.hypot(px - x1, py - y1)
        t = ((px - x1) * dx + (py - y1) * dy) / float(dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
        proj_x = x1 + t * dx
        proj_y = y1 + t * dy
        return math.hypot(px - proj_x, py - proj_y)

    # ------------------------------------------------------------
    # Constraint checking

    def _validate_constraints(self) -> Tuple[bool, str]:
        """Check vertex/edge counts against the state's soft constraints."""
        v_count = len(self.state.vertices)
        e_count = len(self.state.edges)

        if v_count < self.state.min_vertices:
            return False, f"Need at least {self.state.min_vertices} vertices (have {v_count})."
        if self.state.max_vertices is not None and v_count > self.state.max_vertices:
            return False, f"Can only save with ≤ {self.state.max_vertices} vertices (have {v_count})."

        if e_count < self.state.min_edges:
            return False, f"Need at least {self.state.min_edges} edges (have {e_count})."
        if self.state.max_edges is not None and e_count > self.state.max_edges:
            return False, f"Can only save with ≤ {self.state.max_edges} edges (have {e_count})."

        return True, ""

    # ------------------------------------------------------------

    def run(self, manager) -> None:  # type: ignore[override]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()
        running = True

        # ensure attribute exists for downstream checks
        if not hasattr(manager, "fractal_edit_result"):
            manager.fractal_edit_result = None

        if self._font is None:
            base_size = max(16, renderer.base_tile)
            self._font = pygame.font.SysFont("consolas", base_size)
            self._small_font = pygame.font.SysFont("consolas", max(12, int(base_size * 0.7)))

        # panel rect (full screen if none provided)
        if self.window_rect is None:
            self.panel_rect = pygame.Rect(0, 0, renderer.width, renderer.height)
        else:
            self.panel_rect = self.window_rect.copy()

        def push_undo() -> None:
            self.undo_stack.append(
                (list(self.state.vertices), list(self.state.edges), self.current_root, self.mode)
            )

        def accept() -> None:
            # Enforce soft constraints on save
            ok, msg = self._validate_constraints()
            if not ok:
                # Stay in the editor, show a message, and do not pop the scene
                self.status_msg = msg
                return

            orig_verts = list(self.state.vertices)
            orig_edges = list(self.state.edges)

            # Build a reordered vertex list ensuring anchors at (0,0) and (10,0)
            # while remapping edges to the new indices.
            def ensure_with_remap() -> Tuple[List[Vec2], List[Tuple[int, int]]]:
                verts = list(orig_verts)
                edges = list(orig_edges)

                # Map old index -> new index as we reorder
                mapping = {i: i for i in range(len(verts))}

                def move_vertex_to(pos: Vec2, target_idx: int) -> None:
                    nonlocal verts, mapping
                    if pos in verts:
                        old_idx = verts.index(pos)
                        if old_idx == target_idx:
                            return
                        # Remove and reinsert
                        verts.pop(old_idx)
                        verts.insert(target_idx, pos)
                        # Rebuild mapping
                        new_map = {}
                        for new_i, v in enumerate(verts):
                            # use first matching old index for mapping updates
                            # (if duplicates, mapping may be ambiguous; we assume unique here)
                            try:
                                old_i = orig_verts.index(v)
                            except ValueError:
                                old_i = None
                            if old_i is not None:
                                new_map[old_i] = new_i
                        mapping = new_map
                    else:
                        # Insert new vertex at target_idx and shift mapping
                        verts.insert(target_idx, pos)
                        mapping = {old: new + (1 if new >= target_idx else 0) for old, new in mapping.items()}

                # Ensure root at index 0: baseline start at (0,0)
                move_vertex_to((0, 0), 0)
                # Ensure terminus at end: baseline end at (10,0)
                move_vertex_to((10, 0), len(verts))

                # Remap edges using mapping; drop edges referencing missing vertices
                remapped: List[Tuple[int, int]] = []
                for a, b in edges:
                    if a in mapping and b in mapping:
                        remapped.append((mapping[a], mapping[b]))
                return verts, remapped

            pts, edges = ensure_with_remap()
            # If no edge connects the root, add a default root->first edge
            if pts and len(pts) > 1:
                has_root_edge = any(a == 0 or b == 0 for (a, b) in edges)
                if not has_root_edge:
                    edges.insert(0, (0, 1))
            manager.fractal_edit_result = {
                "vertices": pts,
                "edges": edges,
                "bounds": (
                    self.state.grid_x_min,
                    self.state.grid_x_max,
                    self.state.grid_y_min,
                    self.state.grid_y_max,
                ),
                "grid_kind": self.state.grid_kind,
            }
            manager.pop_scene()

        def cancel() -> None:
            manager.fractal_edit_result = None
            manager.pop_scene()

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return
                # map mouse coords through renderer scaling if present
                if hasattr(manager, "renderer") and hasattr(manager.renderer, "_to_surface"):
                    to_surface = manager.renderer._to_surface  # type: ignore[attr-defined]
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
                    if event.key == pygame.K_RETURN or event.key == pygame.K_KP_ENTER:
                        accept()
                        # Only exit loop if accept() actually popped the scene;
                        # if constraints fail, running stays True.
                        if getattr(manager, "fractal_edit_result", None) is not None:
                            running = False
                        break
                    if event.key == pygame.K_1:
                        self.mode = "draw"
                        self.status_msg = ""
                    if event.key == pygame.K_2:
                        self.mode = "delete"
                        self.status_msg = ""
                    if event.key == pygame.K_3:
                        self.mode = "flip"
                        self.status_msg = ""
                    if event.key == pygame.K_4:
                        self.mode = "edge"
                        self.status_msg = ""
                    if event.key == pygame.K_z and (event.mod & pygame.KMOD_CTRL):
                        if self.undo_stack:
                            verts, edges, root, mode = self.undo_stack.pop()
                            self.state.vertices = verts
                            self.state.edges = edges
                            self.current_root = root
                            self.mode = mode
                            self.status_msg = ""
                    if event.key == pygame.K_f and self.selected_edge is not None:
                        push_undo()
                        a, b = self.state.edges[self.selected_edge]
                        self.state.edges[self.selected_edge] = (b, a)
                    if event.key == pygame.K_DELETE:
                        if self.selected_edge is not None:
                            push_undo()
                            self.state.edges.pop(self.selected_edge)
                            self.selected_edge = None
                        elif self.selected_vertex is not None:
                            push_undo()
                            self._delete_vertex(self.selected_vertex)
                if event.type == pygame.MOUSEBUTTONDOWN:
                    if to_surface:
                        mx, my = to_surface(event.pos)
                    else:
                        mx, my = event.pos
                    if event.button == 1:
                        push_undo()
                        self.status_msg = ""
                        self._handle_left_click(mx, my)
                    elif event.button == 3:
                        push_undo()
                        self.status_msg = ""
                        self._handle_right_click(mx, my)

            # Update panel if full-screen and size changed
            if self.window_rect is None:
                self.panel_rect = pygame.Rect(0, 0, renderer.width, renderer.height)

            # Draw
            surface.fill(renderer.bg)
            self._draw(surface)
            renderer.present()
            clock.tick(60)

    # ------------------------------------------------------------

    def _handle_left_click(self, mx: int, my: int) -> None:
        panel = self.panel_rect
        assert panel is not None
        # mode buttons hit-test first
        for m, _label, rect in self._mode_button_rects(panel):
            if rect.collidepoint(mx, my):
                self.mode = m
                return

        v_hit = self._vertex_at_screen(mx, my, panel)
        e_hit = self._edge_at_screen(mx, my, panel)

        if self.mode == "draw":
            if v_hit is not None:
                # select existing vertex as current root anchor
                self.current_root = v_hit
                self.selected_vertex = v_hit
                self.selected_edge = None
                return
            if e_hit is not None:
                self.selected_edge = e_hit
                self.selected_vertex = None
                return
            gx, gy = self._screen_to_grid(mx, my, panel)
            gx, gy = self._nearest_grid_point(gx, gy)
            if not (
                self.state.grid_x_min <= gx <= self.state.grid_x_max
                and self.state.grid_y_min <= gy <= self.state.grid_y_max
            ):
                return

            # NOTE: we no longer block drawing when over max_vertices;
            # constraints are enforced only on accept/save.

            # avoid duplicate vertex positions
            if (gx, gy) in self.state.vertices:
                idx = self.state.vertices.index((gx, gy))
                self.current_root = idx
                self.selected_vertex = idx
                self.selected_edge = None
                return
            self.state.vertices.append((gx, gy))
            new_idx = len(self.state.vertices) - 1
            root_idx = self.current_root if self.current_root is not None else -1
            if self._add_edge(root_idx, new_idx):
                self.selected_edge = len(self.state.edges) - 1
            self.current_root = new_idx
            self.selected_vertex = new_idx
            self.selected_edge = None

        elif self.mode == "delete":
            if v_hit is not None:
                self._delete_vertex(v_hit)
                return
            if e_hit is not None:
                self._delete_edge(e_hit)
                return

        elif self.mode == "flip":
            if e_hit is not None:
                a, b = self.state.edges[e_hit]
                self.state.edges[e_hit] = (b, a)
                self.selected_edge = e_hit
                self.selected_vertex = None
                return

        elif self.mode == "edge":
            # Add-edge mode: click vertex A, then vertex B to add A->B
            self._handle_add_edge_click(v_hit)

    def _handle_add_edge_click(self, v_hit: Optional[int]) -> None:
        """Handle clicks in 'edge' mode to add edges between existing vertices."""
        if v_hit is None:
            # Click not on a vertex: ignore
            return

        if self.selected_vertex is None:
            # First vertex of the edge
            self.selected_vertex = v_hit
            self.selected_edge = None
        else:
            # Second vertex: attempt to create edge selected_vertex -> v_hit
            start = self.selected_vertex
            end = v_hit
            if start != end:
                if self._add_edge(start, end):
                    self.selected_edge = len(self.state.edges) - 1
            # For ease of chaining, treat the last clicked vertex as the new start
            self.selected_vertex = end

    def _handle_right_click(self, mx: int, my: int) -> None:
        panel = self.panel_rect
        assert panel is not None
        v_hit = self._vertex_at_screen(mx, my, panel)
        if v_hit is not None:
            self._delete_vertex(v_hit)
            return
        e_hit = self._edge_at_screen(mx, my, panel)
        if e_hit is not None:
            self._delete_edge(e_hit)
            return

    def _delete_vertex(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.state.vertices):
            return

        # 1) Gather incoming and outgoing neighbors (using original indices).
        # Note: src can be -1 for the implicit root at (0,0); we keep that.
        incoming = [a for (a, b) in self.state.edges if b == idx]
        outgoing = [b for (a, b) in self.state.edges if a == idx]

        # 2) Build bridge edges from each incoming to each outgoing (A-B-C -> A-C).
        bridge_edges: List[Tuple[int, int]] = []
        for a in incoming:
            for b in outgoing:
                # Avoid self loops and trivial identities
                if a == idx or b == idx or a == b:
                    continue
                bridge_edges.append((a, b))

        # 3) Keep all existing edges that do NOT touch this vertex.
        base_edges: List[Tuple[int, int]] = [
            (a, b) for (a, b) in self.state.edges if a != idx and b != idx
        ]

        # 4) Add the bridge edges, avoiding duplicates.
        existing = set(base_edges)
        for e in bridge_edges:
            if e not in existing:
                base_edges.append(e)
                existing.add(e)

        # 5) After removing the vertex, indices > idx shift down by 1.
        adjusted: List[Tuple[int, int]] = []
        for a, b in base_edges:
            na = a
            nb = b
            # Only adjust non-negative indices; -1 is the implicit root.
            if isinstance(na, int) and na >= 0 and na > idx:
                na = na - 1
            if isinstance(nb, int) and nb >= 0 and nb > idx:
                nb = nb - 1
            adjusted.append((na, nb))

        self.state.edges = adjusted
        self.state.vertices.pop(idx)

        # Update current_root (draw origin) if needed.
        if self.current_root is not None:
            if self.current_root == idx:
                self.current_root = None
            elif self.current_root > idx:
                self.current_root -= 1

        # Update selected_vertex if needed.
        if self.selected_vertex is not None:
            if self.selected_vertex == idx:
                self.selected_vertex = None
            elif self.selected_vertex > idx:
                self.selected_vertex -= 1

        # Edge indices and ordering have changed, so clear selected_edge.
        self.selected_edge = None

    def _delete_edge(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.state.edges):
            return
        self.state.edges.pop(idx)
        if self.selected_edge == idx:
            self.selected_edge = None

    def _add_edge(self, a: int, b: int) -> bool:
        if a == b:
            return False
        if b < 0 or b >= len(self.state.vertices):
            return False
        if (a, b) in self.state.edges:
            return False
        self.state.edges.append((a, b))
        return True

    # ------------------------------------------------------------

    def _draw(self, surface: pygame.Surface) -> None:
        assert self.panel_rect is not None
        panel = self.panel_rect

        # panel background
        overlay = pygame.Surface((panel.width, panel.height), pygame.SRCALPHA)
        pygame.draw.rect(overlay, (10, 10, 20, 230), overlay.get_rect())
        pygame.draw.rect(overlay, (200, 200, 220, 255), overlay.get_rect(), 2)

        # grid
        self._compute_cell_size(panel)
        if self.state.grid_kind == "hex":
            for q in range(self.state.grid_x_min, self.state.grid_x_max + 1):
                for r in range(self.state.grid_y_min, self.state.grid_y_max + 1):
                    corners = self._hex_corners(q, r, panel)
                    pygame.draw.polygon(overlay, (40, 50, 70), corners, 1)
        else:
            w = self.state.grid_x_max - self.state.grid_x_min
            h = self.state.grid_y_max - self.state.grid_y_min
            for gx in range(self.state.grid_x_min, self.state.grid_x_max + 1):
                x, _ = self._grid_to_screen(gx, self.state.grid_y_min, panel)
                _, y_top = self._grid_to_screen(gx, self.state.grid_y_max, panel)
                pygame.draw.line(overlay, (40, 50, 70), (x, y_top), (x, y_top + h * self.cell_size))
            for gy in range(self.state.grid_y_min, self.state.grid_y_max + 1):
                x_left, y = self._grid_to_screen(self.state.grid_x_min, gy, panel)
                x_right, _ = self._grid_to_screen(self.state.grid_x_max, gy, panel)
                pygame.draw.line(overlay, (40, 50, 70), (x_left, y), (x_right, y))

        # edges
        for i, (a_idx, b_idx) in enumerate(self.state.edges):
            ax, ay = self._edge_point(a_idx)
            bx, by = self._edge_point(b_idx)
            pa = self._grid_to_screen(ax, ay, panel)
            pb = self._grid_to_screen(bx, by, panel)
            col_a = YELLOW  # start of edge
            col_b = PURPLE  # end of edge
            # draw gradient with thicker segments
            steps = max(6, int(math.hypot(pb[0] - pa[0], pb[1] - pa[1]) / max(1, self.cell_size // 2)))
            prev = pa
            for s in range(1, steps + 1):
                t = s / steps
                nxt = (int(pa[0] + (pb[0] - pa[0]) * t), int(pa[1] + (pb[1] - pa[1]) * t))
                col = self._lerp(col_a, col_b, t)
                pygame.draw.line(overlay, col, prev, nxt, max(2, self.cell_size // 3))
                prev = nxt
            if self.selected_edge == i:
                pygame.draw.circle(overlay, RED, ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2), 6, 1)

        # vertices
        for idx, (vx, vy) in enumerate(self.state.vertices):
            px, py = self._grid_to_screen(vx, vy, panel)
            base_col = self._color_for_x(vx)
            radius = max(3, self.cell_size // 4)
            pygame.draw.circle(overlay, base_col, (px, py), radius)
            outline = CYAN if self.selected_vertex == idx else WHITE
            pygame.draw.circle(overlay, outline, (px, py), radius + 2, 1)

        # root/terminus markers: always at baseline (0,0) -> (10,0),
        # even if the grid extends beyond that.
        root_px, root_py = self._grid_to_screen(0, 0, panel)
        term_px, term_py = self._grid_to_screen(10, 0, panel)
        pygame.draw.circle(overlay, YELLOW, (root_px, root_py), max(5, self.cell_size // 2), 2)
        pygame.draw.circle(overlay, PURPLE, (term_px, term_py), max(5, self.cell_size // 2), 2)

        # highlight current root if set
        if self.current_root is not None and 0 <= self.current_root < len(self.state.vertices):
            rx, ry = self.state.vertices[self.current_root]
            rpx, rpy = self._grid_to_screen(rx, ry, panel)
            pygame.draw.circle(overlay, (255, 255, 255), (rpx, rpy), max(7, self.cell_size // 2), 2)

        # instructions + counts + status
        if self._small_font:
            lines: List[str] = [
                f"Mode: {self.mode.upper()} (1 draw, 2 delete, 3 flip, 4 edge)",
                "Draw: click grid to add vertex & auto-edge from current root",
                "Edge: click vertex A then vertex B to add edge A->B",
                "Delete: click vertex/edge to remove (incoming edges reattach)",
                "Flip: click edge to swap direction | Ctrl+Z: undo",
                "Enter: accept | Esc: cancel",
            ]

            # Counts + constraint targets
            v = len(self.state.vertices)
            e = len(self.state.edges)
            if self.state.max_vertices is not None:
                v_lim = f"{self.state.min_vertices}–{self.state.max_vertices}"
            else:
                v_lim = f"≥{self.state.min_vertices}"

            if self.state.max_edges is not None:
                e_lim = f"{self.state.min_edges}–{self.state.max_edges}"
            else:
                e_lim = f"≥{self.state.min_edges}"

            lines.append(f"Vertices: {v} (target {v_lim})  |  Edges: {e} (target {e_lim})")

            if self.status_msg:
                lines.append(self.status_msg)

            y = panel.height - 20 * len(lines) - 10
            for ln in lines:
                color = WHITE
                if ln == self.status_msg:
                    color = RED
                txt = self._small_font.render(ln, True, color)
                overlay.blit(txt, (16, y))
                y += txt.get_height() + 2

        # mode buttons on the right
        if self._font:
            btn_w, btn_h = 140, 32
            right_x = panel.right - btn_w - 20
            top_y = 20
            for m, label, rect in self._mode_button_rects(panel):
                color = (70, 90, 120) if self.mode != m else (120, 160, 210)
                pygame.draw.rect(overlay, color, rect, border_radius=6)
                pygame.draw.rect(overlay, (200, 220, 240), rect, 2, border_radius=6)
                txt = self._font.render(label, True, WHITE)
                overlay.blit(txt, (rect.left + 8, rect.top + (btn_h - txt.get_height()) // 2))

        surface.blit(overlay, panel.topleft)

    # ------------------------------------------------------------
    # Helpers

    def _edge_point(self, idx: int) -> Vec2:
        if idx == -1:
            return (0.0, 0.0)
        return self.state.vertices[idx]

    def _mode_button_rects(self, panel: pygame.Rect):
        btn_w, btn_h = 140, 32
        right_x = panel.right - btn_w - 20
        top_y = 20
        buttons = [
            ("draw", "Draw (1)"),
            ("delete", "Delete (2)"),
            ("flip", "Flip (3)"),
            ("edge", "Add edge (4)"),
        ]
        rects = []
        y = top_y
        for m, label in buttons:
            rects.append((m, label, pygame.Rect(right_x, y, btn_w, btn_h)))
            y += btn_h + 8
        return rects
