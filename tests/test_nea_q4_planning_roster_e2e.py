"""
End-to-end tests for Wave 2: Tabular Workstream Extractor Refinement, OCR Row Suppression & JARVIS Disambiguation.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.document_pipeline_core.tabular_roster_extractor import (
    is_tabular_workstream_roster_document,
    parse_tabular_workstream_roster,
    reconcile_tabular_workstream_mutations,
)
from dual_pipeline.identity import resolve_bundle
from dual_pipeline.intake import build_dispositions, build_graph_change_set, build_routes
from dual_pipeline.normalization import normalize_evidence_envelope


def _provision_sandbox_graph(db_path: Path) -> None:
    """Create a hermetic SQLite graph with node_type_registry and existing technical_anchor Jarvis."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE node_type_registry (
            node_type_id TEXT PRIMARY KEY,
            identity_rule TEXT NOT NULL,
            registry_state TEXT NOT NULL
        );
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            node_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            active INTEGER DEFAULT 1
        );
        CREATE TABLE canonical_alias (
            normalized_alias TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE entity_redirect (
            from_node_id TEXT PRIMARY KEY,
            to_node_id TEXT NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            claim_text TEXT,
            normalized_text TEXT,
            source_id TEXT,
            extraction_run_id TEXT,
            currentness_state TEXT DEFAULT 'current',
            superseded_at TEXT,
            superseded_by_assertion_id TEXT,
            classification TEXT DEFAULT 'internal',
            allowed_audiences_json TEXT DEFAULT '["internal_consulting"]'
        );
        """
    )
    for ntype in (
        "workstream", "org", "technical_anchor", "stakeholder", "pattern", "risk", "deliverable", "program", "role_group"
    ):
        conn.execute(
            "INSERT INTO node_type_registry (node_type_id, identity_rule, registry_state) VALUES (?, ?, 'active')",
            (ntype, "Never auto-merge on name similarity; require exact ID or human confirmation."),
        )
    # Seed existing technical_anchor 'Jarvis' and workstreams 'LENS' and 'Conduit'
    conn.execute(
        "INSERT INTO node (node_id, canonical_name, node_type, description, active) VALUES (?, ?, ?, ?, 1)",
        ("entity-487c43902705f2a28c2fc9d05687", "Jarvis", "technical_anchor", "Internal AI knowledge assistant tool."),
    )
    conn.execute(
        "INSERT INTO node (node_id, canonical_name, node_type, description, active) VALUES (?, ?, ?, ?, 1)",
        ("workstream_lens", "LENS", "workstream", "Land & Environmental Siting workstream."),
    )
    conn.commit()
    conn.close()


def _build_90_workstream_nea_fixture() -> str:
    """Build a deterministic 21-page NEA Q4 2026 Planning Dashboard fixture covering all 90 workstreams and key cases."""
    special_rows: Dict[int, str] = {
        1: (
            "5. Forge.CustomerPM: Andrew TurveySM: Kaleena McHugo\n"
            "Product: 5.Forge.Origination\n"
            "TechSales(Reid Schenk)\nNEAOperations(Chris Nunalee)\n"
            "In ProgressNEA5.Forge.CustomerResource Note: Required FTE for Q4\n"
            "$2,442K 100%"
        ),
        6: (
            "BeaconPM: Gordon PaynterSM: William White\n"
            "Product: Beacon\n"
            "EngTech(Chakra Sankaraiah)\nEngineering&Technology(Chakra Sankaraiah)\n"
            "ProposedNEABeacon $-33K 100%"
        ),
        45: (
            "JARVISPM: Taylor MercerSM: Kaleena McHugo\n"
            "Product: JARVIS\n"
            "TechSales(Reid Schenk)\nNEAOperations(Chris Nunalee)\n"
            "In ProgressNEAJARVIS $284K 100%"
        ),
        78: (
            "Regulatory LifecyclePM: Benjamin MillmanSM: William White\n"
            "Bus OpsAI (Farrukh Khan)\nEnterprise AI(Matthew Barrows)\n"
            "In ProgressNo AssociatedCharter\n"
            "$580K 117%"
        ),
        86: (
            "SWARM - Validationand PilotPM: Casey O'KeefeSM: Jake Wellington\n"
            "Product: SWARM | StormWork Optimization\n"
            "T&DAssetOpti (Lori Bartlett)\nEnterprise AI(Matthew Barrows)\n"
            "In ProgressNEASWARM -Validation and Pilot$-212K 91%"
        ),
    }

    verticals = [
        "TechSales (Reid Schenk)", "Enterprise AI (Anuj Chokshi)", "EngTech (Chakra Sankaraiah)",
        "Retail (Jose Medina)", "Gen Asset Opti (Jarod Bryant)", "Infra Dev (Kevin McWhorter)",
        "Energy Mngt (Stephan Nitsche)", "Bus Srv AI (John Andersen III)",
        "T&D Asset Opti (Lori Bartlett)", "Bus Ops AI (Farrukh Khan)",
    ]
    depts = [
        "NEA Operations (Chris Nunalee)", "Enterprise AI (Matthew Barrows)",
        "Eng & Tech (Chakra Sankaraiah)", "Product Ops (Richard Walker)",
    ]

    pages: List[str] = []
    ws_idx = 0
    for p_num in range(1, 22):
        count = 5 if p_num <= 6 else 4  # 6*5 + 15*4 = 90 workstreams
        ws_blocks: List[str] = []
        roster_blocks: List[str] = []
        for i in range(count):
            ws_idx += 1
            if ws_idx in special_rows:
                ws_blocks.append(special_rows[ws_idx])
                if ws_idx == 1:
                    roster_blocks.append("Andrew Turvey\nSenior\nAndy Gaskell\nContractor")
                elif ws_idx == 6:
                    roster_blocks.append("Gordon Paynter\n3\nStaff\nFuture Hire #17\nStaff")
                elif ws_idx == 45:
                    roster_blocks.append("Taylor Mercer\n2\nSenior\nKaleena McHugo\nStaff")
                elif ws_idx == 78:
                    roster_blocks.append("Benjamin Millman\n3\nStaff\nDave Sunderman\n6\nSenior")
                elif ws_idx == 86:
                    roster_blocks.append("Casey O'Keefe\n3\nSenior\nFuture Hire #3\nStaff")
            else:
                vert = verticals[ws_idx % len(verticals)]
                dept = depts[ws_idx % len(depts)]
                pm_name = f"Lead Manager {ws_idx}"
                sm_name = "Jake Wellington" if ws_idx % 3 == 0 else "William Lane"
                ws_blocks.append(
                    f"NEA Workstream Initiative {ws_idx}\n"
                    f"Product: Platform Product {ws_idx}\n"
                    f"PM: {pm_name}\n"
                    f"SM: {sm_name}\n"
                    f"{vert}\n"
                    f"{dept}\n"
                    f"In ProgressNEAInitiative Charter {ws_idx}Resource Note: Allocation {ws_idx}\n"
                    f"${ws_idx * 50}K 95%"
                )
                roster_blocks.append(f"{pm_name}\nStaff\nDave Sunderman\n6\nSenior\nSpecialist {ws_idx}\nContractor")
        header = (
            f"## Page {p_num}\n"
            f"10/20/26, 5:21 PM Team Assignments - NEA Planning\n"
            f"Page {p_num} of 21 https://planning.apex_globalanalytics.com/quarterly-planning/assignments?quarterId=3\n"
        )
        pages.append(header + "\n".join(ws_blocks) + "\n" + "\n".join(roster_blocks))
    return "\n\n".join(pages)


def test_full_90_workstream_extraction_and_charter_attributes() -> None:
    """Verify all 90 workstreams, NAV budgets, progress %, charter missions, and resource notes extract accurately."""
    doc_text = _build_90_workstream_nea_fixture()
    assert is_tabular_workstream_roster_document(doc_text) is True

    workstreams = parse_tabular_workstream_roster(doc_text)
    assert len(workstreams) == 90

    ws_by_name = {w["name"]: w for w in workstreams}
    assert "5.Forge.Customer" in ws_by_name
    forge = ws_by_name["5.Forge.Customer"]
    assert forge["est_nav_usd"] == 2_442_000
    assert forge["assignment_progress_pct"] == 100
    assert forge["charter_mission"] == "NEA 5.Forge.Customer"
    assert "Required FTE" in forge["resource_note"]
    assert forge["product_line"] == "5.Forge.Origination"

    assert "Beacon" in ws_by_name
    beacon = ws_by_name["Beacon"]
    assert beacon["nav_k"] == -33
    assert beacon["est_nav_usd"] == -33_000
    assert beacon["workstream_status"] == "Proposed"
    assert beacon["charter_mission"] == "NEA Beacon"

    assert "NEA JARVIS" in ws_by_name
    jarvis = ws_by_name["NEA JARVIS"]
    assert jarvis["pm"] == "Taylor Mercer"
    assert jarvis["sm"] == "Kaleena McHugo"
    assert jarvis["est_nav_usd"] == 284_000
    assert jarvis["charter_mission"] == "NEA JARVIS"

    reg = ws_by_name["Regulatory Lifecycle"]
    assert reg["charter_mission"] == "No Associated Charter"
    assert reg["assignment_progress_pct"] == 117


def test_jarvis_disambiguation_and_unattributed_ocr_row_suppression() -> None:
    """Verify NEA JARVIS resolves as workstream without colliding with technical_anchor Jarvis, and 'Not specified' OCR rows are suppressed."""
    doc_text = _build_90_workstream_nea_fixture()
    stage03_data: Dict[str, Any] = {}
    logs = reconcile_tabular_workstream_mutations(stage03_data, {"is_document": True}, doc_text)
    assert len(logs) == 1

    mutations = stage03_data["graph_mutations"]
    ents_by_id = {e["id"]: e for e in mutations["entity_upserts"]}
    assert "workstream:nea-jarvis" in ents_by_id
    assert ents_by_id["workstream:nea-jarvis"]["type"] == "workstream"
    assert ents_by_id["workstream:nea-jarvis"]["node_type"] == "workstream"
    assert ents_by_id["workstream:nea-jarvis"]["est_nav_usd"] == 284_000
    assert "system:jarvis-platform" in ents_by_id
    assert ents_by_id["system:jarvis-platform"]["name"] == "JARVIS Platform"

    # Verify Future Hire slots emit role_group entities, CANDIDATE_FOR signals, and entity_id-bound risk_updates
    assert "role:beacon-future-hire-17" in ents_by_id
    assert ents_by_id["role:beacon-future-hire-17"]["type"] == "role"
    assert ents_by_id["role:beacon-future-hire-17"]["node_type"] == "role_group"
    assert any(
        s["from_id"] == "role:beacon-future-hire-17"
        and s["to_id"] == "workstream:beacon"
        and s["relationship"] == "CANDIDATE_FOR"
        for s in mutations["relationship_signals"]
    )
    risks_by_id = {r["risk_id"]: r for r in mutations["risk_updates"]}
    assert "risk:beacon-future-hire-17" in risks_by_id
    assert risks_by_id["risk:beacon-future-hire-17"]["entity_id"] == "workstream:beacon"
    assert risks_by_id["risk:beacon-future-hire-17"]["subject_id"] == "workstream:beacon"

    # Build an Evidence Envelope containing both the tabular projection and unattributed "Not specified" OCR table rows
    with tempfile.TemporaryDirectory(prefix="wave2_nea_e2e_") as tmp_dir:
        graph_path = Path(tmp_dir) / "sandbox_graph.sqlite"
        _provision_sandbox_graph(graph_path)

        subset_ents = [
            ents_by_id["workstream:nea-jarvis"],
            ents_by_id["system:jarvis-platform"],
            ents_by_id["workstream:5-forge-customer"],
            ents_by_id["workstream:beacon"],
            ents_by_id["role:beacon-future-hire-17"],
            ents_by_id["person:traci-metzger"],
            ents_by_id["person:kaleena-mchugo"],
        ]
        subset_sigs = [
            s for s in mutations["relationship_signals"]
            if s["from_id"] in {e["id"] for e in subset_ents} and s["to_id"] in {e["id"] for e in subset_ents}
        ]

        package = seal_artifact({
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-nea-q4-planning-test",
            "meeting": {
                "meeting_id": "document-nea-q4-planning-2026-10-20",
                "title": "Document Team Assignments - NEA Planning",
                "started_at": "2026-10-20T17:21:00-04:00",
                "source_location": "https://planning.apex_globalanalytics.com/quarterly-planning/assignments?quarterId=3",
                "source_hash": "a" * 64,
                "attendees": ["Not specified"],
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "template_version": "5.8",
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-not-specified",
                    "reported_name": "Not specified",
                    "email": None,
                    "role_or_org": "Document Table Row",
                    "statements": [
                        {
                            "statement_id": "ocr-row-01",
                            "kind": "summary",
                            "text": "5. Forge.Customer PM: Andrew Turvey SM: Kaleena McHugo $2,442K 100%",
                            "locator": {"kind": "section", "value": "## Page 1"},
                            "gemini_confidence": 0.85,
                        },
                        {
                            "statement_id": "ocr-row-02",
                            "kind": "summary",
                            "text": "JARVIS PM: Taylor Mercer SM: Kaleena McHugo $284K 100%",
                            "locator": {"kind": "section", "value": "## Page 11"},
                            "gemini_confidence": 0.85,
                        },
                    ],
                }
            ],
            "actions": [],
            "adapter": {"name": "nee-evidence-envelope-intake", "version": "1.0.0"},
            "extensions": {
                "com.partner_advisory.nee.canonical-signal-projection": {
                    "entity_upserts": subset_ents,
                    "relationship_signals": subset_sigs,
                }
            },
        })

        bundle = normalize_evidence_envelope(package, run_context="wave2_e2e")
        validate_artifact(bundle)

        # Verify zero "Not specified" candidates were emitted into bundle["candidates"]
        for cand in bundle["candidates"]:
            for m in cand.get("entity_mentions", []):
                assert m["reported_text"].lower() != "not specified"

        resolutions = resolve_bundle(bundle, graph_path)
        # Verify NEA JARVIS did not collide with technical_anchor Jarvis
        jarvis_res = [r for r in resolutions if r["reported_name"] == "NEA JARVIS"]
        assert len(jarvis_res) >= 1
        for jr in jarvis_res:
            assert jr["outcome"] == "CREATE_NEW_ENTITY_PROPOSAL"
            assessment = jr["extensions"]["com.partner_advisory.nee.new-node-assessment"]
            assert assessment["status"] == "PROPOSE_NEW_CANDIDATE"
            assert assessment["declared_node_type"] == "workstream"

        comparison = {
            "comparisons": {
                c["candidate_id"]: {"exact_matches": [], "partial_overlaps": [], "corroborating_evidence": []}
                for c in bundle["candidates"]
            }
        }
        routes = build_routes(bundle, resolutions, comparison)
        route_counts: Dict[str, int] = {}
        for r in routes:
            rt = r["intake_route"]
            route_counts[rt] = route_counts.get(rt, 0) + 1
        assert route_counts.get("ENTITY_ALIAS_REVIEW", 0) == 0

        dispositions = build_dispositions(bundle, routes, resolutions)
        change_set = build_graph_change_set(bundle, dispositions, resolutions, graph_path)
        assert change_set is not None
        validate_artifact(change_set)

        # Verify zero operations in change_set have subject "Not specified"
        payloads = change_set["extensions"]["com.partner_advisory.nee.operation-payloads"]
        for op in change_set["operations"]:
            pl = payloads.get(op["operation_id"], {})
            subj = str(pl.get("subject_node_id") or "")
            assert "not-specified" not in subj.lower()
            assert "not_specified" not in subj.lower()
