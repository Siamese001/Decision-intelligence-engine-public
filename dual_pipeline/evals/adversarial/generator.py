"""
Synthetic Adversarial Edge-Case Generator.

Synthesizes difficult boundary-stress test fixtures:
- Homoglyph and unicode confusion attacks.
- Cyclic graph topology traps.
- Dense fanout hub nodes.
- Temporal skew and date anomalies.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple

HOMOGLYPH_MAP = {
    "a": "а",  # Cyrillic small letter a
    "e": "е",  # Cyrillic small letter ie
    "o": "о",  # Cyrillic small letter o
    "p": "р",  # Cyrillic small letter er
    "c": "с",  # Cyrillic small letter es
    "x": "х",  # Cyrillic small letter ha
    "y": "у",  # Cyrillic small letter u
    "s": "ѕ",  # Cyrillic small letter dze
    "i": "і",  # Cyrillic small letter byelorussian-ukrainian i
}


class SyntheticAdversarialGenerator:
    """Generates synthetic adversarial perturbations and topological edge cases."""

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def inject_homoglyphs(self, text: str, substitution_rate: float = 0.3) -> str:
        """Replace standard Latin characters with identical-looking Cyrillic homoglyphs."""
        chars = list(text)
        for i, char in enumerate(chars):
            lower_char = char.lower()
            if lower_char in HOMOGLYPH_MAP and self.rng.random() < substitution_rate:
                replacement = HOMOGLYPH_MAP[lower_char]
                chars[i] = replacement.upper() if char.isupper() else replacement
        return "".join(chars)

    def generate_cyclic_graph(self, num_nodes: int = 4) -> List[Dict[str, Any]]:
        """Generate a cyclic sequence of directed graph assertions (A -> B -> C -> ... -> A)."""
        nodes = [f"node:synthetic_cycle_{i}" for i in range(num_nodes)]
        assertions = []
        for i in range(num_nodes):
            src = nodes[i]
            dst = nodes[(i + 1) % num_nodes]
            assertions.append({
                "assertion_id": f"ast:cycle_{i}_{src}_{dst}",
                "subject_node_id": src,
                "predicate": "depends_on",
                "object_value": dst,
                "status": "CONFIRMED",
            })
        return assertions

    def generate_star_hub(
        self, hub_node_id: str = "node:super_hub", leaf_count: int = 30
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Generate a dense hub node with high fan-out leaf nodes to test branch controls."""
        hub = {
            "node_id": hub_node_id,
            "node_type": "system",
            "name": "Central Substation Hub",
        }
        leaves = []
        for i in range(leaf_count):
            leaves.append({
                "node_id": f"node:leaf_feeder_{i}",
                "node_type": "system",
                "name": f"Feeder Line {i}",
                "connected_to": hub_node_id,
            })
        return hub, leaves
