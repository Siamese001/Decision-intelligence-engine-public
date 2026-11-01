"""Wave 1 verification tests for graded QRELs (0-3), de-leaked paraphrases, and hard negatives."""
from __future__ import annotations

from dual_pipeline.retrieval_release_core.metrics import (
    _ndcg_at_k,
    evaluate_graded_qrel_ranking,
    standard_ndcg_at_k,
)
from dual_pipeline.retrieval_suite_core.cases import (
    _case,
    build_graded_qrels_for_group,
    compute_content_word_jaccard,
    mine_sibling_hard_negatives,
    synthesize_low_overlap_paraphrase,
)


def _sample_corpus() -> list[dict]:
    return [
        {
            "record_id": "rec-auth-001",
            "record_type": "assertion",
            "lane": "approved",
            "answer_use": "factual_answer",
            "title": "Cloud Migration Cutover Schedule",
            "retrieval_text": "Production cutover for the retail data warehouse is scheduled for November 14 with zero downtime window.",
            "canonical_entities": ["Retail Data Warehouse", "Cloud Migration"],
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "extensions": {
                "com.partner_advisory.nee.graph-assertion-ids": ["ast-cutover-001"],
                "com.partner_advisory.nee.graph-object-id": "ast-cutover-001",
            },
        },
        {
            "record_id": "rec-facet-002",
            "record_type": "entity_facet",
            "lane": "approved",
            "answer_use": "navigation_only",
            "title": "Entity facet: Retail Data Warehouse",
            "retrieval_text": "Entity facet summarizing Retail Data Warehouse architecture and migration dependencies.",
            "canonical_entities": ["Retail Data Warehouse"],
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "extensions": {
                "com.partner_advisory.nee.graph-assertion-ids": ["ast-cutover-001"],
            },
        },
        {
            "record_id": "rec-topical-003",
            "record_type": "assertion",
            "lane": "approved",
            "answer_use": "factual_answer",
            "title": "Legacy Warehouse Decommission",
            "retrieval_text": "Historical archive retention policy for the legacy reporting cluster.",
            "canonical_entities": ["Retail Data Warehouse"],
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "extensions": {
                "com.partner_advisory.nee.graph-assertion-ids": ["ast-legacy-999"],
                "com.partner_advisory.nee.graph-object-id": "ast-legacy-999",
            },
        },
        {
            "record_id": "rec-hardneg-004",
            "record_type": "assertion",
            "lane": "approved",
            "answer_use": "factual_answer",
            "title": "Staging Cutover Schedule",
            "retrieval_text": "Staging cutover for the retail data warehouse is scheduled for October 28 during maintenance window.",
            "canonical_entities": ["Cloud Migration"],
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "extensions": {
                "com.partner_advisory.nee.graph-assertion-ids": ["ast-staging-404"],
                "com.partner_advisory.nee.graph-object-id": "ast-staging-404",
            },
        },
    ]


def test_graded_qrel_exponential_ndcg_distinguishes_authoritative_from_facet() -> None:
    """Graded nDCG@k (0-3) must strictly prefer [grade 3, grade 2] over [grade 2, grade 3]."""
    corpus = _sample_corpus()
    target = [corpus[0]]
    qrel_grades = build_graded_qrels_for_group(target, corpus)

    assert qrel_grades["rec-auth-001"] == 3
    assert qrel_grades["rec-facet-002"] == 2
    assert qrel_grades["rec-hardneg-004"] == 0

    ideal_order = ["rec-auth-001", "rec-facet-002", "rec-topical-003"]
    inverted_order = ["rec-facet-002", "rec-auth-001", "rec-topical-003"]

    ndcg_ideal = _ndcg_at_k(ideal_order, {"rec-auth-001", "rec-facet-002"}, top_k=5, qrel_grades=qrel_grades)
    ndcg_inverted = _ndcg_at_k(inverted_order, {"rec-auth-001", "rec-facet-002"}, top_k=5, qrel_grades=qrel_grades)

    assert ndcg_ideal is not None and ndcg_inverted is not None
    assert abs(ndcg_ideal - 1.0) < 1e-9
    assert ndcg_inverted < ndcg_ideal

    # Binary QRELs treat both orders as 1.0 when both are in expected set
    binary_ideal = standard_ndcg_at_k(ideal_order[:2], {"rec-auth-001", "rec-facet-002"}, top_k=5)
    binary_inverted = standard_ndcg_at_k(inverted_order[:2], {"rec-auth-001", "rec-facet-002"}, top_k=5)
    assert binary_ideal == 1.0
    assert binary_inverted == 1.0


def test_low_overlap_paraphrase_prevents_lexical_leakage() -> None:
    """Synthesized semantic paraphrases must have <25% content-word Jaccard overlap with target text."""
    corpus = _sample_corpus()
    for record in corpus:
        for variant in (1, 2, 3):
            paraphrase = synthesize_low_overlap_paraphrase(record, variant_index=variant)
            jaccard = compute_content_word_jaccard(paraphrase, record["retrieval_text"])
            assert jaccard < 0.25, f"Paraphrase leaked lexical tokens ({jaccard:.3f}): {paraphrase}"


