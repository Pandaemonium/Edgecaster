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


def _get_active_quest(game: Any, proto_id: str):
    try:
        for quest in getattr(game, "active_quests", {}).values():
            if quest.proto_id == proto_id and getattr(quest, "status", None) == "active":
                return quest
    except Exception:
        return None
    return None


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


def _effect_accept_quest(
    proto_id: str,
    *,
    quest_location: tuple[int, int] | None,
    dialogue_tail: str | None,
    poi_id: str | None = None,
) -> Callable[[Any], None]:
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
            if poi_id and hasattr(game, "add_poi_rumor"):
                try:
                    game.add_poi_rumor(str(poi_id), log=False)
                    game.log.add("A location has been marked on your world map.")
                except Exception:
                    pass
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
    quest_loc_t = None
    if hasattr(game, "inventor_zone"):
        try:
            quest_loc_t = (int(game.inventor_zone[0]), int(game.inventor_zone[1]))  # type: ignore[attr-defined]
        except Exception:
            quest_loc_t = None
    if quest_loc_t is None:
        quest_loc = npc_def.get("quest_location")
        if isinstance(quest_loc, (list, tuple)) and len(quest_loc) >= 2:
            try:
                quest_loc_t = (int(quest_loc[0]), int(quest_loc[1]))
            except Exception:
                quest_loc_t = None

    failing_loc_t = None
    if hasattr(game, "failing_rune_zone"):
        try:
            failing_loc_t = (int(game.failing_rune_zone[0]), int(game.failing_rune_zone[1]))  # type: ignore[attr-defined]
        except Exception:
            failing_loc_t = None

    if not quest_proto_id:
        return DialogueTree(
            id=f"npc:{npc_id}",
            music_key="sergeant",

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

    quest = _get_active_quest(game, quest_proto_id)
    active = quest is not None
    completed = _has_completed_quest(game, quest_proto_id)

    nodes: dict[str, DialogueNode] = {}

    if completed:
        nodes["start"] = DialogueNode(
            id="start",
            title=title,
            body=base_body + "\n\nYou already found the inventor. Good luck out there.",
            choices=[DialogueChoice(text="Continue...", next_id=None)],
        )
        return DialogueTree(id=f"npc:{npc_id}", start_id="start",music_key="sergeant",nodes=nodes)

    if active:
        stage = int(getattr(quest, "stage", 0) or 0)

        def reveal_failing_rune(game: Any) -> None:
            if quest is None or failing_loc_t is None:
                return
            try:
                tags = getattr(quest, "tags", {}) or {}
                if tags.get("failing_rune_revealed"):
                    return
                from edgecaster.systems import quests as quest_system

                quest_system.add_quest_location(quest, failing_loc_t)
                quest_system.add_quest_note(
                    quest,
                    f"The guide marked the failing rune at ({failing_loc_t[0]}, {failing_loc_t[1]}).",
                )
                tags["failing_rune_revealed"] = True
                quest.tags = tags
                if hasattr(game, "add_poi_rumor"):
                    game.add_poi_rumor("failing_rune", log=True)
            except Exception:
                pass

        if stage >= 2 and failing_loc_t is not None:
            hint = f"\n\nThe failing rune is at ({failing_loc_t[0]}, {failing_loc_t[1]})."
            nodes["start"] = DialogueNode(
                id="start",                
                title=title,
                body=base_body + "\n\nYou have the crystal. Time to bind the seal." + hint,
                choices=[DialogueChoice(text="Mark it for me.", next_id=None, effect=reveal_failing_rune)],
            )
            return DialogueTree(id=f"npc:{npc_id}", start_id="start",music_key="sergeant",nodes=nodes)

        hint = ""
        if quest_loc_t is not None:
            hint = f"\n\nTheir workshop is marked near ({quest_loc_t[0]}, {quest_loc_t[1]})."
        nodes["start"] = DialogueNode(
            id="start",
            
            title=title,
            body=base_body + "\n\nYou're already on this trail." + hint,
            choices=[DialogueChoice(text="Thanks.", next_id=None)],
        )
        return DialogueTree(id=f"npc:{npc_id}", start_id="start",music_key="sergeant", nodes=nodes)

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
        poi_id="inventor_workshop",
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
    return DialogueTree(id=f"npc:{npc_id}", start_id="start",music_key="sergeant", nodes=nodes)


def _build_inventor(game: Any, npc: Any, npc_id: str, npc_def: dict) -> DialogueTree:
    title = _npc_name(npc, npc_def)
    body = _npc_dialogue_body(npc, npc_def)

    quest_proto_id = str(npc_def.get("quest_complete") or "")
    quest = _get_active_quest(game, quest_proto_id) if quest_proto_id else None
    active = quest is not None

    if not active:
        return DialogueTree(
            id=f"npc:{npc_id}",
            start_id="start",
            music_key="polka",

            nodes={
                "start": DialogueNode(
                    id="start",
                    title=title,
                    body=body,
                    choices=[DialogueChoice(text="Continue...", next_id=None)],
                )
            },
        )

    def _count_item(game: Any, item_type: str) -> int:
        try:
            from edgecaster.systems import inventory as inventory_system

            inv = inventory_system.get_player_inventory(game)
            total = 0
            for it in inv:
                tags = getattr(it, "tags", {}) or {}
                if tags.get("item_type") == item_type:
                    total += inventory_system.get_quantity(it)
            return total
        except Exception:
            return 0

    def _consume_item(game: Any, item_type: str) -> bool:
        try:
            from edgecaster.systems import inventory as inventory_system

            inv = inventory_system.get_player_inventory(game)
            for idx, it in enumerate(list(inv)):
                tags = getattr(it, "tags", {}) or {}
                if tags.get("item_type") != item_type:
                    continue
                qty = inventory_system.get_quantity(it)
                if qty > 1:
                    inventory_system.set_quantity(it, qty - 1)
                else:
                    inv.pop(idx)
                return True
        except Exception:
            return False
        return False

    def _grant_coherence_crystal(game: Any) -> None:
        try:
            player = game._player()
            ent = game._spawn_entity_from_template("coherence_crystal", player.pos)
            inv = game.get_inventory(player.id)
            inv.append(ent)
            game.log.add("You receive a Coherence Crystal.")
            game.refresh_actor_actions(player.id)
        except Exception:
            pass

    def _complete_talk_effect(game: Any) -> None:
        try:
            from edgecaster.systems import quests as quest_system

            messages = quest_system.update_quest_progress(game, "dialogue", npc_id=npc_id)
            for msg in messages:
                game.log.add(msg)
            quest = _get_active_quest(game, quest_proto_id)
            if quest is None:
                return
            coord = getattr(game, "destabilizer_ruin_zone", None)
            if coord:
                zx, zy = int(coord[0]), int(coord[1])
                quest_system.add_quest_location(quest, (zx, zy))
                quest_system.add_quest_note(
                    quest,
                    f"The inventor marked a ruin at ({zx}, {zy}) rumored to hold a destabilizer.",
                )
                if hasattr(game, "add_poi_rumor"):
                    game.add_poi_rumor("destabilizer_ruin", log=True)
        except Exception:
            pass

    def _deliver_destabilizer(game: Any) -> None:
        if not _consume_item(game, "destabilizer"):
            game.log.add("You don't have a destabilizer.")
            return
        _grant_coherence_crystal(game)
        try:
            from edgecaster.systems import quests as quest_system

            messages = quest_system.update_quest_progress(game, "collect_item", item_type="destabilizer")
            for msg in messages:
                game.log.add(msg)
            quest = _get_active_quest(game, quest_proto_id)
            if quest is not None:
                quest_system.add_quest_note(
                    quest,
                    "The inventor handed you a Coherence Crystal. Show it to the guide.",
                )
        except Exception:
            pass

    stage = int(getattr(quest, "stage", 0) or 0) if quest is not None else 0
    has_destabilizer = _count_item(game, "destabilizer") > 0

    nodes: dict[str, DialogueNode] = {}

    if stage == 0:
        nodes["start"] = DialogueNode(
            id="start",
            title=title,
            body=body,
            choices=[
                DialogueChoice(text="What do you need?", next_id="task", effect=_complete_talk_effect),
                DialogueChoice(text="Not now.", next_id=None),
            ],
        )
    else:
        nodes["start"] = DialogueNode(
            id="start",
            title=title,
            body=body,
            choices=[DialogueChoice(text="Continue...", next_id="task")],
        )

    if stage >= 2:
        nodes["task"] = DialogueNode(
            id="task",
            title=title,
            body="You have the crystal. Go bind the seal and try not to fumble it.",
            choices=[DialogueChoice(text="I'll handle it.", next_id=None)],
        )
    elif has_destabilizer:
        nodes["task"] = DialogueNode(
            id="task",
            title=title,
            body="You actually brought a destabilizer? Huh. Hand it over.",
            choices=[DialogueChoice(text="Here.", next_id=None, effect=_deliver_destabilizer)],
        )
    else:
        nodes["task"] = DialogueNode(
            id="task",
            title=title,
            body="Bring me a destabilizer. No destabilizer, no crystal. Simple.",
            choices=[DialogueChoice(text="I'll return.", next_id=None)],
        )

    return DialogueTree(
        id=f"npc:{npc_id}",
        start_id="start",
        music_key="polka",
        nodes=nodes,
    )


def _build_merchant(_game: Any, npc: Any, npc_id: str, npc_def: dict) -> DialogueTree:
    title = _npc_name(npc, npc_def)
    body = _npc_dialogue_body(npc, npc_def)

    merchant_actor_id = str(getattr(npc, "id", ""))

    def open_trade(game: Any) -> None:
        try:
            # Optional: some merchants (e.g. the starting-zone "dev" merchant)
            # refresh their stock each time you open trade.
            lvl = game._level()
            merchant = getattr(lvl, "actors", {}).get(merchant_actor_id)
            if merchant is not None:
                try:
                    from edgecaster.systems import trade as trade_system

                    tags = getattr(merchant, "tags", None) or {}
                    needs_refresh = bool(tags.get("merchant_refresh_on_talk") or tags.get("merchant_all_items"))
                    already_init = bool(tags.get("merchant_initialized"))
                    trade_system.ensure_merchant_initialized(game, lvl, merchant)
                    # Avoid double-restocking on first interaction: ensure_merchant_initialized
                    # performs the initial force-restock already.
                    if needs_refresh and already_init:
                        trade_system.restock_merchant(game, lvl, merchant, force=True)
                except Exception:
                    pass

            # Keep shop music across the dialogue->merchant transition gap.
            try:
                game.pending_music_override_key = "shop"
            except Exception:
                pass



            game.merchant_requested = merchant_actor_id
        except Exception:
            pass

    return DialogueTree(
        id=f"npc:{npc_id}",
        start_id="start",
        music_key="shop",

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


def _build_chakra_sage(game: Any, npc: Any, npc_id: str, npc_def: dict) -> DialogueTree:
    """
    Build a comprehensive dialogue tree explaining the chakra system.

    This NPC provides detailed, branching explanations of:
    - What chakras are and how they relate to body nodes
    - How unlocking and activation work
    - The gating system (branch roots)
    - How chakras generate fractal patterns
    - Alignment and resonance bonuses
    - Can unlock one chakra per conversation
    """
    title = _npc_name(npc, npc_def)
    from edgecaster.systems.chakras import chakra_display_name

    # Helper to get player's chakra info
    def _get_chakra_count(game: Any) -> tuple[int, int]:
        """Return (unlocked_count, active_count)."""
        try:
            player = game._player()
            if not hasattr(player, "chakra_state") or player.chakra_state is None:
                return (1, 1)  # Default: torso only
            cs = player.chakra_state
            return (len(cs.unlocked), len(cs.active))
        except Exception:
            return (1, 1)

    def _get_unlockable_chakras(game: Any) -> list[str]:
        """
        Return all currently unlockable chakra ids based on shared gating rules.
        """
        try:
            from edgecaster.prototypes import resolve_body_schema
            from edgecaster.systems.chakras import list_unlockable_chakras

            player = game._player()
            if not hasattr(player, "chakra_state") or player.chakra_state is None:
                return []

            body_schema = resolve_body_schema(player)
            return list_unlockable_chakras(body_schema, player.chakra_state)
        except Exception:
            return []

    def _effect_unlock_chakra(node_id: str) -> Callable[[Any], None]:
        """
        Create an effect function that unlocks a specific chakra.

        Choices for the Sage are pre-filtered to currently unlockable nodes.
        Also grants the "chakra" ability if not already owned.
        """
        def effect(game: Any) -> None:
            try:
                from edgecaster.systems.chakras import unlock_chakra

                player = game._player()
                if not hasattr(player, "chakra_state") or player.chakra_state is None:
                    return

                chakra_state = player.chakra_state

                if unlock_chakra(chakra_state, node_id, auto_activate=True):
                    display_name = chakra_display_name(node_id)
                    game.log.add(f"The Chakra Sage awakens your {display_name} chakra!")
                    game.log.add("You feel new energy flowing through you.")

                # Grant the "chakra" ability if not already owned
                if hasattr(game, "grant_ability"):
                    if game.grant_ability("chakra"):
                        game.log.add("You have learned to channel your chakras into patterns!")
                        game.log.add("The 'Chakra' ability has been added to your ability bar.")
            except Exception:
                pass
        return effect

    unlocked, active = _get_chakra_count(game)
    unlockable = _get_unlockable_chakras(game)

    # Build the dialogue nodes
    nodes: dict[str, DialogueNode] = {}

    # =========================================================================
    # START NODE
    # =========================================================================
    start_choices = [
        DialogueChoice(text="What are chakras?", next_id="what_are_chakras"),
        DialogueChoice(text="How do I unlock more?", next_id="unlocking"),
        DialogueChoice(text="How do patterns work?", next_id="patterns"),
        DialogueChoice(text="Tell me about resonance.", next_id="resonance"),
        DialogueChoice(text="Tell me about chakra charge.", next_id="chakra_charge"),
    ]

    # Add chakra unlock option if there are unlockable chakras
    if unlockable:
        start_choices.insert(0, DialogueChoice(
            text="Awaken a chakra for me.",
            next_id="unlock_chakra"
        ))

    start_choices.append(DialogueChoice(text="Maybe later.", next_id=None))

    nodes["start"] = DialogueNode(
        id="start",
        title=title,
        body=(
            "Ah, a seeker of patterns. I sense the potential within you.\n\n"
            f"You currently have {unlocked} chakra{'s' if unlocked != 1 else ''} unlocked, "
            f"with {active} active.\n\n"
            "Your body is a map of energy centers - chakras - each one a vertex in the "
            "great pattern. Would you like to learn about the chakra system?"
        ),
        choices=start_choices,
    )

    # =========================================================================
    # UNLOCK CHAKRA NODE
    # =========================================================================
    if unlockable:
        # Build choices for all currently unlockable chakras.
        unlock_choices: list[DialogueChoice] = []
        for node_id in unlockable:
            display_name = chakra_display_name(node_id)
            unlock_choices.append(DialogueChoice(
                text=display_name,
                next_id="unlock_done",
                effect=_effect_unlock_chakra(node_id)
            ))
        unlock_choices.append(DialogueChoice(text="Not right now.", next_id="start"))

        nodes["unlock_chakra"] = DialogueNode(
            id="unlock_chakra",
            title=title,
            body=(
                "I can sense the dormant energy points within you.\n\n"
                f"There are {len(unlockable)} chakras ready to awaken. "
                "Choose one, and I shall open the flow of energy to it.\n\n"
                "Which chakra would you like me to awaken?"
            ),
            choices=unlock_choices,
        )

        nodes["unlock_done"] = DialogueNode(
            id="unlock_done",
            title=title,
            body=(
                "It is done. I have opened the flow of energy to that chakra.\n\n"
                "Press Shift+C to open your chakra management screen and see your "
                "newly awakened energy center. You can toggle it active or inactive there.\n\n"
                "Return to me when you are ready to awaken another."
            ),
            choices=[
                DialogueChoice(text="Thank you, Sage.", next_id=None),
                DialogueChoice(text="Tell me more about chakras.", next_id="start"),
            ],
        )

    # =========================================================================
    # WHAT ARE CHAKRAS
    # =========================================================================
    nodes["what_are_chakras"] = DialogueNode(
        id="what_are_chakras",
        title=title,
        body=(
            "Chakras are energy centers aligned with your physical form.\n\n"
            "Every part of your body - your torso, shoulders, hands, even your "
            "fingertips - has a corresponding chakra point.\n\n"
            "When you cast a pattern, your active chakras become VERTICES in that "
            "pattern. The more chakras you activate, the more complex and powerful "
            "your patterns become.\n\n"
            "Think of it this way: your body IS the seed of your fractal."
        ),
        choices=[
            DialogueChoice(text="Tell me about body nodes.", next_id="body_nodes"),
            DialogueChoice(text="What's a vertex?", next_id="vertices"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["body_nodes"] = DialogueNode(
        id="body_nodes",
        title=title,
        body=(
            "Your body is organized as a TREE of nodes:\n\n"
            "  TORSO (root)\n"
            "    ├── Chest, Back, Abdomen\n"
            "    ├── Head → Neck → Skull → Face...\n"
            "    ├── Arms → Shoulder → Elbow → Wrist → Hand...\n"
            "    └── Legs → Thigh → Knee → Calf → Foot...\n\n"
            "Each node is a potential chakra. Deeper nodes (like fingertips) "
            "require you to first unlock the nodes leading to them.\n\n"
            "The TORSO is always your root chakra - it cannot be deactivated."
        ),
        choices=[
            DialogueChoice(text="How deep does it go?", next_id="depth"),
            DialogueChoice(text="What about hands?", next_id="hands"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["depth"] = DialogueNode(
        id="depth",
        title=title,
        body=(
            "The tree goes remarkably deep:\n\n"
            "HAND BRANCH:\n"
            "  Wrist → Palm → Thumb/Index/Middle/Ring/Pinky\n"
            "  Each finger → Knuckle 1 → Knuckle 2 → Knuckle 3 → Nail\n\n"
            "FOOT BRANCH:\n"
            "  Ankle → Sole → Heel / Big Toe / other toes...\n\n"
            "HEAD BRANCH:\n"
            "  Neck → Skull → Face → Nose/Eyes/Forehead/Mouth\n\n"
            "A master who unlocks all chakras wields patterns of extraordinary "
            "complexity. But such mastery takes a lifetime."
        ),
        choices=[
            DialogueChoice(text="How do I unlock more?", next_id="unlocking"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["hands"] = DialogueNode(
        id="hands",
        title=title,
        body=(
            "Ah, the hands - a favorite among edgecasters.\n\n"
            "Each hand has:\n"
            "  • WRIST - The gateway to hand chakras\n"
            "  • PALM - The center of hand energy\n"
            "  • FIVE FINGERS - Thumb, Index, Middle, Ring, Pinky\n\n"
            "Each finger has FOUR nodes:\n"
            "  Knuckle 1 → Knuckle 2 → Knuckle 3 → Nail\n\n"
            "Activating all five fingers creates a 'full hand' resonance - "
            "a powerful bonus that amplifies your pattern's effect and "
            "helps your charge build faster.\n\n"
            "And remember: you have TWO hands. Bilateral symmetry grants "
            "its own resonance bonuses."
        ),
        choices=[
            DialogueChoice(text="What are resonance bonuses?", next_id="resonance"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["vertices"] = DialogueNode(
        id="vertices",
        title=title,
        body=(
            "In pattern magic, a VERTEX is a point of power.\n\n"
            "When you cast, each active chakra becomes a vertex positioned "
            "according to your body's layout. These vertices are connected by "
            "EDGES following your body's natural structure.\n\n"
            "For example, with torso, shoulder, and elbow active:\n"
            "  • Torso vertex at center\n"
            "  • Shoulder vertex offset from torso\n"
            "  • Elbow vertex offset from shoulder\n"
            "  • Edges: torso↔shoulder, shoulder↔elbow\n\n"
            "This forms the SEED PATTERN, which is then fractally iterated "
            "to create your final spell shape."
        ),
        choices=[
            DialogueChoice(text="What's fractal iteration?", next_id="fractals"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    # =========================================================================
    # UNLOCKING
    # =========================================================================
    nodes["unlocking"] = DialogueNode(
        id="unlocking",
        title=title,
        body=(
            "Unlocking chakras requires understanding the GATING system.\n\n"
            "Chakras are gated by BRANCH ROOTS - the entry points to body regions:\n"
            "  • SHOULDER gates the entire arm\n"
            "  • WRIST gates the entire hand\n"
            "  • PALM gates all five fingers\n"
            "  • THIGH gates the entire leg\n"
            "  • ANKLE gates the entire foot\n\n"
            "To unlock a chakra, all branch roots in its path must be unlocked first.\n\n"
            "Example: To unlock Knuckle 2 on your index finger, you need:\n"
            "  Torso → Shoulder → Wrist → Palm (all branch roots in path)"
        ),
        choices=[
            DialogueChoice(text="What about intermediate nodes?", next_id="intermediate"),
            DialogueChoice(text="How do I actually unlock them?", next_id="unlock_methods"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["intermediate"] = DialogueNode(
        id="intermediate",
        title=title,
        body=(
            "This is subtle but important:\n\n"
            "INTERMEDIATE nodes do NOT gate each other.\n\n"
            "Within the arm, Elbow and Forearm are intermediate - neither "
            "is a branch root. So you can unlock Forearm without Elbow.\n\n"
            "Within a finger, Knuckles 1, 2, and 3 are intermediate - "
            "you can unlock Knuckle 3 directly without Knuckle 1 or 2.\n\n"
            "Only BRANCH ROOTS gate:\n"
            "  • Shoulder (arm entry)\n"
            "  • Wrist (hand entry)\n"
            "  • Ankle (foot entry)\n"
            "  • Neck (head entry)\n"
            "  • And so on...\n\n"
            "This means you can strategically skip intermediate chakras "
            "to reach deeper ones faster."
        ),
        choices=[
            DialogueChoice(text="Show me a gating chain.", next_id="gating_chain"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["gating_chain"] = DialogueNode(
        id="gating_chain",
        title=title,
        body=(
            "Let's trace the path to your index fingernail:\n\n"
            "TARGET: Index Finger → Nail (deepest point)\n\n"
            "GATING CHAIN (branch roots only):\n"
            "  1. Torso (body root) - always unlocked\n"
            "  2. Shoulder (arm branch root)\n"
            "  3. Wrist (hand branch root)\n"
            "  4. Palm is NOT a branch root for fingers!\n\n"
            "Wait - Palm isn't a gate? Correct!\n\n"
            "Fingers branch directly from the HAND schema, and their root "
            "is Knuckle 1. But Knuckle 1 itself isn't a branch root for "
            "Knuckle 2 or 3.\n\n"
            "So to reach your index fingernail, you only need:\n"
            "  Torso + Shoulder + Wrist\n\n"
            "Then you can unlock ANY node in the hand/finger subtree!"
        ),
        choices=[
            DialogueChoice(text="That's clever design.", next_id="design_philosophy"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["design_philosophy"] = DialogueNode(
        id="design_philosophy",
        title=title,
        body=(
            "The gating system reflects anatomical reality:\n\n"
            "You can't move your fingers without having an arm. But once "
            "you have wrist control, you can move any finger independently.\n\n"
            "This creates meaningful PROGRESSION:\n"
            "  • Early game: Torso only (simple patterns)\n"
            "  • Mid game: Arms and legs unlocked (complex shapes)\n"
            "  • Late game: Hands and feet fully unlocked (intricate fractals)\n"
            "  • Mastery: Every chakra active (cosmic-level patterns)\n\n"
            "The deeper you go, the more vertices you add, and the more "
            "powerful your patterns become."
        ),
        choices=[
            DialogueChoice(text="How do I actually unlock them?", next_id="unlock_methods"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["unlock_methods"] = DialogueNode(
        id="unlock_methods",
        title=title,
        body=(
            "Chakras are unlocked through various means:\n\n"
            "• LEVELING UP - Each level grants a chakra unlock point\n"
            "• QUEST REWARDS - Some quests grant specific chakras\n"
            "• SPECIAL ITEMS - Chakra stones can unlock specific nodes\n"
            "• MEDITATION - At certain shrines, you can meditate to unlock\n"
            "• COMBAT MASTERY - Defeating powerful enemies may awaken chakras\n\n"
            "Once unlocked, a chakra can be ACTIVATED or DEACTIVATED at will. "
            "More active chakras = more vertices = more complex patterns.\n\n"
            "But beware: more complexity isn't always better. Sometimes a "
            "simple, focused pattern is more effective than an unwieldy one."
        ),
        choices=[
            DialogueChoice(text="How do patterns work?", next_id="patterns"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    # =========================================================================
    # PATTERNS
    # =========================================================================
    nodes["patterns"] = DialogueNode(
        id="patterns",
        title=title,
        body=(
            "Pattern generation follows a three-step process:\n\n"
            "1. SEED PATTERN\n"
            "   Your active chakras become vertices.\n"
            "   Body tree edges become pattern edges.\n\n"
            "2. FRACTAL ITERATION\n"
            "   The seed is transformed by fractal generators.\n"
            "   Koch, Branch, Zigzag - each creates different shapes.\n\n"
            "3. PROJECTION\n"
            "   The pattern is projected into the world.\n"
            "   Vertices become points of power; edges channel energy.\n\n"
            "The more chakras active, the more complex your seed, "
            "and the more intricate your final pattern."
        ),
        choices=[
            DialogueChoice(text="What's fractal iteration?", next_id="fractals"),
            DialogueChoice(text="Tell me about alignment.", next_id="alignment"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["fractals"] = DialogueNode(
        id="fractals",
        title=title,
        body=(
            "Fractal iteration transforms simple shapes into complex ones.\n\n"
            "Take a simple line segment. Apply the KOCH generator:\n"
            "  Before: ────────────\n"
            "  After:  ──/\\──\n\n"
            "Apply it again:\n"
            "  After:  /\\/\\/\\/\\\n\n"
            "Each iteration adds detail. Your chakra seed might start as a "
            "simple star shape, but after iteration becomes an intricate mandala.\n\n"
            "Different generators create different effects:\n"
            "  • KOCH - Classic snowflake bumps\n"
            "  • BRANCH - Tree-like splitting\n"
            "  • ZIGZAG - Jagged lightning patterns\n"
            "  • SUBDIVIDE - Smooth subdivision"
        ),
        choices=[
            DialogueChoice(text="How does this affect combat?", next_id="combat"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["alignment"] = DialogueNode(
        id="alignment",
        title=title,
        body=(
            "Each chakra has an ALIGNMENT - a subtle positional offset.\n\n"
            "Perfect alignment means your chakra sits exactly where your "
            "body's layout defines. But chakras can drift, creating unique "
            "pattern variations.\n\n"
            "Your DEXTERITY stat affects alignment wobble:\n"
            "  • Low dex: Chakras wobble randomly (unpredictable patterns)\n"
            "  • High dex: Chakras stay aligned (precise, repeatable patterns)\n\n"
            "Some edgecasters deliberately misalign chakras to create "
            "asymmetric patterns with unexpected properties.\n\n"
            "Experiment! A slightly off-center heart chakra might create "
            "a pattern that spirals rather than radiates."
        ),
        choices=[
            DialogueChoice(text="What are resonance bonuses?", next_id="resonance"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["resonance"] = DialogueNode(
        id="resonance",
        title=title,
        body=(
            "Certain chakra constellations create RESONANCE - harmonies that "
            "change how your power flows. Resonance only counts ACTIVE chakras.\n\n"
            "• BILATERAL ARMS (arm + arm_m or shoulder + shoulder_m)\n"
            "  Symmetry in the arms reduces mana cost and steadies damage.\n\n"
            "• FULL HAND (thumb/index/middle/ring/pinky)\n"
            "  A complete hand accelerates charge gain and deepens activations.\n\n"
            "• FULL HAND (mirrored)\n"
            "  The other hand can resonate the same way.\n\n"
            "• GROUNDED (leg + leg_m or thigh + thigh_m)\n"
            "  Rooted legs widen your activation radius and calm mana strain.\n\n"
            "• CENTERED (body core)\n"
            "  Your core steadies output and strengthens chakra generators.\n\n"
            "Resonance is the easiest way to make a smaller pattern feel "
            "stronger. It also makes chakra charge grow faster."
        ),
        choices=[
            DialogueChoice(text="How does this affect combat?", next_id="combat"),
            DialogueChoice(text="Explain chakra charge.", next_id="chakra_charge"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["chakra_charge"] = DialogueNode(
        id="chakra_charge",
        title=title,
        body=(
            "Chakra charge is the heat your active chakras build while a "
            "pattern is HELD in the world.\n\n"
            "In practice:\n"
            "• Charge builds only while you have a live pattern.\n"
            "• It accrues per ACTIVE chakra and slowly decays when idle.\n"
            "• Dexterity steadies the flow and increases gain.\n\n"
            "What charge does:\n"
            "• Higher charge increases damage and activation radius.\n"
            "• It trims mana cost slightly.\n"
            "• It strengthens the chakra generator's amplitude.\n\n"
            "Spending charge:\n"
            "• Activate R / Activate N consume charge.\n"
            "• Using the Chakra generator consumes charge.\n"
            "• Resonance (especially full hands) raises charge gain and cap.\n\n"
            "You can view charge in the Chakra screen by hovering a node."
        ),
        choices=[
            DialogueChoice(text="Tell me about resonance.", next_id="resonance"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["combat"] = DialogueNode(
        id="combat",
        title=title,
        body=(
            "In combat, your pattern determines your power:\n\n"
            "• MORE VERTICES = More points of damage\n"
            "  Each vertex near an enemy deals damage.\n\n"
            "• PATTERN SHAPE = Coverage area\n"
            "  Wide patterns hit more enemies; focused patterns hit harder.\n\n"
            "• FRACTAL COMPLEXITY = Total power\n"
            "  More iterations = more vertices = more damage potential.\n\n"
            "But there are tradeoffs:\n"
            "  • Complex patterns take longer to cast\n"
            "  • Wide patterns dilute damage per enemy\n"
            "  • Too many active chakras can drain mana faster\n\n"
            "Master edgecasters learn to adjust their chakra configuration "
            "for each situation: few chakras for quick strikes, many for "
            "devastating area attacks."
        ),
        choices=[
            DialogueChoice(text="Any final advice?", next_id="advice"),
            DialogueChoice(text="Back to the beginning.", next_id="start"),
        ],
    )

    nodes["advice"] = DialogueNode(
        id="advice",
        title=title,
        body=(
            "My advice to you, young edgecaster:\n\n"
            "1. START SMALL\n"
            "   Master your torso chakra before rushing to unlock more.\n\n"
            "2. UNLOCK STRATEGICALLY\n"
            "   Branch roots first - they gate entire regions.\n\n"
            "3. EXPERIMENT WITH ACTIVATION\n"
            "   You don't need every unlocked chakra active. Less can be more.\n\n"
            "4. SEEK RESONANCE\n"
            "   Bilateral symmetry and full-limb activations grant bonuses.\n\n"
            "5. MIND YOUR DEXTERITY\n"
            "   High dex = precise patterns. Low dex = chaos.\n\n"
            "May your chakras align and your fractals flourish."
        ),
        choices=[
            DialogueChoice(text="Thank you, Sage.", next_id=None),
            DialogueChoice(text="I have more questions.", next_id="start"),
        ],
    )

    return DialogueTree(
        id=f"npc:{npc_id}",
        start_id="start",
        nodes=nodes,
        music_key="chakric",
    )


def _build_lair_informant(game: Any, npc: Any, npc_id: str, npc_def: dict) -> DialogueTree:
    title = _npc_name(npc, npc_def)
    base_body = _npc_dialogue_body(npc, npc_def)

    try:
        lairs = list(getattr(game, "get_nearest_legendary_lairs")(5))
    except Exception:
        lairs = []

    poi_to_name: dict[str, str] = {}
    try:
        registry = getattr(game, "legendary_registry", None) or {}
        for rec in registry.values():
            pid = rec.get("poi_id")
            name = rec.get("name")
            if pid and name:
                poi_to_name[str(pid)] = str(name)
    except Exception:
        poi_to_name = {}

    if not lairs:
        lairs_body = "I haven't heard any lair rumors worth repeating."
    else:
        lines = ["I know of these nearby lairs:"]
        for pid, coord in lairs:
            name = poi_to_name.get(pid) or pid.replace("_", " ")
            zx, zy, _ = coord
            lines.append(f"- {name} at ({zx}, {zy})")
        lines.append("")
        lines.append("I've marked them on your map.")
        lairs_body = "\n".join(lines)

    def mark_lairs(game_obj: Any) -> None:
        if not lairs:
            return
        try:
            before = set(getattr(game_obj, "rumored_pois", set()) or set())
        except Exception:
            before = set()
        for pid, _ in lairs:
            try:
                game_obj.add_poi_rumor(pid, log=False)
            except Exception:
                continue
        try:
            after = set(getattr(game_obj, "rumored_pois", set()) or set())
            newly_added = len(after - before)
        except Exception:
            newly_added = 0
        try:
            if newly_added > 0:
                game_obj.log.add("You mark several lairs on your map.")
            else:
                game_obj.log.add("Those lairs are already marked on your map.")
        except Exception:
            pass

    return DialogueTree(
        id=f"npc:{npc_id}",
        start_id="start",
        nodes={
            "start": DialogueNode(
                id="start",
                title=title,
                body=base_body,
                choices=[
                    DialogueChoice(text="Show me the nearest lairs.", next_id="lairs", effect=mark_lairs),
                    DialogueChoice(text="Maybe later.", next_id=None),
                ],
            ),
            "lairs": DialogueNode(
                id="lairs",
                title=title,
                body=lairs_body,
                choices=[DialogueChoice(text="Thanks.", next_id=None)],
            ),
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
    if npc_id == "local_guide":
        return _build_guide(game, npc, npc_id, npc_def)
    if npc_id == "inventor_npc":
        return _build_inventor(game, npc, npc_id, npc_def)
    if npc_id == "merchant":
        return _build_merchant(game, npc, npc_id, npc_def)
    if npc_id == "lair_informant":
        return _build_lair_informant(game, npc, npc_id, npc_def)
    if npc_id == "chakra_sage":
        return _build_chakra_sage(game, npc, npc_id, npc_def)

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
