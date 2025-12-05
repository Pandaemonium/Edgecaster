from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Tile:
    walkable: bool = True
    visible: bool = False
    explored: bool = False
    glyph: str = "."


def _make_grid(width: int, height: int) -> List[List[Tile]]:
    return [[Tile() for _ in range(width)] for _ in range(height)]


@dataclass
class World:
    width: int
    height: int
    tiles: List[List[Tile]] = field(init=False)
    entry: Tuple[int, int] = field(default=(0, 0))
    up_stairs: Optional[Tuple[int, int]] = None
    down_stairs: Optional[Tuple[int, int]] = None
    is_lab: bool = False

    def __post_init__(self) -> None:
        self.tiles = _make_grid(self.width, self.height)

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def get_tile(self, x: int, y: int) -> Optional[Tile]:
        if not self.in_bounds(x, y):
            return None
        return self.tiles[y][x]

    def is_walkable(self, x: int, y: int) -> bool:
        tile = self.get_tile(x, y)
        return bool(tile and tile.walkable)

    def clear_visibility(self) -> None:
        for row in self.tiles:
            for tile in row:
                tile.visible = False
