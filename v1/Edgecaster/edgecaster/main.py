"""Edgecaster entrypoint."""
from edgecaster import config
from edgecaster.game import Game
from edgecaster.rng import new_rng
from edgecaster.render.ascii import AsciiRenderer


def main() -> None:
    cfg = config.GameConfig()
    rng = new_rng(cfg.seed)
    game = Game(cfg, rng)
    renderer = AsciiRenderer(cfg.view_width, cfg.view_height, cfg.tile_size)
    try:
        renderer.render(game)
    finally:
        renderer.teardown()


if __name__ == "__main__":
    main()
