"""End-to-end and regression tests for Wave 5: Fail-Closed GroundedDeliveryGuard Wiring, Footnote Entailment & Anti-Sycophancy Gate."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from dual_pipeline.cli.engine import _apply_seamless_lineage_formatting
from dual_pipeline.grounding.contracts import (
    ClaimManifest,
    ClaimRecord,
    SourceLocator,
)
from dual_pipeline.grounding.delivery_boundary import (
    GroundedDeliveryGuard,
    verify_grounded_delivery,
)
from dual_pipeline.grounding.eligibility import (
    AssertionEligibilityAuditor,
    EligibilityDecision,
)
from dual_pipeline.grounding.metrics import (
    check_claim_span_coverage,
    check_footnote_clause_entailment,
    check_sycophancy_and_meta_commentary,
    evaluate_text_metrics,
)
from dual_pipeline.grounding.snapshot import ReadOnlyGraphSnapshot, _compute_sha256

EXACT_BROKEN_USER_RESPONSE = """* **Why It Should Not Have Been Included** — You are spot on: under our knowledge graph governance rules, candidate assertions from a single uncorroborated transcript cannot establish internal fact and should never be cited in an executive question [1].
* **Canonical-Only Standard** — Stripping out all candidate material leaves only promoted, user-confirmed canonical facts from the active graph snapshot—specifically our 106-engineer Apex Global Enterprises footprint [1], the shift from isolated pods and generic capacity to a co-hydrated Product and Engineering model [2][3], and the Engineering Exchange building a cross-workstream reusable pattern library across LENS, Swarm, and FlexAI [4].

### Executive Question — Anchored Strictly in Promoted Canonical Facts

> "At Apex Global Enterprises, where we have 106 software engineers across the account, our client moved away from buying isolated vendor pods or interchangeable engineering hours toward a co-hydrated product and engineering model—where every architectural pattern we solve in one workstream is shared through our Engineering Exchange to compound across the entire portfolio [1][2][3][4]. As the four of you look across PartnerAdvisory, how do we scale that same compounding model firm-wide—so putting the capital 'W' in 'We' means every local market and global capability operates as one connected learning network rather than separate teams?"

---

### Evidence & Graph Lineage

