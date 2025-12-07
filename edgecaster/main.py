from edgecaster import config
from edgecaster.engine import Engine


def main() -> None:
    cfg = config.GameConfig()
    engine = Engine(cfg)
    engine.run()


if __name__ == "__main__":
    main()
