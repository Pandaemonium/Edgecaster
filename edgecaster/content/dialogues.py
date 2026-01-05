from __future__ import annotations

from typing import Any, Callable

from edgecaster.events import DialogueChoice, DialogueNode, DialogueTree

from . import npcs as npc_content


def _npc_name(npc: Any, npc_def: dict) -> str:
    return str(npc_def.get("name") or getattr(npc, "name", "") or "Someone")


def _npc_dialogue_body(npc: Any, npc_def: dict) -> str:
    lines = list(npc_def.get("dialogue", []) or [])
    if lines:
        return "\n".join(str(x) for x in lines)
    return f"{getattr(npc, 'name', 'Someone')} waits patiently."


def _has_active_quest(game: Any, proto_id: str) -> bool:
    try:
        return any(q.proto_id == proto_id for q in getattr(game, "active_quests", {}).values())
    except Exception:
        return False


def _has_completed_quest(game: Any, proto_id: str) -> bool:
    try:
        return proto_id in (getattr(game, "completed_quests", []) or [])
    except Exception:
        return False


def _effect_open_fractal_editor(state_builder: Callable[[], Any], note: str) -> Callable[[Any], None]:
    def effect(game: Any) -> None:
        try:
            game.fractal_editor_state = state_builder()
            game.fractal_editor_requested = True
        except Exception:
            pass
        try:
            game.log.add(str(note))
        except Exception:
            pass

    return effect


def _effect_grant_generator(gen: str) -> Callable[[Any], None]:
    def effect(game: Any) -> None:
        try:
            owned = getattr(game, "unlocked_generators", None)
            if owned is None:
                owned = []
                game.unlocked_generators = owned
            if gen in owned:
                return
            owned.append(gen)
            if getattr(game, "character", None) is not None:
                game.character.generator = gen
        except Exception:
            pass

        try:
            if hasattr(game, "grant_ability"):
                game.grant_ability(gen)
        except Exception:
            pass

        try:
            game.log.add(f"{gen.title()} added to your repertoire.")
        except Exception:
            pass

    return effect


def _effect_accept_quest(proto_id: str, *, quest_location: tuple[int, int] | None, dialogue_tail: str | None) -> Callable[[Any], None]:
    def effect(game: Any) -> None:
        try:
            # Don't duplicate the quest if already active.
            if _has_active_quest(game, proto_id):
                return
            from edgecaster.systems import quests as quest_system

            quest = quest_system.create_quest_from_proto(proto_id, game._level().current_tick)
            if quest_location is not None:
                quest.known_locations.append(tuple(quest_location))
            if dialogue_tail:
                quest.dialogue_history.append(str(dialogue_tail))

            game.active_quests[quest.id] = quest
            game.log.add(f"Quest accepted: {quest.name}")
            game.log.add("Press J to view your journal for active quests.")
        except Exception as e:
            try:
                game.log.add(f"(Quest error: {e!r})")
            except Exception:
                pass

    return effect


def _effect_complete_quest_dialogue(npc_id: str, dialogue_head: str | None) -> Callable[[Any], None]:
    def effect(game: Any) -> None:
        try:
            from edgecaster.systems import quests as quest_system

            messages = quest_system.update_quest_progress(game, "dialogue", npc_id=npc_id)
            for msg in messages:
                game.log.add(msg)
        except Exception:
            pass

        # Best-effort: stash one line in the quest's dialogue history (if present).
        if dialogue_head:
            try:
                for quest in list(getattr(game, "active_quests", {}).values()):
                    if getattr(quest, "status", None) != "active":
                        continue
                    quest.dialogue_history.append(str(dialogue_head))
            except Exception:
                pass

    return effect


def _build_mentor(game: Any, npc: Any, npc_id: str, npc_def: dict) -> DialogueTree:
    title = _npc_name(npc, npc_def)
    body = _npc_dialogue_body(npc, npc_def)

    all_gens = ["koch", "branch", "zigzag"]
    owned = set(getattr(game, "unlocked_generators", []) or [])
    choices: list[DialogueChoice] = []

    for gen in all_gens:
        if gen in owned:
            continue
        choices.append(DialogueChoice(text=gen.title(), next_id=None, effect=_effect_grant_generator(gen)))

    if not choices:
        body = body + "\n\nYou already know every pattern I can teach."
        choices = [DialogueChoice(text="Continue...", next_id=None)]
    else:
        choices.append(DialogueChoice(text="Maybe later.", next_id=None))

    return DialogueTree(
        id=f"npc:{npc_id}",
        start_id="start",
        nodes={
            "start": DialogueNode(id="start", title=title, body=body, choices=choices),
        },
    )


