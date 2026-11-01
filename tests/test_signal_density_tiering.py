"""
tests/test_signal_density_tiering.py

Unit tests for Technical Signal Density Index (TSDI) calculation,
RigorTier classification, and adaptive word budgeting in Gate 2.
"""
from __future__ import annotations

from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_qa_core.signal_density import (
    RigorTier,
    compute_technical_signal_density,
)


def test_tsdi_deep_architecture_classification():
    text = """
    We analyzed the dual-vision inference mesh combining Model A OCR and Model B ViT with spatial IoU.
    The pre-flight gate enforces Laplacian variance >= 100 and luminance exposure between 40 and 220.
    Reconciliation uses Hirschmann dominance with RS20-0800T1T1 prefix.
    Straight-through processing STP threshold is set to 85% before Maximo persistence.
    ```ascii
    ╔══════════════════════════════╗
    ║ ZONE 1: Intake               ║
    ╚══════════════════════════════╝
    ```
    """
    profile = compute_technical_signal_density(text, metadata={"has_architecture_diagram": True, "duration": 56})
    assert profile.has_diagrams is True
    assert profile.tsdi_score >= 0.15
    assert profile.rigor_tier == RigorTier.TIER_1_DEEP_ARCHITECTURE
    assert profile.max_total_words >= 2600


def test_tsdi_lightweight_status_classification():
    text = """
    Quick check-in with the team. We discussed scheduling for next week's sync and confirmed
    that onboarding paperwork was submitted. Everyone is aligned on the current timeline.
    No technical changes or architecture modifications were reviewed today.
    """
    profile = compute_technical_signal_density(text, metadata={"duration": 20})
    assert profile.has_diagrams is False
    assert profile.tsdi_score < 0.06
    assert profile.rigor_tier == RigorTier.TIER_3_LIGHTWEIGHT_STATUS
    assert profile.min_narrative_words <= 350


def test_gate2_lightweight_status_passes_with_concise_words():
    body = """# Subject: Team Catchup -- Executive Strategic & Technical Brief | 09/19/2026

Date: 09/19/2026
Time: 09:00 AM - 09:20 AM ET (Duration: ~20m)
Attendees:
- PartnerAdvisory: Jen Pan (Lead Engineer), Amit Ayer (Lead Enterprise Architect)

---

## Meeting Objective
Conduct an informal status check-in to align on weekly scheduling, review onboarding milestones, and confirm developer access credentials across non-production environments.

## Key Discussion Points & Findings
1. Onboarding Status: Reviewed developer workstation provisioning and environment setup, confirming all security training modules are complete.
2. Sprint Coordination: Aligned on sprint cadence and confirmed timing for the upcoming bi-weekly architecture checkpoint.
3. Access Verification: Verified access grants for internal tooling dashboards and documented open ticketing requests for sandbox environments.

## Strategic Synthesis
| # | What We Heard (Operational Friction / Spoken Dialogue) | What It Means (Enterprise Architecture / Delivery Implication) |
| :--- | :--- | :--- |
| 1 | Onboarding tickets require multi-day approval chains. | Need automated role-based access for future onboarding cohorts. |

## Recommendations & Decisions Required
The team formally decided to proceed with existing onboarding workflows while tracking access ticket cycle times. No technical architecture modifications or pipeline alterations were ratified during this alignment session. Alternative ticketing options were deferred until next sprint.

## Action & Governance Ledger
| # | Type | Description | Accountable Owner | Target Timing | Locators | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Action | Follow up on sandbox access tickets | Jen Pan | 09/26/2026 | [05:12] | In Progress |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Jen Pan - PartnerAdvisory, Lead Engineer
- **Onboarding Progress and Tooling Setup**: Jen confirmed that local development environments are operational and verified that project dependencies install cleanly. She noted that sandbox access requests remain pending in IT queues.
- **Sprint Schedule Alignment**: She reviewed upcoming sprint deliverables and confirmed availability for upcoming team reviews and stakeholder syncs.

# Amit Ayer - PartnerAdvisory, Lead Enterprise Architect
- **Access Governance and Operational Enablement**: Amit affirmed support for escalating sandbox access tickets to IT leadership if not resolved within two business days.
- **Delivery Coordination**: He emphasized keeping sprint coordination lightweight while critical project assets and pipeline dependencies remain in pre-flight design.
"""
    meta = {
        "meeting_type": "GENERAL",
        "duration": 20,
    }
    res = run_qa_gate_2(body, meta=meta)
    assert res["status"] == "PASS", f"Gate 2 failed: {res.get('failures')}"
    assert res["passed"] is True


