import random
from typing import Optional

class RNG(random.Random):
    """Seeded RNG to keep deterministic behavior."""


def new_rng(seed: Optional[int] = None) -> RNG:
    rng = RNG()
    rng.seed(seed)
    return rng
