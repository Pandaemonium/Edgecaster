from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Callable, List, Optional
from edgecaster.visuals import VisualProfile


@dataclass
class DialogueChoice:
    text: str
    # id of the next node; None means “end dialogue”
    next_id: Optional[str] = None
    # optional side-effect when this choice is picked
    effect: Optional[Callable[[object], None]] = None


@dataclass
class DialogueNode:
    id: str
    title: str
    body: str
    choices: List[DialogueChoice]


@dataclass
class DialogueTree:
    id: str
    start_id: str
    nodes: Dict[str, DialogueNode]


@dataclass
class Event:
    id: str
    title: str
    body: str
    choices: List[str]
    # effect(choice_index, game)
    effect: Callable[[int, object], None]
    weight: int = 1
    overworld_only: bool = True  # defaults to overworld; easy to relax later
    depth_filter: Optional[int] = None  # if not None, only fire on that depth


# --- Effects -------------------------------------------------------------


def effect_alligator(choice_index: int, game) -> None:
    """Lose 50% of current HP."""
    player = game._player()
    hp_before = player.stats.hp
    damage = max(1, hp_before // 2)
    player.stats.hp = max(0, hp_before - damage)
    player.stats.clamp()
    game.log.add("The alligator mauls your leg! You lose half your health.")

    if player.stats.hp <= 0:
        game.set_urgent(
            "by way of a surprise alligator.",
            title="You unravel...",
            choices=["Continue..."],
        )


def effect_imps_aplenty(choice_index: int, game) -> None:
    """Spawn 3–8 imps on nearby tiles."""
    level = game._level()
    player = game._player()
    num = game.rng.randint(3, 8)
    spawned = game._spawn_imps_near(level, player.pos, num)

    if spawned > 0:
        suffix = "" if spawned == 1 else "s"
        game.log.add(
            f"A cackle of {spawned} imp{suffix} crawls out of the yawning earth!"
        )
    else:
        game.log.add("The ground shudders, but nothing emerges...")


def effect_berry_glut(choice_index: int, game) -> None:
    """Spawn a glut of berries around the player."""
    level = game._level()
    player = game._player()
    # "10ish" berries: 8–12 feels about right.
    num = game.rng.randint(8, 12)
    spawned = game._spawn_berries_near(level, player.pos, num)

    if spawned > 0:
        suffix = "" if spawned == 1 else "es"
        game.log.add(
            f"The season ripens in an instant. {spawned} berry bush{suffix} burst into being around you."
        )
    else:
        game.log.add("The foliage shivers, but nothing seems to take root...")

def effect_mysterious_stranger(choice_index: int, game: "Game") -> None:
    """
    choice_index 0: "Give him a berry"
        - If the player has a berry anywhere in their inventory, path1 (blessing).
        - Otherwise, path2 (you fail him).

    choice_index 1: "Nothing to spare"
        - path2

    choice_index 2: "Pluck the eyeballs from the beggar's skull and eat them like berries."
        - path3 + Cursed status + mirrored world
    """
    if choice_index == 0:
        if _player_has_berry(game):
            effect_bless_player(game)
            start_dialogue(game, MYSTERIOUS_STRANGER_DIALOGUE, start_node="path1")
        else:
            start_dialogue(game, MYSTERIOUS_STRANGER_DIALOGUE, start_node="path2")
    elif choice_index == 1:
        start_dialogue(game, MYSTERIOUS_STRANGER_DIALOGUE, start_node="path2")
    elif choice_index == 2:
        start_dialogue(game, MYSTERIOUS_STRANGER_DIALOGUE, start_node="path3")




# --- Event table ---------------------------------------------------------


EVENTS: List[Event] = [
    Event(
        id="alligator",
        title="Holy Christ an alligator!",
        body="An alligator leaps out of fucking nowhere and snaps you on the leg.",
        choices=["Damn, that sucks"],
        effect=effect_alligator,
        weight=1,
        overworld_only=True,
    ),
    Event(
        id="imps_aplenty",
        title="Imps aplenty",
        body="A cackle of imps erupts from a yawn in the earth.",
        choices=["Foul vermin!"],
        effect=effect_imps_aplenty,
        weight=1,
        overworld_only=True,
    ),
    Event(
        id="berry_glut",
        title="Berry glut",
        body="The season is ripe for propagation.",
        choices=["Impressive foliage..."],
        effect=effect_berry_glut,
        weight=1,
        overworld_only=True,
    ),
    Event(
        id="beggarly_vagrant_event",
        title="Beggarly Vagrant",
        body=(
            "A tall man with kingly bearing, crusted and worn by years of regret.\n"
            "\"I am cursed to wander these fruitless lands. Will you quench my longing?\""
        ),
        choices=["Give him a berry", "Nothing to spare", "Pluck the eyeballs from his skull and eat them like berries"],
        effect=effect_mysterious_stranger,
        weight=1,
        overworld_only=True,
    ),


]


def pick_random_event(game) -> Optional[Event]:
    """Pick a weighted random event that matches the current zone, or None."""
    depth = game.zone_coord[2]

    # Filter by depth / overworld flags
    eligible: List[Event] = []
    for ev in EVENTS:
        if ev.overworld_only and depth != 0:
            continue
        if ev.depth_filter is not None and ev.depth_filter != depth:
            continue
        eligible.append(ev)

    if not eligible:
        return None

    total = sum(ev.weight for ev in eligible)
    r = game.rng.random() * total
    for ev in eligible:
        r -= ev.weight
        if r <= 0:
            return ev

    return eligible[-1]
    
    
def _open_dialogue_node(game, tree: DialogueTree, node_id: str) -> None:
    """
    Legacy urgent-popup fallback for dialogues.

    If there's a SceneManager attached, delegate to start_dialogue(...) so
    we use the proper DialoguePopupScene and stay on a single window
    instead of nesting urgent popups. Only use the urgent popup path when
    there is *no* scene manager (e.g. tests / headless mode).
    """
    manager = getattr(game, "scene_manager", None)
    if manager is not None:
        # Re-enter the "real" dialogue path starting from this node.
        start_dialogue(game, tree, start_node=node_id)
        return

    # --- Original urgent-popup behaviour (no manager case) ---
    node = tree.nodes[node_id]

    def on_choice(choice_index: int, g) -> None:
        """Callback used by the urgent popup; signature matches Event.effect."""
        choice = node.choices[choice_index]

        # Run any per-choice effect
        if choice.effect is not None:
            choice.effect(g)

        # Advance to the next node if there is one
        if choice.next_id is not None:
            _open_dialogue_node(g, tree, choice.next_id)
        # else: dialogue ends here; just return to the previous scene

    game.set_urgent(
        node.body,
        title=node.title,
        choices=[c.text for c in node.choices],
        on_choice_effect=on_choice,
    )



def start_dialogue(game: "Game", tree: DialogueTree, start_node: Optional[str] = None) -> None:
    """Entry point for triggering a dialogue from game logic.

    If start_node is given, begin at that node instead of tree.start_id.
    """
    manager = getattr(game, "scene_manager", None)
    entry = start_node or tree.start_id

    # If there's no scene manager attached, fall back to the legacy urgent popup.
    if manager is None:
        game.log.add("(Dialogue fallback: no scene manager attached.)")
        _open_dialogue_node(game, tree, entry)
        return

    try:
        from edgecaster.scenes.dialogue_scene import DialoguePopupScene

        manager.open_window_scene(
            DialoguePopupScene,
            game=game,
            tree=tree,
            node_id=entry,
            scale=0.7,
        )
    except Exception as e:
        game.log.add(f"(Dialogue error: {e!r})")
        _open_dialogue_node(game, tree, entry)






# --- Helper effects -------------------------------------------------------

def _iter_all_inventory_items(game, owner_id: Optional[str] = None, _visited: Optional[set[str]] = None):
    """
    Yield (owner_id, item) for every item reachable from the player’s inventory,
    including items inside containers, recursively.

    - Starts from the current player’s inventory by default.
    - Walks through container inventories via game.inventories[entity.id].
    - Uses a visited set of owner_ids to avoid infinite loops
      (e.g. recursive Inventory that contains itself).
    """
    if _visited is None:
        _visited = set()

    # Initial root: the current host (player) inventory
    if owner_id is None:
        owner_id = getattr(game, "player_id", None)
        if owner_id is None:
            return
        # Use the new per-player inventory property if available
        root_inv = getattr(game, "player_inventory", None)
        if root_inv is None:
            # Fallback for any legacy paths
            root_inv = getattr(game, "inventory", []) or []
    else:
        if owner_id in _visited:
            return
        # Use the unified inventory registry if present
        if hasattr(game, "get_inventory"):
            root_inv = game.get_inventory(owner_id)
        else:
            root_inv = getattr(game, "inventory", []) or []

    if owner_id in _visited:
        return
    _visited.add(owner_id)

    for ent in root_inv:
        yield owner_id, ent

        # If this item itself owns an inventory, recurse into it.
        eid = getattr(ent, "id", None)
        if eid and hasattr(game, "inventories") and eid in getattr(game, "inventories", {}):
            yield from _iter_all_inventory_items(game, eid, _visited)


def _player_has_berry(game) -> bool:
    """Returns True if any item anywhere in the player's inventory tree is a berry."""
    for owner_id, ent in _iter_all_inventory_items(game):
        tags = getattr(ent, "tags", {}) or {}
        if tags.get("test_berry") or tags.get("item_type") in {
            "blueberry",
            "raspberry",
            "strawberry",
        }:
            return True
    return False



def _consume_one_berry(game) -> bool:
    """
    Removes exactly one berry from the player's inventory tree.

    Returns True if a berry was found and removed, False otherwise.
    """
    # First pass: find the berry and remember which owner's inventory it's in.
    for owner_id, ent in _iter_all_inventory_items(game):
        tags = getattr(ent, "tags", {}) or {}
        if tags.get("test_berry") or tags.get("item_type") in {
            "blueberry",
            "raspberry",
            "strawberry",
        }:
            # Second pass: actually pop it from that specific inventory list.
            if hasattr(game, "get_inventory"):
                inv = game.get_inventory(owner_id)
            else:
                inv = getattr(game, "inventory", []) or []

            for i, e in enumerate(inv):
                if e is ent:
                    inv.pop(i)
                    return True
            # If for some reason we didn't find it to pop, keep searching.
    return False



def effect_bless_player(game: "Game") -> None:
    """
    Apply / refresh the Blessed status.

    Also:
    - If the player was Cursed, remove that status.
    - Clear any global visual profile (e.g. un-mirror the world).
    """
    lvl = game._level()
    player = lvl.actors.get(game.player_id)
    if player is None:
        return

    # If you were cursed, remove that status.
    if hasattr(player, "statuses") and "cursed" in player.statuses:
        del player.statuses["cursed"]

    # Clear any global visual profile (un-mirror the world).
    manager = getattr(game, "scene_manager", None)
    if manager is not None and hasattr(manager, "set_global_visual_profile"):
        manager.set_global_visual_profile(None)

    # Apply / refresh the 'blessed' status.
    if not game._has_status(player, "blessed"):
        game._add_status(
            player,
            "blessed",
            duration=200,
            on_apply="You feel a gentle warmth settle over you.",
        )
    else:
        # Refresh / extend blessing duration
        if "blessed" in player.statuses:
            player.statuses["blessed"] = max(
                player.statuses.get("blessed", 0),
                200,
            )




def effect_curse_player(game: "Game") -> None:
    """
    Apply the Cursed status and trigger the global mirrored visual.

    This is invoked from the 'eat the eyeballs' choice.
    """
    lvl = game._level()
    player = lvl.actors.get(game.player_id)
    if player is None:
        return

    # Add a long-lived 'cursed' status. Duration is effectively "until cleansed".
    if not game._has_status(player, "cursed"):
        game._add_status(
            player,
            "cursed",
            duration=10_000,  # very long; effectively permanent for now
            on_apply="A vertiginous chill grips you. The world feels subtly wrong.",
        )

    # If we have a live scene manager, ask it to flip the entire game horizontally.
    manager = getattr(game, "scene_manager", None)
    if manager is not None and hasattr(manager, "set_global_visual_profile"):
        manager.set_global_visual_profile(VisualProfile(flip_x=True))

    # IMPORTANT: do NOT call start_dialogue() here.
    # The DialoguePopupScene sees next_id=None for this choice,
    # so it will close itself after this effect runs.




# --- Adaptive Choice Effect ----------------------------------------------

def effect_give_berry(game):
    """
    Attempt to give him a berry.

    - If the player has a berry: consume one and go to 'path1'.
    - If not: behave like the refusal path and go to 'path2'.
    """
    if _player_has_berry(game):
        _consume_one_berry(game)
        return "path1"
    else:
        return "path2"



# --- Updated Dialogue Tree ------------------------------------------------

def start_dialogue(game: "Game", tree: DialogueTree, start_node: Optional[str] = None) -> None:
    """Entry point for triggering a dialogue from game logic.

    If start_node is given, begin at that node instead of tree.start_id.
    """
    manager = getattr(game, "scene_manager", None)
    entry = start_node or tree.start_id

    # If there's no scene manager attached, fall back to the legacy urgent popup.
    if manager is None:
        game.log.add("(Dialogue fallback: no scene manager attached.)")
        _open_dialogue_node(game, tree, entry)
        return

    try:
        from edgecaster.scenes.dialogue_scene import DialoguePopupScene

        manager.open_window_scene(
            DialoguePopupScene,
            game=game,
            tree=tree,
            node_id=entry,
            scale=0.7,
        )
    except Exception as e:
        game.log.add(f"(Dialogue error: {e!r})")
        _open_dialogue_node(game, tree, entry)



MYSTERIOUS_STRANGER_DIALOGUE = DialogueTree(
    id="beggarly_vagrant",
    # Default start_id is mostly irrelevant now because we always pass start_node,
    # but keep something sensible as a fallback:
    start_id="path2",
    nodes={
        # ------------------------------------------------------
        # PATH IF YOU SUCCESSFULLY GIVE A BERRY
        # ------------------------------------------------------
        "path1": DialogueNode(
            id="path1",
            title="A Quiet Benediction",
            body=(
                "Even the smallest gesture may bring incommensurate joy.\n"
                "The man beams with renewed vigor and ineffable gratitude."
            ),
            choices=[
                DialogueChoice(
                    text="May you find solace.",
                    next_id=None,
                ),
            ],
        ),

        # ------------------------------------------------------
        # PATH IF YOU REFUSE OR CANNOT GIVE
        # ------------------------------------------------------
        "path2": DialogueNode(
            id="path2",
            title="Parting of Beggars",
            body=(
                "His brow furrows, his eyes soften, long since lost the capacity of judgment.\n"
                "\"I expected no more, and no less. Farewell, fellow beggar.\""
            ),
            choices=[
                DialogueChoice(
                    text="Part ways.",
                    next_id=None,
                ),
            ],
        ),
        "path3": DialogueNode(
            id="path3",
            title="A Feast of Eyeballs",
            body=(
                "The man offers no resistance, welcoming blindness as a mercy.\n\n"
                "The eyeberries taste tart.\n\n"
                "Your vision swims as you take a view from a new perspective..."
            ),
            choices=[
                DialogueChoice("Twice the eyeballs...", next_id=None, effect = effect_curse_player),
            ],
        ),
    },
)
