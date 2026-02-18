from __future__ import annotations

from edgecaster import prototypes


def _first_resolve_rule(proto_id: str) -> dict:
    spec = prototypes.resolve_proto(str(proto_id))
    assert isinstance(spec, dict)
    tags = spec.get("tags", {}) or {}
    assert isinstance(tags, dict)
    recipe = tags.get("resolve")
    assert isinstance(recipe, list)
    assert recipe
    first = recipe[0]
    assert isinstance(first, dict)
    return first


def _resolve_children(proto_id: str) -> list[str]:
    first = _first_resolve_rule(proto_id)
    children = first.get("children")
    assert isinstance(children, list)
    return [str(c) for c in children]


def test_starttsgard_resolves_city_neighborhood_buildings_rooms() -> None:
    # Site -> City
    assert _resolve_children("site_starttsgard") == ["city_starttsgard"]
    # City -> Neighborhood
    assert _resolve_children("city_starttsgard") == ["neighborhood_starttsgard_core"]
    # Neighborhood -> Buildings
    assert _resolve_children("neighborhood_starttsgard_core") == [
        "building_item_depot",
        "building_bureau",
        "building_chakric_shrine",
    ]
    # Building -> Room
    assert _resolve_children("building_bureau") == ["room_bureau_main"]
    assert _resolve_children("building_chakric_shrine") == ["room_chakric_shrine_main"]
    assert _resolve_children("building_item_depot") == ["room_item_depot_main"]


def test_world_hierarchy_chain_exists() -> None:
    continent = prototypes.resolve_proto("world_continent")
    assert isinstance(continent, dict)
    continent_tags = continent.get("tags", {}) or {}
    assert isinstance(continent_tags, dict)
    assert continent_tags.get("world_entity") is True
    assert continent_tags.get("aggregate") is True
    assert continent_tags.get("unique_world_root") is True
    assert float(continent_tags.get("worldgen_chance", 0.0) or 0.0) > 0.0

    continent_rule = _first_resolve_rule("world_continent")
    assert str(continent_rule.get("kind", "")) == "children_scaled"
    assert _resolve_children("world_continent") == ["world_region"]
    continent_count = continent_rule.get("count")
    assert isinstance(continent_count, dict)
    assert int(continent_count.get("min", 0) or 0) >= 1
    assert int(continent_count.get("max", 0) or 0) >= int(continent_count.get("min", 0) or 0)

    region_rule = _first_resolve_rule("world_region")
    assert str(region_rule.get("kind", "")) == "children_scaled"
    assert _resolve_children("world_region") == ["world_state"]

    state_rule = _first_resolve_rule("world_state")
    assert str(state_rule.get("kind", "")) == "children_scaled"
    assert _resolve_children("world_state") == ["world_city"]

    city_rule = _first_resolve_rule("world_city")
    assert str(city_rule.get("kind", "")) == "children_scaled"
    assert _resolve_children("world_city") == ["world_neighborhood"]

    neighborhood_rule = _first_resolve_rule("world_neighborhood")
    assert str(neighborhood_rule.get("kind", "")) == "children_scaled"
    neighborhood_children = _resolve_children("world_neighborhood")
    assert "building_fishmonger_shack" in neighborhood_children
    assert "building_leaf_market" in neighborhood_children
    assert "building_hunter_lodge" in neighborhood_children
    n_count = neighborhood_rule.get("count")
    assert isinstance(n_count, dict)
    assert int(n_count.get("min", 0) or 0) >= 1
