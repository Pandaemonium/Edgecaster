from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Callable, List, Optional

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

def effect_mysterious_stranger(choice_index: int, game) -> None:
    """
    Beggarly Vagrant root interaction.

    choice_index 0: "Give him a berry"
        - If player has a berry: consume one and go to the generous path.
        - If not: fall through to the refusal-style path.

    choice_index 1: "Nothing to spare"
        - Go straight to the refusal path.
    """
    if choice_index == 0:
        # "Give him a berry"
        if _player_has_berry(game):
            _consume_one_berry(game)
            # Go to the grateful response
            start_dialogue(game, MYSTERIOUS_STRANGER_DIALOGUE, start_node="path1")
        else:
            # Tried to give, but empty hands → treat as option 2
            start_dialogue(game, MYSTERIOUS_STRANGER_DIALOGUE, start_node="path2")
    else:
        # "Nothing to spare"
        start_dialogue(game, MYSTERIOUS_STRANGER_DIALOGUE, start_node="path2")


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
        choices=["Give him a berry", "Nothing to spare"],
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

def _player_has_berry(game) -> bool:
    """Returns True if any item in inventory is tagged as a berry."""
    for ent in getattr(game, "inventory", []):
        tags = getattr(ent, "tags", {}) or {}
        if tags.get("test_berry") or tags.get("item_type") in {
            "blueberry", "raspberry", "strawberry"
        }:
            return True
    return False


def _consume_one_berry(game) -> bool:
    """Removes exactly one berry from inventory. Returns True if one was removed."""
    inv = getattr(game, "inventory", [])
    for i, ent in enumerate(inv):
        tags = getattr(ent, "tags", {}) or {}
        if tags.get("test_berry") or tags.get("item_type") in {
            "blueberry", "raspberry", "strawberry"
        }:
            inv.pop(i)
            return True
    return False


def effect_bless_player(game):
    """Apply the temporary 'blessed' status to the player."""
    player = game._player()
    # Let's say blessed lasts 200 ticks for now — you can tune it later.
    game._add_status(player, "blessed", duration=200,
                     on_apply="A quiet warmth settles over you. You feel blessed.")


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
                    effect=effect_bless_player,  # grants 'blessed'
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
    },
)