def _build_hexmage(_game: Any, npc: Any, npc_id: str, npc_def: dict) -> DialogueTree:
    title = _npc_name(npc, npc_def)
    body = _npc_dialogue_body(npc, npc_def)

    def state_builder():
        from edgecaster.scenes.fractal_editor_scene import FractalEditorState

        return FractalEditorState(grid_kind="hex", max_vertices=4, max_edges=None)

    return DialogueTree(
        id=f"npc:{npc_id}",
        start_id="start",
        nodes={
            "start": DialogueNode(
                id="start",
                title=title,
                body=body,
                choices=[
                    DialogueChoice(
                        text="Let's draft.",
                        next_id=None,
                        effect=_effect_open_fractal_editor(state_builder, "The Hexmage opens a hexagonal drafting grid."),
                    ),
                    DialogueChoice(text="Maybe later.", next_id=None),
                ],
            )
        },
    )


def _build_cartographer(_game: Any, npc: Any, npc_id: str, npc_def: dict) -> DialogueTree:
    title = _npc_name(npc, npc_def)
    body = _npc_dialogue_body(npc, npc_def)

    def state_builder():
        from edgecaster.scenes.fractal_editor_scene import FractalEditorState

        return FractalEditorState(
            grid_x_min=-5,
            grid_x_max=15,
            grid_y_min=-10,
            grid_y_max=10,
            grid_kind="rect",
            max_edges=None,
        )

    return DialogueTree(
        id=f"npc:{npc_id}",
        start_id="start",
        nodes={
            "start": DialogueNode(
                id="start",
                title=title,
                body=body,
                choices=[
                    DialogueChoice(
                        text="Let's draft.",
                        next_id=None,
                        effect=_effect_open_fractal_editor(state_builder, "The Cartographer unrolls a wide rectangular grid."),
                    ),
                    DialogueChoice(text="Maybe later.", next_id=None),
                ],
            )
        },
    )


def _build_guide(game: Any, npc: Any, npc_id: str, npc_def: dict) -> DialogueTree:
    title = _npc_name(npc, npc_def)
    base_body = _npc_dialogue_body(npc, npc_def)

    quest_proto_id = str(npc_def.get("quest_trigger") or "")
    quest_loc = npc_def.get("quest_location")
    quest_loc_t = None
    if isinstance(quest_loc, (list, tuple)) and len(quest_loc) >= 2:
        try:
            quest_loc_t = (int(quest_loc[0]), int(quest_loc[1]))
        except Exception:
            quest_loc_t = None

    if not quest_proto_id:
        return DialogueTree(
            id=f"npc:{npc_id}",
            start_id="start",
            nodes={
                "start": DialogueNode(
                    id="start",
                    title=title,
                    body=base_body,
                    choices=[DialogueChoice(text="Continue...", next_id=None)],
                )
            },
        )

    active = _has_active_quest(game, quest_proto_id)
    completed = _has_completed_quest(game, quest_proto_id)

    nodes: dict[str, DialogueNode] = {}

    if completed:
        nodes["start"] = DialogueNode(
            id="start",
            title=title,
            body=base_body + "\n\nYou already found the inventor. Good luck out there.",
            choices=[DialogueChoice(text="Continue...", next_id=None)],
        )
        return DialogueTree(id=f"npc:{npc_id}", start_id="start", nodes=nodes)

    if active:
        hint = ""
        if quest_loc_t is not None:
            hint = f"\n\nTheir workshop is marked near ({quest_loc_t[0]}, {quest_loc_t[1]})."
        nodes["start"] = DialogueNode(
            id="start",
            title=title,
            body=base_body + "\n\nYou're already on this trail." + hint,
            choices=[DialogueChoice(text="Thanks.", next_id=None)],
        )
        return DialogueTree(id=f"npc:{npc_id}", start_id="start", nodes=nodes)

    # Not active: offer a second screen before accepting.
    details = (
        "The inventor is a recluse with a talent for measuring the land's distortions.\n"
        "If you can reach them, they might help you understand what's happening.\n"
    )
    if quest_loc_t is not None:
        details += f"\nI can mark their workshop at ({quest_loc_t[0]}, {quest_loc_t[1]})."

    accept_effect = _effect_accept_quest(
        quest_proto_id,
        quest_location=quest_loc_t,
        dialogue_tail=(npc_def.get("dialogue") or [None])[-1],
    )

    nodes["start"] = DialogueNode(
        id="start",
        title=title,
        body=base_body,
        choices=[
            DialogueChoice(text="Tell me more.", next_id="details"),
            DialogueChoice(text="Not now.", next_id=None),
        ],
    )
    nodes["details"] = DialogueNode(
        id="details",
        title=title,
        body=details,
        choices=[
            DialogueChoice(text="I'll go.", next_id=None, effect=accept_effect),
            DialogueChoice(text="Not now.", next_id=None),
        ],
    )
    return DialogueTree(id=f"npc:{npc_id}", start_id="start", nodes=nodes)


