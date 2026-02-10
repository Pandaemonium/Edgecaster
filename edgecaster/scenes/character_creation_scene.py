from __future__ import annotations

import pygame
from dataclasses import dataclass
from typing import Optional, List, Tuple

from edgecaster.character import Character, default_character
from edgecaster.systems.chakras import ChakraState, collect_all_chakra_nodes
from edgecaster.prototypes import resolve_body_schema

from .base import (
    PanelScene,
    MenuInput,
    MENU_ACTION_UP,
    MENU_ACTION_DOWN,
    MENU_ACTION_LEFT,
    MENU_ACTION_RIGHT,
    MENU_ACTION_ACTIVATE,
    MENU_ACTION_BACK,
    MENU_ACTION_FULLSCREEN,
)


# ---------------------------------------------------------------------------
# Character classes (legacy list preserved)
# ---------------------------------------------------------------------------

CHAR_CLASSES: List[str] = [
    "Kochbender",
    "Automoton",
    "Strange Attractor",
    "Weaver",
    "Relativist",
    "Chromaticist",
    "Epiphenomenal",
    "Monk",
    "Blade",
    "Gardener",
]

CLASS_DESCRIPTIONS = {
    "Kochbender": "Who commands the icy lash of runes carved infinitely sharp? Who obeys?",
    "Automoton": "A common misconception that machines cannot perform magic. Quite the contrary.",
    "Strange Attractor": "Cursed to dance among weird energies, or they among her.",
    "Weaver": "Everyone wears clothing, but few make their own. So too with destiny.",
    "Relativist": "'Twould be folly to presume that everyone marches at the same pace.",
    "Chromaticist": "Disciple of the Eightfold Way, correct in the habit that water flows.",
    "Epiphenomenal": "What's in a name? Is there anything else besides?",
    "Monk": "A living mandala. Your body is the rune, your chakras the pattern.",
    "Blade": "A fractal duelist. Your knife is a theorem sharpened to violence.",
    "Gardener": "Patient root, sudden thorn. You coax living runes into grasping vines.",
}

# Species options (label -> template id)
SPECIES_OPTIONS: List[Tuple[str, str]] = [
    ("Human", "human_base"),
]

DEFAULT_NAME = "Pandaemonium"


# ---------------------------------------------------------------------------
# UI model
# ---------------------------------------------------------------------------

@dataclass
class RuneEditorState:
    """Embedded Kochbender rune editor (grid snap)."""
    root: Tuple[int, int] = (0, 0)
    terminus: Tuple[int, int] = (10, 0)
    max_pts: int = 4
    midpoints: List[Tuple[int, int]] = None  # type: ignore[assignment]
    hover: Optional[Tuple[int, int]] = None
    ended: bool = False  # when True, user has clicked the terminus to end early

    def __post_init__(self) -> None:
        if self.midpoints is None:
            self.midpoints = []

    def clear(self) -> None:
        self.midpoints.clear()
        self.ended = False

    def undo(self) -> None:
        if self.midpoints:
            self.midpoints.pop()
        # If we undo, we consider the pattern "open" again.
        self.ended = False

    def can_add(self) -> bool:
        # Once ended, you can't add more points unless you undo/clear (or click a new point, see scene logic).
        return (not self.ended) and (len(self.midpoints) < self.max_pts)

    def add(self, pt: Tuple[int, int]) -> None:
        if self.can_add():
            self.midpoints.append(pt)

    def end_now(self) -> None:
        # Allow early termination before max_pts is reached.
        self.ended = True
        self.hover = None

    def finalize(self) -> List[Tuple[float, float]]:
        pts: List[Tuple[float, float]] = []
        pts.append((float(self.root[0]), float(self.root[1])))
        for x, y in self.midpoints:
            pts.append((float(x), float(y)))
        pts.append((float(self.terminus[0]), float(self.terminus[1])))
        return pts


@dataclass
class CharCreateState:
    char: Character
    class_idx: int = 0

    # Species selection
    species: List[Tuple[str, str]] = None  # type: ignore[assignment]
    species_idx: int = 0

    # Kochbender-specific options
    generators: List[str] = None  # type: ignore[assignment]
    illuminators: List[str] = None  # type: ignore[assignment]

    # Seed handling
    seed_mode: str = "random"      # "random" or "fixed"
    seed_text: str = ""
    seed_focus: bool = False

    # focus model
    focus: str = "name"            # name | class | stats | seed | start | rune
    stat_focus: str = "con"        # which stat row is focused when focus=="stats"

    # embedded rune editor (only shown for Kochbender)
    rune: RuneEditorState = None  # type: ignore[assignment]

    # Monk-specific chakra selection
    monk_base: Optional[str] = None
    monk_picks: List[str] = None  # type: ignore[assignment]
    monk_state: Optional[ChakraState] = None
    monk_scroll: int = 0

    def __post_init__(self) -> None:
        if self.generators is None:
            self.generators = ["custom", "koch", "branch", "zigzag"]
        if self.illuminators is None:
            self.illuminators = ["radius", "neighbors"]
        if self.species is None:
            self.species = list(SPECIES_OPTIONS)
        if self.rune is None:
            self.rune = RuneEditorState()
        if self.monk_picks is None:
            self.monk_picks = []
        if self.monk_state is None:
            self.monk_state = ChakraState(unlocked=set(), active=set())
        if self.monk_base is None:
            self.monk_base = "body"

        # Safeguards (preserve old behaviour)
        if not getattr(self.char, "name", None):
            self.char.name = DEFAULT_NAME
        if getattr(self.char, "generator", None) is None:
            self.char.generator = "custom"
        if getattr(self.char, "illuminator", None) is None:
            self.char.illuminator = "radius"
        if getattr(self.char, "stats", None) is None:
            self.char.stats = {"con": 0, "agi": 0, "int": 0, "res": 0}
        if getattr(self.char, "point_pool", None) is None:
            self.char.point_pool = 10

        if getattr(self.char, "species", None) is None:
            try:
                self.char.species = self.species_label.lower()
            except Exception:
                self.char.species = "human"

        # seed defaults
        if getattr(self.char, "seed", None) is None:
            self.seed_mode = "random"
            self.seed_text = ""
        else:
            # keep whatever default is in config/character, but default focus to random
            self.seed_mode = "random"
            self.seed_text = str(self.char.seed)

    @property
    def char_class(self) -> str:
        return CHAR_CLASSES[self.class_idx % len(CHAR_CLASSES)]

    def is_kochbender(self) -> bool:
        return self.char_class == "Kochbender"

    def is_monk(self) -> bool:
        return self.char_class == "Monk"

    @property
    def species_label(self) -> str:
        return self.species[self.species_idx % len(self.species)][0]

    @property
    def species_template(self) -> str:
        return self.species[self.species_idx % len(self.species)][1]


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------

