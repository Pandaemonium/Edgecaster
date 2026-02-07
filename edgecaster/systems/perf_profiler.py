from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import time
from typing import Any, Callable, Dict


@dataclass
class _WindowStat:
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0


class PerfProfiler:
    """
    Lightweight rolling profiler for live gameplay timing.

    Design goals:
    - Very low overhead when disabled.
    - Emit periodic summaries to debug.log (via game._debug).
    - Easy to remove later (single module + thin call sites).
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        flush_seconds: float = 2.0,
        top_n: int = 8,
        min_avg_ms: float = 0.05,
    ) -> None:
        self.enabled: bool = bool(enabled)
        self.flush_seconds: float = max(0.25, float(flush_seconds))
        self.top_n: int = max(1, int(top_n))
        self.min_avg_ms: float = max(0.0, float(min_avg_ms))
        self._window: Dict[str, _WindowStat] = {}
        self._window_start: float = time.perf_counter()

    def record(self, name: str, elapsed_s: float) -> None:
        if not self.enabled:
            return
        ms = max(0.0, float(elapsed_s) * 1000.0)
        stat = self._window.get(name)
        if stat is None:
            stat = _WindowStat()
            self._window[name] = stat
        stat.count += 1
        stat.total_ms += ms
        if ms > stat.max_ms:
            stat.max_ms = ms

    def maybe_flush(self, debug_log: Callable[[str], None] | None) -> None:
        if not self.enabled or debug_log is None:
            return
        now = time.perf_counter()
        window_s = now - self._window_start
        if window_s < self.flush_seconds:
            return
        if not self._window:
            self._window_start = now
            return

        rows = []
        for name, stat in self._window.items():
            if stat.count <= 0:
                continue
            avg_ms = stat.total_ms / float(stat.count)
            if avg_ms < self.min_avg_ms:
                continue
            rows.append((stat.total_ms, name, avg_ms, stat.max_ms, stat.count))

        rows.sort(reverse=True)
        if rows:
            parts = []
            for _total, name, avg_ms, max_ms, count in rows[: self.top_n]:
                parts.append(f"{name} avg={avg_ms:.2f}ms max={max_ms:.2f}ms n={count}")
            try:
                debug_log(f"[perf] {window_s:.2f}s | " + " | ".join(parts))
            except Exception:
                pass

        self._window.clear()
        self._window_start = now


@contextmanager
def measure(game: Any, name: str):
    """
    Profile a code block against game._perf_profiler, if enabled.

    Usage:
        with perf_profiler.measure(game, "render.query_renderables"):
            ...
    """
    profiler = getattr(game, "_perf_profiler", None)
    if profiler is None or not getattr(profiler, "enabled", False):
        yield
        return

    t0 = time.perf_counter()
    try:
        yield
    finally:
        try:
            profiler.record(name, time.perf_counter() - t0)
            dbg = getattr(game, "_debug", None)
            profiler.maybe_flush(dbg if callable(dbg) else None)
        except Exception:
            # Profiling must never affect gameplay stability.
            pass

