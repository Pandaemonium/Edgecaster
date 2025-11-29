import math
import heapq
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

Vec2 = Tuple[float, float]

@dataclass(order=True)
class ScheduledEvent:
    tick: int
    order: int
    callback: Callable[[], None] = field(compare=False)


class Pattern:
    """
    Simplest representation: an ordered polyline of vertices in continuous space.
    Edges are implicit between consecutive vertices.
    """
    def __init__(self) -> None:
        self.points: List[Vec2] = []

    def clear(self) -> None:
        self.points.clear()

    def add_root_and_vertex(self, root: Vec2, target: Vec2) -> None:
        self.points = [root, target]

    def extend(self) -> bool:
        """Extend by repeating last segment direction."""
        if len(self.points) < 2:
            return False
        x1, y1 = self.points[-2]
        x2, y2 = self.points[-1]
        dx, dy = x2 - x1, y2 - y1
        new_pt = (x2 + dx, y2 + dy)
        self.points.append(new_pt)
        return True

    def subdivide_segment(self, index: int, parts: int) -> bool:
        """
        Subdivide segment points[index] -> points[index+1] into 'parts' equal segments.
        Inserts parts-1 intermediate vertices.
        """
        if index < 0 or index >= len(self.points) - 1:
            return False
        if parts < 2:
            return False
        ax, ay = self.points[index]
        bx, by = self.points[index + 1]
        new_points: List[Vec2] = []
        # keep all points up to index
        new_points.extend(self.points[:index+1])
        for i in range(1, parts):
            t = i / parts
            nx = ax + (bx - ax) * t
            ny = ay + (by - ay) * t
            new_points.append((nx, ny))
        # then rest of original points from index+1
        new_points.extend(self.points[index+1:])
        self.points = new_points
        return True

    def apply_simple_fractal(self, index: int) -> bool:
        """
        Replace segment AB with a simple triangular bump (like a single Koch step).
        A -- P1 -- peak -- P3 -- B
        """
        if index < 0 or index >= len(self.points) - 1:
            return False
        ax, ay = self.points[index]
        bx, by = self.points[index + 1]
        # Calculate 1/3 and 2/3 points
        p1 = (ax + (bx - ax) / 3.0, ay + (by - ay) / 3.0)
        p3 = (ax + 2.0 * (bx - ax) / 3.0, ay + 2.0 * (by - ay) / 3.0)
        # Normal vector for the "bump"
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            return False
        # unit perpendicular (rotate 90 degrees)
        nx, ny = -dy / length, dx / length
        # height as some fraction of segment length
        height = length / 4.0
        peak = ((p1[0] + p3[0]) / 2.0 + nx * height,
                (p1[1] + p3[1]) / 2.0 + ny * height)
        # rebuild point list
        new_points: List[Vec2] = []
        new_points.extend(self.points[:index+1])
        new_points.append(p1)
        new_points.append(peak)
        new_points.append(p3)
        new_points.extend(self.points[index+1:])
        self.points = new_points
        return True


