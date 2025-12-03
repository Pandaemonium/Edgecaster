from edgecaster import config
from edgecaster.render.ascii import AsciiRenderer
from edgecaster.scenes import SceneManager
from edgecaster.rng import new_rng


def main() -> None:
    cfg = config.GameConfig()
    renderer = AsciiRenderer(cfg.view_width, cfg.view_height, cfg.tile_size)
    manager = SceneManager(cfg, renderer)

    try:
        manager.run()
    finally:
        renderer.teardown()


if __name__ == "__main__":
    main()
