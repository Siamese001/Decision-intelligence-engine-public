#!/usr/bin/env python3
"""
Prepare and emit authoritative manifests for APEX Development - Rewire:
1. source_manifest.json
2. attribution_manifest.json
3. base_graph_snapshot.json
4. normalized_claim_ledger.json
"""

import hashlib
import json
from pathlib import Path

WORKSPACE_ROOT = Path(r"c:\Git\decision-intelligence-engine")
SOURCE_PATH = Path(
    r"C:\Users\amit.ayer\OneDrive - PartnerAdvisory\Documents\NEE\NEA\Nexus\Nexus Reid - 8.28.2026\Project_Atlas_Transcript_Attributed_08.28.2026.md"
)
OUTPUT_DIR = WORKSPACE_ROOT / "output" / "project_atlas" / "08-28-2026"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

source_bytes = SOURCE_PATH.read_bytes()
source_sha256 = hashlib.sha256(source_bytes).hexdigest()
source_len = len(source_bytes)

# Define 10 verified evidence quotes from source
quotes_defs = [
    {
        "id": "ev-01",
        "quote": "what that first thing that we should be targeting is a Q4 milestone, right?",
        "kind": "strategic",
        "speaker": "collective:room-mic",
        "mode": "SHARED_ROOM_MIC",
    },
    {
        "id": "ev-02",
        "quote": "solar 40, batteries between 10, and depending if data centers are coming in, 10 and 20 individual projects.",
        "kind": "technical",
        "speaker": "collective:room-mic",
        "mode": "SHARED_ROOM_MIC",
    },
    {
        "id": "ev-03",
        "quote": "basically built out our nexus, so don't let him kind of not say. I built an HTML. I built an HTML.",
        "kind": "technical",
        "speaker": "collective:room-mic",
        "mode": "SHARED_ROOM_MIC",
    },
    {
        "id": "ev-04",
        "quote": "Our transmission system is in the minds of people that sit in Miami.",
        "kind": "risk",
        "speaker": "collective:room-mic",
        "mode": "SHARED_ROOM_MIC",
    },
    {
        "id": "ev-05",
        "quote": "But LAMS is nothing more than a database with a bunch of entries into it. Can we query that?",
        "kind": "technical",
        "speaker": "collective:room-mic",
        "mode": "SHARED_ROOM_MIC",
    },
    {
        "id": "ev-06",
        "quote": "the access to the right data sources piece, that's the, yeah, that's the blocker",
        "kind": "risk",
        "speaker": "collective:room-mic",
        "mode": "SHARED_ROOM_MIC",
    },
    {
        "id": "ev-07",
        "quote": "So what our real ask is for Q4 is how do we get this built?",
        "kind": "strategic",
        "speaker": "collective:room-mic",
        "mode": "SHARED_ROOM_MIC",
    },
    {
        "id": "ev-08",
        "quote": "I think we can get started next week and just kind of like have a probably another, a little bit deeper meeting",
        "kind": "strategic",
        "speaker": "person:dan-okray",
        "mode": "DIRECT",
    },
    {
        "id": "ev-09",
        "quote": "I'll connect with you Monday morning.",
        "kind": "strategic",
        "speaker": "person:dan-okray",
        "mode": "DIRECT",
    },
    {
        "id": "ev-10",
        "quote": "like a 50 step process flow going through development.",
        "kind": "strategic",
        "speaker": "collective:room-mic",
        "mode": "SHARED_ROOM_MIC",
    },
]

evidence_index = {}
for item in quotes_defs:
    qb = item["quote"].encode("utf-8")
    pos = source_bytes.find(qb)
    assert pos >= 0, f"Quote not found: {item['quote']}"
    evidence_index[item["id"]] = {
        "evidence_id": item["id"],
        "statement_id": item["id"],
        "source_id": "source-apex-rewire-transcript",
        "speaker": item["speaker"],
        "kind": item["kind"],
        "source_classification": item["kind"],
        "text": item["quote"],
        "exact_text": item["quote"],
        "attribution_mode": item["mode"],
        "speaker_id": item["speaker"],
        "start_offset": pos,
        "end_offset": pos + len(qb),
        "artifact_sha256": source_sha256,
        "locator": {
            "kind": "section",
            "value": "## Transcript",
            "start_offset": pos,
            "end_offset": pos + len(qb),
            "exact_text": item["quote"],
            "artifact_sha256": source_sha256,
        },
    }

# 1. source_manifest.json
source_manifest = {
    "source_path": str(SOURCE_PATH.resolve()),
    "raw_sha256": source_sha256,
    "raw_byte_length": source_len,
    "encoding": "utf-8",
    "attribution_segments": [
        {
            "speaker_id": "collective:room-mic",
            "attribution_mode": "SHARED_ROOM_MIC",
            "blocked_individuals": [],
        },
        {"speaker_id": "person:dan-okray", "attribution_mode": "DIRECT"},
        {"speaker_id": "person:amit-ayer", "attribution_mode": "DIRECT"},
        {"speaker_id": "person:justin-muskivitch", "attribution_mode": "DIRECT"},
    ],
    "vocabulary": [
        "LAMS",
        "Documentum",
        "Unifier",
        "P6",
        "purple sheet",
        "Nexus",
        "264,000",
    ],
}
(OUTPUT_DIR / "source_manifest.json").write_text(
    json.dumps(source_manifest, indent=2), encoding="utf-8"
)

