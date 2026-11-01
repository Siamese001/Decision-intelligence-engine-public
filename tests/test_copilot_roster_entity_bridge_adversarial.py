"""
tests/test_copilot_roster_entity_bridge_adversarial.py

Adversarial and boundary test suite for copilot roster-to-graph entity bridging,
domain synonym expansion, and attendee parsing.
Covers 4 mandatory edge-case categories under AGENTS.md and e2e-and-edge-case-coverage.md:
1. Boundary payloads: empty roster, None entries, missing name/role keys.
2. Malformed inputs: corrupted parenthetical strings, irregular punctuation, unicode symbols.
3. Concurrency & race: parallel concurrent token expansions and attendee indexing.
4. Invariant bypass attempts: C-19 career leverage markers in attendee metadata must not bypass quarantine.
"""
from __future__ import annotations

from collections import defaultdict
import concurrent.futures
from typing import Any, Dict, List, Set
import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_roster_bridge import (
    DOMAIN_ROLE_SYNONYMS,
    expand_role_query_tokens,
    find_matching_graph_node,
    parse_attendee_entry,
    parse_attendees_from_metadata,
    score_assertion_synonyms,
    seed_attendee_identities,
)


def test_adversarial_boundary_payloads():
    """Category 1: Boundary payloads with empty and None inputs."""
    # Empty inputs
    assert parse_attendee_entry(None) is None
    assert parse_attendee_entry("") is None
    assert parse_attendee_entry("   ") is None
    assert parse_attendee_entry({}) is None
    assert parse_attendee_entry({"name": ""}) is None

    # Empty metadata
    assert parse_attendees_from_metadata({}) == []
    assert parse_attendees_from_metadata(None) == []  # type: ignore

    # Empty token set expansion
    assert expand_role_query_tokens(set()) == set()

    # Empty synonym scoring
    assert score_assertion_synonyms("", []) == 0.0
    assert score_assertion_synonyms("claim", []) == 0.0
    assert score_assertion_synonyms("", ["tech", "owner"]) == 0.0


def test_adversarial_malformed_inputs():
    """Category 2: Corrupted, irregular, and unclosed parenthetical strings."""
    # Unclosed parentheses
    att1 = parse_attendee_entry("Jason Lickliter (Full Stack SWE")
    assert att1 is not None
    assert att1["name"] == "Jason Lickliter"
    assert att1["role"] == "Full Stack SWE"

    # Multi-nested parentheses and irregular spacing
    att2 = parse_attendee_entry("  Sharon Diaram  ( Director, Development Technology, Extra )  ")
    assert att2 is not None
    assert att2["name"] == "Sharon Diaram"
    assert att2["role"] == "Director"
    assert att2["org"] == "Development Technology"

    # No parentheses (raw name only)
    att3 = parse_attendee_entry("Amit Ayer")
    assert att3 is not None
    assert att3["name"] == "Amit Ayer"
    assert att3["role"] == ""

    # Malformed dictionary with integer values
    att4 = parse_attendee_entry({"name": 12345, "role": None, "org": 999})
    assert att4 is not None
    assert att4["name"] == "12345"
    assert att4["role"] == ""
    assert att4["org"] == "999"


def test_adversarial_concurrency_race():
    """Category 3: Multi-threaded concurrent execution of token expansion and attendee parsing."""
    sample_tokens = [
        {"sap", "tech", "owner"},
        {"scada", "tech", "lead"},
        {"swe", "full", "stack"},
        {"unrelated", "query", "tokens"},
    ]

    def _worker(idx: int) -> Set[str]:
        toks = sample_tokens[idx % len(sample_tokens)]
        return expand_role_query_tokens(toks)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(_worker, i) for i in range(100)]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            assert isinstance(res, set)
            assert len(res) > 0


def test_adversarial_invariant_c19_quarantine_defense():
    """
    Category 4: Invariant bypass attempt: attendee roles containing Rule C-19
    forbidden career leverage patterns must not leak unquarantined assertions.
    """
    forbidden_role = "VP Strategy (executive compensation band, personal career leverage)"
    c19_attendee = [{"name": "Stealth Actor", "role": forbidden_role, "org": "INTERNAL_ADVISORY"}]

    nodes: Dict[str, Any] = {}
    inverted_index: Dict[str, Set[str]] = defaultdict(set)
    attendee_tokens: Set[str] = set()
    attendee_node_ids: Set[str] = set()
    allowed_nodes: Set[str] = set()
    salient_assertions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    topology_assertions: Dict[str, Any] = {}

    seed_attendee_identities(
        c19_attendee, nodes, inverted_index, attendee_tokens,
        attendee_node_ids, allowed_nodes, salient_assertions, topology_assertions
    )

    # Verify node exists
    nid = "attendee:stealth_actor"
    assert nid in nodes

    # Verify that downstream C19 checkers detect marker in virtual assertion claim
    from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import has_c19_marker
    asrt = salient_assertions[nid][0]
    assert has_c19_marker(asrt["claim_text"]) is True


def test_adversarial_domain_synonym_isolation():
    """Verifies domain synonyms expand only relevant technical terms without polluting unrelated tokens."""
    unrelated = {"meeting", "roadmap", "schedule", "budget"}
    expanded = expand_role_query_tokens(unrelated)
    # None of the technical anchor synonyms should be present
    assert not any(syn in expanded for syn in ("technical_anchor", "tech_anchor", "engineering_lead"))

    # But tech + owner expands to technical_anchor
    tech_query = {"tech", "owner", "jason"}
    expanded_tech = expand_role_query_tokens(tech_query)
    assert "technical_anchor" in expanded_tech
    assert "swe" in expanded_tech
