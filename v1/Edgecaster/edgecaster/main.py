"""Edgecaster entrypoint."""
from edgecaster import config
from edgecaster.render.ascii import AsciiRenderer
from edgecaster.scenes import SceneManager
from edgecaster.char_creation import run_character_creation


def main() -> None:
    cfg = config.GameConfig()
    rng = new_rng(cfg.seed)
    char = run_character_creation(cfg)
    game = Game(cfg, rng, character=char)
    renderer = AsciiRenderer(cfg.view_width, cfg.view_height, cfg.tile_size)
    manager = SceneManager(cfg, renderer)
    try:
        manager.run()
    finally:
        renderer.teardown()


if __name__ == "__main__":
    main()