def _build_inventor(game: Any, npc: Any, npc_id: str, npc_def: dict) -> DialogueTree:
    title = _npc_name(npc, npc_def)
    body = _npc_dialogue_body(npc, npc_def)

    quest_proto_id = str(npc_def.get("quest_complete") or "")
    active = bool(quest_proto_id) and _has_active_quest(game, quest_proto_id)

    if not active:
        return DialogueTree(
            id=f"npc:{npc_id}",
            start_id="start",
            nodes={
                "start": DialogueNode(
                    id="start",
                    title=title,
                    body=body,
                    choices=[DialogueChoice(text="Continue...", next_id=None)],
                )
            },
        )

    # Active quest: let the dialogue choice complete objective progress.
    head_line = None
    try:
        head_line = (npc_def.get("dialogue") or [None])[0]
    except Exception:
        head_line = None

    complete_effect = _effect_complete_quest_dialogue(npc_id, dialogue_head=str(head_line) if head_line else None)

    return DialogueTree(
        id=f"npc:{npc_id}",
        start_id="start",
        nodes={
            "start": DialogueNode(
                id="start",
                title=title,
                body=body,
                choices=[
                    DialogueChoice(text="Continue...", next_id=None, effect=complete_effect),
                ],
            )
        },
    )


def _build_merchant(_game: Any, npc: Any, npc_id: str, npc_def: dict) -> DialogueTree:
    title = _npc_name(npc, npc_def)
    body = _npc_dialogue_body(npc, npc_def)

    merchant_actor_id = str(getattr(npc, "id", ""))

    def open_trade(game: Any) -> None:
        try:
            game.merchant_requested = merchant_actor_id
        except Exception:
            pass

    return DialogueTree(
        id=f"npc:{npc_id}",
        start_id="start",
        nodes={
            "start": DialogueNode(
                id="start",
                title=title,
                body=body,
                choices=[
                    DialogueChoice(text="Trade.", next_id=None, effect=open_trade),
                    DialogueChoice(text="Maybe later.", next_id=None),
                ],
            )
        },
    )


def build_npc_dialogue_tree(game: Any, npc: Any) -> DialogueTree:
    """
    Build a DialogueTree for an NPC actor.

    This is the unified path for all "Talk" interactions.
    """
    tags = getattr(npc, "tags", None) or {}
    npc_id = str(tags.get("npc_id") or "")
    npc_def = npc_content.NPC_DEFS.get(npc_id, {}) if npc_id else {}

    if npc_id == "mentor":
        return _build_mentor(game, npc, npc_id, npc_def)
    if npc_id == "hexmage":
        return _build_hexmage(game, npc, npc_id, npc_def)
    if npc_id == "cartographer":
        return _build_cartographer(game, npc, npc_id, npc_def)
    if npc_id == "guide_npc":
        return _build_guide(game, npc, npc_id, npc_def)
    if npc_id == "inventor_npc":
        return _build_inventor(game, npc, npc_id, npc_def)
    if npc_id == "merchant":
        return _build_merchant(game, npc, npc_id, npc_def)

    # Generic fallback: show whatever dialogue lines exist, then end.
    title = _npc_name(npc, npc_def)
    body = _npc_dialogue_body(npc, npc_def)
    return DialogueTree(
        id=f"npc:{npc_id or title}",
        start_id="start",
        nodes={
            "start": DialogueNode(
                id="start",
                title=title,
                body=body,
                choices=[DialogueChoice(text="Continue...", next_id=None)],
            )
        },
    )
