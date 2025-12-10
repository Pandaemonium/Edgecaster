"""Visual profile helpers for rendering adjustments."""
from dataclasses import dataclass


@dataclass
class VisualProfile:
    scale_x: float = 1.0
    scale_y: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    angle: float = 0.0
    alpha: float = 1.0
    flip_x: bool = False
    flip_y: bool = False
