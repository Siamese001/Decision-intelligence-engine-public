"""Hypothesis Metamorphic & RuleBasedStateMachine Property Suite for Speaker Remediation, L0 Speaker Policy, Entity Healer, Attestation Storage, and Live Buffer Coalescing."""
from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List

from hypothesis import given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from dual_pipeline.graph_maintenance_core.speaker_hygiene_rules import (
    resolve_candidate_provenance_speaker,
)
from dual_pipeline.l0.speaker_and_qualifier_policy import (
    classify_speaker_attribution_defect,
    is_placeholder_speaker,
    normalize_candidate_claim_text,
    parse_qualifiers_dict,
    speakers_match_identity,
    sync_canonical_qualifiers_json,
)
from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import (
    _clean_live_turn_claim,
    _expand_substantive_tokens,
    score_utterance_domain_substance,
)
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import (
    coalesce_contiguous_turns,
)
from dual_pipeline.transcript_stages_core.graph_post_entity_healer import (
    heal_and_align_entities,
)
from tools.attestation_core.envelope import READINESS_PREDICATE_TYPE
from tools.attestation_core.storage import get_attestation_path
from tools.lint_automation_ssot_links import audit_reconciliation_manifest
from tools.scan_ssot_configuration_violations_core.rca_checks import (
    _check_rca_03_hitl_margin,
    _check_rca_04_wal_timeout,
)
from tools.tier2_dedup_core.speaker_and_qualifier_remediation import (
    execute_rebound_candidate_promotion,
    remediate_speaker_and_qualifier_fixes,
)


@settings(max_examples=30, deadline=None)
@given(
    raw_text=st.from_regex(r"[A-Za-z0-9][A-Za-z0-9 .,;:!?]{0,80}[A-Za-z0-9.!?]", fullmatch=True),
    prefix=st.sampled_from(
        [
            "",
            ": ",
            "**Downstream Production Integration:** ",
            ": **Client Production Milestone:** ",
            "**Jake Stark:**** ",
        ]
    ),
)
def test_property_normalize_candidate_claim_text_idempotence(raw_text: str, prefix: str) -> None:
    """Property: normalize_candidate_claim_text is strictly idempotent on non-empty claim bodies: f(f(x)) == f(x)."""
    combined = prefix + raw_text
    once = normalize_candidate_claim_text(combined)
    twice = normalize_candidate_claim_text(once)
    assert once == twice
    assert not once.startswith(": ")


@settings(max_examples=30, deadline=None)
@given(
    disposition=st.sampled_from(["CANDIDATE", "APPROVED", "STAGED"]),
    answer_use=st.sampled_from(["candidate_hypothesis", "factual_answer", "citation_only", "guidance"]),
    retrieval_kind=st.sampled_from(["assertion", "evidence_span", "entity_facet", "guidance"]),
    fact_eligible=st.booleans(),
    quarantined=st.booleans(),
)
def test_property_sync_canonical_qualifiers_idempotence_and_quarantine_removal(
    disposition: str,
    answer_use: str,
    retrieval_kind: str,
    fact_eligible: bool,
    quarantined: bool,
) -> None:
    """Property: sync_canonical_qualifiers_json is idempotent, removes speaker_hygiene_quarantined, and sets APPROVED."""
    payload = {
        "dual_pipeline_disposition": disposition,
        "answer_use": answer_use,
        "retrieval_kind": retrieval_kind,
        "fact_promotion_eligible": fact_eligible,
        "dual_pipeline_candidate_id": "cand-prop-1",
    }
    if quarantined:
        payload["speaker_hygiene_quarantined"] = True

    raw_json = json.dumps(payload)
    synced_once = sync_canonical_qualifiers_json(raw_json)
    synced_twice = sync_canonical_qualifiers_json(synced_once)
    assert synced_once == synced_twice

    parsed = parse_qualifiers_dict(synced_once)
    assert "speaker_hygiene_quarantined" not in parsed
    assert parsed["dual_pipeline_disposition"] == "APPROVED"


