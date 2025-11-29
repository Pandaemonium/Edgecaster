"""Simple pattern library with a starter glyph."""
from edgecaster.state.patterns import Pattern
from edgecaster.patterns import builder


def starter_pattern() -> Pattern:
    """
    Start from a long line, apply a Koch bump and a subdivision pass,
    producing a small fractal path similar in spirit to the editor default.
    """
    base = builder.line_pattern((-6.0, 0.0), (6.0, 0.0))
    steps = [
        (builder.KochGenerator(height_factor=0.35), 1),
        (builder.SubdivideGenerator(parts=3), 1),
        (builder.BranchGenerator(angle_deg=22.0, length_factor=0.45), 1),
        (builder.JitterGenerator(magnitude_factor=0.05), 1),
    ]
    return builder.apply_chain(base, steps, max_segments=4000, dedup=True)