class CharacterCreationScene(PanelScene):
    """
    PanelScene-based character creation screen (no extra pygame window).

    Design goals:
      - Use the *existing* renderer/window; do not call pygame.display.set_mode.
      - Support mouse + keyboard navigation using the modern MenuInput mapping.
      - Kochbender shows an embedded rune grid + Generator/Illuminator options on the right.
    """

    def __init__(self) -> None:
        super().__init__(window_rect=None)  # fullscreen logical by default
        self.menu_input = MenuInput()
        self.state: Optional[CharCreateState] = None

        # mouse hitboxes (panel-local)
        self._hits: dict[str, pygame.Rect] = {}

        # small UX: when name is still default, first backspace clears it all
        self._name_touched: bool = False

        # name editing mode: Enter to begin, Enter to confirm, Esc to cancel
        self._name_editing: bool = False
        self._name_before_edit: str = DEFAULT_NAME

        # rune sub-focus when focus == "rune" (keyboard navigation inside right panel)
        # gen | illum | grid | undo | clear
        self._rune_subfocus: str = "grid"

        self._hover_key: str | None = None

        # monk list render metadata (used for scrolling)
        self._monk_list_total: int = 0
        self._monk_list_capacity: int = 0


    def _set_text_input(self, enabled: bool) -> None:
        try:
            if enabled:
                pygame.key.start_text_input()
            else:
                pygame.key.stop_text_input()
        except Exception:
            pass



    # ------------------------------------------------------------------ #
    # PanelScene hooks
    # ------------------------------------------------------------------ #

    def update(self, dt_ms: int, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        # keep MenuInput repeat behaviour consistent with other menus
        if self._name_editing:
            # while typing, do not allow held movement keys to scroll focus
            self.menu_input.update()
            return

        act = self.menu_input.update()
        if act in (MENU_ACTION_UP, MENU_ACTION_DOWN, MENU_ACTION_LEFT, MENU_ACTION_RIGHT):
            self._handle_nav_action(act, manager)

    def handle_event(self, event, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        # init state lazily, after manager exists
        if self.state is None:
            self._init_state(manager)
        assert self.state is not None
        st = self.state

        # --- Text entry events (reliable letter input, including WASD) ---
        if event.type == pygame.TEXTINPUT:
            if self._name_editing and st.focus == "name":
                if st.char.name == DEFAULT_NAME and not self._name_touched:
                    st.char.name = ""
                st.char.name += getattr(event, "text", "")
                self._name_touched = True
                return

            if st.focus == "seed" and st.seed_mode == "fixed" and st.seed_focus:
                txt = getattr(event, "text", "")
                for ch in txt:
                    if ch.isdigit():
                        st.seed_text += ch
                    elif ch == "-" and not st.seed_text:
                        st.seed_text = "-"
                return

        # --- Key events ---
        if event.type == pygame.KEYDOWN:
            # If we're typing a name, ONLY honor a tiny subset of keys.
            if self._name_editing and st.focus == "name":
                if event.key == pygame.K_ESCAPE:
                    st.char.name = self._name_before_edit
                    self._name_editing = False
                    try:
                        pygame.key.stop_text_input()
                    except Exception:
                        pass
                    return

                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self._name_editing = False
                    try:
                        pygame.key.stop_text_input()
                    except Exception:
                        pass
                    return

                if event.key == pygame.K_BACKSPACE:
                    if st.char.name == DEFAULT_NAME and not self._name_touched:
                        st.char.name = ""
                        self._name_touched = True
                    else:
                        st.char.name = st.char.name[:-1]
                        self._name_touched = True
                    return

                # ignore everything else (prevents WASD from moving focus while typing)
                return

            act = self.menu_input.handle_keydown(event.key)

            if act == MENU_ACTION_FULLSCREEN:
                if hasattr(manager, "renderer") and hasattr(manager.renderer, "toggle_fullscreen"):
                    manager.renderer.toggle_fullscreen()
                return

            if act == MENU_ACTION_BACK:
                # Esc cancels seed focus first (if editing fixed seed)
                if st.focus == "seed" and st.seed_focus:
                    st.seed_focus = False
                    self._set_text_input(False)

                    return

                from .main_menu import MainMenuScene
                manager.set_scene(MainMenuScene())
                return

            if act == MENU_ACTION_ACTIVATE and event.key != pygame.K_SPACE:
                self._activate(manager)
                return

            if act in (MENU_ACTION_UP, MENU_ACTION_DOWN, MENU_ACTION_LEFT, MENU_ACTION_RIGHT):
                self._handle_nav_action(act, manager)
                return

            self._handle_text_key(event.key, manager)
            return

        if event.type == pygame.KEYUP:
            self.menu_input.handle_keyup(event.key)
            return

        # --- Mouse events (convert to panel-local coords first) ---
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            e2 = self._to_panel_event(event, manager)
            self._handle_click(getattr(e2, "pos", event.pos), manager)
            return

        if event.type == pygame.MOUSEMOTION:
            e2 = self._to_panel_event(event, manager)
            self._handle_motion(getattr(e2, "pos", event.pos), manager)
            return

        if event.type == pygame.MOUSEWHEEL:
            # rune grid does not scroll; ignore wheel if cursor is over it
            pos = getattr(event, "pos", None)
            if pos is None:
                pos = pygame.mouse.get_pos()

            dummy = type("E", (), {"pos": pos})()
            e2 = self._to_panel_event(dummy, manager)
            p = getattr(e2, "pos", pos)

            if self._hits.get("rune_grid") and self._hits["rune_grid"].collidepoint(p):
                return

            # monk list scrolling
            if st.is_monk():
                lst = self._hits.get("monk_list")
                if lst and lst.collidepoint(p):
                    total = max(0, self._monk_list_total)
                    cap = max(1, self._monk_list_capacity)
                    max_scroll = max(0, total - cap)
                    st.monk_scroll = max(0, min(max_scroll, st.monk_scroll - event.y))
                    return


    def draw_panel(self, panel: pygame.Surface, renderer, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        if self.state is None:
            self._init_state(manager)
        assert self.state is not None

        st = self.state

        # styling (match renderer)
        bg = getattr(renderer, "bg", (10, 10, 20))
        fg = getattr(renderer, "fg", (220, 230, 240))
        dim = getattr(renderer, "dim", (120, 130, 150))
        sel = getattr(renderer, "sel", (255, 230, 120))
        bar_bg = getattr(renderer, "bar_bg", (40, 40, 60))

        font = getattr(renderer, "menu_font", getattr(renderer, "font"))
        title_font = getattr(renderer, "menu_title_font", font)
        small = getattr(renderer, "small_font", font)

        panel.fill(bg)
        self._hits.clear()

        W, H = panel.get_size()

        # Layout: two columns (left main, right kochbender panel)
        pad = max(14, int(min(W, H) * 0.03))
        gap = max(12, int(pad * 0.75))
        right_w = int(W * 0.42)
        left_w = W - pad * 2 - gap - right_w
        left_x = pad
        right_x = left_x + left_w + gap
        top_y = pad

        # Title
        title = title_font.render("Character Creation", True, fg)
        panel.blit(title, (left_x, top_y))
        ty = top_y + title.get_height() + gap

        # Name field
        ty = self._draw_name(panel, (left_x, ty, left_w, 42), font, fg, sel, dim, st)

        # Species field
        ty = self._draw_species(panel, (left_x, ty, left_w, 42), font, fg, sel, dim, st)

        # Class section + class selection grid
        ty += 6
        ty = self._draw_class(panel, (left_x, ty, left_w, 270), font, small, fg, sel, dim, st)

        # Stats section (lower left)
        ty += 12
        stats_h = 170
        ty = self._draw_stats(panel, (left_x, ty, left_w, stats_h), font, small, fg, sel, dim, bar_bg, st)

        # Footer hint (reserve space so buttons never overlap it)
        hint = "Arrows/WASD move • Enter confirm • Esc back • F11 fullscreen"
        hint_s = small.render(hint, True, dim)
        footer_y = H - pad - hint_s.get_height()
        panel.blit(hint_s, (left_x, footer_y))

        # Seed + Start button row near bottom (clamped above footer)
        bottom_area_h = 130
        content_bottom = footer_y - 8
        bottom_y = content_bottom - bottom_area_h
        self._draw_seed(panel, (left_x, bottom_y, left_w, 86), font, small, fg, sel, dim, bar_bg, st)
        self._draw_start(panel, (left_x, bottom_y + 92, left_w, 38), font, fg, sel, dim, st)

        # Right panel (Kochbender-specific)
        self._draw_right(panel, (right_x, top_y, right_w, H - pad * 2), font, small, fg, sel, dim, bar_bg, st)

    # ------------------------------------------------------------------ #
    # State init / apply
    # ------------------------------------------------------------------ #

    def _init_state(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        cfg = getattr(manager, "cfg", None)
        ch = default_character()

        # keep any config-provided seed if present
        default_seed = getattr(cfg, "seed", None) if cfg else None
        if default_seed is not None:
            ch.seed = default_seed

        self.state = CharCreateState(char=ch, class_idx=0)
        self._name_touched = False
        self._sync_monk_state()

    # ------------------------------------------------------------------ #
    # Monk chakra selection helpers
    # ------------------------------------------------------------------ #

    def _monk_body_schema(self) -> dict:
        """Resolve the body schema for the currently selected species."""
        assert self.state is not None
        st = self.state
        try:
            return resolve_body_schema(st.species_template)
        except Exception:
            return resolve_body_schema("human_base")

    def _monk_can_unlock(self, node_id: str, unlocked: set[str]) -> bool:
        """Prefix-based gating: requires all parent prefixes to be unlocked."""
        if node_id in unlocked:
            return False
        parts = node_id.split(".")
        for i in range(1, len(parts)):
            prefix = ".".join(parts[:i])
            if prefix not in unlocked:
                return False
        return True

    def _sync_monk_state(self) -> None:
        """Rebuild monk chakra state from base + pick list."""
        if self.state is None:
            return
        st = self.state
        if not st.is_monk():
            return

        base = st.monk_base or "body"
        # Build a fresh state so gating remains deterministic.
        monk_state = ChakraState(unlocked=set(), active=set())
        monk_state.unlocked = {base}
        monk_state.active = {base}
        monk_state.pattern_root = base

        new_picks: List[str] = []
        for node_id in st.monk_picks:
            if self._monk_can_unlock(node_id, monk_state.unlocked):
                monk_state.unlocked.add(node_id)
                monk_state.active.add(node_id)
                new_picks.append(node_id)

        st.monk_picks = new_picks
        st.monk_state = monk_state

    def _monk_available_nodes(self) -> List[Tuple[str, str, int]]:
        """Return available (unlockable) chakra nodes for Monk selection."""
        if self.state is None:
            return []
        st = self.state
        if not st.is_monk():
            return []

        body_schema = self._monk_body_schema()
        state = st.monk_state or ChakraState(unlocked=set(), active=set())
        unlocked = set(state.unlocked)

        # All nodes that are currently visible per gating rules.
        all_nodes = collect_all_chakra_nodes(body_schema, unlocked)

        results: List[Tuple[str, str, int]] = []
        for node_id, display, depth in all_nodes:
            if node_id == st.monk_base:
                continue
            if node_id in st.monk_picks:
                results.append((node_id, display, depth))
                continue
            if self._monk_can_unlock(node_id, unlocked):
                results.append((node_id, display, depth))

        return results

    def _commit_and_start(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        assert self.state is not None
        st = self.state

        # ensure name non-empty
        if not st.char.name.strip():
            st.char.name = DEFAULT_NAME

        # apply seed options
        if st.seed_mode == "random":
            st.char.use_random_seed = True
            st.char.seed = None
        else:
            st.char.use_random_seed = False
            text = st.seed_text.strip()
            if text:
                try:
                    st.char.seed = int(text)
                except ValueError:
                    st.char.seed = None
            else:
                st.char.seed = None

        # apply class
        setattr(st.char, "char_class", st.char_class)
        setattr(st.char, "player_class", st.char_class)
        st.char.species = st.species_label.lower()
        st.char.template_id = st.species_template

        # apply Kochbender rune pattern only if relevant
        if st.is_kochbender() and st.char.generator == "custom":
            st.char.custom_pattern = st.rune.finalize()
        else:
            # non-Kochbender classes should not carry the custom pattern draft
            st.char.custom_pattern = None

        # apply Monk chakra init (if relevant)
        if st.is_monk():
            self._sync_monk_state()
            if st.monk_state is not None:
                st.char.chakra_init = st.monk_state.to_dict()
        else:
            st.char.chakra_init = None

        manager.character = st.char

        from .dungeon import DungeonScene
        manager.set_scene(DungeonScene())

    # ------------------------------------------------------------------ #
    # Input handling
    # ------------------------------------------------------------------ #

    def _activate(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        assert self.state is not None
        st = self.state

        if st.focus == "name":
            if not self._name_editing:
                self._name_before_edit = st.char.name
                self._name_editing = True
                try:
                    pygame.key.start_text_input()
                except Exception:
                    pass
            else:
                self._name_editing = False
                try:
                    pygame.key.stop_text_input()
                except Exception:
                    pass
            return

        if st.focus == "start":
            self._commit_and_start(manager)
            return

        if st.focus == "seed":
            st.seed_mode = "fixed" if st.seed_mode == "random" else "random"
            st.seed_focus = (st.seed_mode == "fixed")
            return

        if st.focus == "class":
            return

        if st.focus == "rune":
            if not st.is_kochbender():
                return
            sf = self._rune_subfocus
            if sf == "undo":
                st.rune.undo()
                return
            if sf == "clear":
                st.rune.clear()
                return
            if sf == "grid":
                st.char.generator = "custom"
                st.char.custom_pattern = st.rune.finalize()
                return
            return

        self._focus_next(+1)

    def _handle_nav_action(self, action: str, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        assert self.state is not None
        st = self.state

        if st.focus == "seed" and st.seed_focus and st.seed_mode == "fixed":
            if action in (MENU_ACTION_UP, MENU_ACTION_DOWN):
                return

        if action == MENU_ACTION_UP:
            if st.focus == "stats":
                order = ["con", "agi", "int", "res"]
                i = order.index(st.stat_focus) if st.stat_focus in order else 0
                if i > 0:
                    st.stat_focus = order[i - 1]
                    return
                # escape upward out of the stats block
                self._focus_next(-1)
                return

            if st.focus == "rune" and st.is_kochbender():
                order = ["gen", "illum", "grid", "undo", "clear"]
                i = order.index(self._rune_subfocus) if self._rune_subfocus in order else 2
                if i > 0:
                    self._rune_subfocus = order[i - 1]
                    return
                # escape upward out of the rune block
                self._focus_next(-1)
                return

            self._focus_next(-1)
            return

        if action == MENU_ACTION_DOWN:
            if st.focus == "stats":
                order = ["con", "agi", "int", "res"]
                i = order.index(st.stat_focus) if st.stat_focus in order else 0
                if i < len(order) - 1:
                    st.stat_focus = order[i + 1]
                    return
                # escape downward out of the stats block
                self._focus_next(+1)
                return

            if st.focus == "rune" and st.is_kochbender():
                order = ["gen", "illum", "grid", "undo", "clear"]
                i = order.index(self._rune_subfocus) if self._rune_subfocus in order else 2
                if i < len(order) - 1:
                    self._rune_subfocus = order[i + 1]
                    return
                # escape downward out of the rune block
                self._focus_next(+1)
                return

            self._focus_next(+1)
            return

        if action in (MENU_ACTION_LEFT, MENU_ACTION_RIGHT):
            delta = -1 if action == MENU_ACTION_LEFT else 1

            if st.focus == "class":
                st.class_idx = (st.class_idx + delta) % len(CHAR_CLASSES)
                if st.is_monk():
                    self._sync_monk_state()
                return

            if st.focus == "species":
                st.species_idx = (st.species_idx + delta) % len(st.species)
                if st.is_monk():
                    self._sync_monk_state()
                return

            if st.focus == "stats":
                self._adjust_stat(st.stat_focus, delta)
                return

            if st.focus == "seed":
                st.seed_mode = "random" if st.seed_mode == "fixed" else "fixed"
                st.seed_focus = (st.seed_mode == "fixed")
                return

            if st.focus == "rune" and st.is_kochbender():
                if self._rune_subfocus == "gen":
                    self._cycle_generator(delta)
                elif self._rune_subfocus == "illum":
                    self._cycle_illuminator(delta)
                return


    def _handle_text_key(self, key: int, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        assert self.state is not None
        st = self.state

        # Seed editing via TEXTINPUT; keep a few explicit controls here
        if st.focus == "seed":
            if key == pygame.K_BACKSPACE and st.seed_mode == "fixed":
                st.seed_text = st.seed_text[:-1]
                return
            if key == pygame.K_MINUS and st.seed_mode == "fixed":
                if not st.seed_text:
                    st.seed_text = "-"
                return
            return

        if st.focus == "rune" and st.is_kochbender():
            if key == pygame.K_BACKSPACE:
                st.rune.undo()
                return
            if key == pygame.K_DELETE:
                st.rune.clear()
                return
            if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                st.char.generator = "custom"
                st.char.custom_pattern = st.rune.finalize()
                return
            # convenience hotkeys
            if key == pygame.K_g:
                self._cycle_generator(+1)
                return
            if key == pygame.K_i:
                self._cycle_illuminator(+1)
                return


    def _handle_click(self, pos: Tuple[int, int], manager: "SceneManager") -> None:  # type: ignore[name-defined]
        assert self.state is not None
        st = self.state

        # If we were typing a name and the user clicks elsewhere, confirm and leave edit mode.
        if self._name_editing and st.focus == "name":
            for _k, _r in self._hits.items():
                if _r.collidepoint(pos):
                    break
            else:
                self._name_editing = False
                try:
                    pygame.key.stop_text_input()
                except Exception:
                    pass

        for k, r in self._hits.items():
            if r.collidepoint(pos):
                if k == "name":
                    st.focus = "name"
                    return
                if k == "species":
                    st.focus = "species"
                    return
                if k == "class":
                    st.focus = "class"
                    return
                if k.startswith("class_opt:"):
                    st.focus = "class"
                    try:
                        new_idx = int(k.split(":", 1)[1])
                    except Exception:
                        return
                    if 0 <= new_idx < len(CHAR_CLASSES):
                        st.class_idx = new_idx
                        if st.is_monk():
                            self._sync_monk_state()
                    return
                if k.startswith("stat_plus_"):
                    stat = k.split("_")[-1]
                    st.focus = "stats"
                    st.stat_focus = stat
                    self._adjust_stat(stat, +1)
                    return
                if k.startswith("stat_minus_"):
                    stat = k.split("_")[-1]
                    st.focus = "stats"
                    st.stat_focus = stat
                    self._adjust_stat(stat, -1)
                    return
                if k.startswith("stat_row_"):
                    stat = k.split("_")[-1]
                    st.focus = "stats"
                    st.stat_focus = stat
                    return
                if k == "seed":
                    st.focus = "seed"
                    st.seed_focus = True
                    self._set_text_input(st.seed_mode == "fixed" and st.seed_focus)

                    return
                if k == "seed_fixed":
                    st.focus = "seed"
                    st.seed_mode = "fixed"
                    st.seed_focus = True
                    self._set_text_input(st.seed_mode == "fixed" and st.seed_focus)

                    return
                if k == "seed_random":
                    st.focus = "seed"
                    st.seed_mode = "random"
                    st.seed_focus = True
                    self._set_text_input(st.seed_mode == "fixed" and st.seed_focus)

                    return
                if k == "start":
                    st.focus = "start"
                    self._commit_and_start(manager)
                    return

                if k == "rune_grid" and st.is_kochbender():
                    self._rune_subfocus = "grid"
                    st.focus = "rune"
                    self._rune_click(pos, st)
                    return
                if k == "rune_clear" and st.is_kochbender():
                    self._rune_subfocus = "clear"
                    st.focus = "rune"
                    st.rune.clear()
                    return
                if k == "rune_undo" and st.is_kochbender():
                    self._rune_subfocus = "undo"
                    st.focus = "rune"
                    st.rune.undo()
                    return
                if k == "gen_left" and st.is_kochbender():
                    self._rune_subfocus = "gen"
                    st.focus = "rune"
                    self._cycle_generator(-1)
                    return
                if k == "gen_right" and st.is_kochbender():
                    self._rune_subfocus = "gen"
                    st.focus = "rune"
                    self._cycle_generator(+1)
                    return
                if k == "illum_left" and st.is_kochbender():
                    self._rune_subfocus = "illum"
                    st.focus = "rune"
                    self._cycle_illuminator(-1)
                    return
                if k == "illum_right" and st.is_kochbender():
                    self._rune_subfocus = "illum"
                    st.focus = "rune"
                    self._cycle_illuminator(+1)
                    return

                if k.startswith("monk_base:") and st.is_monk():
                    base_id = k.split(":", 1)[1]
                    st.focus = "chakra"
                    st.monk_base = base_id
                    st.monk_picks = []
                    self._sync_monk_state()
                    return

                if k.startswith("monk_pick:") and st.is_monk():
                    node_id = k.split(":", 1)[1]
                    st.focus = "chakra"
                    if node_id in st.monk_picks:
                        st.monk_picks = [n for n in st.monk_picks if n != node_id]
                    else:
                        if len(st.monk_picks) < 3:
                            st.monk_picks.append(node_id)
                    self._sync_monk_state()
                    return

        st.seed_focus = False
        self._set_text_input(False)


    def _handle_motion(self, pos: Tuple[int, int], manager: "SceneManager") -> None:  # type: ignore[name-defined]
        if self.state is None:
            return
        st = self.state

        # MenuScene-style hover highlight: whichever hitbox we're over becomes "hot".
        self._hover_key = None
        for k, r in self._hits.items():
            if r.collidepoint(pos):
                self._hover_key = k
                break

        # Rune-grid hover (separate from general hover_key; used for snapping feedback)
        if st.is_kochbender():
            grid = self._hits.get("rune_grid")
            if grid and grid.collidepoint(pos):
                gx, gy = self._rune_snap(pos, grid)
                st.rune.hover = (gx, gy)
            else:
                st.rune.hover = None


    # ------------------------------------------------------------------ #
    # Drawing helpers
    # ------------------------------------------------------------------ #

    def _draw_box(self, surf: pygame.Surface, rect: pygame.Rect, *, fill, border, border_w: int = 1) -> None:
        pygame.draw.rect(surf, fill, rect)
        pygame.draw.rect(surf, border, rect, border_w)

    def _draw_name(
        self,
        surf: pygame.Surface,
        area: Tuple[int, int, int, int],
        font: pygame.font.Font,
        fg, sel, dim,
        st: CharCreateState,
    ) -> int:
        x, y, w, h = area
        label = "Name:"
        hot = (st.focus == "name") or (self._hover_key == "name")
        col = sel if hot else fg
        txt = font.render(label, True, col)
        surf.blit(txt, (x, y))
        box = pygame.Rect(x + txt.get_width() + 10, y - 2, min(360, w - txt.get_width() - 10), h)
        self._hits["name"] = box

        self._draw_box(
            surf, box,
            fill=(18, 18, 30),
            border=sel if hot else dim,
            border_w=2 if (st.focus == "name") else 1,
        )

        val = st.char.name
        if not val:
            val = "(enter name)"
        val_s = font.render(val, True, fg)
        surf.blit(val_s, (box.x + 10, box.y + (box.h - val_s.get_height()) // 2))

        # caret while editing
        if st.focus == "name" and self._name_editing:
            caret_x = box.x + 12 + val_s.get_width()
            caret_y = box.y + (box.h - val_s.get_height()) // 2
            pygame.draw.line(surf, sel, (caret_x, caret_y), (caret_x, caret_y + val_s.get_height()), 2)
        return y + h + 6

    def _draw_class(
        self,
        surf: pygame.Surface,
        area: Tuple[int, int, int, int],
        font: pygame.font.Font,
        small: pygame.font.Font,
        fg, sel, dim,
        st: CharCreateState,
    ) -> int:
        x, y, w, h = area
        hot = (st.focus == "class") or (self._hover_key == "class")
        col = sel if hot else fg
        lab = font.render("Class:", True, col)
        surf.blit(lab, (x, y))

        cls = st.char_class
        cls_s = font.render(cls, True, col if st.focus == "class" else fg)
        cls_rect = pygame.Rect(x + lab.get_width() + 10, y - 2, min(360, w - lab.get_width() - 10), cls_s.get_height() + 10)
        self._hits["class"] = cls_rect

        self._draw_box(
            surf, cls_rect,
            fill=(18, 18, 30),
            border=sel if hot else dim,
            border_w=2 if (st.focus == "class") else 1,
        )
        surf.blit(cls_s, (cls_rect.x + 10, cls_rect.y + 5))

        desc = CLASS_DESCRIPTIONS.get(cls, "")
        dy = y + max(cls_rect.h, lab.get_height()) + 10
        if desc:
            lines = self._wrap(desc, small, w)
            for line in lines[:3]:
                s = small.render(line, True, dim)
                surf.blit(s, (x, dy))
                dy += s.get_height() + 2

        # Always show the full class list in a 3-column grid.
        dy += 8
        cols = 3
        gap_x = 8
        gap_y = 6
        cell_w = max(120, (w - gap_x * (cols - 1)) // cols)
        cell_h = max(30, small.get_height() + 10)
        rows = (len(CHAR_CLASSES) + cols - 1) // cols

        for idx, class_name in enumerate(CHAR_CLASSES):
            row = idx // cols
            col_idx = idx % cols
            rect = pygame.Rect(
                x + col_idx * (cell_w + gap_x),
                dy + row * (cell_h + gap_y),
                cell_w,
                cell_h,
            )
            self._hits[f"class_opt:{idx}"] = rect

            selected = (idx == (st.class_idx % len(CHAR_CLASSES)))
            hovered = (self._hover_key == f"class_opt:{idx}")
            focused_selected = (st.focus == "class" and selected)
            border_col = sel if (selected or hovered) else dim
            fill_col = (28, 28, 44) if selected else (18, 18, 30)
            border_w = 2 if (selected or focused_selected) else 1
            self._draw_box(surf, rect, fill=fill_col, border=border_col, border_w=border_w)

            text_col = sel if selected else fg
            txt = small.render(class_name, True, text_col)
            surf.blit(txt, (rect.x + 8, rect.y + (rect.h - txt.get_height()) // 2))

        return dy + rows * (cell_h + gap_y)

    def _draw_species(
        self,
        surf: pygame.Surface,
        area: Tuple[int, int, int, int],
        font: pygame.font.Font,
        fg, sel, dim,
        st: CharCreateState,
    ) -> int:
        x, y, w, h = area
        hot = (st.focus == "species") or (self._hover_key == "species")
        col = sel if hot else fg
        lab = font.render("Species:", True, col)
        surf.blit(lab, (x, y))

        label = st.species_label
        val_s = font.render(label, True, col if st.focus == "species" else fg)
        val_rect = pygame.Rect(x + lab.get_width() + 10, y - 2, min(260, w - lab.get_width() - 10), val_s.get_height() + 10)
        self._hits["species"] = val_rect

        self._draw_box(
            surf, val_rect,
            fill=(18, 18, 30),
            border=sel if hot else dim,
            border_w=2 if (st.focus == "species") else 1,
        )
        surf.blit(val_s, (val_rect.x + 10, val_rect.y + 5))

        return y + h + 6

    def _draw_stats(
        self,
        surf: pygame.Surface,
        area: Tuple[int, int, int, int],
        font: pygame.font.Font,
        small: pygame.font.Font,
        fg, sel, dim, bar_bg,
        st: CharCreateState,
    ) -> int:
        x, y, w, h = area

        title = font.render(f"Stats (Points left: {st.char.point_pool})", True, fg)
        surf.blit(title, (x, y))
        y += title.get_height() + 8

        rows = [("con", "Constitution"), ("agi", "Agility"), ("int", "Intelligence"), ("res", "Resonance")]
        for stat, label in rows:
            row_sel = (st.focus == "stats" and st.stat_focus == stat)
            row_hover = self._hover_key in (f"stat_row_{stat}", f"stat_plus_{stat}", f"stat_minus_{stat}")
            hot = row_sel or row_hover
            col = sel if hot else fg

            minus = pygame.Rect(x, y + 2, 24, 24)
            plus = pygame.Rect(x + 30, y + 2, 24, 24)
            self._hits[f"stat_minus_{stat}"] = minus
            self._hits[f"stat_plus_{stat}"] = plus

            pygame.draw.rect(surf, bar_bg, minus)
            pygame.draw.rect(surf, bar_bg, plus)
            pygame.draw.rect(surf, sel if hot else dim, minus, 2 if row_sel else 1)
            pygame.draw.rect(surf, sel if hot else dim, plus, 2 if row_sel else 1)

            m = small.render("-", True, fg)
            p = small.render("+", True, fg)
            surf.blit(m, m.get_rect(center=minus.center))
            surf.blit(p, p.get_rect(center=plus.center))

            val = st.char.stats.get(stat, 0)
            txt = font.render(f"{label}: {val}", True, col)
            tx = x + 70
            surf.blit(txt, (tx, y))
            self._hits[f"stat_row_{stat}"] = pygame.Rect(tx, y, txt.get_width(), txt.get_height())
            y += txt.get_height() + 6

        return area[1] + h

    def _draw_seed(
        self,
        surf: pygame.Surface,
        area: Tuple[int, int, int, int],
        font: pygame.font.Font,
        small: pygame.font.Font,
        fg, sel, dim, bar_bg,
        st: CharCreateState,
    ) -> None:
        x, y, w, h = area
        hot_title = (st.focus == "seed") or (self._hover_key in ("seed","seed_fixed","seed_random"))
        col = sel if hot_title else fg
        title = font.render("World Seed", True, col)
        surf.blit(title, (x, y))
        y += title.get_height() + 6

        def radio(cx, cy, checked, label, key_name):
            pygame.draw.circle(surf, fg, (cx, cy), 9, 2)
            if checked:
                pygame.draw.circle(surf, sel, (cx, cy), 5)
            tcol = sel if checked else fg
            t = small.render(label, True, tcol)
            surf.blit(t, (cx + 14, cy - t.get_height() // 2))
            self._hits[key_name] = pygame.Rect(cx - 12, cy - 12, 24, 24)

        cy1 = y + 10
        radio(x + 10, cy1, st.seed_mode == "fixed", "Fixed:", "seed_fixed")

        box = pygame.Rect(x + 110, y, min(220, w - 140), 30)
        self._hits["seed"] = box
        self._draw_box(
            surf, box,
            fill=(18, 18, 30),
            border=sel if hot_title else dim,
            border_w=2 if (st.focus == "seed") else 1,
        )
        shown = st.seed_text if st.seed_mode == "fixed" else ""
        if shown == "":
            shown = "enter number" if st.seed_mode == "fixed" else "(random)"
        t = small.render(shown, True, fg if st.seed_mode == "fixed" else dim)
        surf.blit(t, (box.x + 8, box.y + (box.h - t.get_height()) // 2))

        cy2 = y + 44
        radio(x + 10, cy2, st.seed_mode == "random", "Random each run", "seed_random")

    def _draw_start(
        self,
        surf: pygame.Surface,
        area: Tuple[int, int, int, int],
        font: pygame.font.Font,
        fg, sel, dim,
        st: CharCreateState,
    ) -> None:
        x, y, w, h = area
        rect = pygame.Rect(x, y, min(320, w), h)
        self._hits["start"] = rect

        focused = (st.focus == "start")
        hot = focused or (self._hover_key == "start")
        self._draw_box(
            surf, rect,
            fill=(20, 60, 30) if focused else (18, 40, 26),
            border=sel if hot else dim,
            border_w=2 if focused else 1,
        )
        t = font.render("Start Run", True, fg)
        surf.blit(t, (rect.centerx - t.get_width() // 2, rect.centery - t.get_height() // 2))

    def _draw_right(
        self,
        surf: pygame.Surface,
        area: Tuple[int, int, int, int],
        font: pygame.font.Font,
        small: pygame.font.Font,
        fg, sel, dim, bar_bg,
        st: CharCreateState,
    ) -> None:
        x, y, w, h = area
        panel = pygame.Rect(x, y, w, h)
        self._draw_box(surf, panel, fill=(14, 14, 24), border=(60, 60, 80), border_w=1)

        if st.is_monk():
            self._draw_monk_panel(surf, area, font, small, fg, sel, dim, bar_bg, st)
            return

        if not st.is_kochbender():
            t = small.render("Class-specific panel", True, dim)
            surf.blit(t, (x + 14, y + 14))
            t2 = small.render("(Kochbender shows rune grid)", True, dim)
            surf.blit(t2, (x + 14, y + 14 + t.get_height() + 4))
            return

        head = font.render("Kochbender", True, fg)
        surf.blit(head, (x + 14, y + 12))
        y0 = y + head.get_height() + 10

        gen_y = y0
        gen_hot = (st.focus == "rune" and self._rune_subfocus == "gen") or (self._hover_key in ("gen_left","gen_right"))
        gen_label = small.render("Generator", True, sel if gen_hot else fg)
        surf.blit(gen_label, (x + 14, gen_y))
        gen_box = pygame.Rect(x + 120, gen_y - 4, w - 140, 30)
        self._draw_box(surf, gen_box, fill=(18, 18, 30), border=sel if gen_hot else dim, border_w=2 if (st.focus == "rune" and self._rune_subfocus == "gen") else 1)

        left_btn = pygame.Rect(gen_box.x + 6, gen_box.y + 6, 18, 18)
        right_btn = pygame.Rect(gen_box.right - 24, gen_box.y + 6, 18, 18)
        self._hits["gen_left"] = left_btn
        self._hits["gen_right"] = right_btn
        pygame.draw.rect(surf, bar_bg, left_btn)
        pygame.draw.rect(surf, bar_bg, right_btn)
        pygame.draw.rect(surf, dim, left_btn, 1)
        pygame.draw.rect(surf, dim, right_btn, 1)
        l = small.render("<", True, fg)
        r = small.render(">", True, fg)
        surf.blit(l, l.get_rect(center=left_btn.center))
        surf.blit(r, r.get_rect(center=right_btn.center))

        gen_val = st.char.generator
        if gen_val == "custom":
            gen_val = "custom (edit below)"
        val_s = small.render(gen_val, True, fg)
        surf.blit(val_s, (left_btn.right + 8, gen_box.y + (gen_box.h - val_s.get_height()) // 2))

        illum_y = gen_y + 40
        illum_hot = (st.focus == "rune" and self._rune_subfocus == "illum") or (self._hover_key in ("illum_left","illum_right"))
        illum_label = small.render("Illuminator", True, sel if illum_hot else fg)
        surf.blit(illum_label, (x + 14, illum_y))
        illum_box = pygame.Rect(x + 120, illum_y - 4, w - 140, 30)
        self._draw_box(surf, illum_box, fill=(18, 18, 30), border=sel if illum_hot else dim, border_w=2 if (st.focus == "rune" and self._rune_subfocus == "illum") else 1)
        il = pygame.Rect(illum_box.x + 6, illum_box.y + 6, 18, 18)
        ir = pygame.Rect(illum_box.right - 24, illum_box.y + 6, 18, 18)
        self._hits["illum_left"] = il
        self._hits["illum_right"] = ir
        pygame.draw.rect(surf, bar_bg, il)
        pygame.draw.rect(surf, bar_bg, ir)
        pygame.draw.rect(surf, dim, il, 1)
        pygame.draw.rect(surf, dim, ir, 1)
        surf.blit(l, l.get_rect(center=il.center))
        surf.blit(r, r.get_rect(center=ir.center))
        illum_val = small.render(str(st.char.illuminator), True, fg)
        surf.blit(illum_val, (il.right + 8, illum_box.y + (illum_box.h - illum_val.get_height()) // 2))

        grid_top = illum_y + 46
        grid_h = h - (grid_top - area[1]) - 70
        grid = pygame.Rect(x + 14, grid_top, w - 28, max(120, grid_h))
        self._hits["rune_grid"] = grid

        focused = (st.focus == "rune" and self._rune_subfocus == "grid")
        hot_grid = focused or (self._hover_key == "rune_grid")
        self._draw_box(
            surf,
            grid,
            fill=(8, 8, 14),
            border=sel if hot_grid else dim,
            border_w=2 if focused else 1,
        )
        self._draw_rune_grid(surf, grid, small, fg, sel, dim, st)

        btn_y = grid.bottom + 10
        btn_w = (grid.w - 10) // 2
        undo = pygame.Rect(grid.x, btn_y, btn_w, 30)
        clear = pygame.Rect(grid.x + btn_w + 10, btn_y, btn_w, 30)
        self._hits["rune_undo"] = undo
        self._hits["rune_clear"] = clear
        undo_hot = (st.focus == "rune" and self._rune_subfocus == "undo") or (self._hover_key == "rune_undo")
        clear_hot = (st.focus == "rune" and self._rune_subfocus == "clear") or (self._hover_key == "rune_clear")
        self._draw_box(surf, undo, fill=(30, 30, 44), border=sel if undo_hot else dim, border_w=2 if (st.focus == "rune" and self._rune_subfocus == "undo") else 1)
        self._draw_box(surf, clear, fill=(44, 24, 24), border=sel if clear_hot else dim, border_w=2 if (st.focus == "rune" and self._rune_subfocus == "clear") else 1)
        ut = small.render("Undo (Backspace)", True, fg)
        ct = small.render("Clear (Del)", True, fg)
        surf.blit(ut, (undo.centerx - ut.get_width() // 2, undo.centery - ut.get_height() // 2))
        surf.blit(ct, (clear.centerx - ct.get_width() // 2, clear.centery - ct.get_height() // 2))

        hint = small.render("Click to add points • Enter to commit • G/I cycle", True, dim)
        surf.blit(hint, (grid.x, clear.bottom + 8))

    def _draw_monk_panel(
        self,
        surf: pygame.Surface,
        area: Tuple[int, int, int, int],
        font: pygame.font.Font,
        small: pygame.font.Font,
        fg, sel, dim, bar_bg,
        st: CharCreateState,
    ) -> None:
        """Draw the Monk chakra selection panel (base + 3 picks)."""
        x, y, w, h = area

        head = font.render("Monk", True, fg)
        surf.blit(head, (x + 14, y + 12))
        y0 = y + head.get_height() + 12

        # --- Base chakra selection ---
        lab = small.render("Base Chakra (start here):", True, fg)
        surf.blit(lab, (x + 14, y0))
        y0 += lab.get_height() + 8

        base_opts = [("body", "Body"), ("head", "Head"), ("arm", "Arm"), ("leg", "Leg")]
        btn_w = (w - 40) // 2
        btn_h = 28
        for i, (bid, label) in enumerate(base_opts):
            row = i // 2
            col = i % 2
            bx = x + 14 + col * (btn_w + 12)
            by = y0 + row * (btn_h + 8)
            rect = pygame.Rect(bx, by, btn_w, btn_h)
            is_sel = (st.monk_base == bid)
            hot = is_sel or (self._hover_key == f"monk_base:{bid}")
            self._draw_box(
                surf, rect,
                fill=(24, 24, 36) if is_sel else (18, 18, 30),
                border=sel if hot else dim,
                border_w=2 if is_sel else 1,
            )
            t = small.render(label, True, sel if is_sel else fg)
            surf.blit(t, (rect.centerx - t.get_width() // 2, rect.centery - t.get_height() // 2))
            self._hits[f"monk_base:{bid}"] = rect

        y0 += btn_h * 2 + 18

        # --- Pick list ---
        picks = len(st.monk_picks)
        info = small.render(f"Choose 3 chakras (selected {picks}/3)", True, fg if picks < 3 else sel)
        surf.blit(info, (x + 14, y0))
        y0 += info.get_height() + 8

        list_rect = pygame.Rect(x + 14, y0, w - 28, h - (y0 - y) - 16)
        self._hits["monk_list"] = list_rect
        self._draw_box(surf, list_rect, fill=(10, 10, 18), border=(40, 40, 60), border_w=1)

        nodes = self._monk_available_nodes()
        line_h = small.get_height() + 6
        capacity = max(1, list_rect.h // line_h)
        self._monk_list_total = len(nodes)
        self._monk_list_capacity = capacity

        max_scroll = max(0, len(nodes) - capacity)
        st.monk_scroll = max(0, min(max_scroll, st.monk_scroll))

        start = st.monk_scroll
        end = min(len(nodes), start + capacity)

        for idx in range(start, end):
            node_id, display, depth = nodes[idx]
            row_y = list_rect.y + (idx - start) * line_h
            row_rect = pygame.Rect(list_rect.x + 4, row_y, list_rect.w - 8, line_h)

            selected = node_id in st.monk_picks
            col = sel if selected else fg
            indent = 12 * max(0, depth)
            label = f"{display}"

            if selected:
                pygame.draw.rect(surf, (40, 40, 64), row_rect)
                pygame.draw.rect(surf, sel, row_rect, 1)

            text = small.render(label, True, col if selected else fg)
            surf.blit(text, (row_rect.x + 6 + indent, row_rect.y + (row_rect.h - text.get_height()) // 2))
            self._hits[f"monk_pick:{node_id}"] = row_rect

        hint = small.render("Click entries to toggle • Scroll to see more", True, dim)
        surf.blit(hint, (list_rect.x, list_rect.bottom + 4))

    def _draw_rune_grid(self, surf: pygame.Surface, rect: pygame.Rect, font: pygame.font.Font, fg, sel, dim, st: CharCreateState) -> None:
        # Grid coordinates: x in 0..10, y in -5..5 (11x11)
        grid_w = 10
        grid_h = 10
        cell = min(rect.w / (grid_w + 1), rect.h / (grid_h + 1))
        cell = max(10, cell)

        origin_x = rect.x + (rect.w - int(cell * (grid_w + 1))) // 2
        origin_y = rect.y + (rect.h - int(cell * (grid_h + 1))) // 2

        def to_px(gx: int, gy: int) -> Tuple[int, int]:
            px = int(origin_x + gx * cell)
            py = int(origin_y + (gy + 5) * cell)
            return px, py

        line_col = (50, 50, 80)
        for xi in range(0, 11):
            x = int(origin_x + xi * cell)
            pygame.draw.line(surf, line_col, (x, origin_y), (x, origin_y + int(10 * cell)), 1)
        for yi in range(0, 11):
            y = int(origin_y + yi * cell)
            pygame.draw.line(surf, line_col, (origin_x, y), (origin_x + int(10 * cell), y), 1)

        def draw_dot(pt: Tuple[int, int], color, radius: int = 5):
            px, py = to_px(pt[0], pt[1])
            pygame.draw.circle(surf, color, (px, py), radius)

        draw_dot(st.rune.root, (255, 230, 140))
        draw_dot(st.rune.terminus, (140, 230, 255))

        pts = [st.rune.root] + list(st.rune.midpoints)
        if st.rune.ended or len(st.rune.midpoints) >= st.rune.max_pts:
            pts.append(st.rune.terminus)
        elif st.rune.hover is not None and st.rune.can_add():
            pts.append(st.rune.hover)

        if len(pts) >= 2:
            for i in range(1, len(pts)):
                a = pts[i - 1]
                b = pts[i]
                pa = to_px(a[0], a[1])
                pb = to_px(b[0], b[1])
                pygame.draw.line(surf, (180, 220, 255), pa, pb, 3)

        for (gx, gy) in st.rune.midpoints:
            px, py = to_px(gx, gy)
            pygame.draw.circle(surf, (120, 200, 255), (px, py), 4)

        if st.rune.hover is not None and st.rune.can_add():
            px, py = to_px(st.rune.hover[0], st.rune.hover[1])
            pygame.draw.circle(surf, (255, 230, 120), (px, py), 5, 2)

        legend = font.render(f"pts: {len(st.rune.midpoints)}/{st.rune.max_pts}", True, dim)
        surf.blit(legend, (rect.x + 6, rect.y + 6))

    # ------------------------------------------------------------------ #
    # Rune editor helpers
    # ------------------------------------------------------------------ #

    def _rune_snap(self, pos: Tuple[int, int], rect: pygame.Rect) -> Tuple[int, int]:
        grid_w = 10
        grid_h = 10
        cell = min(rect.w / (grid_w + 1), rect.h / (grid_h + 1))
        cell = max(10, cell)
        origin_x = rect.x + (rect.w - int(cell * (grid_w + 1))) // 2
        origin_y = rect.y + (rect.h - int(cell * (grid_h + 1))) // 2

        mx, my = pos
        gx = int(round((mx - origin_x) / cell))
        gy = int(round((my - origin_y) / cell)) - 5

        gx = max(0, min(10, gx))
        gy = max(-5, min(5, gy))
        return gx, gy

    def _rune_click(self, pos: Tuple[int, int], st: CharCreateState) -> None:
        grid = self._hits.get("rune_grid")
        if not grid:
            return
        gx, gy = self._rune_snap(pos, grid)

        # Clicking the root does nothing.
        if (gx, gy) == st.rune.root:
            return

        # Clicking the terminus ends the pattern immediately (even before max_pts).
        if (gx, gy) == st.rune.terminus:
            st.rune.end_now()
            st.char.generator = "custom"
            st.char.custom_pattern = st.rune.finalize()
            return

        # If the user previously ended early and then clicks a new point, we treat that as reopening the pattern.
        if st.rune.ended:
            st.rune.ended = False

        if st.rune.can_add():
            st.rune.add((gx, gy))
            st.char.generator = "custom"
            st.char.custom_pattern = st.rune.finalize()


    # ------------------------------------------------------------------ #
    # Option cyclers
    # ------------------------------------------------------------------ #

    def _cycle_generator(self, delta: int) -> None:
        assert self.state is not None
        st = self.state
        try:
            i = st.generators.index(st.char.generator)
        except ValueError:
            i = 0
        st.char.generator = st.generators[(i + delta) % len(st.generators)]

    def _cycle_illuminator(self, delta: int) -> None:
        assert self.state is not None
        st = self.state
        try:
            i = st.illuminators.index(st.char.illuminator)
        except ValueError:
            i = 0
        st.char.illuminator = st.illuminators[(i + delta) % len(st.illuminators)]

    # ------------------------------------------------------------------ #
    # Stats
    # ------------------------------------------------------------------ #

    def _adjust_stat(self, stat: str, delta: int) -> None:
        assert self.state is not None
        st = self.state
        cur = st.char.stats.get(stat, 0)
        if delta > 0 and st.char.point_pool <= 0:
            return
        new_val = cur + delta
        if new_val < 0:
            return
        st.char.stats[stat] = new_val
        st.char.point_pool -= delta

    # ------------------------------------------------------------------ #
    # Focus
    # ------------------------------------------------------------------ #

    def _focus_next(self, step: int) -> None:
        assert self.state is not None
        st = self.state

        order = ["name", "species", "class", "stats", "seed", "start"]
        if st.is_kochbender():
            order = ["name", "species", "class", "stats", "seed", "rune", "start"]
        elif st.is_monk():
            order = ["name", "species", "class", "stats", "seed", "chakra", "start"]

        try:
            i = order.index(st.focus)
        except ValueError:
            i = 0
        old_focus = st.focus
        st.focus = order[(i + step) % len(order)]

        if old_focus == "name" and self._name_editing and st.focus != "name":
            self._name_editing = False
            try:
                pygame.key.stop_text_input()
            except Exception:
                pass

        if st.focus != "seed":
            st.seed_focus = False
            self._set_text_input(False)


    # ------------------------------------------------------------------ #
    # Text wrapping
    # ------------------------------------------------------------------ #

    def _wrap(self, text: str, font: pygame.font.Font, max_width: int) -> List[str]:
        words = text.split()
        lines: List[str] = []
        cur: List[str] = []
        for w in words:
            test = " ".join(cur + [w]) if cur else w
            if font.size(test)[0] <= max_width:
                cur.append(w)
            else:
                if cur:
                    lines.append(" ".join(cur))
                cur = [w]
        if cur:
            lines.append(" ".join(cur))
        return lines or [text]
