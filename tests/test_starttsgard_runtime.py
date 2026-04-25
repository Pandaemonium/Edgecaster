from __future__ import annotations

from edgecaster.game import Game
from edgecaster.systems import attention as attention_system


def test_starttsgard_attention_sync_realizes_walls_and_npcs() -> None:
    game = Game()
    level = game._level()

    zone_w = level.world.width
    zone_h = level.world.height
    zx, zy, _zz = game.zone_coord
    abs_rect = (zx * zone_w, zy * zone_h, (zx + 1) * zone_w, (zy + 1) * zone_h)

    attention_system.sync_attention_instantiation(game, abs_rect, cam_lod=0.0)

    wall_count = sum(
        1
        for ent in level.entities.values()
        if isinstance(getattr(ent, "tags", None), dict) and bool(ent.tags.get("wall"))
    )
    npc_count = sum(
        1
        for actor in level.actors.values()
        if isinstance(getattr(actor, "tags", None), dict) and bool(actor.tags.get("npc"))
    )

    assert wall_count > 0
    assert npc_count > 0
