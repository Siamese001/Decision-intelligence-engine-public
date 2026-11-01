"""Hypothesis @given metamorphic and RuleBasedStateMachine stateful model-checking suites across top copilot and graph hotspots."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from hypothesis import given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from dual_pipeline.graph_maintenance_core import transcript_turn_resolver
from dual_pipeline.graph_retrieval_core import capability_bridge, clearance_cache
from dual_pipeline.graph_writer_core import fast_promotion
from dual_pipeline.live_meeting_recorder_core import (
    copilot_intents,
    copilot_retriever,
    copilot_roster_bridge,
    speaker_hint_matcher,
)
from dual_pipeline.normalization_core import meeting_postprocess
from dual_pipeline.uwg_core import learning_commit


@settings(max_examples=35, deadline=None)
@given(
    subj=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_", min_size=1, max_size=15),
    pred=st.sampled_from(["HAS_CLAIM_TEXT", "uses_component", "owned by", "  DEPENDS_ON  "]),
    claim=st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,;-", min_size=3, max_size=50),
    hex_sfx=st.from_regex(r"\[[0-9a-f]{6}\]", fullmatch=True),
)
def test_property_fast_promotion_tuple_normalization_idempotent(
    subj: str, pred: str, claim: str, hex_sfx: str
) -> None:
    """Metamorphic property: trailing hex suffixes and 'in Meeting.' suffixes normalize to identical assertion tuples."""
    base_tuple = fast_promotion._normalize_assertion_tuple(subj, pred, None, claim, None)
    with_hex = fast_promotion._normalize_assertion_tuple(
        subj, pred, None, f"{claim} {hex_sfx}", None
    )
    with_meeting = fast_promotion._normalize_assertion_tuple(
        subj, pred, None, f"{claim} in Weekly Sync.", None
    )
    assert base_tuple == with_hex
    assert base_tuple == with_meeting
    assert base_tuple[1] == base_tuple[1].upper() and " " not in base_tuple[1]


@settings(max_examples=35, deadline=None)
@given(
    hh=st.integers(min_value=0, max_value=23),
    mm=st.integers(min_value=0, max_value=59),
    ss=st.integers(min_value=0, max_value=59),
)
def test_property_transcript_turn_resolver_timestamp_canonicalization(
    hh: int, mm: int, ss: int
) -> None:
    """Property: 2-part and 3-part timestamp locators normalize deterministically to HH:MM:SS."""
    raw_3 = f"[{hh}:{mm:02d}:{ss:02d}]"
    norm_3 = transcript_turn_resolver.normalize_timestamp_locator(raw_3)
    assert norm_3 == f"{hh:02d}:{mm:02d}:{ss:02d}"
    assert transcript_turn_resolver.normalize_timestamp_locator(norm_3) == norm_3

    raw_2 = f"### [{mm}:{ss:02d}]"
    norm_2 = transcript_turn_resolver.normalize_timestamp_locator(raw_2)
    assert norm_2 == f"00:{mm:02d}:{ss:02d}"


@settings(max_examples=35, deadline=None)
@given(
    raw_text=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'’!?,.- ",
        min_size=0,
        max_size=80,
    )
)
def test_property_speaker_hint_matcher_normalization_idempotency(raw_text: str) -> None:
    """Property: normalize_search_text is idempotent after contraction expansion and punctuation stripping."""
    once = speaker_hint_matcher.normalize_search_text(raw_text)
    twice = speaker_hint_matcher.normalize_search_text(once)
    assert once == twice
    if once:
        seg = {"text": raw_text, "verbatim": "", "normalized": ""}
        assert speaker_hint_matcher.segment_contains_quote(seg, once) is True


@settings(max_examples=30, deadline=None)
@given(
    name=st.from_regex(r"[A-Z][a-z]{2,10} [A-Z][a-z]{2,10}", fullmatch=True),
    role=st.sampled_from(["Tech Lead", "Product Owner", "Architect", "Full Stack SWE"]),
    org=st.sampled_from(["PartnerAdvisory", "Apex Global Enterprises", "APEX_TRANS"]),
)
def test_property_copilot_roster_bridge_and_intents_metamorphic(
    name: str, role: str, org: str
) -> None:
    """Property: string and dict attendee representations parse to identical normalized roster dictionaries."""
    as_str = f"{name} ({role}, {org})"
    as_dict = {"name": name, "role": role, "org": org}
    parsed_str = copilot_roster_bridge.parse_attendee_entry(as_str)
    parsed_dict = copilot_roster_bridge.parse_attendee_entry(as_dict)
    assert parsed_str == parsed_dict
    assert parsed_str is not None and parsed_str["name"] == name

    # BlendedDialogueContext deduplicates dialogue seeds against target/topic_query
    ctx = copilot_intents.BlendedDialogueContext(
        dialogue=name, target=name, topic_query=role, topic_keywords=[org]
    )
    seeds = ctx.retrieval_seeds()
    assert seeds.count(name) == 1
    assert name in seeds and role in seeds and org in seeds

    # Copilot retriever project term normalization preserves non-empty strings/lists
    norm_proj = copilot_retriever.normalize_project_terms(f"{name} {role} in LENS")
    assert isinstance(norm_proj, (str, list)) and len(norm_proj) > 0


@settings(max_examples=30, deadline=None)
@given(
    node_names=st.lists(
        st.sampled_from(
            [
                "Amazon S3",
                "AWS S3",
                "S3 Ingestion Bucket",
                "Skydio Drone",
                "Percepto Drone",
                "Buzz Solutions Vision Model",
                "Pano AI",
            ]
        ),
        min_size=2,
        max_size=7,
        unique=True,
    )
)
def test_property_capability_bridge_permutation_invariance(node_names: list[str]) -> None:
    """Metamorphic permutation invariance: capability bridge and technical alias pairs are invariant to node insertion order."""
    forward_nodes = {
        f"node_{i}": {"canonical_name": n, "node_type": "technical_anchor"}
        for i, n in enumerate(node_names)
    }
    reverse_nodes = {
        k: forward_nodes[k] for k in reversed(list(forward_nodes.keys()))
    }
    assert capability_bridge.collect_technical_alias_pairs(
        forward_nodes
    ) == capability_bridge.collect_technical_alias_pairs(reverse_nodes)
    assert capability_bridge.collect_capability_bridge_pairs(
        forward_nodes, {}
    ) == capability_bridge.collect_capability_bridge_pairs(reverse_nodes, {})


class CandidateFastPromotionStateMachine(RuleBasedStateMachine):
    """Stateful model-checking machine for candidate deduplication, clearance lane checks, and UWG CAS matching."""

    def __init__(self) -> None:
        super().__init__()
        self.seen_tuples: dict[tuple[str, str, str, str], str] = {}
        self.to_promote: list[str] = []
        self.to_dedup: list[tuple[str, str]] = []
        self.unique_canonical_keys: set[tuple[str, str, str, str]] = set()
        self.candidates: list[dict[str, Any]] = []

    @rule(
        aid=st.from_regex(r"asrt_[a-z0-9]{4,8}", fullmatch=True),
        subj=st.sampled_from(["node_lens", "node_grid", "node_s3"]),
        pred=st.sampled_from(["USES_COMPONENT", "HAS_CLAIM_TEXT", "DEPENDS_ON"]),
        claim=st.sampled_from(
            [
                "Amazon S3 stores drone imagery.",
                "Percepto drones inspect substations.",
                "LineVision monitors dynamic line ratings.",
            ]
        ),
        suffix=st.sampled_from(["", " [abcdef]", " in Weekly Review."]),
    )
    def submit_candidate(
        self, aid: str, subj: str, pred: str, claim: str, suffix: str
    ) -> None:
        if aid in self.to_promote or any(d[0] == aid for d in self.to_dedup):
            return
        row: Any = {
            "assertion_id": aid,
            "subject_node_id": subj,
            "predicate": pred,
            "object_node_id": "",
            "scope_text": f"{claim}{suffix}",
            "object_literal": f"{claim}{suffix}",
        }
        norm_key = fast_promotion._normalize_assertion_tuple(
            subj, pred, "", f"{claim}{suffix}", f"{claim}{suffix}"
        )
        self.unique_canonical_keys.add(norm_key)
        fast_promotion._classify_dedup_or_promote(
            row, self.seen_tuples, self.to_promote, self.to_dedup
        )
        self.candidates.append(
            {
                "candidate_id": aid,
                "candidate_type": "assertion",
                "normalized_text": f"{claim}{suffix}",
                "evidence_ids": [f"ev_{aid}"],
                "entity_mentions": [],
            }
        )

    @invariant()
    def promoted_and_deduped_partition_is_exact(self) -> None:
        # Every unique normalized key has exactly one promoted winner
        assert len(self.to_promote) == len(self.unique_canonical_keys)
        promoted_set = set(self.to_promote)
        dedup_ids = {d[0] for d in self.to_dedup}
        assert promoted_set.isdisjoint(dedup_ids)
        for _, winner_id in self.to_dedup:
            assert winner_id in promoted_set

        # Verify meeting_postprocess deduplication groups identical normalized claims
        if self.candidates:
            deduped = meeting_postprocess.deduplicate_candidates(self.candidates)
            assert 1 <= len(deduped) <= len(self.candidates)

        # Verify UWG learning_commit float tolerance CAS matching invariant
        assert learning_commit._values_match(0.20, 0.2000001) is True
        assert learning_commit._values_match(0.20, 0.25) is False
        assert clearance_cache._expand_internal_audiences_frozen(
            ["nee-engineering"], "internal"
        ) == frozenset({"nee-engineering", "internal_consulting"})


TestCandidateFastPromotionStateful = CandidateFastPromotionStateMachine.TestCase
TestCandidateFastPromotionStateful.settings = settings(
    max_examples=25, stateful_step_count=15, deadline=None
)
