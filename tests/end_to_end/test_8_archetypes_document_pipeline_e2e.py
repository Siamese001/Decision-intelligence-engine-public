"""Wave 4 E2E Lifecycle Test: 8 Document Archetypes, Cross-Doc Entity Canonicalizer, Epistemic/Bio Scoping & Calibrated Gate 3."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

import tests.end_to_end.test_wave1_mutation_hardening_e2e as _wave1_fixture
from dual_pipeline.canonical_pipeline.visual_org_extractor import (
    extract_visual_org_projection,
)
from dual_pipeline.contracts import create_artifact, seal_artifact
from dual_pipeline.core import ZERO_HASH, connect_sqlite, digest, sha256_file
from dual_pipeline.document_pipeline_core.account_plan_extractor import (
    extract_account_plan_projection,
)
from dual_pipeline.document_pipeline_core.archetype_classifier import (
    DocumentArchetype,
    classify_document_archetype,
)
from dual_pipeline.document_pipeline_core.entity_canonicalizer import (
    canonicalize_projection_entities,
    resolve_canonical_person,
)
from dual_pipeline.document_pipeline_core.quality import (
    validate_signal_claim_grounding,
    validate_slide_deck_coverage_and_yield,
)
from dual_pipeline.document_pipeline_core.orals_proposal_extractor import (
    extract_orals_proposal_projection,
)
from dual_pipeline.document_pipeline_core.sow_contract_extractor import (
    extract_offering_playbook_projection,
    extract_sow_contract_projection,
)
from dual_pipeline.document_pipeline_core.tabular_roster_extractor import (
    reconcile_tabular_workstream_mutations,
)
from dual_pipeline.document_pipeline_core.unit_decomposition import (
    classify_unit_sieve,
    extract_slide_and_section_units,
)
from dual_pipeline.document_pipeline_core.xlsx_projection import (
    extract_staffing_workbook_projection,
)
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.signal_extraction import SignalExtractor
from tools.graph_mutation_proof_core import raw_byte_scan


def _make_governed_change_set_from_projections(
    graph_path: Path,
    projections: List[Dict[str, Any]],
    run_id: str = "run-wave4-8arch",
) -> tuple[dict[str, Any], list[str]]:
    """Build a sealed graph-change-set from canonicalized projections and seed nodes in staging graph."""
    conn = connect_sqlite(graph_path)
    now = "2026-10-22T12:00:00Z"
    for proj in projections:
        canonicalize_projection_entities(proj)
        muts = proj.get("graph_mutations") or {}
        for ent in muts.get("entity_upserts") or []:
            nid = str(ent.get("id") or "")
            name = str(ent.get("name") or ent.get("label") or nid)
            ntype = str(ent.get("type") or "entity")
            if nid:
                conn.execute(
                    "INSERT OR REPLACE INTO node VALUES (?, ?, ?, 1, ?)",
                    (nid, name, ntype, now),
                )
        for rel in muts.get("relationship_signals") or []:
            for ep_id in (str(rel.get("from_id") or rel.get("from") or ""), str(rel.get("to_id") or rel.get("to") or "")):
                if ep_id and ":" in ep_id:
                    ep_type, ep_slug = ep_id.split(":", 1)
                    conn.execute(
                        "INSERT OR IGNORE INTO node VALUES (?, ?, ?, 1, ?)",
                        (ep_id, ep_slug.replace("-", " ").title(), ep_type, now),
                    )
    conn.commit()
    conn.close()

    base_hash = sha256_file(graph_path)
    operations: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}
    assertion_ids: list[str] = []
    seq = 1

    seen_rel_keys: set[tuple[str, str, str]] = set()
    for proj in projections:
        muts = proj.get("graph_mutations") or {}
        for rel in muts.get("relationship_signals") or []:
            subj = str(rel.get("from_id") or rel.get("from") or "")
            obj = str(rel.get("to_id") or rel.get("to") or "")
            pred = str(rel.get("relationship") or rel.get("predicate") or "RELATED_TO")
            if not subj or not obj or (subj, obj, pred) in seen_rel_keys:
                continue
            seen_rel_keys.add((subj, obj, pred))
            op_id = f"op-w4-{seq:03d}"
            as_id = f"as-w4-{seq:03d}"
            assertion_ids.append(as_id)
            cand = {
                "candidate_id": f"cand-{as_id}",
                "candidate_type": "assertion",
                "normalized_text": f"{subj} {pred} {obj}",
                "classification": "internal",
                "audiences": ["engineering"],
                "evidence_ids": ["ev-e2e-001"],
                "extraction_confidence": 0.95,
                "materiality": "high",
            }
            payload = {
                "candidate": cand,
                "subject_node_id": subj,
                "predicate": pred,
                "object_node_id": obj,
                "disposition": "CANDIDATE",
                "retrieval_audiences": ["engineering"],
            }
            operations.append({
                "operation_id": op_id,
                "sequence": seq,
                "operation": "add_candidate_assertion",
                "target_type": "assertion",
                "target_id": as_id,
                "candidate_id": f"cand-{as_id}",
                "classification": "internal",
                "audiences": ["engineering"],
                "evidence_ids": ["ev-e2e-001"],
                "before_state_hash": ZERO_HASH,
                "after_state_hash": digest(payload),
            })
            payloads[op_id] = payload
            seq += 1

    source = {
        "source_id": "source-e2e-01",
        "source_version_id": "sv-e2e-01",
        "name": "E2E Source",
        "package_digest": "b" * 64,
        "authority_rank": 7,
        "source_type": "governed_internal",
    }
    cs = create_artifact(
        "graph-change-set",
        "cs-wave4-8arch",
        change_set_id="cs-wave4-8arch",
        run_id=run_id,
        trace_id="d" * 32,
        approval_id="app-e2e-001",
        status="approved",
        base_graph_snapshot="snapshot-wave4-01",
        base_graph_hash=base_hash,
        transaction_required=True,
        rollback_required=True,
        operations=operations,
        extensions={
            "com.partner_advisory.nee.source": source,
            "com.partner_advisory.nee.operation-payloads": payloads,
        },
    )
    return seal_artifact(cs), assertion_ids


@pytest.mark.e2e
def test_8_archetypes_cross_doc_canonicalization_and_physical_proof_e2e() -> None:
    """Ingest all 8 archetypes, verify 0 split-identity duplicates, epistemic/bio scoping, Gate 3 zero-noise veto, and 5 physical checks."""
    case = _wave1_fixture.Wave1MutationHardeningE2ETests("test_e2e_mutation_wal_checkpoint_and_hash_change")
    case.setUp()
    try:
        graph_path = case.graph_path

        # 1. Archetype #1 (Tabular Portfolio Export) with nickname/typo variants
        roster_text = """
        ## Page 1
        Vertical: Generation | Leader: Matt Barrows | Department: Analytics | Leader: Christopher Nunalee
        Workstream: Conduit Q4 Planning | Product: Conduit Platform | $1,200,000 NAV | 100% Assigned
        Nicholas Krivov - AI Product Manager (100%)
        Kurt Hopmann - Product Manager (100%)
        Dianna Valencia - Product Manager (100%)
        Ray Laffer - Product Manager (100%)
        Reid Shenk - Senior Director (100%)
        Lori Bartlet - Director (100%)
        Amit A. - PartnerAdvisory Managing Director (100%)
        """
        assert classify_document_archetype(roster_text) in (
            DocumentArchetype.TABULAR_PORTFOLIO_EXPORT,
            DocumentArchetype.AUTHORITATIVE_MEMO_OR_SPEC,
        )
        proj_roster: Dict[str, Any] = {
            "evidence_index": {},
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:apex_global-energy", "type": "org", "name": "Apex Global Enterprises", "mutation": "CREATE", "evidence_ids": ["ev-roster-1"]}
                ],
                "relationship_signals": [],
            },
        }
        reconcile_tabular_workstream_mutations(proj_roster, {}, roster_text)
        # Ensure all 8 variant leaders are in entity_upserts so we test cross-doc convergence against Visual Org Chart
        variant_inputs = [
            ("person:matt-barrows", "Matt Barrows"),
            ("person:christopher-nunalee", "Christopher Nunalee"),
            ("person:nicholas-krivov", "Nicholas Krivov"),
            ("person:kurt-hopmann", "Kurt Hopmann"),
            ("person:dianna-valencia", "Dianna Valencia"),
            ("person:ray-laffer", "Ray Laffer"),
            ("person:reid-shenk", "Reid Shenk"),
            ("person:lori-bartlet", "Lori Bartlet"),
            ("person:amit-a", "Amit A."),
        ]
        for vid, vname in variant_inputs:
            proj_roster["graph_mutations"]["entity_upserts"].append({
                "id": vid, "type": "person", "name": vname, "mutation": "CREATE", "evidence_ids": ["ev-roster-1"],
            })
            proj_roster["graph_mutations"]["relationship_signals"].append({
                "signal_id": f"sig-{vid}-leads", "from_id": vid, "to_id": "org:apex_global-energy",
                "relationship": "LEADS", "predicate": "LEADS", "evidence_ids": ["ev-roster-1"],
            })

        # 2. Archetype #3 (Visual Org Chart) containing canonical names for the same 8 NEA leaders
        org_chart_text = (
            "< Organization\n"
            "- **Richard Argentieri** — President, Apex Global Analytics (reports to Petter Skantze)\n"
            "  - **Matthew Barrows** — VP Enterprise AI Products (reports to Richard Argentieri)\n"
            "    - **Jarod Bryant** — Executive Director, Generation Asset Optimization (reports to Matthew Barrows)\n"
            "      - **Lancey Lexima** — Manager of Technical Product Management (reports to Jarod Bryant)\n"
            "    - **Anuj Chokshi** — Senior Director, Enterprise AI Products (reports to Matthew Barrows)\n"
            "      - **Stefanos Georgiades** — Lead AI Product Manager (reports to Anuj Chokshi)\n"
            "      - **Nick Krivov** — AI Product Manager (reports to Anuj Chokshi)\n"
            "      - **Curt Hopmann** — Product Manager (reports to Anuj Chokshi)\n"
            "      - **Ray Lauffer** — Product Manager (reports to Anuj Chokshi)\n"
            "    - **Lori Bartlett** — Senior Director, T&D Asset Optimization (reports to Matthew Barrows)\n"
            "  - **Christopher Nunalee** — VP Apex Global Analytics (reports to Richard Argentieri)\n"
            "    - **Jose Medina** — Director, Retail & TechSales (reports to Christopher Nunalee)\n"
            "      - **Reid Schenk** — Executive Director, Energy Analytics (reports to Jose Medina)\n"
            "        - **Diana Valencia** — Principal Data Scientist (reports to Reid Schenk)\n"
            "      - **Tanner Stevens** — Product Manager, TechSales (reports to Jose Medina)\n"
        )
        assert classify_document_archetype(org_chart_text, {"is_visual_document": True}) == DocumentArchetype.VISUAL_TOPOLOGY_AND_ORG_CHART
        proj_org = extract_visual_org_projection(org_chart_text)

        # 3. Archetype #4 (Pursuit Orals Proposal) with current-state CONFIRMED facts, Option 1/2 proposals, and resume prior employers
        orals_raw = {
            "schema_version": "5.7",
            "meeting_id": "doc-infra-orals",
            "evidence_index": {
                "ev-orals-1": {
                    "exact_text": "IT4U SAGE PLA Project Beacon 97 PartnerAdvisory staff Option 1: $2.67M vs Option 2: $540K Pfizer Exelon",
                    "text": "IT4U SAGE PLA Project Beacon 97 PartnerAdvisory staff Option 1: $2.67M vs Option 2: $540K Pfizer Exelon",
                    "locator": {"kind": "slide", "value": "p5"},
                    "grounding_status": "GROUNDED",
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:it4u", "type": "system", "name": "IT4U", "epistemic_status": "CONFIRMED", "evidence_ids": ["ev-orals-1"]},
                    {"id": "system:sage", "type": "system", "name": "SAGE", "epistemic_status": "CONFIRMED", "evidence_ids": ["ev-orals-1"]},
                    {"id": "system:pla", "type": "system", "name": "PLA", "epistemic_status": "CONFIRMED", "evidence_ids": ["ev-orals-1"]},
                    {"id": "system:project_beacon", "type": "system", "name": "Project Beacon", "epistemic_status": "CONFIRMED", "evidence_ids": ["ev-orals-1"]},
                    {"id": "metric:partner_advisory-staff-97", "type": "metric", "name": "97 PartnerAdvisory Staff", "epistemic_status": "CONFIRMED", "evidence_ids": ["ev-orals-1"]},
                    {"id": "org:pfizer", "type": "org", "name": "Pfizer", "evidence_ids": ["ev-orals-1"]},
                    {"id": "org:exelon", "type": "org", "name": "Exelon", "evidence_ids": ["ev-orals-1"]},
                ],
                "relationship_signals": [
                    {
                        "signal_id": "sig-bio-pfizer", "from_id": "person:amit-ayer", "to_id": "org:pfizer",
                        "relationship": "MEMBER_OF", "predicate": "MEMBER_OF",
                        "_audit_rationale": "Resume prior experience at Pfizer", "evidence_ids": ["ev-orals-1"],
                    },
                    {
                        "signal_id": "sig-bio-exelon", "from_id": "person:amit-ayer", "to_id": "org:exelon",
                        "relationship": "MEMBER_OF", "predicate": "MEMBER_OF",
                        "_audit_rationale": "Resume prior experience at Exelon", "evidence_ids": ["ev-orals-1"],
                    },
                ],
                "decision_updates": [
                    {
                        "decision_id": "decision:orals-option-1-vs-2",
                        "title": "Pursuit Pricing Option 1: $2.67M vs Option 2: $540K",
                        "description": "Proposed Workstream 1A-5C fixed-price paths Option 1: $2.67M vs Option 2: $540K",
                        "status": "PENDING",
                        "evidence_ids": ["ev-orals-1"],
                    }
                ],
                "proposal_updates": [],
                "risk_updates": [],
            },
        }
        proj_orals, _ = SignalExtractor().extract_and_gate(
            orals_raw,
            metadata={"is_document": True, "output3_only": True},
            forensic_transcript="IT4U SAGE PLA Project Beacon 97 PartnerAdvisory staff Option 1: $2.67M vs Option 2: $540K Pfizer Exelon",
        )
        # Verify Orals Option 1/2 routed to proposal_updates and Pfizer/Exelon scoped to PRIOR_AFFILIATION_WITH
        assert len(proj_orals["graph_mutations"]["proposal_updates"]) == 1
        assert len(proj_orals["graph_mutations"]["decision_updates"]) == 0
        for rel in proj_orals["graph_mutations"]["relationship_signals"]:
            assert rel["relationship"] == "PRIOR_AFFILIATION_WITH"
            assert rel["temporal_scope"] == "HISTORICAL_BIO"

        # 4. Archetype #5 (SOW), #6/#7 (Account Plan), #8 (Staffing Workbook)
        proj_sow = extract_sow_contract_projection(
            "STATEMENT OF WORK: ENTERPRISE4U Align + PRF Integration\nWorkstream A (Align) HR4U Moveworks\nSection 2.3 Out of Scope\nDrew Oakley"
        )
        proj_acct = extract_account_plan_projection(
            "Plan Summary: 2027 Apex Global Enterprises\n2027 Target: $24M | 2028 Target: $38M | 2029 Target: ~$45M\n"
            "Business Unit Buyers: NEA $12M, EDO $7M, IT $2M, ISC $1M, PGD $1M, PD $1M\n"
            "Juno FL $22M, St. Paul MN $2M, Richmond VA $6M\nDominion Merger 2H 2027\nGridOpti, TRM Lakehouse, Nexus Jarvis\n"
            "66/106 SWEs\nABR: $230/$235\nAI Office: 0%\nMainframe Mod: 0%"
        )
        proj_wb = extract_staffing_workbook_projection(
            "Global Pricing v2.0\nENDUR ETRM TRM Lakehouse\nTBD1 | Lead | $250 | Total Hours: 1500\nTBD10 | Analyst | $0 | Total Hours: 1936\nTBD19 | Empty | $0 | Total Hours: 0"
        )

        # 5. Apply GovernedGraphWriter across the merged 8-archetype projections into isolated SQLite DB
        cs, assertion_ids = _make_governed_change_set_from_projections(
            graph_path, [proj_roster, proj_org, proj_orals, proj_sow, proj_acct, proj_wb]
        )
        write_res = GovernedGraphWriter(graph_path).apply(cs)
        proof = write_res["physical_proof"]
        assert proof["overall"] == "PASS"
        assert set(proof["checks"]) == {"file_changed", "raw_bytes", "integrity", "readback", "row_delta"}
        assert all(st == "PASS" for st in proof["checks"].values())

        # Independent read-only SQLite verification (`file:...?mode=ro`)
        ro_uri = f"file:{graph_path.resolve()}?mode=ro"
        ro_conn = sqlite3.connect(ro_uri, uri=True)
        try:
            integrity = ro_conn.execute("PRAGMA integrity_check").fetchone()[0]
            assert integrity == "ok"

            # Verify 0 split-identity duplicates across all 8 NEA name-variant pairs + Amit Ayer
            expected_canonical_ids = [
                "person:matthew-barrows",
                "person:chris-nunalee",
                "person:nick-krivov",
                "person:curt-hopmann",
                "person:diana-valencia",
                "person:ray-lauffer",
                "person:reid-schenk",
                "person:lori-bartlett",
                "person:amit-ayer",
            ]
            forbidden_split_ids = [
                "person:matt-barrows",
                "person:christopher-nunalee",
                "person:nicholas-krivov",
                "person:kurt-hopmann",
                "person:dianna-valencia",
                "person:ray-laffer",
                "person:reid-shenk",
                "person:lori-bartlet",
                "person:amit-a",
            ]
            db_node_ids = {r[0] for r in ro_conn.execute("SELECT node_id FROM node").fetchall()}
            for cid in expected_canonical_ids:
                assert cid in db_node_ids, f"Missing canonical person ID {cid}"
            for fid in forbidden_split_ids:
                assert fid not in db_node_ids, f"Split-identity duplicate leaked into SQLite: {fid}"

            # Verify zero Q1-Q7 noise entities (Netflix, Roger Bannister, Comcast, Maria Delgado, Matsu, Mainframe Mod)
            db_labels = {r[0].lower() for r in ro_conn.execute("SELECT canonical_name FROM node").fetchall()}
            for noise_name in ("netflix", "roger bannister", "comcast", "maria delgado", "matsu", "mainframe mod"):
                assert noise_name not in db_labels

            # Verify REPORTS_TO + LEADS edge convergence on canonical IDs
            for cid in expected_canonical_ids[:8]:
                preds = {
                    r[0]
                    for r in ro_conn.execute(
                        "SELECT predicate FROM assertion WHERE subject_node_id=?", (cid,)
                    ).fetchall()
                }
                assert "REPORTS_TO" in preds and "LEADS" in preds, f"Expected both REPORTS_TO and LEADS on {cid}, got {preds}"

            # Verify Pfizer/Exelon have PRIOR_AFFILIATION_WITH and zero active MEMBER_OF edges
            bio_preds = {
                r[0]
                for r in ro_conn.execute(
                    "SELECT predicate FROM assertion WHERE object_node_id IN ('org:pfizer', 'org:exelon')"
                ).fetchall()
            }
            assert bio_preds == {"PRIOR_AFFILIATION_WITH"}
        finally:
            ro_conn.close()

        # Verify raw bytes on disk
        scan_res = raw_byte_scan(graph_path, assertion_ids[:5])
        assert all(offset is not None for offset in scan_res.values())
    finally:
        case.tearDown()


@pytest.mark.e2e
def test_gate3_sparse_enablement_and_quarantined_unit_leakage_veto() -> None:
    """Verify Gate 3 passes sparse enablement deck on C1-C4 substantive units and fails closed on Q1-Q7 unit citation."""
    slides = []
    units = []
    for i in range(1, 16):
        if i in (2, 7, 13):
            txt = f"## Page {i}\nApex Global Enterprises PartnerAdvisory Traverse AMS 101 architecture capability {i}."
            sieve = "CORE_STRATEGIC_PROSE"
            mat = "SUBSTANTIVE"
        else:
            txt = f"## Page {i}\nREAD ME THEN DELETE [Account Name] Lorem ipsum template instructions {i}."
            sieve = "QUARANTINE_TEMPLATE_SCAFFOLD"
            mat = "QUARANTINED"
        slides.append(txt)
        units.append({
            "unit_id": f"u-{i}",
            "content": txt,
            "locator": f"p{i}",
            "metadata": {"slide_number": i, "sieve_class": sieve, "disposition": sieve, "materiality": mat},
        })
    source_text = "\n\n".join(slides)
    metadata = {"slide_units": units, "page_count": 15, "document_archetype": "SPARSE_ENABLEMENT_WORKSHOP"}

    valid_proj = {
        "evidence_index": {
            "ev-2": {"exact_text": "Apex Global Enterprises PartnerAdvisory Traverse AMS 101 architecture capability 2.", "locator": {"kind": "slide", "value": "p2"}},
            "ev-7": {"exact_text": "Apex Global Enterprises PartnerAdvisory Traverse AMS 101 architecture capability 7.", "locator": {"kind": "slide", "value": "p7"}},
            "ev-13": {"exact_text": "Apex Global Enterprises PartnerAdvisory Traverse AMS 101 architecture capability 13.", "locator": {"kind": "slide", "value": "p13"}},
        },
        "graph_mutations": {"entity_upserts": [{"id": f"system:s{k}"} for k in range(10)]},
    }
    passed, errors, _ = validate_slide_deck_coverage_and_yield(valid_proj, source_text, metadata=metadata, mutation_count=10)
    assert passed is True, f"Expected sparse deck to pass Gate 3, got {errors}"

    # Inject a citation to quarantined slide 5 (Q1 template scaffold) -> must fail closed with DOCUMENT_QUARANTINED_UNIT_LEAKAGE
    leaked_proj = json.loads(json.dumps(valid_proj))
    leaked_proj["evidence_index"]["ev-leak"] = {
        "exact_text": "READ ME THEN DELETE [Account Name] Lorem ipsum template instructions 5.",
        "locator": {"kind": "slide", "value": "p5"},
    }
    passed_leak, errors_leak, _ = validate_slide_deck_coverage_and_yield(leaked_proj, source_text, metadata=metadata, mutation_count=10)
    assert passed_leak is False
    assert any("DOCUMENT_QUARANTINED_UNIT_LEAKAGE" in e for e in errors_leak)


@pytest.mark.e2e
def test_real_uploaded_8_documents_orals_full_scope_and_sqlite_physical_proof_e2e() -> None:
    """Run full-scope extraction across the real uploaded files on disk and verify 5-check SQLite physical proof."""
    from dual_pipeline.document_pipeline import load_document_content

    home_dir = Path.home()
    repo_root = next(
        (p for p in Path(__file__).resolve().parents if (p / "inputs" / "documents").exists()),
        Path(__file__).resolve().parents[2],
    )

    orals_path = home_dir / "Downloads" / "NEE Infrastructure Automation Transformation RFP_Orals.pdf"
    enterprise4u_sow_path = repo_root / "documents/hrit/ENTERPRISE4U_Employee_Experience_Align_Plus_PRF_Integration_SOW.docx"
    lens_sow_path = repo_root / "inputs/documents/LENS ΓÇô Visual Asset Intelligence SOW 6.12.26.docx"
    ams_path = repo_root / "inputs/documents/AMS_Training_and_Solution_Materials_AMS_101_Florida.pdf"
    nea_roster_path = repo_root / "inputs/documents/team_assignments_nea_planning_20261020.pdf"
    acct_pptx_path = home_dir / "Downloads" / "apex_global_energy_2027_account_plan_updated.pptx"
    if not acct_pptx_path.exists():
        acct_pptx_path = home_dir / "Downloads" / "Florida 2027 Account Planning NEE.pptx"

    if not orals_path.exists():
        return

    orals_raw = load_document_content(orals_path)
    orals_txt = orals_raw[0] if isinstance(orals_raw, tuple) else str(orals_raw)
    orals_units = [u.to_dict() for u in extract_slide_and_section_units(orals_txt, "orals", "pdf")]
    orals_meta = {"slide_units": orals_units, "page_count": 111, "document_archetype": "PURSUIT_ORALS_PROPOSAL"}

    proj_orals = extract_orals_proposal_projection(orals_txt, orals_meta)
    orals_muts = proj_orals["graph_mutations"]
    assert 60 <= len(orals_muts["entity_upserts"]) <= 95
    assert 90 <= len(orals_muts["relationship_signals"]) <= 140
    assert len(orals_muts["proposal_updates"]) >= 2
    assert len(orals_muts["risk_updates"]) == 18

    g_pass, g_errs = validate_signal_claim_grounding(proj_orals, orals_txt)
    assert g_pass is True, f"Orals grounding errors: {g_errs}"
    c_pass, c_errs, c_tel = validate_slide_deck_coverage_and_yield(
        proj_orals,
        orals_txt,
        metadata=orals_meta,
        mutation_count=len(orals_muts["entity_upserts"]) + len(orals_muts["relationship_signals"]),
    )
    assert c_pass is True, f"Orals Gate 3 errors: {c_errs}"
    assert c_tel["substantive_coverage_ratio"] >= 0.85

    real_projections: List[Dict[str, Any]] = [proj_orals]
    if enterprise4u_sow_path.exists():
        s_raw = load_document_content(enterprise4u_sow_path)
        real_projections.append(extract_sow_contract_projection(s_raw[0] if isinstance(s_raw, tuple) else str(s_raw)))
    if lens_sow_path.exists():
        l_raw = load_document_content(lens_sow_path)
        real_projections.append(extract_sow_contract_projection(l_raw[0] if isinstance(l_raw, tuple) else str(l_raw)))
    if ams_path.exists():
        a_raw = load_document_content(ams_path)
        real_projections.append(extract_offering_playbook_projection(a_raw[0] if isinstance(a_raw, tuple) else str(a_raw)))
    if nea_roster_path.exists():
        r_raw = load_document_content(nea_roster_path)
        r_txt = r_raw[0] if isinstance(r_raw, tuple) else str(r_raw)
        p_roster: Dict[str, Any] = {"evidence_index": {}, "graph_mutations": {}}
        reconcile_tabular_workstream_mutations(p_roster, {"is_document": True}, r_txt)
        real_projections.append(p_roster)
    if acct_pptx_path.exists():
        p_raw = load_document_content(acct_pptx_path)
        real_projections.append(extract_account_plan_projection(p_raw[0] if isinstance(p_raw, tuple) else str(p_raw)))

    case = _wave1_fixture.Wave1MutationHardeningE2ETests("test_e2e_mutation_wal_checkpoint_and_hash_change")
    case.setUp()
    try:
        cs, assertion_ids = _make_governed_change_set_from_projections(
            case.graph_path, real_projections, run_id="run-wave4-real-docs"
        )
        write_res = GovernedGraphWriter(case.graph_path).apply(cs)
        proof = write_res["physical_proof"]
        assert proof["overall"] == "PASS"
        assert all(st == "PASS" for st in proof["checks"].values())

        ro_uri = f"file:{case.graph_path.resolve()}?mode=ro"
        ro_conn = sqlite3.connect(ro_uri, uri=True)
        try:
            node_count = ro_conn.execute("SELECT COUNT(*) FROM node").fetchone()[0]
            assertion_count = ro_conn.execute("SELECT COUNT(*) FROM assertion").fetchone()[0]
            assert node_count >= 380
            assert assertion_count >= 420
            db_node_ids = {r[0] for r in ro_conn.execute("SELECT node_id FROM node").fetchall()}
            for forbidden in ("person:disney", "person:universal-destinations", "org:mike-jarro", "system:total"):
                assert forbidden not in db_node_ids
        finally:
            ro_conn.close()
    finally:
        case.tearDown()

