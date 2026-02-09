"""
Chakra passive effects.

This module is intentionally small and data-driven:
- Rules map "active chakra conditions" -> "granted passive effect values".
- Runtime systems (FOV, abilities, etc.) can query aggregated effect values.

Design goal:
Keep chakra condition logic in one place so future chakra passives do not
spread ad-hoc checks across unrelated gameplay systems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Sequence, Set, Tuple


@dataclass(frozen=True)
class ChakraEffectRule:
    """Single passive rule evaluated from a set of active chakra node ids."""

    id: str
    description: str
    # Token conditions are matched against normalized path tokens.
    # Example node id: "head.skull.face.eye_m" -> tokens include "eye".
    requires_all_tokens: Tuple[str, ...] = ()
    requires_any_tokens: Tuple[str, ...] = ()
    # Numeric values are additive when multiple rules grant the same key.
    grants: Dict[str, float] = field(default_factory=dict)


@dataclass
class ChakraEffectSnapshot:
    """Aggregated passive effects for an actor's current chakra activation."""

    active_rules: Set[str] = field(default_factory=set)
    values: Dict[str, float] = field(default_factory=dict)

    def value(self, key: str, default: float = 0.0) -> float:
        return float(self.values.get(str(key), float(default)))


def _normalize_active_tokens(active_nodes: Iterable[str]) -> Set[str]:
    """Return normalized token set from active chakra node ids.

    Normalization rules:
    - Split full node id by "."
    - Lowercase tokens
    - Strip mirror suffix "_m" for shared semantic matching
      (e.g. eye and eye_m both match token "eye")
    """
    out: Set[str] = set()
    for node_id in active_nodes:
        text = str(node_id or "").strip().lower()
        if not text:
            continue
        for tok in text.split("."):
            t = tok.strip()
            if not t:
                continue
            out.add(t)
            if t.endswith("_m"):
                out.add(t[:-2])
    return out


def _rule_matches(rule: ChakraEffectRule, tokens: Set[str]) -> bool:
    if rule.requires_all_tokens:
        for tok in rule.requires_all_tokens:
            if str(tok).lower() not in tokens:
                return False
    if rule.requires_any_tokens:
        ok = False
        for tok in rule.requires_any_tokens:
            if str(tok).lower() in tokens:
                ok = True
                break
        if not ok:
            return False
    return True


DEFAULT_RULES: Tuple[ChakraEffectRule, ...] = (
    ChakraEffectRule(
        id="eye_clarity",
        description="Eye chakra sight attunement (+3 FOV radius).",
        requires_any_tokens=("eye",),
        grants={"fov_radius_bonus": 3.0},
    ),
    ChakraEffectRule(
        id="foot_stride",
        description="Foot chakra stride attunement (20% faster movement).",
        requires_any_tokens=("foot",),
        # Multiplier applied to move action delay: 10 -> 8 by default.
        grants={"move_delay_mult": 0.8},
    ),
)


def evaluate_effects(
    active_nodes: Iterable[str],
    *,
    rules: Sequence[ChakraEffectRule] = DEFAULT_RULES,
) -> ChakraEffectSnapshot:
    """Evaluate all passive chakra rules for the given active chakra ids."""
    tokens = _normalize_active_tokens(active_nodes)
    snap = ChakraEffectSnapshot()
    if not tokens:
        return snap

    for rule in rules:
        if not _rule_matches(rule, tokens):
            continue
        snap.active_rules.add(rule.id)
        for key, val in rule.grants.items():
            k = str(key)
            snap.values[k] = float(snap.values.get(k, 0.0)) + float(val)
    return snap
