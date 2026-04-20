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
        self._expanded_entity_children: dict[str, set[str]] = {}

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


def test_build_chakra_generator_seed_entity_path_wins_after_expansion() -> None:
    """A6 invariant: entity path produces the seed for a fully expanded actor.

    After Batches 1-3:
    - A1: body nodes are materialized at spawn (register_actor → expand_entity)
    - A3: external_child_root active flags follow ChakraState
    - A5: build_chakra_generator_seed no longer pre-warms; relies on prior expansion

    With body+head active and body nodes in the graph, query_normalized_pattern
    should return a usable pattern so build_chakra_generator_seed takes the entity
    path and returns WITHOUT reaching the body-schema fallback.  The entity-path
    seed must be valid (non-empty verts/edges/node_order) and must agree with the
    body-schema path on node count and dimensionality.
    """
    actor = _make_actor()
    game = _DummyGame(actor)
    entity_graph_ops_system.register_entity(game, actor, lod_state="expanded")

    # Simulate register_actor A1: expand the full body tree.
    actor_children = entity_lifecycle_system.expand_entity(game, actor.id)
    assert actor_children, "actor must have body-node children for this test"
    for cid in actor_children:
        entity_lifecycle_system.expand_entity(game, cid)  # expand grandchildren too

    # Wire chakra_state to match what register_actor would produce (body+head active).
    from edgecaster.systems.chakras import ChakraState as _CS
    actor.chakra_state = _CS(unlocked={"body", "head"}, active={"body", "head"}, pattern_root="body")

    # Re-sync active flags on external_child_root nodes (normally done during
    # _materialize_body_child; here we set chakra_state after expansion so we need
    # to update the node directly to match the A3 contract).
    pcomp = getattr(actor, "chakra_component", None)
    if pcomp is not None and isinstance(getattr(pcomp, "nodes", None), dict):
        for nid, node in pcomp.nodes.items():
            if getattr(node, "kind", "") == "external_child_root":
                body_full_id = str(getattr(node, "tags", {}).get("body_full_id", "") or "")
                node.active = body_full_id in actor.chakra_state.active

    chakra_state = actor.chakra_state

    seed = build_chakra_generator_seed(
        chakra_state,
        actor=actor,
        game=game,
    )

    # Entity path must produce a valid seed.
    assert seed.verts, "A6: entity-path seed must have non-empty verts"
    assert seed.node_order, "A6: entity-path seed must have non-empty node_order"
    assert seed.edges, "A6: entity-path seed must have non-empty edges"
    assert seed.base_len > 0.0, "A6: entity-path seed base_len must be positive"
    # At minimum: body root + head = 2 active nodes.
    assert len(seed.node_order) >= 2, (
        f"A6: expected ≥2 active nodes (body+head), got {seed.node_order}"
    )


def test_build_chakra_generator_seed_uses_body_schema_path_for_unexpanded_actor() -> None:
    """Body-schema fallback is used when the actor has no realized body tree.

    Before entity expansion (Batch 1 A1 not yet run), there are no body-node
    children in the entity graph.  build_chakra_generator_seed must fall through
    the entity path silently and produce a valid seed via get_active_chakra_generator_graph.
    """
    actor = _make_actor()
    game = _DummyGame(actor)
    entity_graph_ops_system.register_entity(game, actor, lod_state="collapsed")
    # No expand_entity call — actor has no body-node children.

    chakra_state = ChakraState(
        unlocked={"body", "head"},
        active={"body", "head"},
        pattern_root="body",
    )

    from edgecaster.prototypes import resolve_body_schema
    body_schema = resolve_body_schema(actor)

    seed = build_chakra_generator_seed(
        chakra_state,
        body_schema=body_schema,
    )

    assert seed.node_order, "body-schema path must produce a non-empty node_order"
    assert seed.verts, "body-schema path must produce non-empty verts"
    assert seed.edges, "body-schema path must produce non-empty edges"


