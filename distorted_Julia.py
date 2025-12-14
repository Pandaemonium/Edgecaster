# mandelbrot_julia_orbit_viewer.py
# 2x2 panels:
#   Top:   Real/Imag distortion fields over the z-plane
#   Bottom: Mandelbrot (c-plane) + Distorted Julia (z-plane)
#
# Fix: distortion field is solved on a FIXED vp_field (world domain),
#      zooming vp_j only changes sampling, not the underlying field.
#
# Features:
# - Linked crosshairs across all panels on hover
# - Top bar: slider + Distort button to randomize spline fields
#   (scale=0 + Distort => zero distortion)
#
# Requires: pygame, numpy

import sys
from dataclasses import dataclass
import numpy as np
import pygame


# ----------------------------
# Config
# ----------------------------
H_BOTTOM = 800
H_TOP = 240
W_PANEL = 720
PADDING = 8
FPS = 60

TOPBAR_H = 56
UI_H = 180

# Field texture resolution (stable world-map sampling)
FIELD_TEX_W = 512
FIELD_TEX_H = 512

# Randomization defaults
RAND_POINTS = 40


# ----------------------------
# Viewport + palettes
# ----------------------------
@dataclass
class Viewport:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    width: int
    height: int

    def pixel_to_complex(self, px: float, py: float) -> complex:
        x = self.x_min + (px / (self.width - 1)) * (self.x_max - self.x_min)
        y = self.y_max - (py / (self.height - 1)) * (self.y_max - self.y_min)
        return complex(x, y)

    def complex_to_pixel(self, z: complex) -> tuple[int, int]:
        x = (z.real - self.x_min) / (self.x_max - self.x_min) * (self.width - 1)
        y = (self.y_max - z.imag) / (self.y_max - self.y_min) * (self.height - 1)
        return int(round(x)), int(round(y))


def make_palette(n: int) -> np.ndarray:
    t = np.linspace(0, 1, n, endpoint=False)
    r = (0.5 + 0.5 * np.sin(2 * np.pi * (t + 0.00))) ** 0.9
    g = (0.5 + 0.5 * np.sin(2 * np.pi * (t + 0.33))) ** 0.9
    b = (0.5 + 0.5 * np.sin(2 * np.pi * (t + 0.67))) ** 0.9
    pal = np.stack([r, g, b], axis=1)
    return (255.0 * pal).clip(0, 255).astype(np.uint8)


PALETTE = make_palette(1024)


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def colorize_escape(iters: np.ndarray, z_abs2: np.ndarray, max_iter: int) -> np.ndarray:
    h, w = iters.shape
    img = np.zeros((h, w, 3), dtype=np.uint8)

    inside = (iters >= max_iter)
    escaped = ~inside
    img[inside] = (5, 5, 10)

    if np.any(escaped):
        abs_z = np.sqrt(np.maximum(z_abs2[escaped], 1e-12))
        mu = iters[escaped].astype(np.float32) + 1.0 - np.log2(np.log(abs_z))
        mu = np.nan_to_num(mu, nan=0.0, posinf=0.0, neginf=0.0)
        idx = (mu * 20.0).astype(np.int32) % len(PALETTE)
        img[escaped] = PALETTE[idx]

    return img