def test_mine_sibling_hard_negatives_and_intrusion_metrics() -> None:
    """Hard negative miner selects in-corpus distractors with lexical/entity overlap but distinct scope."""
    corpus = _sample_corpus()
    target = [corpus[0]]
    negatives = mine_sibling_hard_negatives(target, corpus, max_negatives=2)
    neg_ids = [n["record_id"] for n in negatives]

    assert "rec-auth-001" not in neg_ids
    assert "rec-facet-002" not in neg_ids  # shares scope ast-cutover-001
    assert "rec-hardneg-004" in neg_ids

    qrel_grades = build_graded_qrels_for_group(target, corpus)
    metrics = evaluate_graded_qrel_ranking(
        ["rec-hardneg-004", "rec-auth-001", "rec-facet-002"],
        qrel_grades,
        top_k=3,
    )
    assert metrics["authoritative_recall_at_k"] == 1.0
    assert metrics["supporting_recall_at_k"] == 1.0
    assert abs((metrics["mrr_at_k"] or 0.0) - 0.5) < 1e-9
    assert abs((metrics["hard_negative_intrusion_rate"] or 0.0) - (1.0 / 3.0)) < 1e-9


def test_case_builder_attaches_qrel_grades_only_when_provided() -> None:
    """_case omits qrel_grades by default for E3 schema strictness and attaches it when supplied."""
    default_case = _case(
        case_id="c-default",
        query_kind="semantic_paraphrase",
        query="test query",
        record_ids=["rec-1"],
        lane="approved",
        retrieval_mode="factual",
        classification="internal",
        audiences=["internal_consulting"],
        top_k=5,
        expected=True,
    )
    assert "qrel_grades" not in default_case

    graded_case = _case(
        case_id="c-graded",
        query_kind="semantic_paraphrase",
        query="test query",
        record_ids=["rec-1"],
        lane="approved",
        retrieval_mode="factual",
        classification="internal",
        audiences=["internal_consulting"],
        top_k=5,
        expected=True,
        qrel_grades={"rec-1": 3, "rec-2": 2, "rec-neg": 0},
    )
    assert graded_case["qrel_grades"] == {"rec-1": 3, "rec-2": 2, "rec-neg": 0}


def test_human_labeled_gold_qrels_and_bare_stub_capping() -> None:
    """Human-labeled QRELs cap bare identification stubs at Grade 1 and prefer rich records."""
    from dual_pipeline.retrieval_suite_core.qrel_grades import (
        HUMAN_LABELED_GOLD_QRELS,
        is_bare_identification_stub,
        is_supporting_only_record,
    )

    assert len(HUMAN_LABELED_GOLD_QRELS) == 15
    modes = {item["retrieval_mode"] for item in HUMAN_LABELED_GOLD_QRELS.values()}
    assert {
        "factual",
        "strategic",
        "entity_navigation",
        "status_snapshot_current",
        "status_snapshot_history",
        "guidance",
        "candidate_review",
    } <= modes
    assert is_bare_identification_stub(
        "vanguard repeatable-benefit scale gate is an identified control in Document 20260721 Vanguard See Sense Act."
    )
    assert is_bare_identification_stub(
        "Good news is that our extraction accuracy, we are still maintaining a nice high 88%."
    )
    assert is_supporting_only_record(
        "Brian Mendicino, Contractor specialist assigned to Q4 Conduit: Asset Performance."
    )
    assert not is_bare_identification_stub(
        "PartnerAdvisory is contracted to take vanguard from its existing prototype to production in two releases."
    )

    stub_record = {
        "record_id": "rec-stub-01",
        "record_type": "assertion",
        "lane": "approved",
        "retrieval_text": "Maximo is an identified technical_anchor in Document 20261012 grid_os Q4 Mission.",
        "canonical_entities": ["GRID_OS"],
        "extensions": {"com.partner_advisory.nee.graph-assertion-ids": ["ast-grid_os-1"]},
    }
    supporting_record = {
        "record_id": "rec-supp-02",
        "record_type": "assertion",
        "lane": "approved",
        "retrieval_text": "Amit Ayer and Andy Kilinskis reported that the criticism had not yet been tied to a specific KPI.",
        "canonical_entities": ["GRID_OS"],
        "extensions": {"com.partner_advisory.nee.graph-assertion-ids": ["ast-grid_os-2"]},
    }
    grades = build_graded_qrels_for_group(
        [stub_record, supporting_record],
        human_overrides=HUMAN_LABELED_GOLD_QRELS["qrel-human-001"]["grades"],
    )
    assert grades["rec-stub-01"] == 1
    assert grades["rec-supp-02"] == 2
    assert grades["retrieval-record-00121205a4757d3096a057c27b9f"] == 3
    assert grades["retrieval-record-1a47f1c475b31b27677e1f7d9495"] == 1