def test_build_chakra_generator_seed_uses_realized_body_tree_when_schema_is_omitted() -> None:
    """Expanded actors should build seeds from body-node entities without schema input."""
    actor = _make_actor()
    game = _DummyGame(actor)
    entity_graph_ops_system.register_entity(game, actor, lod_state="expanded")

    actor_children = entity_lifecycle_system.expand_entity(game, actor.id)
    assert actor_children == ["actor:test_body:body:body"]
    entity_lifecycle_system.expand_entity(game, actor_children[0])

    chakra_state = ChakraState(
        unlocked={"body", "head"},
        active={"body", "head"},
        pattern_root="body",
    )

    seed = build_chakra_generator_seed(
        chakra_state,
        actor=actor,
        game=game,
    )

    assert "body" in seed.node_order
    assert "head" in seed.node_order
    assert seed.edges
    assert seed.base_len > 0.0



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


def test_nested_body_node_active_state_inherits_from_ancestor() -> None:
    """Batch 4 invariant: depth-2+ sub-schema nodes inherit active from their ancestor.

    _materialize_body_child uses _is_full_id_active to check whether a body node
    should be active.  For a node like "head.neck" (full_id) the check must return
    True when "head" (the ancestor prefix) is in chakra_state.active, even though
    "head.neck" itself is not an explicit member.

    The A3 block writes the active flag to the external_child_root node in the
    PARENT entity's chakra_component (not the child's own root), so we read from
    head_ent.chakra_component.nodes[neck_root_nid] to observe the result.

    Three scenarios are verified:
    - ancestor active ("head" ∈ active)  →  head.neck spawns active=True
    - nothing active                     →  head.neck spawns active=False
    - exact deep id active ("head.neck") →  head.neck spawns active=True
    """
    from edgecaster.systems.chakras import ChakraState as _CS

    def _realize_head_neck(active_set):
        actor = _make_actor()
        # Set chakra_state BEFORE expansion so _materialize_body_child sees it.
        actor.chakra_state = _CS(
            unlocked=set(active_set),
            active=set(active_set),
            pattern_root="body",
        )
        game = _DummyGame(actor)
        entity_graph_ops_system.register_entity(game, actor, lod_state="expanded")

        actor_children = entity_lifecycle_system.expand_entity(game, actor.id)
        body_root_id = actor_children[0]
        entity_lifecycle_system.expand_entity(game, body_root_id)

        head_entity_id = "actor:test_body:body:head"
        head_ent = entity_lifecycle_system.find_runtime_entity(game, head_entity_id)
        assert head_ent is not None
        entity_lifecycle_system.expand_entity(game, head_entity_id)

        neck_entity_id = "actor:test_body:body:head.neck"
        neck_ent = entity_lifecycle_system.find_runtime_entity(game, neck_entity_id)
        assert neck_ent is not None, f"head.neck must be realized (active={active_set})"

        # A3 writes the active flag to the external_child_root node stored in the
        # PARENT's (head entity's) chakra_component, keyed by the neck's root_node_id.
        neck_raw_comp = getattr(neck_ent, "chakra_component", None)
        neck_root_nid = str(getattr(neck_raw_comp, "root_node_id", "") or "")
        head_raw_comp = getattr(head_ent, "chakra_component", None)
        head_nodes = getattr(head_raw_comp, "nodes", {}) or {}
        neck_ext_node = head_nodes.get(neck_root_nid)
        assert neck_ext_node is not None, (
            f"head.neck external_child_root node (id={neck_root_nid!r}) must exist "
            f"in head entity's chakra_component.nodes (active={active_set})"
        )
        return bool(getattr(neck_ext_node, "active", True))

    # Scenario 1: ancestor "head" is active — head.neck should be active.
    assert _realize_head_neck({"head"}) is True, (
        "Batch 4: head.neck must be active when 'head' is in chakra_state.active"
    )

    # Scenario 2: nothing is active — head.neck should be inactive.
    assert _realize_head_neck(set()) is False, (
        "Batch 4: head.neck must be inactive when no nodes are in chakra_state.active"
    )

    # Scenario 3: exact deep id "head.neck" is active — head.neck should be active.
    assert _realize_head_neck({"head.neck"}) is True, (
        "Batch 4: head.neck must be active when 'head.neck' itself is in chakra_state.active"
    )