# ----------------------------
# Field sampling & rendering (stable field domain)
# ----------------------------
def upsample_bilinear(arr: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    """Vectorized bilinear upsample for 2D float arrays."""
    in_h, in_w = arr.shape
    if in_h == out_h and in_w == out_w:
        return arr.astype(np.float32, copy=False)

    x = np.linspace(0, in_w - 1, out_w, dtype=np.float32)
    y = np.linspace(0, in_h - 1, out_h, dtype=np.float32)

    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = np.clip(x0 + 1, 0, in_w - 1)
    y1 = np.clip(y0 + 1, 0, in_h - 1)

    wx = x - x0
    wy = y - y0

    Ia = arr[y0[:, None], x0[None, :]]
    Ib = arr[y0[:, None], x1[None, :]]
    Ic = arr[y1[:, None], x0[None, :]]
    Id = arr[y1[:, None], x1[None, :]]

    wa = (1.0 - wy)[:, None] * (1.0 - wx)[None, :]
    wb = (1.0 - wy)[:, None] * wx[None, :]
    wc = wy[:, None] * (1.0 - wx)[None, :]
    wd = wy[:, None] * wx[None, :]

    return (Ia * wa + Ib * wb + Ic * wc + Id * wd).astype(np.float32, copy=False)


def sample_tex_to_view_bilinear(tex: np.ndarray, vp_view: Viewport, vp_field: Viewport) -> np.ndarray:
    """
    Sample a FIELD texture (defined on vp_field) into an output grid (vp_view).
    Outside vp_field => 0.
    """
    out_h, out_w = vp_view.height, vp_view.width
    h, w = tex.shape

    xs = np.linspace(vp_view.x_min, vp_view.x_max, out_w, dtype=np.float32)
    ys = np.linspace(vp_view.y_max, vp_view.y_min, out_h, dtype=np.float32)
    X, Y = np.meshgrid(xs, ys)

    span_x = float(vp_field.x_max - vp_field.x_min)
    span_y = float(vp_field.y_max - vp_field.y_min)
    if span_x <= 0.0 or span_y <= 0.0:
        return np.zeros((out_h, out_w), dtype=np.float32)

    # Convert complex-plane coords -> texture coords
    tx = (X - vp_field.x_min) / span_x * (w - 1)
    ty = (vp_field.y_max - Y) / span_y * (h - 1)

    # Mask outside
    inside = (tx >= 0) & (tx <= (w - 1)) & (ty >= 0) & (ty <= (h - 1))
    if not np.any(inside):
        return np.zeros((out_h, out_w), dtype=np.float32)

    x0 = np.floor(tx).astype(np.int32)
    y0 = np.floor(ty).astype(np.int32)
    x1 = np.clip(x0 + 1, 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)

    wx = (tx - x0).astype(np.float32)
    wy = (ty - y0).astype(np.float32)

    Ia = tex[y0, x0]
    Ib = tex[y0, x1]
    Ic = tex[y1, x0]
    Id = tex[y1, x1]

    out = (Ia * (1 - wx) * (1 - wy) +
           Ib * (wx) * (1 - wy) +
           Ic * (1 - wx) * (wy) +
           Id * (wx) * (wy)).astype(np.float32)

    out[~inside] = 0.0
    return out


def sample_distortion_nn_tex(z: complex, vp_field: Viewport, tex_re: np.ndarray, tex_im: np.ndarray) -> complex:
    """Nearest-neighbor sample of the field texture in world coords; outside => 0."""
    span_x = vp_field.x_max - vp_field.x_min
    span_y = vp_field.y_max - vp_field.y_min
    if span_x <= 0 or span_y <= 0:
        return 0j

    tx = (z.real - vp_field.x_min) / span_x
    ty = (vp_field.y_max - z.imag) / span_y
    if tx < 0.0 or tx > 1.0 or ty < 0.0 or ty > 1.0:
        return 0j

    h, w = tex_re.shape
    ix = int(tx * (w - 1) + 0.5)
    iy = int(ty * (h - 1) + 0.5)
    return complex(float(tex_re[iy, ix]), float(tex_im[iy, ix]))


def field_to_surface(values: np.ndarray) -> pygame.Surface:
    # values: (h, w), float
    h, w = values.shape
    max_abs = float(np.max(np.abs(values))) if values.size else 0.0
    max_abs = max(max_abs, 1e-6)
    gray = (128.0 + (values / max_abs) * 127.0).clip(0, 255).astype(np.uint8)
    rgb = np.stack([gray, gray, gray], axis=2)
    surf = pygame.Surface((w, h))
    pygame.surfarray.blit_array(surf, np.transpose(rgb, (1, 0, 2)))
    return surf


# ----------------------------
# Fractals
# ----------------------------
def fractal_mandelbrot(vp: Viewport, max_iter: int) -> pygame.Surface:
    xs = np.linspace(vp.x_min, vp.x_max, vp.width, dtype=np.float32)
    ys = np.linspace(vp.y_max, vp.y_min, vp.height, dtype=np.float32)
    X, Y = np.meshgrid(xs, ys)
    C = X + 1j * Y

    Z = np.zeros_like(C, dtype=np.complex64)
    iters = np.zeros(C.shape, dtype=np.uint16)

    mask = np.ones(C.shape, dtype=bool)
    escape_radius2 = 4.0 * 4.0

    for i in range(max_iter):
        Z[mask] = Z[mask] * Z[mask] + C[mask]
        abs2 = (Z.real * Z.real + Z.imag * Z.imag)
        escaped_now = mask & (abs2 > escape_radius2)
        iters[escaped_now] = i
        mask[escaped_now] = False
        if not mask.any():
            break

    iters[mask] = max_iter
    abs2_final = (Z.real * Z.real + Z.imag * Z.imag).astype(np.float32)
    rgb = colorize_escape(iters, abs2_final, max_iter)

    surf = pygame.Surface((vp.width, vp.height))
    pygame.surfarray.blit_array(surf, np.transpose(rgb, (1, 0, 2)))
    return surf


def fractal_julia_distorted(vp: Viewport, c: complex, max_iter: int,
                            z_re_offset: np.ndarray, z_im_offset: np.ndarray) -> pygame.Surface:
    """Julia set where z0 is locally offset by two scalar fields (real/imag)."""
    xs = np.linspace(vp.x_min, vp.x_max, vp.width, dtype=np.float32)
    ys = np.linspace(vp.y_max, vp.y_min, vp.height, dtype=np.float32)
    X, Y = np.meshgrid(xs, ys)

    Z = (X + 1j * Y).astype(np.complex64)
    if z_re_offset.shape == Z.shape:
        Z.real = Z.real + z_re_offset.astype(np.float32, copy=False)
    if z_im_offset.shape == Z.shape:
        Z.imag = Z.imag + z_im_offset.astype(np.float32, copy=False)

    C = np.complex64(c)
    iters = np.zeros(Z.shape, dtype=np.uint16)
    mask = np.ones(Z.shape, dtype=bool)
    escape_radius2 = 4.0 * 4.0

    for i in range(max_iter):
        Z[mask] = Z[mask] * Z[mask] + C
        abs2 = (Z.real * Z.real + Z.imag * Z.imag)
        escaped_now = mask & (abs2 > escape_radius2)
        iters[escaped_now] = i
        mask[escaped_now] = False
        if not mask.any():
            break

    iters[mask] = max_iter
    abs2_final = (Z.real * Z.real + Z.imag * Z.imag).astype(np.float32)
    rgb = colorize_escape(iters, abs2_final, max_iter)

    surf = pygame.Surface((vp.width, vp.height))
    pygame.surfarray.blit_array(surf, np.transpose(rgb, (1, 0, 2)))
    return surf


def compute_orbit_distorted(z0: complex, c: complex, max_steps: int, escape_radius: float,
                            vp_field: Viewport, tex_re: np.ndarray, tex_im: np.ndarray) -> list[complex]:
    """
    Orbit with local-parameter distortion:
        z_{n+1} = z_n^2 + (c + d(z_n))
    d(z) sampled from the stable field texture; outside => 0.
    """
    pts = [z0]
    z = z0
    esc2 = escape_radius * escape_radius
    for _ in range(max_steps - 1):
        d = sample_distortion_nn_tex(z, vp_field, tex_re, tex_im)
        z = z * z + (c + d)
        pts.append(z)
        if (z.real * z.real + z.imag * z.imag) > esc2:
            break
    return pts


# ----------------------------
# SplineField (same as yours)
# ----------------------------
class SplineField:
    """A lightweight 2D 'spline-like' scalar field via harmonic relaxation."""

    def __init__(self, grid_w: int, grid_h: int) -> None:
        self.grid_w = int(grid_w)
        self.grid_h = int(grid_h)
        self.control: dict[tuple[float, float], float] = {}
        self.grid = np.zeros((self.grid_h, self.grid_w), dtype=np.float32)
        self._dirty = True
        self._last_sig: tuple[float, float, float, float, int] | None = None

    def add_delta(self, z: complex, delta: float) -> None:
        key = (round(float(z.real), 6), round(float(z.imag), 6))
        self.control[key] = float(self.control.get(key, 0.0) + float(delta))
        self._dirty = True

    def clear(self) -> None:
        self.control.clear()
        self.grid.fill(0.0)
        self._dirty = True

    def solve(self, vp: Viewport, iters: int) -> np.ndarray:
        iters = int(clamp(iters, 1, 2000))
        sig = (vp.x_min, vp.x_max, vp.y_min, vp.y_max, iters)
        if not self._dirty and self._last_sig == sig:
            return self.grid

        h, w = self.grid_h, self.grid_w
        pinned = np.zeros((h, w), dtype=bool)
        pinned_vals = np.zeros((h, w), dtype=np.float32)

        # Pin boundaries to 0 (stable frame).
        pinned[0, :] = True
        pinned[-1, :] = True
        pinned[:, 0] = True
        pinned[:, -1] = True

        span_x = float(vp.x_max - vp.x_min)
        span_y = float(vp.y_max - vp.y_min)
        if span_x <= 0.0 or span_y <= 0.0:
            self.grid.fill(0.0)
            self._dirty = False
            self._last_sig = sig
            return self.grid

        for (xr, yi), val in self.control.items():
            x = float(xr)
            y = float(yi)
            if x < vp.x_min or x > vp.x_max or y < vp.y_min or y > vp.y_max:
                continue
            tx = (x - vp.x_min) / span_x
            ty = (vp.y_max - y) / span_y
            ix = int(round(tx * (w - 1)))
            iy = int(round(ty * (h - 1)))
            ix = int(clamp(ix, 0, w - 1))
            iy = int(clamp(iy, 0, h - 1))
            pinned[iy, ix] = True
            pinned_vals[iy, ix] += np.float32(val)

        arr = np.zeros((h, w), dtype=np.float32)
        arr[pinned] = pinned_vals[pinned]

        for _ in range(iters):
            new = arr.copy()
            new[1:-1, 1:-1] = 0.25 * (
                arr[1:-1, :-2]
                + arr[1:-1, 2:]
                + arr[:-2, 1:-1]
                + arr[2:, 1:-1]
            )
            new[pinned] = pinned_vals[pinned]
            arr = new

        self.grid = arr
        self._dirty = False
        self._last_sig = sig
        return self.grid


# ----------------------------
# UI widgets
# ----------------------------
@dataclass
class ParamBox:
    label: str
    key: str
    rect: pygame.Rect
    kind: str  # "int" or "float"
    text: str
    focused: bool = False
    select_all: bool = False

    def draw(self, screen: pygame.Surface, font: pygame.font.Font) -> None:
        lab = font.render(self.label, True, (230, 230, 240))
        screen.blit(lab, (self.rect.x, self.rect.y - 20))

        border = (170, 170, 220) if self.focused else (90, 90, 130)
        pygame.draw.rect(screen, (22, 22, 30), self.rect)
        pygame.draw.rect(screen, border, self.rect, 2)

        t = self.text if self.text else ""
        txt = font.render(t, True, (240, 240, 250))
        text_pos = (self.rect.x + 8, self.rect.y + 6)
        if self.focused and self.select_all and t:
            highlight_rect = txt.get_rect(topleft=text_pos)
            pygame.draw.rect(screen, (60, 100, 160), highlight_rect)
        screen.blit(txt, text_pos)

    def handle_keydown(self, event: pygame.event.Event) -> bool:
        if not self.focused:
            return False

        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            return True
        if event.key == pygame.K_BACKSPACE:
            if self.select_all:
                self.text = ""
                self.select_all = False
            else:
                self.text = self.text[:-1]
            return False
        if event.key == pygame.K_ESCAPE:
            self.focused = False
            self.select_all = False
            return False

        ch = event.unicode or ""
        if not ch:
            return False

        allowed = "0123456789"
        if self.kind == "float":
            allowed += ".-eE+"
        else:
            allowed += "-,"

        if ch in allowed:
            if self.select_all:
                self.text = ch
                self.select_all = False
            else:
                self.text += ch
        return False





def try_parse(kind: str, s: str):
    s = s.strip()
    if kind == "int":
        return int(float(s))
    return float(s)


# ----------------------------
# Zoom helpers
# ----------------------------
def zoom_viewport(vp: Viewport, center_px: tuple[int, int], scroll_y: int) -> None:
    if scroll_y == 0:
        return
    span_factor = 0.9 if scroll_y > 0 else 1.0 / 0.9
    cx, cy = center_px
    z_center = vp.pixel_to_complex(cx, cy)

    span_x_old = vp.x_max - vp.x_min
    span_y_old = vp.y_max - vp.y_min
    span_x_new = span_x_old * span_factor
    span_y_new = span_y_old * span_factor

    t_x = cx / (vp.width - 1)
    t_y = cy / (vp.height - 1)

    new_x_min = z_center.real - t_x * span_x_new
    new_x_max = new_x_min + span_x_new
    new_y_max = z_center.imag + t_y * span_y_new
    new_y_min = new_y_max - span_y_new

    vp.x_min, vp.x_max = new_x_min, new_x_max
    vp.y_min, vp.y_max = new_y_min, new_y_max


def quick_zoom_preview(surf: pygame.Surface, factor: float, cursor: tuple[int, int], size: tuple[int, int]) -> pygame.Surface:
    w, h = size
    cx, cy = cursor
    scaled_w = max(1, int(w * factor))
    scaled_h = max(1, int(h * factor))
    scaled = pygame.transform.smoothscale(surf, (scaled_w, scaled_h))
    preview = pygame.Surface((w, h))
    preview.fill((12, 12, 16))

    dx = cx - int(cx * factor)
    dy = cy - int(cy * factor)
    preview.blit(scaled, (dx, dy))
    return preview


# ----------------------------
# Drawing helpers
# ----------------------------
def draw_text(screen: pygame.Surface, font: pygame.font.Font, text: str, x: int, y: int) -> None:
    surf = font.render(text, True, (230, 230, 240))
    screen.blit(surf, (x, y))


def draw_crosshair(screen: pygame.Surface, rect: pygame.Rect, local_x: int, local_y: int, col=(255, 255, 255)) -> None:
    if not (0 <= local_x < rect.width and 0 <= local_y < rect.height):
        return
    x = rect.x + local_x
    y = rect.y + local_y
    pygame.draw.line(screen, col, (rect.x, y), (rect.right, y), 1)
    pygame.draw.line(screen, col, (x, rect.y), (x, rect.bottom), 1)
    pygame.draw.circle(screen, col, (x, y), 5, 1)


# ----------------------------
# Simple UI: Button + Slider
# ----------------------------
@dataclass
class Button:
    label: str
    rect: pygame.Rect

    def draw(self, screen: pygame.Surface, font: pygame.font.Font) -> None:
        pygame.draw.rect(screen, (22, 22, 30), self.rect)
        pygame.draw.rect(screen, (90, 90, 130), self.rect, 2)
        lab = font.render(self.label, True, (240, 240, 250))
        lab_rect = lab.get_rect(center=self.rect.center)
        screen.blit(lab, lab_rect.topleft)

    def hit(self, pos: tuple[int, int]) -> bool:
        return self.rect.collidepoint(pos)


@dataclass
class HSlider:
    label: str
    rect: pygame.Rect
    vmin: float
    vmax: float
    value: float
    dragging: bool = False

    def _t(self) -> float:
        if self.vmax <= self.vmin:
            return 0.0
        return (self.value - self.vmin) / (self.vmax - self.vmin)

    def _set_from_x(self, x: int) -> None:
        t = (x - self.rect.x) / max(1, self.rect.w)
        t = clamp(t, 0.0, 1.0)
        self.value = self.vmin + t * (self.vmax - self.vmin)

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Returns True if value changed."""
        changed = False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.dragging = True
                self._set_from_x(event.pos[0])
                return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.dragging:
                self.dragging = False
        elif event.type == pygame.MOUSEMOTION:
            if self.dragging:
                self._set_from_x(event.pos[0])
                changed = True
        return changed

    def draw(self, screen: pygame.Surface, font: pygame.font.Font) -> None:
        # label
        lab = font.render(f"{self.label}: {self.value:.3f}", True, (230, 230, 240))
        screen.blit(lab, (self.rect.x, self.rect.y - 20))

        # track
        pygame.draw.rect(screen, (22, 22, 30), self.rect)
        pygame.draw.rect(screen, (90, 90, 130), self.rect, 2)

        # knob
        t = self._t()
        knob_x = int(self.rect.x + t * self.rect.w)
        knob = pygame.Rect(knob_x - 6, self.rect.y - 2, 12, self.rect.h + 4)
        pygame.draw.rect(screen, (170, 170, 220), knob)


# ----------------------------
# SplineField: allow setting the coarse grid directly
# ----------------------------
def _sig_for(vp: Viewport, iters: int) -> tuple[float, float, float, float, int]:
    iters = int(clamp(iters, 1, 2000))
    return (vp.x_min, vp.x_max, vp.y_min, vp.y_max, iters)


def splinefield_set_grid(field: "SplineField", grid: np.ndarray, vp: Viewport, iters: int) -> None:
    """
    Force a SplineField's coarse grid (grid_h x grid_w) and mark it 'clean' for this viewport/iters.
    Keeps your downstream upsample pipeline unchanged.
    """
    if grid.shape != (field.grid_h, field.grid_w):
        raise ValueError(f"grid must be {(field.grid_h, field.grid_w)} got {grid.shape}")
    field.control.clear()
    field.grid[:, :] = grid.astype(np.float32, copy=False)
    field._dirty = False
    field._last_sig = _sig_for(vp, iters)


# ----------------------------
# Distortion initializers (operate on your coarse grid)
# ----------------------------
def init_zero(field_re: "SplineField", field_im: "SplineField") -> None:
    field_re.clear()
    field_im.clear()


def init_pylons(
    field_re: "SplineField",
    field_im: "SplineField",
    vp: Viewport,
    strength: float,
    n_pylons: int = 14,
    seed: int | None = None,
) -> None:
    """
    POI-like hot spots. Implemented as multiple control-point injections per pylon so
    your harmonic relaxation makes a smooth "hill".
    """
    rng = np.random.default_rng(seed)
    field_re.clear()
    field_im.clear()

    if strength <= 0.0:
        return

    # (x,y) in vp; inject center + ring of jittered points
    for _ in range(n_pylons):
        x = rng.uniform(vp.x_min, vp.x_max)
        y = rng.uniform(vp.y_min, vp.y_max)
        p = complex(x, y)

        # complex amplitude per pylon
        a_re = float(rng.normal(0.0, 1.0) * strength)
        a_im = float(rng.normal(0.0, 1.0) * strength)

        field_re.add_delta(p, a_re)
        field_im.add_delta(p, a_im)

        # "Gaussian-ish" spread by adding smaller deltas nearby
        for _k in range(6):
            jx = float(rng.normal(0.0, 0.08)) * (vp.x_max - vp.x_min)
            jy = float(rng.normal(0.0, 0.08)) * (vp.y_max - vp.y_min)
            pj = complex(x + jx, y + jy)
            field_re.add_delta(pj, 0.35 * a_re)
            field_im.add_delta(pj, 0.35 * a_im)


def init_wavefronts(
    field_re: "SplineField",
    field_im: "SplineField",
    vp: Viewport,
    strength: float,
    n_waves: int = 5,
    n_samples: int = 140,
    seed: int | None = None,
) -> None:
    """
    Band-y tides / fronts. We sample the function at random points and use those as control points.
    """
    rng = np.random.default_rng(seed)
    field_re.clear()
    field_im.clear()

    if strength <= 0.0:
        return

    # random wave parameters
    waves = []
    span = max(vp.x_max - vp.x_min, vp.y_max - vp.y_min)
    for _ in range(n_waves):
        theta = float(rng.uniform(0, 2 * np.pi))
        ux, uy = np.cos(theta), np.sin(theta)
        k = float(rng.uniform(2.0, 8.0)) / max(span, 1e-6)  # cycles across the view
        phase = float(rng.uniform(0, 2 * np.pi))
        amp_re = float(rng.normal(0.0, 1.0))
        amp_im = float(rng.normal(0.0, 1.0))
        waves.append((ux, uy, k, phase, amp_re, amp_im))

    for _ in range(n_samples):
        x = float(rng.uniform(vp.x_min, vp.x_max))
        y = float(rng.uniform(vp.y_min, vp.y_max))
        s_re = 0.0
        s_im = 0.0
        for (ux, uy, k, phase, a_re, a_im) in waves:
            t = k * (ux * x + uy * y) + phase
            s = np.sin(t)
            s_re += a_re * s
            s_im += a_im * s

        # normalize-ish and inject
        s_re = float(s_re * strength / max(1.0, n_waves))
        s_im = float(s_im * strength / max(1.0, n_waves))
        p = complex(x, y)
        field_re.add_delta(p, s_re)
        field_im.add_delta(p, s_im)


def _spectral_grid(h: int, w: int, alpha: float, rng: np.random.Generator) -> np.ndarray:
    """
    Smooth random field via spectral synthesis.
    Output roughly ~[-1,1] (after normalization).
    """
    ky = np.fft.fftfreq(h)[:, None]          # shape (h,1)
    kx = np.fft.rfftfreq(w)[None, :]         # shape (1,w//2+1)
    k2 = kx * kx + ky * ky
    k = np.sqrt(np.maximum(k2, 1e-12))

    # amplitude decay: 1/k^alpha
    amp = 1.0 / (k ** alpha)
    amp[0, 0] = 0.0

    phase = rng.uniform(0.0, 2.0 * np.pi, size=amp.shape).astype(np.float32)
    mag = rng.normal(0.0, 1.0, size=amp.shape).astype(np.float32)

    spec = (mag * amp).astype(np.float32) * (np.cos(phase) + 1j * np.sin(phase))
    field = np.fft.irfft2(spec, s=(h, w)).astype(np.float32)

    m = float(np.max(np.abs(field))) if field.size else 1.0
    m = max(m, 1e-6)
    return (field / m).astype(np.float32, copy=False)


def init_spectral(
    field_re: "SplineField",
    field_im: "SplineField",
    vp: Viewport,
    strength: float,
    stiff_re: int,
    stiff_im: int,
    alpha: float = 1.8,
    seed: int | None = None,
) -> None:
    """
    Sets the *coarse* grids directly (no control points needed), keeping your downstream
    upsample pipeline intact.
    """
    field_re.clear()
    field_im.clear()

    if strength <= 0.0:
        return

    rng = np.random.default_rng(seed)
    g_re = _spectral_grid(field_re.grid_h, field_re.grid_w, float(alpha), rng) * float(strength)
    g_im = _spectral_grid(field_im.grid_h, field_im.grid_w, float(alpha), rng) * float(strength)

    splinefield_set_grid(field_re, g_re, vp, stiff_re)
    splinefield_set_grid(field_im, g_im, vp, stiff_im)

def _fade(t: np.ndarray) -> np.ndarray:
    # Perlin "fade" curve
    return t * t * t * (t * (t * 6 - 15) + 10)

def _lerp(a: np.ndarray, b: np.ndarray, t: np.ndarray) -> np.ndarray:
    return a + t * (b - a)

def perlin2d(h: int, w: int, res_y: int, res_x: int, seed: int | None = None) -> np.ndarray:
    """
    2D tileable Perlin noise in [-1, 1] (roughly).
    res_y/res_x = number of lattice cells across height/width.
    """
    res_y = max(1, int(res_y))
    res_x = max(1, int(res_x))
    rng = np.random.default_rng(seed)

    # Random unit gradients on a res_y x res_x lattice (tileable via modulo indexing)
    angles = rng.uniform(0.0, 2.0 * np.pi, size=(res_y, res_x)).astype(np.float32)
    grads = np.stack([np.cos(angles), np.sin(angles)], axis=-1).astype(np.float32)  # (ry, rx, 2)

    # Coordinates in lattice space
    xs = (np.arange(w, dtype=np.float32) / max(1, w)) * res_x
    ys = (np.arange(h, dtype=np.float32) / max(1, h)) * res_y
    X, Y = np.meshgrid(xs, ys)  # (h,w)

    xi = np.floor(X).astype(np.int32)
    yi = np.floor(Y).astype(np.int32)
    xf = (X - xi).astype(np.float32)
    yf = (Y - yi).astype(np.float32)

    # Wrap for tiling
    x0 = xi % res_x
    y0 = yi % res_y
    x1 = (x0 + 1) % res_x
    y1 = (y0 + 1) % res_y

    # Corner gradients
    g00 = grads[y0, x0]
    g10 = grads[y0, x1]
    g01 = grads[y1, x0]
    g11 = grads[y1, x1]

    # Dot products corner->point
    d00 = g00[..., 0] * xf + g00[..., 1] * yf
    d10 = g10[..., 0] * (xf - 1.0) + g10[..., 1] * yf
    d01 = g01[..., 0] * xf + g01[..., 1] * (yf - 1.0)
    d11 = g11[..., 0] * (xf - 1.0) + g11[..., 1] * (yf - 1.0)

    u = _fade(xf)
    v = _fade(yf)

    nx0 = _lerp(d00, d10, u)
    nx1 = _lerp(d01, d11, u)
    n = _lerp(nx0, nx1, v)

    # Normalize-ish to [-1,1] (Perlin already centered; clamp for safety)
    n = np.clip(n, -1.0, 1.0).astype(np.float32, copy=False)
    return n

def fbm_perlin(h: int, w: int, base_res_y: int, base_res_x: int,
               octaves: int, persistence: float, lacunarity: float,
               seed: int | None = None) -> np.ndarray:
    """
    Fractal Brownian motion: sum of Perlin octaves.
    Output normalized to ~[-1,1].
    """
    octaves = int(clamp(octaves, 1, 12))
    persistence = float(clamp(persistence, 0.1, 0.95))
    lacunarity = float(clamp(lacunarity, 1.5, 3.5))

    total = np.zeros((h, w), dtype=np.float32)
    amp = 1.0
    amp_sum = 0.0

    rng = np.random.default_rng(seed)
    seeds = rng.integers(0, 2**31 - 1, size=octaves, dtype=np.int64)

    res_y = max(1, int(base_res_y))
    res_x = max(1, int(base_res_x))

    for i in range(octaves):
        n = perlin2d(h, w, res_y, res_x, seed=int(seeds[i]))
        total += amp * n
        amp_sum += amp
        amp *= persistence
        res_y = max(1, int(res_y * lacunarity))
        res_x = max(1, int(res_x * lacunarity))

    if amp_sum > 1e-6:
        total /= amp_sum

    m = float(np.max(np.abs(total))) if total.size else 1.0
    m = max(m, 1e-6)
    return (total / m).astype(np.float32, copy=False)

def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = (x - edge0) / max(1e-6, (edge1 - edge0))
    t = np.clip(t, 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32, copy=False)

def init_landscape(
    field_re: "SplineField",
    field_im: "SplineField",
    vp: Viewport,
    strength: float,
    stiff_re: int,
    stiff_im: int,
    *,
    mstart: float = 0.25,        # where mountains begin (0..1 from left->right)
    sharpness: float = 1.0,      # >1 pushes mountains to the far right
    ridge_strength: float = 0.5, # how ridgy the mountains get
    base_res: int = 1,           # big hills
    detail_res: int = 5,        # small features
    oct_base: int = 3,
    oct_detail: int = 5,
    persistence: float = 0.55,
    lacunarity: float = 2.0,
    use_gradient: bool = True,   # if True: (dx,dy) distortion; else: (H,H2)
    seed: int | None = None,
) -> None:
    """
    Plains on the left, mountains on the right.

    Produces a terrain height H, then converts it into a 2D distortion vector:
      dist_re = k * dH/dx
      dist_im = k * dH/dy
    (This tends to feel like "flow along slopes".)
    """
    field_re.clear()
    field_im.clear()
    strength = float(strength)
    if strength <= 0.0:
        return

    h, w = field_re.grid_h, field_re.grid_w

    # left->right mask (0..1)
    x = (np.arange(w, dtype=np.float32) / max(1, (w - 1))).astype(np.float32)
    m = _smoothstep(float(mstart), 1.0, x)[None, :]  # (1,w)
    m = np.power(m, float(max(0.25, sharpness))).astype(np.float32)

    # Two fBm fields
    base = fbm_perlin(h, w, base_res, base_res, oct_base, persistence, lacunarity, seed=seed)
    detail = fbm_perlin(h, w, detail_res, detail_res, oct_detail, persistence, lacunarity, seed=None if seed is None else seed + 101)

    # Ridge transform (only matters in mountain zone)
    ridge = np.power(1.0 - np.abs(detail), 2.0).astype(np.float32)

    # Heightfield: gentle base + mountain detail + ridges
    H = (0.35 * base + m * (0.70 * detail + float(ridge_strength) * ridge)).astype(np.float32)

    # Normalize
    H -= float(np.mean(H))
    mx = float(np.max(np.abs(H))) if H.size else 1.0
    mx = max(mx, 1e-6)
    H = (H / mx).astype(np.float32, copy=False)

    if use_gradient:
        dHy, dHx = np.gradient(H)  # y,x
        g_re = (dHx * strength).astype(np.float32)
        g_im = (dHy * strength).astype(np.float32)
    else:
        # simpler fallback: two correlated “height-like” fields
        H2 = fbm_perlin(h, w, detail_res, detail_res, oct_detail, persistence, lacunarity, seed=None if seed is None else seed + 202)
        g_re = (H * strength).astype(np.float32)
        g_im = (H2 * strength).astype(np.float32)

    splinefield_set_grid(field_re, g_re, vp, int(stiff_re))
    splinefield_set_grid(field_im, g_im, vp, int(stiff_im))


# ----------------------------
# Main
# ----------------------------
def main() -> None:
    pygame.init()
    pygame.display.set_caption("Mandelbrot -> Distorted Julia (stable field) + Orbit")
    font = pygame.font.SysFont("consolas", 18)

    screen_w = W_PANEL * 2 + PADDING * 3
    screen_h = TOPBAR_H + H_TOP + H_BOTTOM + PADDING * 5 + UI_H
    screen = pygame.display.set_mode((screen_w, screen_h))
    clock = pygame.time.Clock()

    # Top bar
    rect_topbar = pygame.Rect(PADDING, PADDING, screen_w - 2 * PADDING, TOPBAR_H)

    # Panels (2x2)
    rect_zr = pygame.Rect(PADDING, rect_topbar.bottom + PADDING, W_PANEL, H_TOP)
    rect_zi = pygame.Rect(PADDING * 2 + W_PANEL, rect_topbar.bottom + PADDING, W_PANEL, H_TOP)
    rect_m = pygame.Rect(PADDING, rect_zr.bottom + PADDING, W_PANEL, H_BOTTOM)
    rect_j = pygame.Rect(PADDING * 2 + W_PANEL, rect_zi.bottom + PADDING, W_PANEL, H_BOTTOM)

    # Bottom UI
    ui_top = rect_m.bottom + PADDING
    ui_rect = pygame.Rect(PADDING, ui_top, screen_w - 2 * PADDING, UI_H)

    # Viewports
    vp_m = Viewport(x_min=-2.25, x_max=0.85, y_min=-1.3, y_max=1.3, width=W_PANEL, height=H_BOTTOM)
    vp_j = Viewport(x_min=-1.6, x_max=1.6, y_min=-1.3, y_max=1.3, width=W_PANEL, height=H_BOTTOM)

    # Fixed world/map domain for the distortion fields (THIS is the key fix)
    vp_field = Viewport(x_min=-1.6, x_max=1.6, y_min=-1.3, y_max=1.3, width=FIELD_TEX_W, height=FIELD_TEX_H)

    def vp_top_view() -> Viewport:
        return Viewport(vp_j.x_min, vp_j.x_max, vp_j.y_min, vp_j.y_max, width=W_PANEL, height=H_TOP)

    # Parameters
    params = {
        "m_iter": 256,
        "j_iter": 256,
        "orbit_steps": 80,
        "hit_alpha": 150,
        "max_op": 0.8,
        "escape_r": 4.0,
        "stiff_re": 120,
        "stiff_im": 120,
        "z_step": 0.05,
        "highlight_steps": [1],
                # Landscape params
        "land_mstart": 0.55,
        "land_sharp": 2.0,
        "land_ridge": 0.90,
        "land_base_res": 3,
        "land_detail_res": 10,
        "land_use_grad": 1,   # 1=True, 0=False

    }

    # Initial Mandelbrot parameter c
    c = complex(-0.8, 0.156)

    # Distortion fields (coarse grid)
    z_field_re = SplineField(180, 100)
    z_field_im = SplineField(180, 100)

    # Stable field textures (world coords)
    tex_re = np.zeros((FIELD_TEX_H, FIELD_TEX_W), dtype=np.float32)
    tex_im = np.zeros((FIELD_TEX_H, FIELD_TEX_W), dtype=np.float32)

    # View-sampled distortion arrays (depend on vp_j, but the *field* does not)
    dist_re_view = np.zeros((vp_j.height, vp_j.width), dtype=np.float32)
    dist_im_view = np.zeros((vp_j.height, vp_j.width), dtype=np.float32)

    z_re_surf = pygame.Surface((rect_zr.width, rect_zr.height))
    z_im_surf = pygame.Surface((rect_zi.width, rect_zi.height))

    def rebuild_field_textures() -> None:
        """Solve fields on vp_field (fixed) and build stable textures tex_re/tex_im."""
        nonlocal tex_re, tex_im
        re_grid = z_field_re.solve(vp_field, int(params["stiff_re"]))
        im_grid = z_field_im.solve(vp_field, int(params["stiff_im"]))
        tex_re = upsample_bilinear(re_grid, FIELD_TEX_H, FIELD_TEX_W)
        tex_im = upsample_bilinear(im_grid, FIELD_TEX_H, FIELD_TEX_W)

    def resample_views() -> None:
        """Sample stable field textures into current vp_j and vp_top_view."""
        nonlocal dist_re_view, dist_im_view, z_re_surf, z_im_surf
        dist_re_view = sample_tex_to_view_bilinear(tex_re, vp_j, vp_field)
        dist_im_view = sample_tex_to_view_bilinear(tex_im, vp_j, vp_field)

        top_vp = vp_top_view()
        re_top = sample_tex_to_view_bilinear(tex_re, top_vp, vp_field)
        im_top = sample_tex_to_view_bilinear(tex_im, top_vp, vp_field)
        z_re_surf = field_to_surface(re_top)
        z_im_surf = field_to_surface(im_top)

    def render_julia_full() -> pygame.Surface:
        resample_views()
        return fractal_julia_distorted(vp_j, c, int(params["j_iter"]), dist_re_view, dist_im_view)

    # Precompute images
    mandelbrot_surf = fractal_mandelbrot(vp_m, int(params["m_iter"]))
    rebuild_field_textures()
    resample_views()
    julia_surf = fractal_julia_distorted(vp_j, c, int(params["j_iter"]), dist_re_view, dist_im_view)

    # Selection state for editor
    selected_axis: str | None = None
    selected_z: complex | None = None

    # Debounced full renders after wheel settles
    last_zoom_ts_ms = 0
    zoom_debounce_ms = 500
    pending_full_m = False
    pending_full_j = False
    zoom_version_m = 0
    zoom_version_j = 0

    # Bottom editor buttons
    @dataclass
    class AdjustButton:
        label: str
        mult: int
        rect: pygame.Rect

    btn_w = 54
    btn_h = 26
    btn_gap = 8
    btn_x0 = ui_rect.x + 10
    btn_y0 = ui_rect.y + 34
    adjust_buttons: list[AdjustButton] = []
    for i, (lab, mult) in enumerate([("---", -3), ("--", -2), ("-", -1), ("+", 1), ("++", 2), ("+++", 3)]):
        r = pygame.Rect(btn_x0 + i * (btn_w + btn_gap), btn_y0, btn_w, btn_h)
        adjust_buttons.append(AdjustButton(lab, mult, r))

    # ----------------------------
    # Distortion init controls
    # ----------------------------
    dist_seed = 1  # increments each click so you get "new" randomness

    slider_strength = HSlider(
        "DistStrength",
        pygame.Rect(ui_rect.x + 430, ui_rect.y + 32, 260, 18),
        vmin=0.0,
        vmax=1.0,     # typical good range is 0.25; bump if you want chaos
        value=1.0,
    )

    btn_zero     = Button("Zero",     pygame.Rect(ui_rect.x + 700, ui_rect.y + 24, 70, 28))
    btn_pylons   = Button("Pylons",   pygame.Rect(ui_rect.x + 780, ui_rect.y + 24, 80, 28))
    btn_waves    = Button("Waves",    pygame.Rect(ui_rect.x + 870, ui_rect.y + 24, 80, 28))
    btn_spectral = Button("Spectral", pygame.Rect(ui_rect.x + 960, ui_rect.y + 24, 90, 28))
    btn_landscape = Button("Landscape", pygame.Rect(ui_rect.x + 1060, ui_rect.y + 24, 110, 28))


    init_buttons = [btn_zero, btn_pylons, btn_waves, btn_spectral, btn_landscape]


    # Bottom param boxes
    box_w = 120
    box_h = 30
    gap_x = 18
    gap_y = 18
    x0 = ui_rect.x + 10
    y0 = ui_rect.y + 90

    boxes: list[ParamBox] = [
        ParamBox("Mandelb_iter", "m_iter", pygame.Rect(x0 + 0 * (box_w + gap_x), y0 + 0 * (box_h + gap_y), box_w, box_h), "int", str(params["m_iter"])),
        ParamBox("Julia_iter",   "j_iter", pygame.Rect(x0 + 1 * (box_w + gap_x), y0 + 0 * (box_h + gap_y), box_w, box_h), "int", str(params["j_iter"])),
        ParamBox("OrbitSteps",   "orbit_steps", pygame.Rect(x0 + 2 * (box_w + gap_x), y0 + 0 * (box_h + gap_y), box_w, box_h), "int", str(params["orbit_steps"])),
        ParamBox("Opacity",      "hit_alpha", pygame.Rect(x0 + 3 * (box_w + gap_x), y0 + 0 * (box_h + gap_y), box_w, box_h), "int", str(params["hit_alpha"])),
        ParamBox("MaxOpacity",   "max_op", pygame.Rect(x0 + 4 * (box_w + gap_x), y0 + 0 * (box_h + gap_y), box_w, box_h), "float", f"{params['max_op']:.3f}"),
        ParamBox("Stiff(Re)",    "stiff_re", pygame.Rect(x0 + 0 * (box_w + gap_x), y0 + 1 * (box_h + gap_y), box_w, box_h), "int", str(params["stiff_re"])),
        ParamBox("Stiff(Im)",    "stiff_im", pygame.Rect(x0 + 1 * (box_w + gap_x), y0 + 1 * (box_h + gap_y), box_w, box_h), "int", str(params["stiff_im"])),
        ParamBox("ZStep",        "z_step", pygame.Rect(x0 + 2 * (box_w + gap_x), y0 + 1 * (box_h + gap_y), box_w, box_h), "float", f"{params['z_step']:.3f}"),
    ]
        # After creating the first row boxes (up through MaxOpacity)...
    # Add extra boxes to the right of MaxOpacity:
    maxop_rect = boxes[4].rect  # MaxOpacity is the 5th in your list

    extra_x0 = maxop_rect.right + gap_x
    extra_y0 = y0 + 50  # same row as MaxOpacity

    boxes.extend([
        ParamBox("MtnStart", "land_mstart", pygame.Rect(extra_x0 + 0 * (box_w + gap_x), extra_y0, box_w, box_h), "float", f"{params['land_mstart']:.3f}"),
        ParamBox("Sharp",    "land_sharp",  pygame.Rect(extra_x0 + 1 * (box_w + gap_x), extra_y0, box_w, box_h), "float", f"{params['land_sharp']:.3f}"),
        ParamBox("Ridge",    "land_ridge",  pygame.Rect(extra_x0 + 2 * (box_w + gap_x), extra_y0, box_w, box_h), "float", f"{params['land_ridge']:.3f}"),
        ParamBox("BaseRes",  "land_base_res",   pygame.Rect(extra_x0 + 3 * (box_w + gap_x), extra_y0, box_w, box_h), "int", str(params["land_base_res"])),
        ParamBox("DetailRes","land_detail_res", pygame.Rect(extra_x0 + 4 * (box_w + gap_x), extra_y0, box_w, box_h), "int", str(params["land_detail_res"])),
        ParamBox("UseGrad(0/1)", "land_use_grad", pygame.Rect(extra_x0 + 5 * (box_w + gap_x), extra_y0, box_w, box_h), "int", str(params["land_use_grad"])),
    ])

    # --- Layout init controls to the right of MaxOpacity box ---
    maxop_box = next(b for b in boxes if b.key == "max_op")

    pad = 12
    x = maxop_box.rect.right + pad
    y = maxop_box.rect.y  # same row as MaxOpacity

    # slider next to the buttons, centered vertically in the row
    slider_h = 18
    slider_strength.rect = pygame.Rect(x, y + (maxop_box.rect.h - slider_h)//2, 180, slider_h)
    x = slider_strength.rect.right + pad

    btn_h = 28
    btn_zero.rect     = pygame.Rect(x, y + (maxop_box.rect.h - btn_h)//2, 70, btn_h); x += 70 + 8
    btn_pylons.rect   = pygame.Rect(x, y + (maxop_box.rect.h - btn_h)//2, 80, btn_h); x += 80 + 8
    btn_waves.rect    = pygame.Rect(x, y + (maxop_box.rect.h - btn_h)//2, 80, btn_h); x += 80 + 8
    btn_spectral.rect = pygame.Rect(x, y + (maxop_box.rect.h - btn_h)//2, 90, btn_h)


    def unfocus_all() -> None:
        for b in boxes:
            b.focused = False
            b.select_all = False

    def apply_box(b: ParamBox) -> None:
        nonlocal mandelbrot_surf, julia_surf
        try:
            v = try_parse(b.kind, b.text)
        except Exception:
            # revert
            if b.key in ("max_op", "z_step"):
                b.text = f"{float(params[b.key]):.3f}"
            else:
                b.text = str(params[b.key])
            return

        if b.key == "m_iter":
            v = int(clamp(v, 16, 5000))
            params["m_iter"] = v
            b.text = str(v)
            mandelbrot_surf = fractal_mandelbrot(vp_m, int(params["m_iter"]))
            return

        if b.key == "j_iter":
            v = int(clamp(v, 16, 5000))
            params["j_iter"] = v
            b.text = str(v)
            rebuild_field_textures()
            julia_surf = render_julia_full()
            return

        if b.key == "orbit_steps":
            v = int(clamp(v, 1, 2000))
            params["orbit_steps"] = v
            b.text = str(v)
            return

        if b.key == "hit_alpha":
            v = int(clamp(v, 0, 255))
            params["hit_alpha"] = v
            b.text = str(v)
            return

        if b.key == "max_op":
            v = float(v)
            v = clamp(v, 0.0, 0.8)
            params["max_op"] = v
            b.text = f"{v:.3f}"
            return

        if b.key in ("stiff_re", "stiff_im"):
            v = int(clamp(v, 1, 10000000))
            params[b.key] = v
            b.text = str(v)
            rebuild_field_textures()
            julia_surf = render_julia_full()
            return

        if b.key == "z_step":
            v = float(v)
            v = clamp(v, 0.0001, 5.0)
            params["z_step"] = v
            b.text = f"{v:.3f}"
            return
        
        if b.key == "land_mstart":
            v = float(v)
            v = clamp(v, -1.0, 1.0)
            params["land_mstart"] = v
            b.text = f"{v:.3f}"
            return

        if b.key == "land_sharp":
            v = float(v)
            v = clamp(v, 0.25, 100.0)
            params["land_sharp"] = v
            b.text = f"{v:.3f}"
            return

        if b.key == "land_ridge":
            v = float(v)
            v = clamp(v, 0.0, 50.0)
            params["land_ridge"] = v
            b.text = f"{v:.3f}"
            return

        if b.key == "land_base_res":
            v = int(clamp(int(v), 1, 100))
            params["land_base_res"] = v
            b.text = str(v)
            return

        if b.key == "land_detail_res":
            v = int(clamp(int(v), 1, 100))
            params["land_detail_res"] = v
            b.text = str(v)
            return

        if b.key == "land_use_grad":
            v = 1 if int(v) != 0 else 0
            params["land_use_grad"] = v
            b.text = str(v)
            return


    # --- Top bar controls: slider + buttons + seed box ---
    slider = HSlider(
        label="",
        rect=pygame.Rect(rect_topbar.x + 170, rect_topbar.y + 30, 240, 10),
        vmin=0.0,
        vmax=1.0,
        value=0.25,
    )
    btn_distort = Button("Distort", pygame.Rect(rect_topbar.x + 10, rect_topbar.y + 18, 120, 28))
    seed_box = ParamBox("Seed", "seed", pygame.Rect(rect_topbar.x + 430, rect_topbar.y + 18, 110, 28), "int", "0")
    btn_apply_seed = Button("ApplySeed", pygame.Rect(rect_topbar.x + 550, rect_topbar.y + 18, 120, 28))

    rng = np.random.default_rng(0)

    def set_rng_from_seed_text() -> None:
        nonlocal rng
        try:
            seed = int(float(seed_box.text.strip() or "0"))
        except Exception:
            seed = 0
            seed_box.text = "0"
        rng = np.random.default_rng(seed)

    set_rng_from_seed_text()

    def randomize_fields(scale: float) -> None:
        """Clear both fields; if scale>0 add random control points; rebuild textures; rerender."""
        nonlocal julia_surf
        scale = float(scale)
        z_field_re.clear()
        z_field_im.clear()

        if scale > 0.0:
            for _ in range(RAND_POINTS):
                x = rng.uniform(vp_field.x_min, vp_field.x_max)
                y = rng.uniform(vp_field.y_min, vp_field.y_max)
                val_re = rng.normal(0.0, scale)
                val_im = rng.normal(0.0, scale)
                z_field_re.add_delta(complex(x, y), float(val_re))
                z_field_im.add_delta(complex(x, y), float(val_im))

        rebuild_field_textures()
        julia_surf = render_julia_full()

    running = True
    while running:
        clock.tick(FPS)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            # slider drag
            slider_changed = slider_strength.handle_event(event)
            if slider_changed:
                # don't re-render on every pixel drag; you can if you want,
                # but it can get expensive. We'll just update the value live.
                pass

            # top-bar rand slider drag
            slider.handle_event(event)

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if any(b.focused for b in boxes) or seed_box.focused:
                        unfocus_all()
                        seed_box.focused = False
                        seed_box.select_all = False
                    else:
                        running = False

                elif event.key == pygame.K_r:
                    vp_m = Viewport(x_min=-2.25, x_max=0.85, y_min=-1.3, y_max=1.3, width=W_PANEL, height=H_BOTTOM)
                    vp_j = Viewport(x_min=-1.6, x_max=1.6, y_min=-1.3, y_max=1.3, width=W_PANEL, height=H_BOTTOM)
                    mandelbrot_surf = fractal_mandelbrot(vp_m, int(params["m_iter"]))
                    rebuild_field_textures()
                    # note: field textures unchanged; just resample + rerender julia
                    julia_surf = render_julia_full()

                # keystrokes to focused box
                if seed_box.focused:
                    wants_apply = seed_box.handle_keydown(event)
                    if wants_apply:
                        set_rng_from_seed_text()
                        seed_box.focused = False
                    continue

                for b in boxes:
                    if b.focused:
                        wants_apply = b.handle_keydown(event)
                        if wants_apply:
                            apply_box(b)
                            b.focused = False
                        break

            elif event.type == pygame.MOUSEBUTTONDOWN:
                mx, my = event.pos

                # top bar buttons
                if event.button == 1 and btn_distort.rect.collidepoint(mx, my):
                    randomize_fields(slider.value)
                    continue
                if event.button == 1 and btn_apply_seed.rect.collidepoint(mx, my):
                    set_rng_from_seed_text()
                    continue

                # seed box focus
                if seed_box.rect.collidepoint(mx, my) and event.button == 1:
                    unfocus_all()
                    seed_box.focused = True
                    seed_box.select_all = True
                    continue

                # focus handling for boxes
                clicked_box = False
                for b in boxes:
                    if b.rect.collidepoint(mx, my):
                        unfocus_all()
                        seed_box.focused = False
                        b.focused = True
                        b.select_all = True
                        clicked_box = True
                        break
                if clicked_box:
                    continue
                else:
                    unfocus_all()
                    seed_box.focused = False

                # Distortion init buttons
                if event.button == 1:
                    clicked_init = False
                    for bbtn in init_buttons:
                        if bbtn.hit((mx, my)):
                            strength = float(slider_strength.value)

                            if bbtn is btn_zero or strength <= 0.0:
                                init_zero(z_field_re, z_field_im)

                            elif bbtn is btn_pylons:
                                init_pylons(
                                    z_field_re, z_field_im,
                                    vp_field,              # <-- use vp_field (fixed domain)
                                    strength=strength,
                                    n_pylons=14,
                                    seed=dist_seed,
                                )

                            elif bbtn is btn_waves:
                                init_wavefronts(
                                    z_field_re, z_field_im,
                                    vp_field,              # <-- use vp_field
                                    strength=strength,
                                    n_waves=5,
                                    n_samples=160,
                                    seed=dist_seed,
                                )

                            elif bbtn is btn_spectral:
                                init_spectral(
                                    z_field_re, z_field_im,
                                    vp_field,              # <-- use vp_field
                                    strength=strength,
                                    stiff_re=int(params["stiff_re"]),
                                    stiff_im=int(params["stiff_im"]),
                                    alpha=1.8,
                                    seed=dist_seed,
                                )
                            
                            elif bbtn is btn_landscape:
                                init_landscape(
                                    z_field_re, z_field_im,
                                    vp_field,
                                    strength=strength,
                                    stiff_re=int(params["stiff_re"]),
                                    stiff_im=int(params["stiff_im"]),
                                    mstart=float(params["land_mstart"]),
                                    sharpness=float(params["land_sharp"]),
                                    ridge_strength=float(params["land_ridge"]),
                                    base_res=int(params["land_base_res"]),
                                    detail_res=int(params["land_detail_res"]),
                                    use_gradient=(int(params["land_use_grad"]) != 0),
                                    seed=dist_seed,
                                )


                            dist_seed += 1

                            rebuild_field_textures()        # <-- CRITICAL
                            julia_surf = render_julia_full()# <-- CRITICAL (assign it)
                            clicked_init = True
                            break

                    if clicked_init:
                        continue



                # z-field selection (top row: z-plane)
                if rect_zr.collidepoint(mx, my) and event.button == 1:
                    local_x = mx - rect_zr.x
                    local_y = my - rect_zr.y
                    selected_axis = "re"
                    selected_z = vp_top_view().pixel_to_complex(local_x, local_y)
                    continue

                if rect_zi.collidepoint(mx, my) and event.button == 1:
                    local_x = mx - rect_zi.x
                    local_y = my - rect_zi.y
                    selected_axis = "im"
                    selected_z = vp_top_view().pixel_to_complex(local_x, local_y)
                    continue

                # z-field adjust buttons
                if event.button == 1 and selected_axis is not None and selected_z is not None:
                    clicked = False
                    for btn in adjust_buttons:
                        if btn.rect.collidepoint(mx, my):
                            delta = float(btn.mult) * float(params["z_step"])
                            if selected_axis == "re":
                                z_field_re.add_delta(selected_z, delta)
                            else:
                                z_field_im.add_delta(selected_z, delta)
                            rebuild_field_textures()
                            julia_surf = render_julia_full()
                            clicked = True
                            break
                    if clicked:
                        continue

                # Mandelbrot click sets c
                if rect_m.collidepoint(mx, my) and event.button == 1:
                    local_x = mx - rect_m.x
                    local_y = my - rect_m.y
                    c = vp_m.pixel_to_complex(local_x, local_y)
                    rebuild_field_textures()
                    julia_surf = render_julia_full()
                    continue

            elif event.type == pygame.MOUSEWHEEL:
                mx, my = pygame.mouse.get_pos()
                if rect_m.collidepoint(mx, my):
                    local_x = mx - rect_m.x
                    local_y = my - rect_m.y
                    zoom_viewport(vp_m, (local_x, local_y), event.y)
                    factor = 1.0 / 0.9 if event.y > 0 else 0.9
                    mandelbrot_surf = quick_zoom_preview(mandelbrot_surf, factor, (local_x, local_y), (rect_m.width, rect_m.height))
                    pending_full_m = True
                    last_zoom_ts_ms = pygame.time.get_ticks()
                    zoom_version_m += 1

                elif rect_j.collidepoint(mx, my):
                    local_x = mx - rect_j.x
                    local_y = my - rect_j.y
                    zoom_viewport(vp_j, (local_x, local_y), event.y)
                    factor = 1.0 / 0.9 if event.y > 0 else 0.9
                    julia_surf = quick_zoom_preview(julia_surf, factor, (local_x, local_y), (rect_j.width, rect_j.height))
                    pending_full_j = True
                    last_zoom_ts_ms = pygame.time.get_ticks()
                    zoom_version_j += 1

        # Draw background
        screen.fill((12, 12, 16))

        # Debounced full renders after wheel settles
        now_ms = pygame.time.get_ticks()
        if pending_full_m and now_ms - last_zoom_ts_ms >= zoom_debounce_ms:
            render_version = zoom_version_m
            surf = fractal_mandelbrot(vp_m, int(params["m_iter"]))
            if render_version == zoom_version_m:
                mandelbrot_surf = surf
                pending_full_m = False

        if pending_full_j and now_ms - last_zoom_ts_ms >= zoom_debounce_ms:
            render_version = zoom_version_j
            if render_version == zoom_version_j:
                rebuild_field_textures()
                julia_surf = render_julia_full()
                pending_full_j = False

        # Resample top field views each frame (cheap) so they track zoom window without changing field
        resample_views()

        # ---- Top bar ----
        pygame.draw.rect(screen, (14, 14, 20), rect_topbar)
        pygame.draw.rect(screen, (60, 60, 90), rect_topbar, 2)

        # Distort button
        pygame.draw.rect(screen, (22, 22, 30), btn_distort.rect)
        pygame.draw.rect(screen, (90, 90, 130), btn_distort.rect, 2)
        lab = font.render(btn_distort.label, True, (240, 240, 250))
        screen.blit(lab, lab.get_rect(center=btn_distort.rect.center).topleft)

        # Slider
        draw_text(screen, font, f"Rand scale: {slider.value:.3f}", slider.rect.x, rect_topbar.y + 8)
        slider.draw(screen, font)

        # Seed controls
        seed_box.draw(screen, font)
        pygame.draw.rect(screen, (22, 22, 30), btn_apply_seed.rect)
        pygame.draw.rect(screen, (90, 90, 130), btn_apply_seed.rect, 2)
        lab2 = font.render("Apply Seed", True, (240, 240, 250))
        screen.blit(lab2, lab2.get_rect(center=btn_apply_seed.rect.center).topleft)

        # ---- Panels ----
        screen.blit(z_re_surf, rect_zr.topleft)
        screen.blit(z_im_surf, rect_zi.topleft)
        screen.blit(mandelbrot_surf, rect_m.topleft)
        screen.blit(julia_surf, rect_j.topleft)

        for r in (rect_zr, rect_zi, rect_m, rect_j):
            pygame.draw.rect(screen, (80, 80, 110), r, 2)

        # Marker on Mandelbrot (c)
        mcx, mcy = vp_m.complex_to_pixel(c)
        pygame.draw.circle(screen, (255, 255, 255), (rect_m.x + mcx, rect_m.y + mcy), 6, 2)
        pygame.draw.circle(screen, (0, 0, 0), (rect_m.x + mcx, rect_m.y + mcy), 7, 1)

        # z-field selection marker
        if selected_axis is not None and selected_z is not None:
            tvp = vp_top_view()
            sx, sy = tvp.complex_to_pixel(selected_z)
            if selected_axis == "re":
                px, py = rect_zr.x + sx, rect_zr.y + sy
            else:
                px, py = rect_zi.x + sx, rect_zi.y + sy
            pygame.draw.circle(screen, (255, 255, 255), (px, py), 6, 2)
            pygame.draw.circle(screen, (0, 0, 0), (px, py), 7, 1)

        # ---- Linked crosshairs + orbit hover ----
        mx, my = pygame.mouse.get_pos()
        hover_z: complex | None = None
        hover_kind: str | None = None

        # Determine hover complex coordinate based on which panel
        if rect_zr.collidepoint(mx, my):
            hover_kind = "z"
            hover_z = vp_top_view().pixel_to_complex(mx - rect_zr.x, my - rect_zr.y)
        elif rect_zi.collidepoint(mx, my):
            hover_kind = "z"
            hover_z = vp_top_view().pixel_to_complex(mx - rect_zi.x, my - rect_zi.y)
        elif rect_m.collidepoint(mx, my):
            hover_kind = "c"
            hover_z = vp_m.pixel_to_complex(mx - rect_m.x, my - rect_m.y)
        elif rect_j.collidepoint(mx, my):
            hover_kind = "z"
            hover_z = vp_j.pixel_to_complex(mx - rect_j.x, my - rect_j.y)

        # Draw crosshairs on all panels using the same complex coordinate,
        # whenever that coordinate lies inside that panel's viewport.
        if hover_z is not None:
            # top panels use vp_top_view()
            tvp = vp_top_view()
            tx, ty = tvp.complex_to_pixel(hover_z)
            draw_crosshair(screen, rect_zr, tx, ty, (230, 230, 240))
            draw_crosshair(screen, rect_zi, tx, ty, (230, 230, 240))

            # julia panel uses vp_j
            jx, jy = vp_j.complex_to_pixel(hover_z)
            draw_crosshair(screen, rect_j, jx, jy, (230, 230, 240))

            # mandelbrot panel uses vp_m
            mx2, my2 = vp_m.complex_to_pixel(hover_z)
            draw_crosshair(screen, rect_m, mx2, my2, (230, 230, 240))

        # If hovering Julia panel, also draw orbit (with your opacity accumulation)
        z0_disp = None
        if rect_j.collidepoint(mx, my):
            local_x = mx - rect_j.x
            local_y = my - rect_j.y
            z0_base = vp_j.pixel_to_complex(local_x, local_y)

            # show "z0 + local z-offset" (your old display idea)
            d0 = sample_distortion_nn_tex(z0_base, vp_field, tex_re, tex_im)
            z0_disp = z0_base + d0

            orbit_pts = compute_orbit_distorted(
                z0_base, c, int(params["orbit_steps"]), float(params["escape_r"]),
                vp_field, tex_re, tex_im
            )

            pix = [vp_j.complex_to_pixel(z) for z in orbit_pts]
            local_pix = [(x, y) for (x, y) in pix]

            overlay = pygame.Surface((rect_j.width, rect_j.height), pygame.SRCALPHA)

            if len(local_pix) >= 2:
                for i in range(len(local_pix) - 1):
                    pygame.draw.line(
                        overlay,
                        (255, 255, 255, int(params["hit_alpha"])),
                        local_pix[i],
                        local_pix[i + 1],
                        2,
                    )

            # points
            for i, (x, y) in enumerate(local_pix):
                if i == 0:
                    col = (120, 240, 140, 180)
                    r = 5
                elif i == len(local_pix) - 1:
                    col = (240, 120, 120, 180)
                    r = 4
                else:
                    col = (220, 220, 235, 90)
                    r = 3
                pygame.draw.circle(overlay, col, (x, y), r)

            cap = int(float(params["max_op"]) * 255)
            overlay.fill((255, 255, 255, cap), special_flags=pygame.BLEND_RGBA_MULT)
            screen.blit(overlay, rect_j.topleft)

        # ---- Labels ----
        draw_text(screen, font, "Z field: Real offset", rect_zr.x + 10, rect_zr.y + 10)
        draw_text(screen, font, "Z field: Imag offset", rect_zi.x + 10, rect_zi.y + 10)
        draw_text(screen, font, "Mandelbrot (c-plane)", rect_m.x + 10, rect_m.y + 10)
        draw_text(screen, font, "Distorted Julia (z-plane)", rect_j.x + 10, rect_j.y + 10)
        draw_text(screen, font, f"c = {c.real:+.6f} {c.imag:+.6f}i", rect_j.x + 10, rect_j.y + 34)
        if z0_disp is not None:
            draw_text(screen, font, f"z0+d = {z0_disp.real:+.6f} {z0_disp.imag:+.6f}i", rect_j.x + 10, rect_j.y + 58)

        # ---- Bottom UI ----
        pygame.draw.rect(screen, (14, 14, 20), ui_rect)
        pygame.draw.rect(screen, (60, 60, 90), ui_rect, 2)
        draw_text(screen, font, "Click top fields to select a point; use ---/--/-/+ buttons to edit. Enter applies boxes. R resets view.", ui_rect.x + 10, ui_rect.y + 8)

        for btn in adjust_buttons:
            pygame.draw.rect(screen, (22, 22, 30), btn.rect)
            pygame.draw.rect(screen, (90, 90, 130), btn.rect, 2)
            lab = font.render(btn.label, True, (240, 240, 250))
            screen.blit(lab, lab.get_rect(center=btn.rect.center).topleft)

        status = "Editing: (none)"
        if selected_axis is not None and selected_z is not None:
            axis_name = "Real" if selected_axis == "re" else "Imag"
            status = f"Editing: {axis_name} @ z={selected_z.real:+.3f}{selected_z.imag:+.3f}i"
        draw_text(screen, font, status, ui_rect.x + 10 + 6 * (btn_w + btn_gap) + 20, btn_y0 + 4)

        for b in boxes:
            b.draw(screen, font)

        draw_text(screen, font, "Init distortion:", ui_rect.x + 430, ui_rect.y + 8)
        slider_strength.draw(screen, font)
        for bbtn in init_buttons:
            bbtn.draw(screen, font)

        
        pygame.display.flip()

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
