#!/usr/bin/env python3
"""Build exact source-bound Sherpa strategic incorporation packages."""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import seal_artifact, sha256_bytes, stable_id, write_json

PROFILE_KEY = "com.partner_advisory.nee.strategic-profile-controls"
CORRECTION_KEY = "com.partner_advisory.nee.semantic-correction"
PRIOR_EXTRACTION_RUN = "extraction-run-48a47671c11ee227d1ab8c597df1"

CURRENT_ASSERTIONS = {
    "chunk-01-evidence-bounded-architecture-governance": "assertion-5516f41eeb17d7990ad3fa643ada",
    "chunk-02-aws-native-governed-a2a-topology": "assertion-6c20823fca26418b7deb1e7656c7",
    "chunk-03-a2a-runtime-and-governed-discovery": "assertion-e5ec8619549217e68bbe11776791",
    "chunk-04-a2a-durable-task-lifecycle-and-recovery": "assertion-57e9d0b965f587d7dce7bdc14475",
    "chunk-05-a2a-delegated-identity-and-tool-authorization": "assertion-1e0d948d0977842e9feb7a7907af",
    "chunk-06-a2a-state-evidence-and-retrieval-separation": "assertion-151340745140b47cc2575d14cf42",
    "chunk-07-a2a-business-unit-reuse-and-isolation": "assertion-09cd5bbab272b94267dc49d35d70",
    "chunk-08-a2a-path-assurance-and-evaluation": "assertion-8e729d9534c55f87e2374cd31b49",
    "chunk-09-governed-agentic-mesh-precursor": "assertion-0aac6b8b14b5a42f92c3066ff773",
    "chunk-10-priority-zero-architecture-validation-backlog": "assertion-f6728e9d4278c91b6ea8f1eb4c06",
    "chunk-11-priority-one-and-priority-two-architecture-validation-backlog": "assertion-1b9d6991629c41c8678df84c37e3",
}


def profile(
    role: str,
    modality: str,
    *,
    realization: str = "not_applicable",
) -> dict[str, str]:
    return {
        "knowledge_role": role,
        "modality": modality,
        "authority_scope": "source_position",
        "applicability_scope": "nee_specific",
        "realization_state": realization,
    }


A2A_PROFILES = {
    "chunk-01-evidence-bounded-architecture-governance": profile(
        "design_principle", "normative"
    ),
    "chunk-02-aws-native-governed-a2a-topology": profile(
        "option", "proposed", realization="planned"
    ),
    "chunk-03-a2a-runtime-and-governed-discovery": profile(
        "design_principle", "normative"
    ),
    "chunk-04-a2a-durable-task-lifecycle-and-recovery": profile(
        "design_principle", "normative"
    ),
    "chunk-05-a2a-delegated-identity-and-tool-authorization": profile(
        "constraint", "normative"
    ),
    "chunk-06-a2a-state-evidence-and-retrieval-separation": profile(
        "design_principle", "normative"
    ),
    "chunk-07-a2a-business-unit-reuse-and-isolation": profile(
        "option", "proposed", realization="planned"
    ),
    "chunk-08-a2a-path-assurance-and-evaluation": profile(
        "evaluation_criterion", "normative"
    ),
    "chunk-09-governed-agentic-mesh-precursor": profile(
        "option", "proposed", realization="planned"
    ),
    "chunk-10-priority-zero-architecture-validation-backlog": profile(
        "option", "proposed", realization="planned"
    ),
    "chunk-11-priority-one-and-priority-two-architecture-validation-backlog": profile(
        "option", "proposed", realization="planned"
    ),
}

PLAN_SPECS = (
    ("intent-not-realization", "Intended outcomes cannot be confused with realized outcomes.", profile("constraint", "normative")),
    ("external-not-nee-fact", "External practices cannot become NEE facts.", profile("constraint", "normative")),
    ("recommendation-not-truth", "Generated recommendations cannot automatically enter graph truth.", profile("constraint", "normative")),
    ("authority-controls-use", "Source authority controls permitted use rather than general retrievability.", profile("design_principle", "normative")),
    ("descriptions-not-material-meaning", "Node descriptions are not required to carry strategically material meaning.", profile("design_principle", "normative")),
    ("chunks-not-only-semantics", "Source chunks support citations but are not the only semantic representation.", profile("design_principle", "normative")),
    ("route-specific-budgets", "Route-specific retrieval budgets and minimum role coverage.", profile("design_principle", "normative")),
    ("no-intended-as-achieved", "No intended outcome is presented as achieved.", profile("constraint", "normative")),
    ("external-pattern-transfer", "Public practices can inform recommendations without asserting Sherpa implementation.", profile("constraint", "normative")),
    ("recommendation-traceability", "Every recommendation traces to objectives, constraints, diagnostics, and evidence.", profile("evaluation_criterion", "normative")),
    ("alternatives-and-status-quo", "Material alternatives and the status quo are considered where applicable.", profile("evaluation_criterion", "normative")),
    ("missing-information-confidence", "Missing information limits confidence rather than disappearing from the answer.", profile("constraint", "normative")),
    ("outcome-measures", "Outcome measures such as architect-touch rate, self-service resolution rate, escalation rate, and decision-cycle time.", profile("metric", "descriptive")),
    ("measurable-outcomes", "Recommendations connect to measurable outcomes.", profile("evaluation_criterion", "normative")),
)


