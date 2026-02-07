"""
Lightweight always-on gameplay telemetry.

Writes newline-delimited JSON (NDJSON) records to:
    C:\\Games\\Edgecaster\\telemetry.ndjson

Design goals:
- Very small API surface: one logger with `emit(event, **payload)`.
- Fail-soft: telemetry should never crash gameplay.
- Easy cleanup: all call-sites use Game._telemetry_emit(), so disabling/removing
  telemetry later is a one-file edit plus call-site removal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
import time
import uuid
from typing import Any, Dict


@dataclass
class TelemetryLogger:
    """Simple append-only NDJSON telemetry logger."""

    path: Path
    enabled: bool = True
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_path(cls, path: str | Path, *, enabled: bool = True) -> "TelemetryLogger":
        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return cls(path=p, enabled=enabled)

    def emit(self, event: str, **payload: Any) -> None:
        """Append a single telemetry event."""
        if not self.enabled:
            return
        record: Dict[str, Any] = {
            "ts_unix": time.time(),
            "session": self.session_id,
            "event": str(event),
        }
        record.update(payload)
        line = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            # Telemetry is diagnostic; never break gameplay for logging failures.
            return

