"""
tests/property/test_graph_healer_json_patch_properties.py

Hypothesis generative property suite verifying surgical JSON subtree patching,
regex capturing group safety on corrupt dates and compound strings, and idempotent
healing transitions in dual_pipeline/transcript_qa_core/graph_healer.py.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, List

from hypothesis import HealthCheck, given, settings, strategies as st

from dual_pipeline.transcript_qa_core.graph_healer import (
    _canonicalize_entity_prefix,
    _heal_actions,
    _heal_compound_attributes,
    _heal_dates,
    _heal_entities,
    _heal_signals,
    _strip_trailing_fragments,
    _unpack_single_packed_string,
    heal_evidence_locators,
    heal_graph_projection,
)


@settings(max_examples=55, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    custom_key=st.from_regex(r"custom_[a-z]{3,10}", fullmatch=True),
    custom_val=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789 _-", min_size=1, max_size=25),
    ent_slug=st.from_regex(r"[a-z]{3,10}", fullmatch=True),
    speaker_name=st.sampled_from(["Amit Ayer", "Speaker_Beta Chacko", "harper Hoffman", "Jake Stark"]),
)
def test_property_surgical_json_subtree_patch_preserves_unrelated_keys(
    custom_key: str,
    custom_val: str,
    ent_slug: str,
    speaker_name: str,
) -> None:
    """Property 1: heal_graph_projection repairs graph_mutations and evidence_index without mutating unrelated top-level JSON keys."""
    payload: Dict[str, Any] = {
        "schema_version": "5.8",
        custom_key: {"nested_preserved": custom_val, "flag": True},
        "custom_list_preserve": [1, 2, custom_val],
        "evidence_index": {
            "ev-01": {
                "text": f"{speaker_name} confirmed architecture integration, and then.",
                "locator": "[00:01:20]",
                "speaker": speaker_name,
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": f"system:system:{ent_slug}",
                    "type": "system",
                    "name": f"System / {ent_slug.capitalize()}",
                    "description": "Core platform component.",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "id": "org:partner_advisory",
                    "type": "org",
                    "name": "PartnerAdvisory",
                    "description": "Delivery organization.",
                    "evidence_ids": ["ev-01"],
                },
            ],
            "relationship_signals": [
                {
                    "from_id": "org:partner_advisory",
                    "to_id": f"system:{ent_slug}",
                    "predicate": "connected-to",
                    "evidence_ids": ["ev-01"],
                }
            ],
        },
    }
    changes: List[str] = []
    healed = heal_graph_projection(copy.deepcopy(payload), changes, metadata={"is_document": True})

    # Unrelated keys must remain untouched
    assert healed[custom_key] == {"nested_preserved": custom_val, "flag": True}
    assert healed["custom_list_preserve"] == [1, 2, custom_val]
    assert healed["schema_version"] == "5.8"

    # Verify serialization round-trip integrity
    roundtrip = json.loads(json.dumps(healed))
    assert roundtrip[custom_key] == payload[custom_key]


@settings(max_examples=55, deadline=None)
@given(
    prefix=st.sampled_from(["person:", "org:", "system:", "workstream:", "project:", "metric:", "role:", "policy:"]),
    repeat_count=st.integers(min_value=1, max_value=5),
    slug=st.from_regex(r"[a-z0-9\-]{3,15}", fullmatch=True),
)
def test_property_entity_prefix_canonicalization_idempotence_and_group_safety(
    prefix: str, repeat_count: int, slug: str
) -> None:
    """Property 2: Regex capturing group deduplication on repeated prefixes is idempotent and preserves the entity slug."""
    corrupt_id = f"{prefix * repeat_count}{slug}"
    healed_once = _canonicalize_entity_prefix(corrupt_id)
    healed_twice = _canonicalize_entity_prefix(healed_once)

    assert healed_once == f"{prefix}{slug}"
    assert healed_once == healed_twice


@settings(max_examples=55, deadline=None)
@given(
    corrupt_date=st.one_of(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz!@#$%^&*()", min_size=1, max_size=20),
        st.sampled_from(["2026-10-25", "10/25/2026", "2026/09/15", "TBD", "invalid-date-9999", ""]),
    ),
    valid_iso=st.dates().map(lambda d: d.strftime("%Y-%m-%d")),
)
def test_property_heal_dates_regex_safety_on_corrupt_and_valid_dates(
    corrupt_date: str, valid_iso: str
) -> None:
    """Property 3: _heal_dates safely normalizes valid ISO dates to MM/DD/YYYY and never raises on corrupt date strings."""
    mutations: Dict[str, Any] = {
        "decision_updates": [
            {"observed_at": corrupt_date, "stale_after": valid_iso, "due_date": corrupt_date}
        ],
        "risk_updates": [
            {"observed_at": valid_iso, "stale_after": corrupt_date}
        ],
    }
    changes: List[str] = []
    _heal_dates(mutations, changes)

    dec = mutations["decision_updates"][0]
    risk = mutations["risk_updates"][0]
    # Valid ISO date must be converted to MM/DD/YYYY format (10 chars with slashes)
    assert len(dec["stale_after"]) == 10 and dec["stale_after"][2] == "/" and dec["stale_after"][5] == "/"
    assert len(risk["observed_at"]) == 10 and risk["observed_at"][2] == "/" and risk["observed_at"][5] == "/"


@settings(max_examples=55, deadline=None)
@given(
    k1=st.from_regex(r"[a-z]{2,8}", fullmatch=True),
    v1=st.from_regex(r"[a-zA-Z0-9]{2,10}", fullmatch=True),
    k2=st.from_regex(r"[a-z]{2,8}", fullmatch=True),
    v2=st.from_regex(r"[a-zA-Z0-9]{2,10}", fullmatch=True),
    trailing=st.sampled_from([
        ", and then",
        " and...",
        " but...",
        " and then.",
        ", i don't know.",
        " so yeah.",
        " or something like that.",
    ]),
)
def test_property_compound_attribute_unpacking_and_fragment_stripping_idempotence(
    k1: str, v1: str, k2: str, v2: str, trailing: str
) -> None:
    """Property 4: Unpacking packed key-value strings and stripping conversational fragments are idempotent."""
    if k1 == k2:
        k2 = f"{k2}_alt"
    packed = f"{k1}: {v1}, {k2}: {v2}"
    unpacked = _unpack_single_packed_string(packed)
    assert unpacked == {k1: v1, k2: v2}

    attrs = {"compound": packed, "atomic": "keep_me"}
    changes: List[str] = []
    healed_attrs = _heal_compound_attributes(attrs, changes)
    healed_twice = _heal_compound_attributes(healed_attrs, changes)
    assert healed_attrs == healed_twice
    assert healed_attrs["atomic"] == "keep_me"
    assert healed_attrs[k1] == v1
    assert healed_attrs[k2] == v2

    base_sentence = "We deployed the service to production"
    noisy = f"{base_sentence}{trailing}"
    stripped_once = _strip_trailing_fragments(noisy)
    stripped_twice = _strip_trailing_fragments(stripped_once)
    assert stripped_once == stripped_twice
    assert stripped_once.endswith(".")


@settings(max_examples=55, deadline=None)
@given(
    hh=st.integers(min_value=0, max_value=23),
    mm=st.integers(min_value=0, max_value=59),
    ss=st.integers(min_value=0, max_value=59),
    raw_pred=st.sampled_from([
        "connected_to", "links-to", "part of", "associated_with", "discussed_with", "relates_to", "related-to"
    ]),
    owner1=st.from_regex(r"person:[a-z]{3,8}", fullmatch=True),
    owner2=st.from_regex(r"person:[a-z]{3,8}", fullmatch=True),
)
def test_property_locator_and_signal_healing_idempotence(
    hh: int, mm: int, ss: int, raw_pred: str, owner1: str, owner2: str
) -> None:
    """Property 5: heal_evidence_locators, _heal_signals, _heal_entities, and _heal_actions are idempotent."""
    ts = f"{hh:02d}:{mm:02d}:{ss:02d}"
    data: Dict[str, Any] = {
        "evidence_index": {
            "ev-01": {
                "locator": {"kind": "timestamp", "value": f"  [{ts}] \t"},
                "source_locators": [f"[{ts}]", "  "],
                "evidence_locators": [f" [{ts}] "],
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "unprefixed_system", "type": "system", "name": "Core / Platform"},
                {"id": "org:org:partner_advisory", "type": "org", "name": "PartnerAdvisory & Partners"},
            ],
            "relationship_signals": [
                {"from_id": "unprefixed_system", "to_id": "org:partner_advisory", "predicate": raw_pred}
            ],
            "action_updates": [
                {"owner_id": f"{owner1} / {owner2}", "description": "Complete review"}
            ],
        },
    }
    changes1: List[str] = []
    heal_evidence_locators(data, changes1)
    _heal_entities(data["graph_mutations"]["entity_upserts"], changes1)
    _heal_signals(data["graph_mutations"]["relationship_signals"], changes1)
    _heal_actions(data["graph_mutations"]["action_updates"], changes1)

    snapshot_after_first = copy.deepcopy(data)

    changes2: List[str] = []
    heal_evidence_locators(data, changes2)
    _heal_entities(data["graph_mutations"]["entity_upserts"], changes2)
    _heal_signals(data["graph_mutations"]["relationship_signals"], changes2)
    _heal_actions(data["graph_mutations"]["action_updates"], changes2)

    assert data == snapshot_after_first
    assert data["evidence_index"]["ev-01"]["locator"]["value"] == ts
    assert data["graph_mutations"]["action_updates"][0]["owner_id"] == owner1
