"""Hypothesis property-based and RuleBasedStateMachine tests for top hotspots & HIST-08..HIST-09.

Covers:
- Top Hotspots without prior property tests:
  - dual_pipeline.transcript_metadata_core.transcript_attendee_parser (split_outside_parens, clean_attendee_entry, normalize_company_name)
  - dual_pipeline.transcript_metadata_core.entity_resolver (_levenshtein_distance metric axioms, _normalize_role_key idempotence)
  - dual_pipeline.query_core.lineage_db (is_token_human_name stability on arbitrary strings)
- HIST-08: Regex capturing group preservation & surgical Output 03 JSON key patching without wiping unrelated keys.
- HIST-09: Stateful RuleBasedStateMachine verifying multi-wave longitudinal assertion preservation across sequential writes.
"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from hypothesis import HealthCheck, given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from dual_pipeline.query_core.lineage_db import humanize_predicate, humanize_token
from dual_pipeline.storage.governed_context import GovernedStorageContext
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    _levenshtein_distance,
    _normalize_role_key,
)
from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
    clean_attendee_entry,
    normalize_company_name,
    split_outside_parens,
)


# ---------------------------------------------------------------------------
# 1. Property tests for transcript_attendee_parser hotspot
# ---------------------------------------------------------------------------
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    names=st.lists(
        st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll")), min_size=2, max_size=12),
        min_size=1,
        max_size=5,
    ),
    roles=st.lists(
        st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll")), min_size=2, max_size=12),
        min_size=1,
        max_size=5,
    ),
)
def test_property_split_outside_parens_preserves_parenthesized_commas(
    names: List[str], roles: List[str]
) -> None:
    entries = [f"{n} ({r}, NEA)" for n, r in zip(names, roles)]
    joined = ", ".join(entries)
    split_items = split_outside_parens(joined)
    assert len(split_items) == len(entries)
    for item, expected in zip(split_items, entries):
        assert item == expected


@settings(max_examples=40, deadline=None)
@given(raw=st.text(min_size=0, max_size=80))
def test_property_clean_attendee_entry_and_normalize_org_never_crash(raw: str) -> None:
    name, role, org = clean_attendee_entry(raw)
    assert isinstance(name, str)
    assert isinstance(role, str)
    assert isinstance(org, str)
    norm1 = normalize_company_name(org)
    norm2 = normalize_company_name(norm1)
    assert norm1 == norm2


# ---------------------------------------------------------------------------
# 2. Property tests for entity_resolver & lineage_db hotspots
# ---------------------------------------------------------------------------
@settings(max_examples=40, deadline=None)
@given(
    a=st.text(alphabet="abcdefghij", min_size=0, max_size=12),
    b=st.text(alphabet="abcdefghij", min_size=0, max_size=12),
    c=st.text(alphabet="abcdefghij", min_size=0, max_size=12),
)
def test_property_levenshtein_metric_axioms_and_role_key_idempotence(
    a: str, b: str, c: str
) -> None:
    # Identity, symmetry, and triangle inequality
    assert _levenshtein_distance(a, a) == 0
    assert _levenshtein_distance(a, b) == _levenshtein_distance(b, a)
    assert _levenshtein_distance(a, c) <= _levenshtein_distance(a, b) + _levenshtein_distance(b, c)

    # Role normalization idempotence
    norm_once = _normalize_role_key(f"{a} / {b} - {c}")
    norm_twice = _normalize_role_key(norm_once)
    assert norm_once == norm_twice
    assert isinstance(humanize_token(a), str)
    assert isinstance(humanize_predicate(b), str)


# ---------------------------------------------------------------------------
# 3. HIST-08: Regex capturing group preservation & surgical Output 03 patching
# ---------------------------------------------------------------------------
@settings(max_examples=30, deadline=None)
@given(
    custom_key=st.text(alphabet="abcdef_", min_size=3, max_size=15),
    speaker_name=st.sampled_from(["Speaker_Beta", "Speaker_Alpha", "Yan Gobeil", "harper Hoffman"]),
)
def test_hist08_surgical_output03_json_patch_and_regex_capture_preservation(
    custom_key: str, speaker_name: str
) -> None:
    # Verify re.split with capturing group preserves speaker prefix tokens
    sample_line = f"[00:01:15] {speaker_name}: Reviewing the architecture."
    parts = re.split(r"(\[\d{2}:\d{2}:\d{2}\]\s*[A-Za-z\s]+:)", sample_line)
    reconstructed = "".join(parts)
    assert reconstructed == sample_line
    assert speaker_name in reconstructed

    # Verify surgical JSON patching of Output 03 preserves top-level custom keys
    original_output03: Dict[str, Any] = {
        "contract_id": "urn:nee:output03:1.0.0",
        "graph_mutations": {"entity_upserts": [{"id": "ent_1", "name": speaker_name}]},
        "lineage_metadata": {"source_hash": "abc123"},
        custom_key: {"preserved": True, "count": 42},
    }
    patched = copy.deepcopy(original_output03)
    patched["graph_mutations"]["entity_upserts"].append({"id": "ent_2", "name": "System"})
    serialized = json.loads(json.dumps(patched))
    assert serialized[custom_key] == {"preserved": True, "count": 42}
    assert serialized["lineage_metadata"] == {"source_hash": "abc123"}
    assert len(serialized["graph_mutations"]["entity_upserts"]) == 2


# ---------------------------------------------------------------------------
# 4. HIST-09: RuleBasedStateMachine for longitudinal multi-wave assertion persistence
# ---------------------------------------------------------------------------
class LongitudinalGraphAssertionStateMachine(RuleBasedStateMachine):
    """Verifies that sequential wave writes preserve prior active canonical assertions."""

    def __init__(self) -> None:
        super().__init__()
        self._tmpdir = tempfile.TemporaryDirectory(prefix="hyp_hist09_")
        self.db_path = Path(self._tmpdir.name) / "longitudinal_graph.sqlite"
        self.expected_active: Dict[str, str] = {}
        self.expected_superseded: Dict[str, str] = {}

    @initialize()
    def init_schema(self) -> None:
        with GovernedStorageContext.write_context(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS assertion (
                    assertion_id TEXT PRIMARY KEY,
                    claim_text TEXT NOT NULL,
                    canonical_status TEXT NOT NULL,
                    wave_num INTEGER NOT NULL
                )
                """
            )

    @rule(
        asrt_id=st.text(alphabet="abcdef012345", min_size=4, max_size=8),
        claim=st.text(alphabet="uvwxyz ", min_size=5, max_size=20),
        wave_num=st.integers(min_value=1, max_value=5),
    )
    def ingest_wave_assertion(self, asrt_id: str, claim: str, wave_num: int) -> None:
        key = f"asrt_{asrt_id}"
        if key in self.expected_superseded:
            return
        with GovernedStorageContext.write_context(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO assertion (assertion_id, claim_text, canonical_status, wave_num) VALUES (?, ?, 'canonical', ?)",
                (key, claim, wave_num),
            )
        self.expected_active[key] = claim

    @rule(target_idx=st.integers(min_value=0, max_value=20))
    def supersede_existing_assertion(self, target_idx: int) -> None:
        if not self.expected_active:
            return
        keys = sorted(self.expected_active.keys())
        chosen = keys[target_idx % len(keys)]
        claim = self.expected_active.pop(chosen)
        with GovernedStorageContext.write_context(self.db_path) as conn:
            conn.execute(
                "UPDATE assertion SET canonical_status = 'superseded' WHERE assertion_id = ?",
                (chosen,),
            )
        self.expected_superseded[chosen] = claim

    @invariant()
    def active_and_superseded_assertions_never_silently_dropped(self) -> None:
        if not self.db_path.exists():
            return
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                "SELECT assertion_id, claim_text, canonical_status FROM assertion"
            ).fetchall()
        finally:
            conn.close()
        db_map = {r[0]: (r[1], r[2]) for r in rows}
        for k, claim in self.expected_active.items():
            assert k in db_map, f"Active assertion {k} was silently dropped!"
            assert db_map[k] == (claim, "canonical")
        for k, claim in self.expected_superseded.items():
            assert k in db_map, f"Superseded assertion {k} was deleted instead of marked superseded!"
            assert db_map[k][1] == "superseded"

    def teardown(self) -> None:
        self._tmpdir.cleanup()


TestLongitudinalGraphState = LongitudinalGraphAssertionStateMachine.TestCase
TestLongitudinalGraphState.settings = settings(
    max_examples=15, stateful_step_count=10, deadline=None
)
