# edgecaster/lorenz.py

from __future__ import annotations
from typing import Any, List, Tuple


def init_lorenz_points(ctx: Any) -> None:
    """Initialize Lorenz points in continuous space.

    Uses ctx.rng for deterministic chaos tied to the run seed.
    Starts each point after a 'burn-in' along the attractor so
    it begins on the wings instead of at the origin, and spreads
    them widely to avoid mirrored lock-in.
    """
    if getattr(ctx, "lorenz_points", None):
        return

    # You can later set ctx.lorenz_num_points in Game.__init__ if you like.
    num_points = getattr(ctx, "lorenz_num_points", 1)

    for i in range(num_points):
        # Start in a fairly wide box so we don't get near-symmetric seeds.
        x = (ctx.rng.random() - 0.5) * 6.0        # ~[-3, 3]
        y = (ctx.rng.random() - 0.5) * 6.0        # ~[-3, 3]
        z = 25.0 + (ctx.rng.random() - 0.5) * 6.0  # typical Lorenz z band

        # Burn-in along the attractor so we land on the wings right away.
        base_burn = 350
        extra_burn = int(ctx.rng.random() * 200) + i * 50
        steps = base_burn + extra_burn

        sigma = ctx.lorenz_sigma
        rho = ctx.lorenz_rho
        beta = ctx.lorenz_beta
        dt = ctx.lorenz_dt
        noise = ctx.lorenz_noise

        for _ in range(steps):
            dx = sigma * (y - x)
            dy = x * (rho - z) - y
            dz = x * y - beta * z
            x += dx * dt + (ctx.rng.random() - 0.5) * 2 * noise
            y += dy * dt + (ctx.rng.random() - 0.5) * 2 * noise
            z += dz * dt + (ctx.rng.random() - 0.5) * 2 * noise

        ctx.lorenz_points.append((x, y, z))


def step_lorenz(ctx: Any, steps: int) -> None:
    """Advance Lorenz points by a given number of small steps."""
    if not getattr(ctx, "lorenz_points", None):
        return

    pts: List[Tuple[float, float, float]] = []
    sigma = ctx.lorenz_sigma
    rho = ctx.lorenz_rho
    beta = ctx.lorenz_beta
    dt = ctx.lorenz_dt
    noise = ctx.lorenz_noise

    for (x, y, z) in ctx.lorenz_points:
        for _ in range(steps):
            dx = sigma * (y - x)
            dy = x * (rho - z) - y
            dz = x * y - beta * z
            x += dx * dt + (ctx.rng.random() - 0.5) * 2 * noise
            y += dy * dt + (ctx.rng.random() - 0.5) * 2 * noise
            z += dz * dt + (ctx.rng.random() - 0.5) * 2 * noise
        pts.append((x, y, z))

    ctx.lorenz_points = pts


def advance_lorenz(ctx: Any, level: Any, delta: int) -> None:
    """Advance the strange-attractor aura in lockstep with game time.

    This is basically Game._advance_lorenz extracted, with `self`
    turned into a generic context object.
    """
    # No player / no level? Nothing to do.
    if not getattr(ctx, "levels", None) or ctx.player_id not in level.actors:
        return

    player = ctx._player()
    px, py = player.pos

    # Detect zone changes and large teleports.
    if ctx._lorenz_prev_pos is None:
        ctx._lorenz_prev_pos = (px, py)

    if ctx._lorenz_prev_zone != ctx.zone_coord:
        # New zone → fresh storm (prevents weird smear across staircases).
        ctx.lorenz_points = []

    max_step = max(
        abs(px - ctx._lorenz_prev_pos[0]),
        abs(py - ctx._lorenz_prev_pos[1]),
    )
    if max_step > 2:
        # Big jump (stairs, teleport, whatever) → re-seed the attractor.
        ctx.lorenz_points = []
        ctx.lorenz_reset_trails = True
    ctx._lorenz_prev_pos = (px, py)
    ctx._lorenz_prev_zone = ctx.zone_coord

    if not ctx.lorenz_points:
        init_lorenz_points(ctx)

    # Tie evolution to discrete ticks. delta is in “time units”
    # (cfg.action_time_fast, etc.)
    steps = max(1, int(ctx.lorenz_steps_per_tick * max(1, delta)))
    step_lorenz(ctx, steps)

    # Player is literally the eye of the storm for now (no lag).
    ctx.lorenz_center_x = float(px)
    ctx.lorenz_center_y = float(py)
