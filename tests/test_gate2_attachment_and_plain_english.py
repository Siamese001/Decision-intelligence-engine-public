"""
tests/test_gate2_attachment_and_plain_english.py

Comprehensive test suite verifying Gate 2 plain-English compliance,
word-salad detection, adaptive duration density floors, and architectural collateral grounding.
"""
from __future__ import annotations

import unittest

from dual_pipeline.llm_judge_core.recap import ExecutiveRecapJudge

from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2


class TestGate2AttachmentAndPlainEnglish(unittest.TestCase):
    def setUp(self):
        self.valid_recap = """# Subject: Architecture Review -- Executive Recap | 09/18/2026

**Date:** 09/18/2026
**Time:** 10:00 AM - 11:00 AM ET
**Attendees:**
- Delivery Partner: Alex Sanchez (Solution Architect), Amit Ayer (Lead Architect)
- Client Organization: harper Hoffman (Product Lead)

---

## Meeting Objective
Align on the target architectural landscape, review data integration boundaries, and ratify the core delivery timeline.

## Key Discussion Points & Findings
The team conducted a comprehensive architectural review of the enterprise platform modernization, focusing on event-driven interfaces, contract governance, and decoupled storage adapters. Engineering leads demonstrated how decoupling synchronous service calls into asynchronous message topics prevents catastrophic cascading timeouts during peak operational periods. The transition plan isolates operational databases from analytic consumers through structured write-ahead logging and governed read replicas.

Alex Sanchez presented the end-to-end integration specifications, detailing schema definitions, validation protocols, and dead-letter queue routing strategies. All payloads must adhere to canonical schema contracts, enforcing strict type validations and RFC-8259 canonical hashing before graph admission. harper Hoffman affirmed that business workflows require continuous operational visibility, requesting that message schemas include immutable traceability envelopes with correlation identifiers and event timestamps.

The database migration timeline was reviewed in depth with all participating technical leads. The team evaluated dual-write synchronization strategies versus log-based change data capture. It was mutually agreed that change data capture provides superior isolation, minimal operational latency, and zero application performance degradation. The implementation schedule establishes phased execution gates with formal regression checkpoints prior to legacy system cutover.

Furthermore, the review addressed error recovery protocols and retry budgets. Automated retry logic will incorporate exponential backoff with randomized jitter to prevent thundering herd conditions on upstream dependencies. System alerts and operational metrics will stream directly into the unified observability dashboard, providing engineering teams with instant anomaly detection and automated circuit breaking capabilities.

Delivery teams aligned on developer enablement milestones, including the rollout of hermetic mock environments, interactive API documentation, and automated test runners. These tools will enable feature teams to develop and test components locally with complete fidelity, eliminating cross-team development bottlenecks and shortening the release feedback loop significantly. Operational runbooks will be established for every stage of service activation.

In addition, the engineering leadership reviewed storage indexing strategies and partition boundaries across primary transactional tables. Partitioning historical audit records by monthly epochs guarantees consistent query response times and prevents unbounded table bloat over multi-year production horizons. Backup automation, disaster recovery replication, and encryption at rest were certified as adhering strictly to enterprise security mandates.

Comprehensive monitoring hooks will capture latency distributions, transaction throughput, and resource utilization across services. The telemetry architecture relies on standardized distributed tracing headers injected at gateway entry points, propagating contextual span data through each intermediate processing node. This granular observability enables rapid root-cause isolation whenever latency thresholds are breached.

## Strategic Synthesis
| What We Heard | What It Means |
|---|---|
| Need reliable integration | Decouple interfaces with message queues |
| Must prevent regression during cutover | Deploy log-based change data capture with automated reconciliation |

## Next Steps & Action Items
| # | Action Item | Accountable Owner | Target Timing | Status | Transcript Locator |
|---|---|---|---|---|---|
| 1 | Finalize the API interface contract | Alex Sanchez | 09/25/2026 | Pending | [12:30] |
| 2 | Deliver initial CDC pipeline prototype | Amit Ayer | 10/02/2026 | In Progress | [24:15] |

## Open Decisions / Blockers
None at this time. All foundational engineering pre-requisites are satisfied for Phase 1 kickoff.

## Business Meaning and Value Case
Enterprise value case established around operational efficiency, reliability, and developer capacity expansion across core operations.
The current enterprise tooling environment spans disparate legacy systems, batch jobs, and siloed data stores,
resulting in significant manual reconciliation overhead, recurring defect leakage, and constant context switching for delivery teams.
By establishing a unified, governed execution spine with automated validation gates and real-time observability,
the organization mitigates operational delivery risk, accelerates feature time-to-market, and unlocks scalable capacity.

## Executive Decision
Strategic direction affirmed for initial execution phase across the enterprise platform modernization and service decoupling roadmap.
Leadership evaluated alternative incremental options against the unified architecture and concluded that
deferring architectural decoupling introduces unacceptable technical debt and elevated risk of downstream project failure.
The ratified decision mandates immediate commencement of the delivery model with dedicated engineering ownership,
clear delivery milestones, and formal architectural signoff required before production release promotion.

## Immediate Delivery Scope & Release Boundaries
Immediate delivery scope confirms core pipeline enablement, event routing, and automated intake validation for this milestone.
Target users include enterprise operations engineers, data platform reviewers, and solution architects evaluating ingestion reliability.
In-scope boundaries include automated ingestion adapters, deterministic routing rules, schema validation components, and test coverage reporting.
Out-of-scope boundaries exclude legacy batch reindexing and third-party partner system rewrites.
Clear acceptance criteria mandate sub-second parsing latency, zero data loss, and strict schema compliance.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Alex Sanchez - Delivery Partner, Solution Architect
- **Architecture and Integration Contracts**: Alex Sanchez established key architectural principles and priorities during the session, emphasizing the necessity of robust contract validation, event-driven decoupling, and reproducible deployment manifests across delivery workstreams.
- **Verification Gates and Technical Safeguards**: Alex Sanchez outlined detailed technical safeguards and structured verification gates to prevent downstream data regressions, reviewing system interfaces and confirming delivery milestones. He also coordinated cross-team dependencies to ensure consistent schema adoption and validated the rollout sequencing for each integration endpoint across delivery workstreams.

### harper Hoffman - Client Organization, Product Lead
- **Operational Usability and Governance Standards**: harper Hoffman articulated operational requirements and usability benchmarks, emphasizing that automated pipelines must provide transparent, actionable feedback for business users and system operators throughout the delivery lifecycle.
- **Data Quality Preview and Acceptance Criteria**: harper Hoffman advocated for interactive preview workflows to validate data quality prior to commit actions, ensuring stakeholders retain complete visibility and confirming business acceptance criteria. She requested that all pipeline outputs include human-readable audit trails and escalation paths for data quality exceptions.
"""

    def test_compliant_plain_english_recap_passes_gate_2(self):
        res = run_qa_gate_2(self.valid_recap, metadata={"duration_minutes": 60})
        self.assertEqual(res["status"], "PASS", f"Gate 2 failed: {res.get('failures')}")
        self.assertTrue(res["gates"]["Gate_2_10_Plain_English_Compliance"]["passed"])

    def test_gate_2_rejects_academic_word_salad(self):
        salad_recap = self.valid_recap.replace(
            "Align on the target architectural landscape",
            "The team discussed evidenced rejection behavior and aspirational operating concept"
        )
        res = run_qa_gate_2(salad_recap, metadata={"duration_minutes": 60})
        self.assertEqual(res["status"], "FAIL")
        failures_joined = " ".join(res.get("failures", []))
        self.assertIn("evidenced rejection behavior", failures_joined)
        self.assertIn("aspirational operating concept", failures_joined)

    def test_gate_2_enforces_collateral_architecture_fidelity(self):
        meta_with_diag = {
            "duration_minutes": 60,
            "attachments": [{"filename": "system_architecture_diagram.png", "attachment_type": "diagram"}]
        }
        # Recap without architecture exhibit should fail Gate 2.9
        res = run_qa_gate_2(self.valid_recap, metadata=meta_with_diag)
        self.assertEqual(res["status"], "FAIL")
        self.assertFalse(res["gates"]["Gate_2_9_Collateral_Architecture_Fidelity"]["passed"])

        # Recap with embedded architecture exhibit should pass Gate 2.9
        recap_with_exhibit = self.valid_recap.replace(
            "## Key Discussion Points & Findings",
            "## Key Discussion Points & Findings\n\n```text\n+------------+     +------------+\n|  Client    | --> |  Gateway   |\n+------------+     +------------+\n```\n"
        )
        res_pass = run_qa_gate_2(recap_with_exhibit, metadata=meta_with_diag)
        self.assertEqual(res_pass["status"], "PASS", f"Failed with exhibit: {res_pass.get('failures')}")
        self.assertTrue(res_pass["gates"]["Gate_2_9_Collateral_Architecture_Fidelity"]["passed"])

    def test_gate_2_duration_adaptive_density_floors(self):
        # Brief 15m meeting with 35 words in sections passes under adaptive floor
        recap_15m = """# Subject: Daily Standup Catchup -- Executive Recap | 09/18/2026

**Date:** 09/18/2026
**Time:** 10:00 AM - 10:15 AM ET
**Attendees:**
- Alex Sanchez (PartnerAdvisory)
- harper Hoffman (Client)

---

## Meeting Objective
Brief 15-minute synchronization on deployment priorities, staging validation, and blocking operational issues across delivery workstreams.

## Key Discussion Points & Findings
The team conducted a focused daily review of sprint tickets and confirmed pipeline stability across shared test environments.
All core build targets passed regression verification cleanly without any unexpected defects or performance slowdowns.
Deployment schedules remain strictly on track for end of week release candidate promotion and stakeholder review signoff.
Operational readiness manifests were validated against infrastructure requirements, confirming zero deployment blockers.

## Strategic Synthesis
| What We Heard | What It Means |
|---|---|
| Need rapid automated verification | Automate end-to-end smoke test runners |

## Next Steps & Action Items
| # | Action Item | Accountable Owner | Target Timing | Status | Transcript Locator |
|---|---|---|---|---|---|
| 1 | Trigger smoke test suite | Alex Sanchez | 09/19/2026 | Pending | [05:00] |

## Open Decisions / Blockers
None at this time. All pre-flight checklist conditions remain satisfied.

## Business Meaning and Value Case
Rapid daily synchronization minimizes miscommunication across cross-functional delivery cohorts,
preventing redundant engineering rework, eliminating duplicate investigations, and keeping the critical delivery path clear
for timely, predictable production deployment and continuous release confidence.

## Executive Decision
Affirmed proceeding with current sprint release scope and timeline without scope reductions or delays.
Deferring deployment would push client acceptance testing into the subsequent quarter, so execution
continues on the agreed delivery trajectory with mandatory verification signoffs.

## Immediate Delivery Scope & Release Boundaries
In-scope boundary includes automated pipeline smoke test execution, integration test suite verification, and test database validation.
Out of scope includes production cluster migration until smoke tests pass.
Acceptance criteria mandates zero blocking failures on automated smoke test execution.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Alex Sanchez (PartnerAdvisory)
- **Technical Analysis & Delivery Readiness**: Alex Sanchez confirmed that build runners and automated pipelines are stable and ready for candidate packaging, highlighting the necessity of running smoke tests prior to promotion. He outlined the testing scope, coordinated dependency checks across integration layers, and confirmed that all pre-flight validation conditions were met before merge authorization.
- **Verification of Staging Containers**: He confirmed that additional verification of staging containers will occur before merge authorization to maintain operational integrity and prevent regressions.

### harper Hoffman (Client)
- **Stakeholder Alignment & Acceptance Verification**: harper Hoffman affirmed that business expectations are met by current deliverables, agreeing to review test outputs upon smoke test completion. She confirmed stakeholder readiness across client-side business units and coordinated communication timelines for the downstream release.
- **Rollout Schedule Coordination**: Clear alignment was reached regarding rollout communication schedules across downstream stakeholder groups and release milestone acceptance criteria for this delivery increment. Mandy confirmed all sign-off checkpoints with business leadership prior to the production release window.
"""
        res_15m = run_qa_gate_2(recap_15m, metadata={"duration_minutes": 15})
        self.assertEqual(res_15m["status"], "PASS", f"15m failed: {res_15m.get('failures')}")

    def test_judge_applies_word_salad_penalty_in_mock(self):
        salad_recap = self.valid_recap.replace(
            "Align on the target architectural landscape",
            "The team discussed bounded execution wedge and evidenced rejection behavior"
        )
        judge = ExecutiveRecapJudge()
        eval_res = judge.evaluate(
            f2_text=salad_recap,
            f1_text=self.valid_recap,
            ref_artifact_text="",
            mock=True,
        )
        self.assertLess(eval_res["composite_score"], 90)
        self.assertIn("DEFECT", eval_res.get("forensic_rationale", ""))


if __name__ == "__main__":
    unittest.main()
