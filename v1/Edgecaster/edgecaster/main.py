from edgecaster import config
from edgecaster.render.ascii import AsciiRenderer
from edgecaster.scenes import SceneManager  # your scene manager
from edgecaster.rng import new_rng
# (char creation is now a scene, so no direct call here)


def main() -> None:
    cfg = config.GameConfig()
    renderer = AsciiRenderer(cfg.view_width, cfg.view_height, cfg.tile_size)
    manager = SceneManager(cfg, renderer)  # pass renderer in

    try:
        manager.run()
    finally:
        renderer.teardown()  # <- this is the ONLY place that quits pygame


if __name__ == "__main__":
    main()