def test_gate2_deep_architecture_with_exhibit_passes():
    diagram = """```ascii
╔══════════════════════════════════════════════════════════════════╗
║ ZONE 1: Intake -> ZONE 2: Pre-flight Gate -> ZONE 3: Dual-Vision ║
║ ZONE 4: Reconciliation -> ZONE 5: Tri-Tier -> ZONE 6: Maximo     ║
╚══════════════════════════════════════════════════════════════════╝
```"""
    body = f"""# Subject: LENS Architecture Deep Dive -- Executive Strategic & Technical Brief | 09/18/2026

Date: 09/18/2026
Time: 12:43 PM - 01:39 PM ET (Duration: ~56m)
Attendees:
- PartnerAdvisory: Jake Stark (Tech Lead), Amit Ayer (Lead Enterprise Architect), Miles (Senior Engineer)

---

## Meeting Objective
Evaluate, debate, and formally ratify the end-to-end target architecture for the Project LENS Autonomous Asset Intelligence Pipeline v1.2, establishing deterministic ingestion filters, dual-vision model inference coordination, semantic reconciliation rules, and straight-through processing thresholds for Maximo persistence.

## Key Discussion Points & Findings
1. Multi-Modal Intake and Field Variability: Technicians capture high-resolution equipment nameplates and wide cabinet shots in variable lighting and physical field conditions. Single-perspective optical capture introduces blur, glare, and partial occlusion, necessitating a multi-perspective ingestion contract pairing macro identification with structural cabinet context [04:12].
2. Target Pipeline Architecture Topology: The engineering leads reviewed and finalized the v1.2 architecture topology across six governed zones:
{diagram}
The pre-flight gate executes a zero-compute token gating mechanism on edge devices before expensive vision models are invoked, validating luminance exposure within 40 to 220 and Laplacian variance >= 100 [15:30].
3. Dual-Vision Model Orchestration: Combining Model A (optical character recognition) and Model B (vision transformer classification) using spatial Intersection-over-Union (IoU) bounding box coordinates. Disagreement between text extraction and visual feature classification triggers automated routing to human review. The team established that neither model acts as an absolute authority; instead, when spatial coordinates overlap by more than 70%, the confidence scores are blended with historical prior calibration to maximize extraction accuracy [24:45].
4. Governed Semantic Reconciliation and Device Hierarchy: Reconciling raw model outputs against the enterprise catalog hierarchy. For network infrastructure, Hirschmann switch components mandate an RS20-0800T1T1 prefix. Non-serialized components such as terminal blocks and busbars are formally recognized with explicit zero-serial design markers rather than failed extractions. This distinction prevents the system from flagging expected un-serialized assets as data capture errors [36:10].
5. Tri-Tier Resolution Routing: Straight-Through Processing (STP) is enforced with a strict >= 85% composite confidence threshold for autonomous updates. Classifications scoring 65% to 84% route to a 1-click human-in-the-loop review queue, while scores below 65% enter the anomaly quarantine vault. This tri-tier architecture ensures field engineers are never overwhelmed by routine classifications while safeguarding enterprise state integrity [42:18].
6. Enterprise State Persistence: Validated extractions persist into Maximo v2 via cryptographically signed event envelopes containing raw image SHA-256 digests, model version stamps, and spatial bounding coordinates. The engineering leads agreed that direct writes without immutable audit receipts pose unacceptable operational risks for turbine maintenance schedules [49:05].

## Architectural Trade-Off Matrix
| Option | Technical Mechanism | Blast Radius | Team Consensus |
| :--- | :--- | :--- | :--- |
| Option A: Dual-Vision Inference Mesh | ViT + OCR with spatial IoU matching and spatial coordinate reconciliation | Isolated to inference service | Unanimously ratified by Jake, Amit, and Miles |
| Option B: Single Vision Transformer | Monolithic multi-task model predicting class and serial simultaneously | High failure risk on edge | Rejected due to severe hallucination on blurry nameplates |

## Active Technical Spikes & Experimental Inquiries
| Spike Title | Hypothesis | Experimental Test Method | Pass/Fail Metric | Lead Explorer | Auto-Expire Date |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Spike 1: Laplacian Filter Calibration | Laplacian variance >= 100 reliably filters motion blur without rejecting valid nameplates | Benchmark across 500 field images from GE2X turbines | False rejection rate < 2.0% | Jake Stark | 10/01/2026 |
| Spike 2: Edge Token Gating Latency | Local edge token gating reduces payload transmission by >= 40% | Profile cellular bandwidth across 50 field uploads | Average payload reduction >= 40% | Miles | 10/05/2026 |

## Recommendations & Decisions Required
The engineering team formally ratified the Project LENS Autonomous Asset Intelligence Pipeline v1.2 as the authoritative baseline for Q4 implementation. The team decided to enforce the 85% straight-through processing threshold for autonomous Maximo updates, mandate edge Laplacian pre-flight filtration, and implement the RS20 prefix reconciliation rule. Delaying this architectural ratification would risk building uncoordinated computer vision pipelines, resulting in expensive GPU compute waste and corrupted enterprise asset records.

## Action & Governance Ledger
| # | Type | Description | Accountable Owner | Target Timing | Locators | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Action | Implement Laplacian variance pre-flight filter on edge ingestion service | Jake Stark | 09/25/2026 | [15:30] | In Progress |
| 2 | Action | Benchmark dual-vision IoU spatial coordinate matching on test dataset | Miles | 10/02/2026 | [28:10] | Not Started |
| 3 | Gate | Review Spike 1 benchmark results before enabling automated Maximo writes | Amit Ayer | 10/06/2026 | [51:40] | Scheduled |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Jake Stark - PartnerAdvisory, Tech Lead
- **Dual-Vision Model Coordination and Spatial Matching**: Jake detailed the technical orchestration between Model A OCR and Model B ViT, explaining that spatial IoU matching provides robust verification against false positives. He demonstrated that model disagreement should route directly to human verification rather than guessing ambiguous serials.
- **Pre-Flight Filtration and Zero-Compute Token Gating**: He established that blur detection must occur prior to cloud ingestion to avoid unnecessary GPU token expenditures, validating Laplacian variance thresholds across baseline test sets from turbine field collections.
- **Edge Deployment Architecture**: He outlined the hardware acceleration constraints for deploying lightweight pre-flight filters directly to mobile capture devices in remote wind farm locations.

# Amit Ayer - PartnerAdvisory, Lead Enterprise Architect
- **Enterprise Reconciliation and Catalog Hierarchy**: Amit articulated the need for deterministic catalog hierarchy matching, noting that Hirschmann switch prefixes follow strict catalog numbering rules. He insisted that missing serials must be recorded as explicit zero-serial design markers rather than failed extractions.
- **Straight-Through Processing Thresholds and Risk Controls**: He mandated that only high-confidence classifications exceeding the 85% STP threshold may directly update Maximo asset records, while borderline cases must be routed to human review queues to safeguard database integrity.
- **Cryptographic Audit Envelopes**: He emphasized that all production updates must produce immutable cryptographic envelopes binding raw image digests, model versioning, and operator approvals.

# Miles - PartnerAdvisory, Senior Engineer
- **Edge Deployment and Compute Latency Optimization**: Miles evaluated the inference latency of running Laplacian filters on field edge devices versus central cloud workers, confirming that local filtering reduces payload transfer volumes significantly across cellular uplinks.
- **Persistence Envelope Design and Database Schema**: He detailed the cryptographic audit envelope required for Maximo record updates, ensuring that every asset transition remains fully traceable to raw image locators and timestamp markers.
- **Human-in-the-Loop Queue Optimization**: He proposed a 1-click review interface for confidence scores between 65% and 84%, minimizing technician click fatigue during daily operational audits.
"""
    meta = {
        "meeting_type": "INTERNAL_TECHNICAL_SYNC",
        "duration": 56,
        "has_architecture_diagram": True,
    }
    res = run_qa_gate_2(body, meta=meta)
    assert res["status"] == "PASS", f"Gate 2 failed: {res.get('failures')}"
    assert res["passed"] is True