class Game:
    def __init__(self, width: int = 20, height: int = 15) -> None:
        self.width = width
        self.height = height
        self.player_x = width // 2
        self.player_y = height // 2
        self.current_tick: int = 0
        self.event_queue: List[ScheduledEvent] = []
        self._event_counter: int = 0
        self.pattern: Optional[Pattern] = None
        self.reach: float = 8.0

    # --- scheduling and time ---

    def schedule(self, delay: int, callback: Callable[[], None]) -> None:
        tick = self.current_tick + delay
        self._event_counter += 1
        heapq.heappush(self.event_queue, ScheduledEvent(tick, self._event_counter, callback))

    def run_due_events(self) -> None:
        while self.event_queue and self.event_queue[0].tick <= self.current_tick:
            ev = heapq.heappop(self.event_queue)
            ev.callback()

    # --- utility ---

    def world_to_grid(self, p: Vec2) -> Tuple[int, int]:
        # Map continuous coordinates to grid cell
        return int(round(p[0])), int(round(p[1]))

    def in_bounds(self, gx: int, gy: int) -> bool:
        return 0 <= gx < self.width and 0 <= gy < self.height

    def can_cast_to(self, target: Vec2) -> bool:
        # range check from player center
        root = (float(self.player_x), float(self.player_y))
        dx = target[0] - root[0]
        dy = target[1] - root[1]
        dist = math.hypot(dx, dy)
        return dist <= self.reach

    # --- game logic actions ---

    def move_player(self, dx: int, dy: int, cost: int = 10) -> None:
        nx = self.player_x + dx
        ny = self.player_y + dy
        if self.in_bounds(nx, ny):
            self.player_x, self.player_y = nx, ny
        self.current_tick += cost

    def cast_vertex(self, target: Vec2, cost: int, speed: str) -> None:
        if not self.can_cast_to(target):
            print("Target out of reach.")
            return

        def do_cast() -> None:
            root = (float(self.player_x), float(self.player_y))
            if self.pattern is None:
                self.pattern = Pattern()
            self.pattern.add_root_and_vertex(root, target)
            print(f"Cast new pattern from {root} to {target}")

        if speed == "fast":
            do_cast()
            self.current_tick += cost
            self.run_due_events()
        else:
            self.schedule(cost, do_cast)
            self.current_tick += cost
            self.run_due_events()

    def extend_pattern(self, cost: int, speed: str) -> None:
        if self.pattern is None:
            print("No pattern to extend.")
            return

        def do_extend() -> None:
            if not self.pattern.extend():
                print("Cannot extend pattern.")
            else:
                print("Extended pattern.")

        if speed == "fast":
            do_extend()
            self.current_tick += cost
            self.run_due_events()
        else:
            self.schedule(cost, do_extend)
            self.current_tick += cost
            self.run_due_events()

    def subdivide_segment(self, index: int, parts: int, cost: int, speed: str) -> None:
        if self.pattern is None:
            print("No pattern to subdivide.")
            return

        def do_subdivide() -> None:
            if self.pattern is None:
                print("Pattern disappeared.")
                return
            if self.pattern.subdivide_segment(index, parts):
                print(f"Subdivided segment {index} into {parts} parts.")
            else:
                print("Failed to subdivide: bad index or parts.")

        if speed == "fast":
            do_subdivide()
            self.current_tick += cost
            self.run_due_events()
        else:
            self.schedule(cost, do_subdivide)
            self.current_tick += cost
            self.run_due_events()

    def fractalize_segment(self, index: int, cost: int, speed: str) -> None:
        if self.pattern is None:
            print("No pattern to fractalize.")
            return

        def do_fractal() -> None:
            if self.pattern is None:
                print("Pattern disappeared.")
                return
            if self.pattern.apply_simple_fractal(index):
                print(f"Applied fractal to segment {index}.")
            else:
                print("Failed to apply fractal: bad index.")

        if speed == "fast":
            do_fractal()
            self.current_tick += cost
            self.run_due_events()
        else:
            self.schedule(cost, do_fractal)
            self.current_tick += cost
            self.run_due_events()

    # --- rendering ---

    def render(self) -> None:
        grid = [["." for _ in range(self.width)] for _ in range(self.height)]
        # draw pattern points
        if self.pattern is not None:
            for i, p in enumerate(self.pattern.points):
                gx, gy = self.world_to_grid(p)
                if self.in_bounds(gx, gy):
                    # mark first point as 'R' root, last as 'V', middle as '*'
                    if i == 0:
                        ch = "R"
                    elif i == len(self.pattern.points) - 1:
                        ch = "V"
                    else:
                        ch = "*"
                    grid[gy][gx] = ch
        # draw player
        grid[self.player_y][self.player_x] = "@"
        # print
        print(f"Tick: {self.current_tick}")
        for row in grid:
            print("".join(row))
        print()
        # if pattern exists, print its points and segments count
        if self.pattern is not None:
            print("Pattern points:")
            for i, (x, y) in enumerate(self.pattern.points):
                print(f"  {i}: ({x:.2f}, {y:.2f})")
            print()

    # --- main loop ---

    def run(self) -> None:
        print("Edgecaster MVP Prototype")
        print("Commands:")
        print("  w/a/s/d         - move")
        print("  cast x y [f|s]  - cast vertex to (x,y) as fast/slow (default fast)")
        print("  extend [f|s]    - extend pattern")
        print("  subdiv idx n [f|s] - subdivide segment idx into n parts")
        print("  fractal idx [f|s]  - apply simple fractal to segment idx")
        print("  wait n          - wait n ticks")
        print("  info            - show pattern info")
        print("  quit            - exit")
        print()

        while True:
            self.run_due_events()
            self.render()
            cmd_line = input("> ").strip()
            if not cmd_line:
                continue
            parts = cmd_line.split()
            cmd = parts[0].lower()

            if cmd in ("q", "quit", "exit"):
                print("Goodbye.")
                break
            elif cmd in ("w", "a", "s", "d"):
                dx, dy = 0, 0
                if cmd == "w":
                    dy = -1
                elif cmd == "s":
                    dy = 1
                elif cmd == "a":
                    dx = -1
                elif cmd == "d":
                    dx = 1
                self.move_player(dx, dy, cost=10)
            elif cmd == "cast":
                if len(parts) < 3:
                    print("Usage: cast x y [f|s]")
                    continue
                try:
                    tx = float(parts[1])
                    ty = float(parts[2])
                except ValueError:
                    print("x and y must be numbers.")
                    continue
                speed = "fast"
                if len(parts) >= 4 and parts[3].lower().startswith("s"):
                    speed = "slow"
                self.cast_vertex((tx, ty), cost=10, speed=speed)
            elif cmd == "extend":
                speed = "fast"
                if len(parts) >= 2 and parts[1].lower().startswith("s"):
                    speed = "slow"
                self.extend_pattern(cost=10, speed=speed)
            elif cmd == "subdiv":
                if len(parts) < 3:
                    print("Usage: subdiv idx parts [f|s]")
                    continue
                try:
                    idx = int(parts[1])
                    n = int(parts[2])
                except ValueError:
                    print("idx and parts must be integers.")
                    continue
                speed = "fast"
                if len(parts) >= 4 and parts[3].lower().startswith("s"):
                    speed = "slow"
                self.subdivide_segment(idx, n, cost=20, speed=speed)
            elif cmd == "fractal":
                if len(parts) < 2:
                    print("Usage: fractal idx [f|s]")
                    continue
                try:
                    idx = int(parts[1])
                except ValueError:
                    print("idx must be integer.")
                    continue
                speed = "fast"
                if len(parts) >= 3 and parts[2].lower().startswith("s"):
                    speed = "slow"
                self.fractalize_segment(idx, cost=25, speed=speed)
            elif cmd == "wait":
                if len(parts) < 2:
                    print("Usage: wait n")
                    continue
                try:
                    n = int(parts[1])
                except ValueError:
                    print("n must be integer.")
                    continue
                self.current_tick += n
                self.run_due_events()
            elif cmd == "info":
                if self.pattern is None:
                    print("No pattern.")
                else:
                    print(
                        f"Pattern has {len(self.pattern.points)} points "
                        f"and {max(0, len(self.pattern.points)-1)} segments."
                    )
            else:
                print("Unknown command.")


if __name__ == "__main__":
    Game().run()