@settings(max_examples=25, deadline=None)
@given(
    entities=st.lists(
        st.sampled_from(
            [
                {"id": "system:lens", "type": "system", "name": "LENS"},
                {"id": "system:maximo", "type": "system", "name": "Maximo"},
                {"id": "system:confluence", "type": "system", "name": "Confluence"},
                {"id": "org:nea", "type": "org", "name": "NEA"},
                {"id": "person:jake-stark", "type": "person", "name": "Jake Stark"},
                {"id": "system:lens-backend", "type": "system", "name": "Banned Backend"},
            ]
        ),
        min_size=1,
        max_size=10,
    )
)
def test_metamorphic_entity_healer_permutation_invariance(entities: List[Dict[str, str]]) -> None:
    """Metamorphic Property: Reordering input entity_upserts yields the exact same healed entity set by ID."""
    ev_index = {"ev-01": {"exact_text": "Project LENS integrates with Maximo and Confluence in NEA."}}
    mut_fwd = {"entity_upserts": copy.deepcopy(entities)}
    mut_rev = {"entity_upserts": copy.deepcopy(list(reversed(entities)))}

    heal_and_align_entities(
        mut_fwd, ev_index=ev_index, fallback_ev=["ev-01"], source_text="LENS Maximo", project="project_lens"
    )
    heal_and_align_entities(
        mut_rev, ev_index=ev_index, fallback_ev=["ev-01"], source_text="LENS Maximo", project="project_lens"
    )

    map_fwd = {e["id"]: e for e in mut_fwd["entity_upserts"]}
    map_rev = {e["id"]: e for e in mut_rev["entity_upserts"]}
    assert map_fwd == map_rev
    assert "system:lens-backend" not in map_fwd


@settings(max_examples=25, deadline=None)
@given(
    chunks=st.lists(
        st.fixed_dictionaries(
            {
                "speaker": st.sampled_from(["Jake Stark", "Marcus Vance", "Eduardo Smith"]),
                "text": st.sampled_from(
                    [
                        "We validated the nameplate rules",
                        "across eighty three substation transformers",
                        "Should we deploy to Maximo?",
                        "Yeah I think we should wait for UAT.",
                    ]
                ),
                "turn_sec": st.floats(min_value=0.0, max_value=120.0, allow_nan=False, allow_infinity=False),
            }
        ),
        min_size=1,
        max_size=12,
    )
)
def test_metamorphic_coalesce_contiguous_turns_time_shift_invariance(chunks: List[Dict[str, object]]) -> None:
    """Metamorphic Property: Shifting all chunk timestamps by a constant +Delta preserves coalesced speaker & text sequence."""
    ordered = sorted(chunks, key=lambda c: float(c["turn_sec"]))
    for i, c in enumerate(ordered):
        c["end_sec"] = float(c["turn_sec"]) + 2.0

    shifted = copy.deepcopy(ordered)
    for c in shifted:
        c["turn_sec"] = float(c["turn_sec"]) + 500.0
        c["end_sec"] = float(c["end_sec"]) + 500.0

    res_base = coalesce_contiguous_turns(ordered)
    res_shift = coalesce_contiguous_turns(shifted)
    assert len(res_base) == len(res_shift)
    for b, s in zip(res_base, res_shift):
        assert b["speaker"] == s["speaker"]
        assert b["text"] == s["text"]


