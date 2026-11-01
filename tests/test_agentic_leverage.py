"""
Unit and integration tests for SVP-Tier Bipartite Namespacing & Hardened Judge Architecture.
Verifies AgenticLeverageNode schema, JudgeAgenticLeverage gating logic,
GraphExtractionJudge evaluation, and Bipartite Namespacing isolation.
"""

import json

import pytest
from pydantic import ValidationError

from dual_pipeline.llm_judge import (
    HARDENED_SVP_JUDGE_PROMPT,
    GraphExtractionJudge,
)
from dual_pipeline.transcript_stages import (
    AGENTIC_EXTRACTION_PROMPT,
    AGENTIC_LEVERAGE_NAMESPACE,
    CLIENT_SAFE_NAMESPACE,
    AgenticLeverageNode,
    BipartiteSignalProjection,
    L2GraphExtractorExecutor,
    OfflineTranscriptSynthesizer,
)
from pipeline_c_retrieval import (
    CLIENT_SAFE_NAMESPACE as PIPELINE_C_CLIENT_SAFE,
)
from pipeline_c_retrieval import (
    INTERNAL_ADVISORY_NAMESPACE,
    PolicyGatedRetrievalEngine,
)
from storage.database import (
    AdvisoryCategory,
    CanonicalGraphNode,
    DatabaseManager,
    InternalAdvisoryNode,
)

# ─────────────────────────────────────────────────────────────────────────────
# Task 1: AgenticLeverageNode Schema Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_agentic_leverage_node_valid_schema():
    """Verify AgenticLeverageNode instantiates and serializes correctly with all required fields."""
    node = AgenticLeverageNode(
        node_type="INFO_ASYMMETRY",
        key_players=["Tanner (VP)", "Drew Oakley (Managing Director)"],
        political_rationale="Tanner is unaware that the cloud infrastructure migration timeline slipped by 6 weeks.",
        networking_wedge="Ping Tanner privately on Teams to offer a buffer plan before the steering committee.",
        verbatim_quote="We have not finalized the dates with Tanner's team yet.",
        speaker="Drew Oakley",
    )
    assert node.node_type == "INFO_ASYMMETRY"
    assert len(node.key_players) == 2
    assert "Tanner" in node.networking_wedge
    assert node.speaker == "Drew Oakley"

    serialized = node.model_dump()
    assert serialized["node_type"] == "INFO_ASYMMETRY"
    assert serialized["speaker"] == "Drew Oakley"


