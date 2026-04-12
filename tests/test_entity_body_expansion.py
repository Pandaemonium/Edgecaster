from __future__ import annotations

from types import SimpleNamespace

from edgecaster import prototypes
from edgecaster import spawn_factory
from edgecaster.state.entity_graph import EntityGraphStore
from edgecaster.systems import attention
from edgecaster.systems import chakra_items as chakra_items_system
from edgecaster.systems import entity_geometry as entity_geometry_system
from edgecaster.systems import entity_body as entity_body_system
from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
from edgecaster.systems import entity_lifecycle as entity_lifecycle_system
from edgecaster.systems.chakras import ChakraState, build_chakra_generator_seed


class _DummyGame:
    def __init__(self, actor: object) -> None:
        self.cfg = SimpleNamespace(world_width=64, world_height=64, seed=789)
        self.entity_graph = EntityGraphStore()
        self.entity_state: dict[str, dict] = {}
        self.attn_store = attention.AttentionCellStore(bin_size=16)
        self.level = SimpleNamespace(
            entities={getattr(actor, "id"): actor},
            actors={getattr(actor, "id"): actor},
            spatial_dirty=False,
        )
        self.levels = {(0, 0, 0): self.level}
        self._attn_active_resolved_children: dict[str, set[str]] = {}

    def patch_entity_state(self, entity_or_id, patch=None, *, lineage_id=None, **fields) -> None:
        key = str(entity_or_id)
        state = dict(self.entity_state.get(key, {}) or {})
        if isinstance(patch, dict):
            state.update(dict(patch))
        if fields:
            state.update(dict(fields))
        if lineage_id:
            state["lineage_id"] = str(lineage_id)
        self.entity_state[key] = state

    def get_effective_entity_state(self, entity_or_id, *, lineage_id=None) -> dict:
        key = str(entity_or_id)
        state = dict(self.entity_state.get(key, {}) or {})
        if lineage_id:
            fallback = self.entity_state.get(str(lineage_id), {})
            if isinstance(fallback, dict):
                merged = dict(fallback)
                merged.update(state)
                return merged
        return state

    def get_zone_for_render(self, coord):  # noqa: ANN001
        return self.levels.get(tuple(coord))


def _make_actor():
    spec = prototypes.resolve_proto("human_base")
    actor = spawn_factory.build_actor_from_spec(
        spec=spec,
        aid="actor:test_body",
        pos=(10, 10),
        abs_pos=(10, 10),
    )
    actor.zone_coord = (0, 0, 0)
    return actor


def _make_feature(proto_id: str, *, entity_id: str) -> object:
    spec = prototypes.resolve_proto(proto_id)
    ent = spawn_factory.build_entity_from_spec(
        spec=spec,
        eid=entity_id,
        pos=(10, 10),
        abs_pos=(10, 10),
    )
    ent.zone_coord = (0, 0, 0)
    return ent


def test_actor_body_expands_in_layers_and_collapses_cleanly() -> None:
    actor = _make_actor()
    game = _DummyGame(actor)
    entity_graph_ops_system.register_entity(game, actor, lod_state="expanded")

    root_children = entity_lifecycle_system.expand_entity(game, actor.id)
    assert root_children == ["actor:test_body:body:body"]

    body_id = root_children[0]
    body_children = set(entity_lifecycle_system.expand_entity(game, body_id))
    assert body_children == {
        "actor:test_body:body:arm",
        "actor:test_body:body:arm_m",
        "actor:test_body:body:body.torso",
        "actor:test_body:body:head",
        "actor:test_body:body:leg",
        "actor:test_body:body:leg_m",
    }

    head_children = entity_lifecycle_system.expand_entity(game, "actor:test_body:body:head")
    assert head_children == ["actor:test_body:body:head.neck"]

    arm_children = entity_lifecycle_system.expand_entity(game, "actor:test_body:body:arm")
    assert arm_children == ["actor:test_body:body:arm.shoulder"]

    assert entity_lifecycle_system.find_runtime_entity(game, "actor:test_body:body:head.neck") is not None
    assert entity_lifecycle_system.find_runtime_entity(game, "actor:test_body:body:arm.shoulder") is not None

    entity_lifecycle_system.collapse_entity(game, actor.id)

    assert entity_lifecycle_system.find_runtime_entity(game, body_id) is None
    assert game.entity_graph.get_node(body_id) is None
    assert game.entity_graph.get_children(actor.id, socket_id="body") == []


def test_actor_body_query_geometry_require_realizes_full_subtree() -> None:
    actor = _make_actor()
    game = _DummyGame(actor)
    entity_graph_ops_system.register_entity(game, actor, lod_state="expanded")

    result = entity_geometry_system.query_geometry(
        game,
        actor.id,
        realize_policy="require",
    )

    ids = {str(node.get("id", "")) for node in result["nodes"]}
    assert result["precision"] == "exact"
    assert "entity:actor:test_body" in ids
    assert "entity:actor:test_body:body:body" in ids
    assert "entity:actor:test_body:body:body.torso" in ids
    assert "entity:actor:test_body:body:arm.shoulder" in ids
    assert "entity:actor:test_body:body:head.neck" in ids


