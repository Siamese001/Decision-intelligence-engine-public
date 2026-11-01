from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

# Domain Dilemma Classification Rules
DOMAIN_DILEMMA_RULES: List[Dict[str, Any]] = [
    {
        "id": "SCADA_NERC_CIP_BOUNDARY",
        "patterns": (
            re.compile(r"\b(?:scada|ems|cip-005|nerc-cip|infrastructure data|get it live)\b", re.IGNORECASE),
        ),
        "snap_cue": "Decouple via PI Historian replica",
        "suggested_script": "Since live SCADA is airgapped by CIP-005, let's ingest from the replicated PI Historian.",
        "risk_trap": "Live SCADA ingress violates CIP-005",
        "category": "TECHNICAL",
        "confidence": 0.96,
    },
    {
        "id": "TIMELINE_2027_SPIKE_WEDGE",
        "patterns": (
            re.compile(r"\b(?:2027|wait for 2027|not wait for|sooner and better|timeline)\b", re.IGNORECASE),
        ),
        "snap_cue": "Anchor to grounded-contract proof spike",
        "suggested_script": "Let's validate this in an initial grounded-contract spike before committing to long enterprise releases.",
        "risk_trap": "Multi-year delay kills adoption",
        "category": "STRATEGIC",
        "confidence": 0.95,
    },
    {
        "id": "SAAS_VS_CUSTOM_BUILD",
        "patterns": (
            re.compile(r"\b(?:pano|pano ai|saas|not really using it|single point of view|custom map|cameras on the towers)\b", re.IGNORECASE),
        ),
        "snap_cue": "Integrate Pano API, avoid rebuild",
        "suggested_script": "Rather than rebuilding map tooling, let's ingest Pano AI alerts into our single pane of glass.",
        "risk_trap": "Rebuilding existing SaaS increases cost",
        "category": "COMMERCIAL",
        "confidence": 0.93,
    },
    {
        "id": "ROCC_PLAYBOOK_STANDARDIZATION",
        "patterns": (
            re.compile(r"\b(?:rocc|playbook|no playbook|substation|alarm|alarms|outage)\b", re.IGNORECASE),
        ),
        "snap_cue": "Standardize ROCC alarm triage playbook",
        "suggested_script": "Can we map the current ROCC alarm triage workflow before defining automated playbooks?",
        "risk_trap": "Ad-hoc procedures create outage delays",
        "category": "ALIGNMENT",
        "confidence": 0.92,
    },
    {
        "id": "RACI_OWNERSHIP_HANDOFF",
        "patterns": (
            re.compile(r"\b(?:who owns|who should own|which decisions|boundary|draw the line)\b", re.IGNORECASE),
        ),
        "snap_cue": "Lock technical DRI boundary",
        "suggested_script": "Let's align technical architecture ownership with PartnerAdvisory, while internal teams drive operations.",
        "risk_trap": "Unassigned ownership creates delivery stalls",
        "category": "ALIGNMENT",
        "confidence": 0.94,
    },
]


def evaluate_flash_snap(text: str, anchors: List[str], graph_hit: bool) -> bool:
    if not graph_hit:
        return False

    hits = 0
    text_lower = text.lower()
    for anchor in anchors:
        if anchor.lower() in text_lower:
            hits += 1

    return hits >= 2


class FlashSnapEngine:
    """Evaluates conversational utterances to produce instant sub-500ms executive advisory cues."""

    def __init__(self, in_memory_graph: Optional[Any] = None) -> None:
        self.in_memory_graph = in_memory_graph

    def evaluate_utterance(
        self,
        utterance: str,
        speaker: str = "Speaker",
        timestamp: str = "",
        graph_context: str = "",
    ) -> Optional[Dict[str, Any]]:
        clean_text = (utterance or "").strip()
        if len(clean_text) < 15:
            return None

        t0 = time.perf_counter()

        matched_rule: Optional[Dict[str, Any]] = None
        for rule in DOMAIN_DILEMMA_RULES:
            if any(p.search(clean_text) for p in rule["patterns"]):
                matched_rule = rule
                break

        if not matched_rule:
            return None

        latency_ms = (time.perf_counter() - t0) * 1000.0

        return {
            "type": "COPILOT_FEEDBACK",
            "has_guidance": True,
            "is_flash_snap": True,
            "track": "TRACK_1_FLASH_SNAP",
            "category": matched_rule["category"],
            "snap_cue": matched_rule["snap_cue"],
            "suggested_script": matched_rule["suggested_script"],
            "risk_trap": matched_rule["risk_trap"],
            "risk_alert": matched_rule["risk_trap"],
            "opportunity": matched_rule["suggested_script"],
            "confidence": matched_rule["confidence"],
            "latency_ms": round(latency_ms, 2),
            "speaker": speaker,
            "timestamp": timestamp,
            "dilemma_id": matched_rule["id"],
            "graph_context": graph_context or "",
        }