def test_agentic_leverage_node_validation_failure():
    """Verify validation errors when mandatory fields are missing."""
    with pytest.raises(ValidationError):
        # Missing networking_wedge and political_rationale
        AgenticLeverageNode(
            node_type="CAREER_VISIBILITY",
            key_players=["Executive A"],
            verbatim_quote="Some quote",
            speaker="Speaker",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Task 2: Machiavellian Prompt Verification
# ─────────────────────────────────────────────────────────────────────────────


def test_agentic_extraction_prompt_content():
    """Verify the adversarial prompt contains core SVP mandate and three blindspots."""
    assert (
        "ruthless, highly strategic political and commercial advisor" in AGENTIC_EXTRACTION_PROMPT
    )
    assert "INFORMATION ASYMMETRY" in AGENTIC_EXTRACTION_PROMPT
    assert "POLITICAL CAPITAL & FRICTION" in AGENTIC_EXTRACTION_PROMPT
    assert "THE CAREER WEDGE" in AGENTIC_EXTRACTION_PROMPT


# ─────────────────────────────────────────────────────────────────────────────
# Task 3: Hardened SVP Judge & Gatekeeper Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_hardened_svp_judge_prompt_content():
    """Verify HARDENED_SVP_JUDGE_PROMPT contains evaluation gates."""
    assert 'THE "SO WHAT" TEST' in HARDENED_SVP_JUDGE_PROMPT
    assert "THE TARGET TEST" in HARDENED_SVP_JUDGE_PROMPT
    assert "THE SOCIAL CALIBRATION TEST" in HARDENED_SVP_JUDGE_PROMPT


def test_judge_agentic_leverage_strict_gating_rules():
    """
    Verify JudgeAgenticLeverage gating logic:
    Must be FALSE if political_materiality < 8 OR networking_target_identified is False.
    """
    judge = GraphExtractionJudge(mock=True)

    # Case 1: High materiality (9/10) with named executive target -> PASS
    high_leverage_node = AgenticLeverageNode(
        node_type="INFO_ASYMMETRY",
        key_players=["Tanner (VP)", "Drew Oakley (Managing Director)"],
        political_rationale="Tanner is unaware that the cloud infrastructure migration timeline slipped by 6 weeks.",
        networking_wedge="Ping Tanner privately on Teams to offer a buffer plan before the steering committee.",
        verbatim_quote="We have not finalized the dates with Tanner's team yet.",
        speaker="Drew Oakley",
    )
    result = judge.evaluate_agentic_leverage(high_leverage_node, mock=True)
    assert result.pass_gate is True
    assert result.political_materiality >= 8
    assert result.networking_target_identified is True
    assert result.is_actionable_wedge is True

    # Case 2: Low materiality / vague advice -> FAIL (So-What Test)
    weak_node = AgenticLeverageNode(
        node_type="CAREER_VISIBILITY",
        key_players=["Tanner (VP)"],
        political_rationale="Short rationale.",
        networking_wedge="Short wedge.",
        verbatim_quote="Short quote.",
        speaker="Dan",
    )
    weak_result = judge.evaluate_agentic_leverage(weak_node, mock=True)
    assert weak_result.pass_gate is False
    assert weak_result.rejection_reason is not None

    # Case 3: Missing executive target -> FAIL (Target Test)
    no_target_node = AgenticLeverageNode(
        node_type="INFO_ASYMMETRY",
        key_players=[],
        political_rationale="A significant information gap exists across teams.",
        networking_wedge="Broadcast an announcement to all project channels.",
        verbatim_quote="Someone should look into this.",
        speaker="Drew Oakley",
    )
    no_target_result = judge.evaluate_agentic_leverage(no_target_node, mock=True)
    assert no_target_result.pass_gate is False
    assert no_target_result.networking_target_identified is False
    assert "Target Test" in no_target_result.rejection_reason


def test_graph_extraction_judge_bipartite_evaluation():
    """Verify GraphExtractionJudge evaluates bipartite payloads without breaking traditional graph rubrics."""
    judge = GraphExtractionJudge(mock=True)

    bipartite_payload = {
        "client_safe": {
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [{"id": "org:partner_advisory", "type": "org", "mutation": "CONFIRM"}],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "action_updates": [],
                "risk_updates": [],
            },
        },
        "agentic_leverage": [
            {
                "node_type": "partner_advisory_MARGIN_WEDGE",
                "key_players": ["Sarah Jenkins (Director)", "PartnerAdvisory Partner"],
                "political_rationale": "Sarah is frustrated by internal resource constraints for the FERC filing.",
                "networking_wedge": "Ping Sarah Jenkins: 'Sarah - noticed the bandwidth pinch on FERC. We can deploy a pod.'",
                "verbatim_quote": "We just don't have enough bodies.",
                "speaker": "Sarah Jenkins",
            }
        ],
    }

    eval_result = judge.evaluate(
        json.dumps(bipartite_payload), f1_text="Mock transcript", mock=True
    )
    assert eval_result["verdict"] == "PASS"
    assert eval_result["bipartite"] is True
    assert "agentic_leverage_evaluation" in eval_result
    assert len(eval_result["agentic_leverage_evaluation"]) == 1
    assert eval_result["agentic_leverage_passed"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Task 4: Bipartite Namespacing & Stealth Mode UI Isolation Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_bipartite_executor_separation():
    """Verify L2GraphExtractorExecutor produces cleanly separated bipartite namespaces."""
    synthesizer = OfflineTranscriptSynthesizer()
    executor = L2GraphExtractorExecutor(synthesizer)

    result = executor.execute_bipartite("Sample forensic dialogue transcript.")
    assert isinstance(result, BipartiteSignalProjection)
    assert isinstance(result.client_safe, dict)
    assert isinstance(result.agentic_leverage, list)
    assert len(result.agentic_leverage) >= 1
    assert result.metadata["client_safe_namespace"] == CLIENT_SAFE_NAMESPACE
    assert result.metadata["agentic_leverage_namespace"] == AGENTIC_LEVERAGE_NAMESPACE


def test_pipeline_c_stealth_mode_isolation(tmp_path):
    """
    Verify Pipeline C retrieval isolation:
    - In STEALTH_MODE=True: INTERNAL_ADVISORY / AGENTIC_LEVERAGE must NEVER be returned.
    - In STEALTH_MODE=False: Both namespaces are queryable.
    """
    db_mgr = DatabaseManager(base_dir=tmp_path, allow_unencrypted_dev_fallback=True)
    engine = PolicyGatedRetrievalEngine(db_manager=db_mgr)

    c_node = CanonicalGraphNode(
        node_id="c_1",
        evidence_id="ev_1",
        type="ARCHITECTURE",
        name="NERC Compliance Grid Arch",
        properties={"context": "Tanner approved the NERC compliance grid architecture."},
        strategic_value=9.0,
        tacticality=1.0,
        confidence=0.95,
        is_quarantined=False,
    )
    adv_node = InternalAdvisoryNode(
        node_id="adv_1",
        evidence_id="ev_1",
        type="DECISION_INFLUENCE",
        name="Tanner VP Budget Leverage",
        category=AdvisoryCategory.DECISION_INFLUENCE,
        properties={"context": "Tanner is unaware of cloud delay.", "signal": "Tanner"},
        strategic_value=9.5,
        confidence=0.98,
        stealth_score=1.0,
        namespace=INTERNAL_ADVISORY_NAMESPACE,
    )
    db_mgr.upsert_canonical_node(c_node)
    db_mgr.upsert_internal_advisory_node(adv_node)

    # 1. Stealth Mode ACTIVE (Screen share ready)
    stealth_results = engine.search(query="Tanner", stealth_mode=True)
    assert len(stealth_results) >= 1
    for rec in stealth_results:
        assert rec.namespace == PIPELINE_C_CLIENT_SAFE
        assert rec.namespace != INTERNAL_ADVISORY_NAMESPACE

    # 2. Stealth Mode OFF (SVP Mode)
    svp_results = engine.search(query="Tanner", stealth_mode=False)
    namespaces_found = {rec.namespace for rec in svp_results}
    assert INTERNAL_ADVISORY_NAMESPACE in namespaces_found
    assert PIPELINE_C_CLIENT_SAFE in namespaces_found