| Ref | Graph Path | Confidence | Verified Evidence |
| :--- | :--- | :--- | :--- |
| `[1]` | `staffing_footprint_partner_advisory_nee` $\\rightarrow$ `ALLOCATES_ENGINEERING_CAPACITY_TO` $\\rightarrow$ `org_nea` | `CONFIRMED (100%)` | [graph.sqlite](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-82c8ea9d374a222c7d153c192b9b/graph.sqlite) — `ev_legacy_edge_edge_v33_partner_advisory_footprint_allocates_66_to_nea` (`source_v33_partner_advisory_engineering_footprint_user_confirmed_2026_07_10`) |
| `[2]` | `model_cohydrated_operating_model` $\\rightarrow$ `HAS_CLAIM_TEXT` $\\rightarrow$ `Co-hydrated NEA–PartnerAdvisory Product + Engineering model` | `CONFIRMED (100%)` | [graph.sqlite](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-82c8ea9d374a222c7d153c192b9b/graph.sqlite) — `asrt_claim_claim_v31_cohydrated_model_selected` (`source_user_hitl_consolidated_2026_07_10`) |
| `[3]` | `sow_nea_ai_engineering_engineering_delivery_teams_q3_2026` $\\rightarrow$ `HAS_OPPORTUNITY` $\\rightarrow$ `opportunity_strategic_scaling_partner` | `HIGH (90%)` | [graph.sqlite](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-82c8ea9d374a222c7d153c192b9b/graph.sqlite) — `asrt_edge_1cc3b3d2017cf7217eb66f90` & `asrt_edge_3607217c1619442848dadc05` (`source_bryce`) |
| `[4]` | `pattern_engineering_exchange` $\\rightarrow$ `BUILDS` $\\rightarrow$ `Cross-workstream reusable pattern library` | `HIGH (85%)` | [graph.sqlite](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-82c8ea9d374a222c7d153c192b9b/graph.sqlite) — `ev_legacy_edge_edge_engineering_exchange_v20_builds_pattern_library` (`source_chakra`) |
"""


def test_exact_broken_user_response_is_rejected_by_all_wave5_gates() -> None:
    """Exact production regression: the broken executive query response is blocked across sycophancy, entailment, and delivery boundary."""
    syc_reasons = check_sycophancy_and_meta_commentary(EXACT_BROKEN_USER_RESPONSE)
    assert any("SYCOPHANTIC_META_COMMENTARY" in r for r in syc_reasons)
    assert any("MISATTRIBUTED_META_FOOTNOTE" in r for r in syc_reasons)

    entail_reasons = check_footnote_clause_entailment(EXACT_BROKEN_USER_RESPONSE)
    assert any("FOOTNOTE_CLAUSE_ENTAILMENT_FAILURE" in r for r in entail_reasons)
    assert any("SUPERSEDED_ASSERTION_CITED" in r for r in entail_reasons)
    assert any("DIRECTIONAL_RISK_INVERSION" in r for r in entail_reasons)
    assert any("CROSS_WORKSTREAM_SPLICING" in r for r in entail_reasons)
    assert any("MALFORMED_LINEAGE_FOOTER" in r for r in entail_reasons)

    metrics = evaluate_text_metrics(EXACT_BROKEN_USER_RESPONSE, max_words=500)
    assert metrics.is_valid is False
    assert len(metrics.reasons) >= 7


def test_grounded_delivery_guard_blocks_broken_response_and_certifies_clean_response() -> None:
    """Verify GroundedDeliveryGuard blocks the broken response and certifies a clean grounded response."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_wave5_") as tmp_dir:
        rt_root = Path(tmp_dir)
        db_path = rt_root / "canonical_graph.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE canonical_graph_nodes (
                node_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                node_type TEXT,
                namespace TEXT DEFAULT 'CLIENT_SAFE'
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT,
                literal_value TEXT,
                status TEXT DEFAULT 'CONFIRMED',
                canonical_status TEXT DEFAULT 'canonical',
                canonical_eligible INTEGER DEFAULT 1,
                validation_state TEXT DEFAULT 'approved',
                currentness_state TEXT DEFAULT 'current',
                observed_at TEXT,
                stale_after TEXT,
                namespace TEXT DEFAULT 'CLIENT_SAFE'
            );
            INSERT INTO canonical_graph_nodes VALUES ('N-FOOTPRINT', 'PartnerAdvisory NEE Staffing Footprint', 'Staffing', 'CLIENT_SAFE');
            INSERT INTO canonical_graph_nodes VALUES ('N-NEA', 'Apex Global Analytics (NEA)', 'Organization', 'CLIENT_SAFE');
            INSERT INTO assertion VALUES (
                'asrt_edge_v33_66_nea', 'N-FOOTPRINT', 'ALLOCATES_ENGINEERING_CAPACITY_TO', 'N-NEA', NULL,
                'CONFIRMED', 'canonical', 1, 'approved', 'current', '2026-07-10T00:00:00Z', NULL, 'CLIENT_SAFE'
            );
            INSERT INTO assertion VALUES (
                'asrt_v31_superseded', 'N-FOOTPRINT', 'HAS_MODEL', 'N-NEA', NULL,
                'CONFIRMED', 'candidate', 0, 'superseded', 'historical', '2026-07-10T00:00:00Z', NULL, 'CLIENT_SAFE'
            );
            """
        )
        conn.commit()
        conn.close()

        graph_sha = _compute_sha256(db_path)
        (rt_root / "active_pair.json").write_text(
            json.dumps(
                {
                    "generation_id": "GEN-WAVE5-001",
                    "graph_path": "canonical_graph.db",
                    "graph_sha256": graph_sha,
                }
            ),
            encoding="utf-8",
        )

        locator = SourceLocator(
            locator_type="transcript_paragraph",
            source_uri="file:///tmp/footprint.md",
            locator_value="para_1",
        )
        claim = ClaimRecord(
            claim_id="CLM-NEA-66",
            subject="PartnerAdvisory NEE Staffing Footprint",
            predicate="ALLOCATES_ENGINEERING_CAPACITY_TO",
            object="Apex Global Analytics (NEA)",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["asrt_edge_v33_66_nea"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[locator],
        )

        # 1. Broken response is blocked by GroundedDeliveryGuard
        broken_sha = hashlib.sha256(EXACT_BROKEN_USER_RESPONSE.encode("utf-8")).hexdigest()
        broken_manifest = ClaimManifest(
            manifest_id="MAN-BROKEN-001",
            runtime_root=str(rt_root),
            graph_generation_id="GEN-WAVE5-001",
            graph_sha256=graph_sha,
            payload_digest=broken_sha,
            claims=[claim],
        )
        guard = GroundedDeliveryGuard()
        dispatched: list[str] = []
        res_broken = guard.deliver(
            payload_text=EXACT_BROKEN_USER_RESPONSE,
            manifest=broken_manifest,
            runtime_root=rt_root,
            surface="QUERY_SYNTHESIS",
            sink=lambda txt: dispatched.append(txt),
            max_words=500,
        )
        assert res_broken.delivered is False
        assert res_broken.receipt.status == "REJECTED"
        assert res_broken.receipt.reason_code == "UNBACKED_PAYLOAD_SPAN"
        assert dispatched == []

        # 2. Clean grounded response with enforce_claim_coverage=True is certified and delivered
        clean_payload = (
            "PartnerAdvisory NEE Staffing Footprint allocates engineering capacity to Apex Global Analytics (NEA) under the confirmed Q3 footprint."
        )
        clean_sha = hashlib.sha256(clean_payload.encode("utf-8")).hexdigest()
        clean_manifest = ClaimManifest(
            manifest_id="MAN-CLEAN-001",
            runtime_root=str(rt_root),
            graph_generation_id="GEN-WAVE5-001",
            graph_sha256=graph_sha,
            payload_digest=clean_sha,
            claims=[claim],
        )
        res_clean = guard.deliver(
            payload_text=clean_payload,
            manifest=clean_manifest,
            runtime_root=rt_root,
            surface="QUERY_SYNTHESIS",
            sink=lambda txt: dispatched.append(txt),
            max_words=500,
            enforce_claim_coverage=True,
        )
        assert res_clean.delivered is True
        assert res_clean.receipt.status == "CERTIFIED"
        assert dispatched == [clean_payload]

        # 3. AssertionEligibilityAuditor rejects superseded/candidate assertion even if status='CONFIRMED'
        snapshot = ReadOnlyGraphSnapshot(rt_root)
        auditor = AssertionEligibilityAuditor(allow_candidate_lane=False)
        superseded_claim = ClaimRecord(
            claim_id="CLM-SUP-001",
            subject="PartnerAdvisory NEE Staffing Footprint",
            predicate="HAS_MODEL",
            object="Apex Global Analytics (NEA)",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["asrt_v31_superseded"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[locator],
        )
        audit_res = auditor.audit_claim(superseded_claim, snapshot)
        assert audit_res.is_eligible is False
        assert audit_res.decision in (
            EligibilityDecision.REJECTED_STATUS,
            EligibilityDecision.REJECTED_UNSUPPORTED,
            EligibilityDecision.REJECTED_ENTITY_ONLY,
        )
        snapshot.close()


def test_cli_apply_seamless_lineage_formatting_flags_blocked_payload() -> None:
    """Verify _apply_seamless_lineage_formatting attaches delivery_guard_status='BLOCKED' on broken markdown."""
    res_dict = {"markdown": EXACT_BROKEN_USER_RESPONSE}
    _apply_seamless_lineage_formatting(res_dict)
    assert res_dict.get("delivery_guard_status") == "BLOCKED"
    assert len(res_dict.get("delivery_guard_violations", [])) >= 4
