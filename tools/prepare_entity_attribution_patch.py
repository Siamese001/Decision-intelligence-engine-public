"""
tools/prepare_entity_attribution_patch.py

Applies canonical entity-attribution and scope-boundary patches to active Graph DB.
Registers 'person_jen_pan' with strict role edges to 'workstream_lens' and affirmative
exclusion from 'workstream_project_beacon'.
Uses GovernedStorageContext with WAL mode, busy timeout, and serialized writes.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path

# Ensure repository root is on PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.storage.governed_context import GovernedStorageContext

logger = logging.getLogger("prepare_entity_attribution_patch")


def sha256_str(val: str) -> str:
    return hashlib.sha256(val.encode("utf-8")).hexdigest()


def apply_patch(db_path: Path) -> None:
    print(f"Applying entity attribution patch to: {db_path}")
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with GovernedStorageContext.write_context(db_path) as conn:
        cursor = conn.cursor()

        # 0. Ensure source exists
        cursor.execute(
            """
            INSERT OR IGNORE INTO source (
                source_id, source_type, source_name, source_date, authority_rank,
                source_posture, cold_archive_ref, notes, created_at
            ) VALUES (
                'source_wave3_lens_0918_hitl_resolution',
                'operator_clarification',
                'Operator affirmation and HITL resolution: Jen Pan Project LENS Delivery Lead / PM',
                '2026-09-18',
                1,
                'authoritative_operator_resolution',
                NULL,
                'Definitive operator resolution binding Jen Pan strictly to Project LENS with zero role on Project Beacon.',
                '2026-09-23T11:41:20Z'
            )
            """
        )

        # 1. Upsert node
        cursor.execute(
            """
            INSERT OR REPLACE INTO node (
                node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at
            ) VALUES (
                'person_jen_pan',
                'stakeholder',
                'Jen Pan',
                'Jen Pan',
                'PartnerAdvisory Delivery Lead / Engagement PM on Project LENS; coordinating client prep syncs with harper Hoffman, driving SOW milestones, and managing client delivery recovery; strictly Project LENS only with zero involvement on Project Beacon.',
                'active',
                1,
                '2026-09-23T11:41:20Z',
                '2026-09-23T11:41:20Z'
            )
            """
        )

        # 2. Upsert node_state (using controlled values)
        cursor.execute(
            """
            INSERT OR REPLACE INTO node_state (
                node_id, lifecycle_state, validation_state, currentness_state, approval_state,
                rag_state, resolution_state, retrieval_eligibility, publication_state, review_state,
                legacy_status_text, intentional_orphan, updated_at
            ) VALUES (
                'person_jen_pan',
                'active',
                'accepted_legacy',
                'current',
                'approved',
                'not_applicable',
                'resolved',
                'eligible',
                'internal',
                'reviewed',
                'PartnerAdvisory Delivery Lead / Engagement PM on Project LENS only; zero involvement on Project Beacon',
                0,
                '2026-09-23T11:41:20Z'
            )
            """
        )

        # 3. Upsert edges with all required NOT NULL columns
        edges = [
            (
                "edge_person_jen_pan_role_lens",
                "person_jen_pan",
                "DATED_ROLE_IN",
                "workstream_lens",
                "PartnerAdvisory Delivery Lead / Engagement PM on Project LENS",
                "formal-status source-attributed lead relationship as of 2026-09-18",
                "high",
                "critical",
                "P0",
                "Amit Ayer",
                0,
                "source_wave3_lens_0918_hitl_resolution",
                "2026-09-23T11:41:20Z",
                "2026-09-23T11:41:20Z",
            ),
            (
                "edge_person_jen_pan_leads_lens",
                "person_jen_pan",
                "OWNS_OR_LEADS",
                "workstream_lens",
                "PartnerAdvisory Delivery Lead for Project LENS engagement",
                "formal-status source-attributed lead relationship as of 2026-09-18",
                "high",
                "high",
                "P0",
                "Amit Ayer",
                0,
                "source_wave3_lens_0918_hitl_resolution",
                "2026-09-23T11:41:20Z",
                "2026-09-23T11:41:20Z",
            ),
            (
                "edge_person_jen_pan_affiliated_partner_advisory",
                "person_jen_pan",
                "AFFILIATED_WITH",
                "org_partner_advisory",
                "PartnerAdvisory Principal Consultant / Delivery Lead",
                "formal-status source-attributed PartnerAdvisory affiliation as of 2026-09-18",
                "high",
                "high",
                "P1",
                "Amit Ayer",
                0,
                "source_wave3_lens_0918_hitl_resolution",
                "2026-09-23T11:41:20Z",
                "2026-09-23T11:41:20Z",
            ),
        ]
        for edge in edges:
            cursor.execute(
                """
                INSERT OR REPLACE INTO edge (
                    edge_id, from_node_id, edge_type, to_node_id, description,
                    evidence_posture, confidence, materiality, validation_priority, validation_owner,
                    hitl_required, source_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                edge,
            )

        # 4. Upsert canonical_route (which triggers canonical_route_sync_ai -> node_card_map)
        routes = [
            ("route_jen_pan_nee02", "node", "person_jen_pan", "NEE_02", "delivery lead", 0, "active", "node_card_map", 1, "2026-09-23T11:41:20Z"),
            ("route_jen_pan_nee06", "node", "person_jen_pan", "NEE_06", "operating model", 0, "active", "node_card_map", 1, "2026-09-23T11:41:20Z"),
            ("route_jen_pan_nee10", "node", "person_jen_pan", "NEE_10", "stakeholder context", 1, "active", "node_card_map", 1, "2026-09-23T11:41:20Z"),
            ("route_jen_pan_nee15", "node", "person_jen_pan", "NEE_15", "alias and resolution", 0, "active", "node_card_map", 1, "2026-09-23T11:41:20Z"),
        ]
        for r in routes:
            cursor.execute(
                """
                INSERT OR REPLACE INTO canonical_route (
                    route_id, object_type, object_id, card_id, route_role,
                    is_primary, route_status, source_mechanism, active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                r,
            )

        # 5. Upsert alias entries
        aliases = [
            (
                "alias_person_jen_pan_1",
                "person_jen_pan",
                "Jen Pan",
                "confirmed",
                "authoritative roster and transcript resolution",
                "source_wave3_lens_0918_hitl_resolution",
                "2026-09-23T11:41:20Z",
            ),
            (
                "alias_person_jen_pan_2",
                "person_jen_pan",
                "Jen",
                "confirmed",
                "authoritative conversational reference on LENS",
                "source_wave3_lens_0918_hitl_resolution",
                "2026-09-23T11:41:20Z",
            ),
            (
                "alias_person_jen_pan_3",
                "person_jen_pan",
                "Jim",
                "confirmed",
                "phonetic ASR mis-transcription of Jen affirmed via HITL modal",
                "source_wave3_lens_0918_hitl_resolution",
                "2026-09-23T11:41:20Z",
            ),
        ]
        for a in aliases:
            cursor.execute(
                """
                INSERT OR REPLACE INTO alias (
                    alias_id, node_id, alias, confidence, evidence_posture, source_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                a,
            )

        # 6. Upsert assertions (using 64-char hex SHA256 fingerprints)
        assertions = [
            (
                "asrt_person_jen_pan_role_lens",
                sha256_str("asrt_person_jen_pan_role_lens:fp"),
                "person_jen_pan",
                "HAS_ROLE_IN",
                "DATED_ROLE_IN",
                "workstream_lens",
                None,
                "confirmed_canonical_fact",
                "atomic",
                "Jen Pan is PartnerAdvisory Delivery Lead / Engagement PM on Project LENS.",
                "source_wave3_lens_0918_hitl_resolution",
                "deterministic_rule",
                "legacy_migration",
                "high",
                1.0,
                "accepted_legacy",
                "current",
                "day",
                "exact",
                "2026-09-23T11:41:20Z",
                "resolved",
                "candidate",
                1,
                1,
                "critical",
                "internal",
                '["internal_consulting"]',
                0,
                '{"workstream": "Project LENS", "role": "Delivery Lead / Engagement PM"}',
                "edge",
                '["edge_person_jen_pan_role_lens"]',
                "2026-09-23T11:41:20Z",
                "2026-09-23T11:41:20Z",
            ),
            (
                "asrt_person_jen_pan_affiliated_partner_advisory",
                sha256_str("asrt_person_jen_pan_affiliated_partner_advisory:fp"),
                "person_jen_pan",
                "AFFILIATED_WITH",
                "AFFILIATED_WITH",
                "org_partner_advisory",
                None,
                "confirmed_canonical_fact",
                "atomic",
                "Jen Pan is affiliated with PartnerAdvisory.",
                "source_wave3_lens_0918_hitl_resolution",
                "deterministic_rule",
                "legacy_migration",
                "high",
                1.0,
                "accepted_legacy",
                "current",
                "day",
                "exact",
                "2026-09-23T11:41:20Z",
                "resolved",
                "candidate",
                1,
                1,
                "high",
                "internal",
                '["internal_consulting"]',
                0,
                '{"organization": "PartnerAdvisory"}',
                "edge",
                '["edge_person_jen_pan_affiliated_partner_advisory"]',
                "2026-09-23T11:41:20Z",
                "2026-09-23T11:41:20Z",
            ),
            (
                "asrt_person_jen_pan_sherpa_exclusion",
                sha256_str("asrt_person_jen_pan_sherpa_exclusion:fp"),
                "person_jen_pan",
                "HAS_CLAIM_TEXT",
                None,
                None,
                "Jen Pan was exclusively on Project LENS. She had zero role, presence, or involvement on Project Beacon (which belonged to the AI Hub / PGD portfolio under different teams). Including Sherpa alongside Project LENS in analysis tables or headings is an entity-attribution defect and semantic scope bleed.",
                "confirmed_canonical_fact",
                "atomic",
                "Strict entity attribution and workstream boundary constraint.",
                "source_wave3_lens_0918_hitl_resolution",
                "deterministic_rule",
                "legacy_migration",
                "high",
                1.0,
                "accepted_legacy",
                "current",
                "day",
                "exact",
                "2026-09-23T11:41:20Z",
                "resolved",
                "candidate",
                1,
                1,
                "critical",
                "internal",
                '["internal_consulting"]',
                0,
                '{"boundary_type": "disjoint_workstream_exclusion", "excluded_workstream": "workstream_project_beacon", "permitted_workstream": "workstream_lens"}',
                "claim",
                '[]',
                "2026-09-23T11:41:20Z",
                "2026-09-23T11:41:20Z",
            ),
        ]

        for asrt in assertions:
            cursor.execute(
                """
                INSERT OR REPLACE INTO assertion (
                    assertion_id, assertion_fingerprint, subject_node_id, predicate,
                    legacy_predicate, object_node_id, object_literal, assertion_type, atomicity_state,
                    scope_text, primary_source_id, extraction_method, origin_type, confidence_label, confidence_score, validation_state,
                    currentness_state, date_precision, date_status, recorded_at,
                    resolution_state, canonical_status, canonical_eligible, retrieval_eligible,
                    materiality, classification, allowed_audiences_json, hitl_required,
                    qualifiers_json, legacy_record_type, legacy_record_ids_json, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                asrt,
            )

    print("Entity attribution patch applied successfully.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply entity attribution patch to Graph DB.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=REPO_ROOT / "graph" / "nee_graph.sqlite",
        help="Path to nee_graph.sqlite database",
    )
    args = parser.parse_args()
    apply_patch(args.db_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