def test_zero_radius_cluster_keeps_starttsgard_city_on_site_anchor() -> None:
    site = _make_feature("site_starttsgard", entity_id="site:test_starttsgard")
    game = _DummyGame(site)
    game.level.actors = {}
    entity_graph_ops_system.register_entity(game, site, lod_state="collapsed")

    child_ids = entity_lifecycle_system.expand_entity(game, site.id)

    assert len(child_ids) == 1
    child = entity_lifecycle_system.find_runtime_entity(game, child_ids[0])
    assert child is not None
    assert getattr(child, "abs_pos", None) == (10, 10)


def test_build_chakra_generator_seed_prewarms_entity_tree() -> None:
    """B1 invariant: seed builder expands the entity tree before building the seed.

    Body-anatomy offsets are sub-tile floats that collapse to the same integer
    grid cell under rounding, so entity abs_pos is not a viable geometry source.
    Geometry comes from the body-schema float-position path.  The B1 contract
    is that build_chakra_generator_seed triggers expand_entity, making body-node
    entities available in the graph for later B2 unlock/active state reads.
    """
    actor = _make_actor()
    game = _DummyGame(actor)
    entity_graph_ops_system.register_entity(game, actor, lod_state="expanded")

    body_schema = getattr(actor, "body_schema", None) or prototypes.resolve_body_schema(actor) or {}
    # Need >= 2 connected active nodes for the body-schema geometry path.
    # pattern_root must be set (require_root=True default).
    chakra_state = ChakraState(
        unlocked={"body", "head"},
        active={"body", "head"},
        pattern_root="body",
    )

    seed = build_chakra_generator_seed(
        body_schema,
        chakra_state,
        actor=actor,
        game=game,
    )

    # Seed geometry comes from the body-schema path and must be valid.
    assert seed.verts, "seed.verts should be non-empty"
    assert seed.node_order, "seed.node_order should be non-empty"

    # B1 invariant: expand_entity was triggered by build_chakra_generator_seed,
    # so the direct body-schema-root child must now exist in the entity graph.
    body_child_id = "actor:test_body:body:body"
    assert game.entity_graph.get_node(body_child_id) is not None, (
        "B1: build_chakra_generator_seed must pre-warm the entity tree "
        "(body-node child should exist in entity graph after seed build)"
    )


def test_toggle_actor_chakra_mirrors_active_state_to_body_node_entity() -> None:
    """B2 invariant: toggle_actor_chakra writes active state to realized body-node entity.

    When the entity tree is realized (body-node children exist in the runtime
    registry), toggle_actor_chakra(game=game) should update the body-node
    entity's chakra_component root node's active flag, not just the actor's.
    """
    actor = _make_actor()
    game = _DummyGame(actor)
    entity_graph_ops_system.register_entity(game, actor, lod_state="expanded")

    # Realize the first body level (body root entity) and its children.
    actor_children = entity_lifecycle_system.expand_entity(game, actor.id)
    assert actor_children, "actor must have body-node children"
    body_root_id = actor_children[0]
    # Expand body root to realize head, arm, leg, etc.
    entity_lifecycle_system.expand_entity(game, body_root_id)

    # Verify head entity exists in runtime.
    head_entity_id = "actor:test_body:body:head"
    head_ent = entity_lifecycle_system.find_runtime_entity(game, head_entity_id)
    assert head_ent is not None, "head body-node entity must be realized before B2 test"

    # Head's chakra_component root node should start as active=True (default).
    head_comp = getattr(head_ent, "chakra_component", None)
    assert head_comp is not None
    from edgecaster.state import chakra_component as chakra_component_state
    head_comp = chakra_component_state.coerce_chakra_component(head_comp, entity_id=head_entity_id)
    root_nid = head_comp.root_node_id
    assert bool(getattr(head_comp.nodes[root_nid], "active", True)) is True

    # Unlock and ensure head is in actor's component first (required for toggle).
    chakra_items_system.unlock_actor_chakra(actor, "head", auto_activate=True, game=game)

    # Now deactivate via toggle — should mirror to body-node entity.
    chakra_items_system.toggle_actor_chakra(actor, "head", active=False, game=game)

    # Re-read body-node entity's chakra_component.
    head_comp_after = chakra_component_state.coerce_chakra_component(
        getattr(head_ent, "chakra_component", None), entity_id=head_entity_id
    )
    assert bool(getattr(head_comp_after.nodes[root_nid], "active", True)) is False, (
        "B2: toggle_actor_chakra must write active=False to body-node entity's chakra_component"
    )

    # Re-activate.
    chakra_items_system.toggle_actor_chakra(actor, "head", active=True, game=game)
    head_comp_reactivated = chakra_component_state.coerce_chakra_component(
        getattr(head_ent, "chakra_component", None), entity_id=head_entity_id
    )
    assert bool(getattr(head_comp_reactivated.nodes[root_nid], "active", True)) is True, (
        "B2: toggle_actor_chakra must write active=True to body-node entity's chakra_component"
    )


def test_generic_resolve_entities_do_not_take_actor_body_expansion_path() -> None:
    city = _make_feature("city_starttsgard", entity_id="feature:test_city")
    game = _DummyGame(city)
    game.level.actors = {}
    entity_graph_ops_system.register_entity(game, city, lod_state="collapsed")

    assert entity_body_system.can_expand_entity(city) is False

    child_ids = entity_lifecycle_system.expand_entity(game, city.id)

    assert len(child_ids) == 1
    assert ":body:" not in child_ids[0]
    child = entity_lifecycle_system.find_runtime_entity(game, child_ids[0])
    assert child is not None
    assert getattr(child, "name", "") == "Starttsgard Core Neighborhood"