# 2. attribution_manifest.json
attribution_manifest = {
    "meeting_archetype": "STRATEGIC_DISCOVERY",
    "allowed_owner_ids": [
        "person:dan-okray",
        "person:amit-ayer",
        "person:justin-muskivitch",
        "person:reid-schenk",
        "person:sharon-diaram",
        "person:daniel-brankin",
        "person:diana-valencia",
        "org:partner_advisory",
        "org:nea",
        "org:apex",
    ],
    "owners": [
        "person:dan-okray",
        "person:amit-ayer",
        "person:justin-muskivitch",
        "person:reid-schenk",
        "person:sharon-diaram",
        "person:daniel-brankin",
        "person:diana-valencia",
        "org:partner_advisory",
        "org:nea",
        "org:apex",
    ],
    "segments": source_manifest["attribution_segments"],
}
(OUTPUT_DIR / "attribution_manifest.json").write_text(
    json.dumps(attribution_manifest, indent=2), encoding="utf-8"
)

# 3. base_graph_snapshot.json
base_graph = {
    "version": "1.0.0",
    "entities": [
        {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory"},
        {"id": "org:nea", "type": "org", "name": "Apex Global Enterprises"},
        {"id": "org:apex", "type": "org", "name": "Apex Power & Grid"},
        {
            "id": "project:apex-development-rewire",
            "type": "project",
            "name": "APEX Development - Rewire",
        },
        {
            "id": "system:lams",
            "type": "system",
            "name": "Land Asset Management System (LAMS)",
        },
        {
            "id": "workstream:partner_advisory-apex-consulting",
            "type": "workstream",
            "name": "PartnerAdvisory APEX Consulting Engagement",
        },
        {
            "id": "decision:apex-ratify-q4-wedge",
            "type": "decision",
            "name": "Ratify Q4 Execution Wedge",
        },
        {
            "id": "risk:apex-transmission-data-access",
            "type": "risk",
            "name": "Transmission Data Access Constraint",
        },
        {
            "id": "action:apex-reid-monday-connect",
            "type": "action",
            "name": "Monday Morning Follow-up Connect",
        },
        {
            "id": "action:apex-q4-scoping-session",
            "type": "action",
            "name": "Deeper Q4 Scoping Session",
        },
        {
            "id": "metric:developer-capacity",
            "type": "metric",
            "name": "Developer Capacity & Portfolio Throughput",
        },
        {
            "id": "action:apex-developer-cycle-time-benchmark",
            "type": "action",
            "name": "Developer Cycle Time Benchmark",
        },
    ],
    "relationships": [
        {
            "signal_id": "sig_org_fpl_owns_project_fpl_development_rewire",
            "from_id": "org:apex",
            "to_id": "project:apex-development-rewire",
            "relationship": "OWNS",
        }
    ],
    "assertions": [],
}
(OUTPUT_DIR / "base_graph_snapshot.json").write_text(
    json.dumps(base_graph, indent=2), encoding="utf-8"
)

# 4. normalized_claim_ledger.json
claim_ledger = {
    "meeting_archetype": "STRATEGIC_DISCOVERY",
    "evidence_index": evidence_index,
    "claims": [
        {
            "claim_id": "claim-01",
            "statement": "Participants discussed targeting a bounded Q4 milestone as the initial objective for project delivery.",
            "text": "Participants discussed targeting a bounded Q4 milestone as the initial objective for project delivery.",
            "kind": "strategic",
            "modality": "PROPOSED",
            "epistemic_status": "DOCUMENTED_FACT",
            "entities": ["project:apex-development-rewire"],
            "owner": None,
            "due_date": None,
            "evidence_ids": ["ev-01"],
        },
        {
            "claim_id": "claim-02",
            "statement": "APEX manages approximately 40 solar projects and between 10 and 20 battery projects depending on data centers.",
            "text": "APEX manages approximately 40 solar projects and between 10 and 20 battery projects depending on data centers.",
            "kind": "technical",
            "modality": "DISCUSSION",
            "epistemic_status": "DOCUMENTED_FACT",
            "entities": ["org:apex", "project:apex-development-rewire"],
            "owner": None,
            "due_date": None,
            "evidence_ids": ["ev-02"],
        },
        {
            "claim_id": "claim-03",
            "statement": "Throughput and developer capacity represent the primary economic value mechanism; baseline developer cycle times remain unquantified.",
            "text": "Throughput and developer capacity represent the primary economic value mechanism; baseline developer cycle times remain unquantified.",
            "kind": "strategic",
            "modality": "PROPOSED",
            "epistemic_status": "MODEL_INFERENCE",
            "review_required": True,
            "entities": ["project:apex-development-rewire"],
            "owner": None,
            "due_date": None,
            "evidence_ids": ["ev-01", "ev-02"],
        },
        {
            "claim_id": "claim-04",
            "statement": "Transmission system knowledge is concentrated in Miami, representing an acknowledged project information access constraint.",
            "text": "Transmission system knowledge is concentrated in Miami, representing an acknowledged project information access constraint.",
            "kind": "risk",
            "modality": "DISCUSSION",
            "epistemic_status": "DIRECT_FACT",
            "entities": ["risk:apex-transmission-data-access", "project:apex-development-rewire"],
            "owner": None,
            "due_date": None,
            "evidence_ids": ["ev-04"],
        },
        {
            "claim_id": "claim-05",
            "statement": "LAMS is a database with entries, with participants asking whether an AI layer can query it.",
            "text": "LAMS is a database with entries, with participants asking whether an AI layer can query it.",
            "kind": "technical",
            "modality": "PROPOSED",
            "epistemic_status": "DOCUMENTED_FACT",
            "entities": ["system:lams", "project:apex-development-rewire"],
            "owner": None,
            "due_date": None,
            "evidence_ids": ["ev-05"],
        },
        {
            "claim_id": "claim-06",
            "statement": "Access to the right data sources represents an acknowledged operational blocker for project integration.",
            "text": "Access to the right data sources represents an acknowledged operational blocker for project integration.",
            "kind": "risk",
            "modality": "BLOCKED",
            "epistemic_status": "DIRECT_FACT",
            "entities": ["risk:apex-transmission-data-access", "project:apex-development-rewire"],
            "owner": None,
            "due_date": None,
            "evidence_ids": ["ev-06"],
        },
        {
            "claim_id": "claim-07",
            "statement": "APEX leadership asked how to build connected capabilities in Q4, while PartnerAdvisory demonstrated an exploratory HTML mockup as a proof of concept.",
            "text": "APEX leadership asked how to build connected capabilities in Q4, while PartnerAdvisory demonstrated an exploratory HTML mockup as a proof of concept.",
            "kind": "strategic",
            "modality": "PROPOSED",
            "epistemic_status": "DOCUMENTED_FACT",
            "entities": ["org:partner_advisory", "project:apex-development-rewire"],
            "owner": None,
            "due_date": None,
            "evidence_ids": ["ev-03", "ev-07"],
        },
        {
            "claim_id": "claim-08",
            "statement": "Candidate starting focus areas for Q4 scoping remain under open evaluation, with final wedge selection unratified.",
            "text": "Candidate starting focus areas for Q4 scoping remain under open evaluation, with final wedge selection unratified.",
            "kind": "strategic",
            "modality": "DISCUSSION",
            "epistemic_status": "OPEN_QUESTION",
            "entities": ["decision:apex-ratify-q4-wedge", "project:apex-development-rewire"],
            "owner": None,
            "due_date": None,
            "evidence_ids": ["ev-01", "ev-07"],
        },
        {
            "claim_id": "claim-09",
            "statement": "Drew Oakley committed to follow up with Reid Schenk Monday morning regarding Q4 scoping logistics.",
            "text": "Drew Oakley committed to follow up with Reid Schenk Monday morning regarding Q4 scoping logistics.",
            "kind": "strategic",
            "modality": "COMMITTED",
            "epistemic_status": "DIRECT_FACT",
            "entities": ["person:dan-okray", "action:apex-reid-monday-connect"],
            "owner": "person:dan-okray",
            "due_date": None,
            "evidence_ids": ["ev-09"],
        },
        {
            "claim_id": "claim-10",
            "statement": "A 50-step process flow through development was referenced as a prior artifact to search for to identify pain points.",
            "text": "A 50-step process flow through development was referenced as a prior artifact to search for to identify pain points.",
            "kind": "strategic",
            "modality": "PROPOSED",
            "epistemic_status": "DOCUMENTED_FACT",
            "entities": ["org:apex", "project:apex-development-rewire"],
            "owner": None,
            "due_date": None,
            "evidence_ids": ["ev-10"],
        },
        {
            "claim_id": "claim-11",
            "statement": "Drew Oakley proposed coordinating a deeper Q4 scoping working session next week to evaluate candidate deliverables.",
            "text": "Drew Oakley proposed coordinating a deeper Q4 scoping working session next week to evaluate candidate deliverables.",
            "kind": "strategic",
            "modality": "PROPOSED",
            "epistemic_status": "MODEL_INFERENCE",
            "review_required": True,
            "entities": ["person:dan-okray", "action:apex-q4-scoping-session"],
            "owner": None,
            "due_date": None,
            "evidence_ids": ["ev-08"],
        },
    ],
}
(OUTPUT_DIR / "normalized_claim_ledger.json").write_text(
    json.dumps(claim_ledger, indent=2), encoding="utf-8"
)

print("All authoritative manifests written to output/project_atlas/08-28-2026/")