def build_a2a(base_path: Path) -> dict:
    base = copy.deepcopy(__import__("json").loads(base_path.read_text(encoding="utf-8")))
    chunks = [
        copy.deepcopy(chunk)
        for chunk in base["chunks"]
        if chunk["chunk_id"] in CURRENT_ASSERTIONS
    ]
    migration_note = (
        "Strategic-role migration: this replacement preserves the governed "
        "candidate semantics while adding an explicit role for strategic retrieval."
    )
    for chunk in chunks:
        chunk["text"] = f"{chunk['text']}\n\n{migration_note}"
        chunk["locator"]["quote_digest"] = sha256_bytes(
            chunk["text"].encode("utf-8")
        )
    controls = {
        chunk_id: {
            **copy.deepcopy(base["extensions"][CORRECTION_KEY]["controls"][chunk_id]),
            "supersedes_assertion_id": assertion_id,
        }
        for chunk_id, assertion_id in CURRENT_ASSERTIONS.items()
    }
    raw = (
        "# Sherpa A2A Strategic Profile Migration v2\n\n"
        "This governed source preserves the candidate authority of the previously "
        "approved A2A semantic corrections and adds explicit strategic retrieval "
        "roles. It does not promote any architecture pattern to an implemented "
        "Sherpa fact.\n\n"
        + "\n\n".join(chunk["text"] for chunk in chunks)
        + "\n"
    )
    value = {
        **base,
        "artifact_id": "markdown-source-sherpa-a2a-strategic-profile-migration-v2-2026-07-29",
        "source": {
            **base["source"],
            "source_id": "source_sherpa_a2a_strategic_profile_migration_v2_2026_07_29",
            "source_version_id": stable_id(
                "source-version", "sherpa-a2a-strategic-profile-migration-v2-2026-07-29"
            ),
            "title": "Sherpa A2A strategic profile migration v2",
            "source_location": "governed/sherpa/a2a-strategic-profile-migration-v2-2026-07-29.md",
            "raw_markdown": raw,
            "source_file_sha256": sha256_bytes(raw.encode("utf-8")),
        },
        "chunks": chunks,
        "extensions": {
            "com.partner_advisory.nee.chunk-selection": {
                "authority_boundary": "Operator-approved strategic classification of the exact governed A2A candidates; no approved-fact promotion.",
                "excluded_sections": ["Primary source map provenance"],
                "profile": "strategic-profile-migration-v1",
            },
            CORRECTION_KEY: {
                "schema_version": "1.0.0",
                "replaces_run_id": PRIOR_EXTRACTION_RUN,
                "controls": controls,
            },
            PROFILE_KEY: A2A_PROFILES,
        },
    }
    return seal_artifact(value)


def build_plan(plan_path: Path) -> dict:
    raw = plan_path.read_text(encoding="utf-8")
    chunks = []
    profiles = {}
    for slug, text, item_profile in PLAN_SPECS:
        if text not in raw:
            raise ValueError(f"plan text not found: {text}")
        chunk_id = f"strategic-plan-{slug}"
        chunks.append(
            {
                "chunk_id": chunk_id,
                "title": text.rstrip("."),
                "text": text,
                "locator": {
                    "kind": "section",
                    "value": slug.replace("-", " "),
                    "quote_digest": sha256_bytes(text.encode("utf-8")),
                },
                "selection_rationale": "Exact operator-supplied strategic-refactoring requirement selected for governed role-aware retrieval.",
                "materiality": "high",
                "extraction_confidence": 1.0,
            }
        )
        profiles[chunk_id] = item_profile
    return seal_artifact(
        {
            "contract_id": "urn:nee:dual-pipeline:markdown-source-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "markdown-source-sherpa-strategic-reasoning-plan-2026-07-29",
            "source": {
                "source_id": "source_sherpa_strategic_reasoning_plan_2026_07_29",
                "source_version_id": stable_id(
                    "source-version", sha256_bytes(raw.encode("utf-8"))
                ),
                "title": "Sherpa strategic-reasoning refactoring plan",
                "source_location": str(plan_path.resolve()),
                "raw_markdown": raw,
                "source_file_sha256": sha256_bytes(raw.encode("utf-8")),
                "source_date": "2026-07-29",
                "classification": "internal",
                "audiences": ["internal_consulting", "nee-engineering"],
                "source_posture": "governed_internal",
                "authority_rank": 9,
                "anchor_entity": {
                    "reported_text": "Project Beacon",
                    "stable_identifier": "project_beacon",
                    "context": "Operator-approved strategic-reasoning requirements for the Project Beacon graph and retrieval model.",
                },
            },
            "chunks": chunks,
            "adapter": {"name": "nee-markdown-source-intake", "version": "1.0.0"},
            "extensions": {PROFILE_KEY: profiles},
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    a2a = build_a2a(
        ROOT
        / "exports"
        / "sherpa-aws-a2a-architecture"
        / "context-retrieval"
        / "sherpa-a2a-semantic-correction-2026-07-29.package.json"
    )
    plan = build_plan(args.plan)
    for name, value in (
        ("sherpa-a2a-strategic-profile-migration-v2-2026-07-29.package.json", a2a),
        ("sherpa-strategic-reasoning-plan-2026-07-29.package.json", plan),
    ):
        validate_artifact(value)
        write_json(args.output_dir / name, value)
    print({"status": "PASS", "a2a_chunks": len(a2a["chunks"]), "plan_chunks": len(plan["chunks"])})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