class SpeakerRemediationStateMachine(RuleBasedStateMachine):
    """Stateful model checking for cloned-generation speaker quarantine, placeholder rebinding, and candidate promotion."""

    def __init__(self) -> None:
        super().__init__()
        self._tmpdir = tempfile.TemporaryDirectory(prefix="eval_sandbox_spk_sm_")
        self.db_path = Path(self._tmpdir.name) / "graph.sqlite"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(
            """
            CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT);
            CREATE TABLE source (source_id TEXT PRIMARY KEY, source_type TEXT, source_name TEXT, cold_archive_ref TEXT);
            CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, transcript_timestamp TEXT, locator_value TEXT);
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                scope_text TEXT,
                object_literal TEXT,
                speaker_or_author TEXT,
                qualifiers_json TEXT,
                primary_source_id TEXT,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                retrieval_eligible INTEGER,
                currentness_state TEXT,
                validation_state TEXT,
                updated_at TEXT,
                approval_reference TEXT
            );
            CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT);
            CREATE TABLE search_document (
                search_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                assertion_id TEXT,
                title TEXT,
                body TEXT,
                object_type TEXT,
                object_id TEXT,
                canonical_status TEXT,
                retrieval_eligible INTEGER,
                updated_at TEXT
            );
            CREATE VIRTUAL TABLE search_fts USING fts5(title, body, object_type, object_id);
            CREATE TABLE governance_approval (
                approval_id TEXT PRIMARY KEY,
                approval_reference TEXT,
                status TEXT,
                active INTEGER
            );
            INSERT INTO node VALUES ('person:jake-stark', 'Jake Stark', 'stakeholder');
            INSERT INTO source VALUES ('src-1', 'dual_pipeline_intake', 'Meeting LENS', NULL);
            INSERT INTO evidence_item VALUES ('ev-1', '00:01:00', '00:01:00');
            INSERT INTO assertion VALUES (
                'asrt-anchor', 'person:jake-stark', 'HAS_CLAIM_TEXT',
                'Jake Stark confirmed the baseline.', 'Jake Stark confirmed the baseline.',
                'Jake Stark', '{}', 'src-1', 'canonical', 1, 1, 'current', 'accepted', '2026-10-01', 'app-init'
            );
            INSERT INTO assertion_evidence VALUES ('asrt-anchor', 'ev-1');
            """
        )
        self.tables = {
            "node",
            "source",
            "evidence_item",
            "assertion",
            "assertion_evidence",
            "search_document",
            "search_fts",
            "governance_approval",
        }
        self.acols = {r[1] for r in self.conn.execute("PRAGMA table_info(assertion)").fetchall()}
        self.model_status: Dict[str, str] = {"asrt-anchor": "canonical"}
        self.model_speaker: Dict[str, str] = {"asrt-anchor": "Jake Stark"}
        self.counter = 0

    def teardown(self) -> None:
        self.conn.close()
        self._tmpdir.cleanup()

    @rule(
        is_placeholder=st.booleans(),
        non_promotable=st.booleans(),
    )
    def insert_assertion(self, is_placeholder: bool, non_promotable: bool) -> None:
        self.counter += 1
        aid = f"asrt-dyn-{self.counter}"
        spk = "Speaker 1" if is_placeholder else "Jake Stark"
        qual = (
            json.dumps({"answer_use": "citation_only"})
            if non_promotable
            else json.dumps({"dual_pipeline_disposition": "CANDIDATE", "dual_pipeline_candidate_id": aid})
        )
        txt = f": **Jake Stark:** Validated asset batch {self.counter} in LENS."
        self.conn.execute(
            """
            INSERT INTO assertion VALUES (?, 'person:jake-stark', 'HAS_CLAIM_TEXT', ?, ?, ?, ?, 'src-1', 'candidate', 0, 0, 'current', 'needs_review', '2026-10-01', NULL)
            """,
            (aid, txt, txt, spk, qual),
        )
        self.conn.execute("INSERT INTO assertion_evidence VALUES (?, 'ev-1')", (aid,))
        self.conn.execute(
            "INSERT INTO search_document (assertion_id, title, body, object_type, object_id, canonical_status, retrieval_eligible, updated_at) VALUES (?, 'T', ?, 'assertion', ?, 'candidate', 0, '2026-10-01')",
            (aid, txt, aid),
        )
        self.model_status[aid] = "candidate"
        self.model_speaker[aid] = spk

    @rule()
    def quarantine_all_candidates(self) -> None:
        cands = [aid for aid, st_val in self.model_status.items() if aid != "asrt-anchor"]
        if not cands:
            return
        remediate_speaker_and_qualifier_fixes(
            self.conn,
            findings=[{"proposed_fix": "quarantine_undiarized_or_misattributed_claim", "entity_references": cands}],
            tables=self.tables,
            now_iso="2026-10-30T16:30:00Z",
            sample_ids=[],
        )
        for aid in cands:
            self.model_status[aid] = "candidate"

    @rule()
    def rebind_and_promote_candidates(self) -> None:
        cands = [aid for aid, st_val in self.model_status.items() if st_val == "candidate"]
        if not cands:
            return
        execute_rebound_candidate_promotion(
            self.conn,
            cands,
            approval_ref="sm-approval-ref",
            acols=self.acols,
            tables=self.tables,
            now_iso="2026-10-30T16:35:00Z",
        )
        for aid in cands:
            q_raw = self.conn.execute(
                "SELECT qualifiers_json, canonical_status, speaker_or_author FROM assertion WHERE assertion_id=?",
                (aid,),
            ).fetchone()
            self.model_status[aid] = q_raw[1]
            self.model_speaker[aid] = q_raw[2]

    @invariant()
    def check_database_and_promotion_invariants(self) -> None:
        for aid, expected_status in self.model_status.items():
            row = self.conn.execute(
                "SELECT canonical_status, retrieval_eligible, speaker_or_author, qualifiers_json FROM assertion WHERE assertion_id=?",
                (aid,),
            ).fetchone()
            assert row is not None
            actual_status, ret_elig, spk, q_raw = row
            assert actual_status == expected_status
            q_dict = parse_qualifiers_dict(q_raw)
            if q_dict.get("answer_use") == "citation_only":
                # Non-promotable qualifier must NEVER become canonical
                assert actual_status == "candidate"
                assert ret_elig == 0
            if actual_status == "canonical" and aid != "asrt-anchor":
                assert ret_elig == 1
                assert not is_placeholder_speaker(spk)
                assert q_dict.get("dual_pipeline_disposition") == "APPROVED"


TestSpeakerRemediationLifecycle = SpeakerRemediationStateMachine.TestCase
TestSpeakerRemediationLifecycle.settings = settings(max_examples=20, stateful_step_count=15, deadline=None)
