"""
tests/test_gate_2_8_adversarial.py

Adversarial edge-case test suite for QA Gate 2.8 and Appendix Healer:
- Boilerplate variations (unbolded, colon placement, lowercase, bullet variations)
- Preamble before first speaker header
- Header level variations (# through ####)
- Non-speaker sections (Table of Contents, Notes, Glossary, Contribution Breakdown)
- Thin section detection across multi-speaker appendixes
- Duration boundary conditions (<15m vs >=15m)
"""

from __future__ import annotations

import unittest

from dual_pipeline.transcript_qa_core.gate2 import (
    _check_appendix_completeness,
    run_qa_gate_2,
)


class TestGate28Adversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.base_recap_prefix = """# Subject: Architecture Review -- Project Nexus | 09/14/2026
**Date:** 09/14/2026
**Time:** 02:00 PM - 02:30 PM ET (Duration: ~30m)
**Attendees:**
- PartnerAdvisory: Alice Smith (Solutions Architect), Bob Jones (Principal)

## Meeting Objective
Review microservice boundary architecture and database decoupling.

## Key Discussion Points & Findings
- Discussed microservice decoupling and migration roadmap.

## Strategic Synthesis
| # | What We Heard | What It Means |
| :--- | :--- | :--- |
| 1 | Database contention in monolith | Must isolate writes to partitioned storage |

## Next Steps & Action Items
| # | Action Item | Accountable Owner | Responsible Party | Target Timing | Spoken Locator | Status |
|---|---|---|---|---|---|---|
| 1 | Draft partition schema | PartnerAdvisory | Alice Smith | 09/20/2026 | [14:20] | Confirmed |

## Open Decisions / Blockers
- Database choice pending load test.

## Business Meaning and Value Case
- **Business problem:** Monolithic database contention causes latency spikes during peak load.
- **Value at stake:** Prevents downtime and protects SLA commitments across customer transactions.
- **Baseline:** 500ms p99 query latency during batch processing.
- **Target:** < 100ms p99 query latency through partitioned domain databases.
- **Value mechanism:** Horizontal sharding and event-driven decoupling.
- **Confidence:** High (90%) backed by architecture benchmarks.
- **Consequence of delay:** Production slowdowns and client SLA penalties.
- **Evidence:** Query latency logs [12:00].

## Executive Decision
- **Executive conclusion:** Decouple order processing database from reporting tables.
- **Decision required:** Approve domain database partitioning plan.
- **Recommended choice:** Implement dedicated Aurora PostgreSQL cluster.
- **Alternatives rejected or deferred:** Scaling vertically on single instance.
- **Reason:** Cost inefficiency and single-point-of-failure risks.
- **Decision owner:** Alice Smith.
- **Decision deadline:** 09/20/2026.
- **Consequence of no decision:** Critical failure during Q4 high-traffic period.
- **Decision status:** Approved.
- **Evidence:** Architecture review notes [24:10].

## Bounded Execution Wedge
- **Target user:** Core transactional processing engine.
- **Business capability:** Partitioned domain database isolation.
- **In scope:** Order schema extraction, CDC replication pipeline, read-model indexing.
- **Out of scope:** Analytics warehouse redesign.
- **Systems touched:** Transaction DB, CDC connector, read replica cluster.
- **Systems explicitly not replaced:** Main billing settlement engine.
- **Required dependencies:** AWS DMS replication agent.
- **Known constraints:** Maintenance window limited to 2 hours.
- **First deliverable:** Proof-of-concept shard cluster.
- **Acceptance test:** Zero transaction drop during failover simulation.
- **Exit criterion:** 100% data fidelity across CDC pipeline.
- **Owner:** Bob Jones.
- **Decision gate:** Architecture review signoff.
- **Success metric:** Latency reduction below 100ms.
- **Evidence:** System design doc v2.1.
- **MVP status:** Ready for pilot deployment.
- **Next required decision:** Schedule migration window.
"""

    def test_boilerplate_detected_across_variations(self) -> None:
        """Adversarial boilerplate variations must all be intercepted."""
        variations = [
            "- **Core Thesis & Strategic Perspective**: Bob argued for decoupling.",
            "- **Core Thesis & Strategic Perspective:** Bob argued for decoupling.",
            "- Core Thesis & Strategic Perspective: Bob argued for decoupling.",
            "- core thesis & strategic perspective: bob argued for decoupling.",
            "- **Key Contributions & Discussion Focus:** Alice detailed CDC replication.",
            "- **Commitments & Next Steps:** Alice will finalize migration plan.",
        ]
        for var in variations:
            recap = f"""{self.base_recap_prefix}
# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Bob Jones
{var}
He explained that horizontal sharding is essential to prevent peak load deadlocks.
We need to isolate writes from analytics queries to keep transaction latency under 100ms.
This approach reduces operational risk and ensures high availability for critical services.
"""
            ok, can_heal, fails = _check_appendix_completeness(recap, 30)
            self.assertFalse(ok, f"Failed to detect boilerplate variation: {var}")
            self.assertTrue(can_heal)
            self.assertTrue(any("GATE_2_8_BOILERPLATE_DETECTED" in f for f in fails))

    def test_appendix_with_preamble_and_headers_passes(self) -> None:
        """Preamble before first speaker section must not be treated as a thin speaker section."""
        recap = f"""{self.base_recap_prefix}
# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

The following perspectives reflect in-depth architectural dialogue recorded during the 30-minute review session.
Each contributor's strategic focus, concerns, and proposed trade-offs are summarized below.

### Alice Smith
- **Database Partitioning and Sharding Architecture**: Alice presented the detailed data partition plan,
explaining that separating high-frequency write operations from analytical reporting is necessary to eliminate lock contention.
- **CDC and Read Model Synchronization**: She demonstrated how event streaming with Change Data Capture will keep downstream read models synchronized with minimal replication lag.
Her recommendations emphasized zero-downtime cutover procedures and automated failover verification before production deployment.

### Bob Jones
- **Operational Risk and Capacity Planning**: Bob focused on production reliability and operational risk during database maintenance.
He highlighted the importance of running synthetic load tests prior to migration and establishing strict latency thresholds.
- **Client SLA Requirements**: Bob emphasized that client SLA requirements mandate sub-100ms response times, making the Aurora cluster the only viable long-term solution.
He confirmed that the architecture team will oversee the migration dry run scheduled for next week.
"""
        ok, _, fails = _check_appendix_completeness(recap, 30)
        self.assertTrue(ok, f"Gate 2.8 failed with preamble: {fails}")

        res = run_qa_gate_2(recap, {"duration_minutes": 30})
        self.assertTrue(res["gates"]["Gate_2_8_Appendix_Completeness"]["passed"])


    def test_non_speaker_sections_are_ignored(self) -> None:
        """Non-speaker headers like Notes, Glossary, Table of Contents must not be audited as speakers."""
        recap = f"""{self.base_recap_prefix}
# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Table of Contents
1. Alice Smith
2. Bob Jones

### Notes:
General meeting observations and architecture context notes.

### Alice Smith
- **Database Partitioning and Sharding Architecture**: Alice presented the detailed data partition plan,
explaining that separating high-frequency write operations from analytical reporting is necessary to eliminate lock contention.
- **CDC and Read Model Synchronization**: She demonstrated how event streaming with Change Data Capture will keep downstream read models synchronized with minimal replication lag.
Her recommendations emphasized zero-downtime cutover procedures and automated failover verification before production deployment.

### Bob Jones
- **Operational Risk and Capacity Planning**: Bob focused on production reliability and operational risk during database maintenance.
He highlighted the importance of running synthetic load tests prior to migration and establishing strict latency thresholds.
- **Client SLA Requirements**: Bob emphasized that client SLA requirements mandate sub-100ms response times, making the Aurora cluster the only viable long-term solution.
He confirmed that the architecture team will oversee the migration dry run scheduled for next week.
"""
        ok, _, fails = _check_appendix_completeness(recap, 30)
        self.assertTrue(ok, f"Gate 2.8 failed with non-speaker headers: {fails}")

    def test_header_levels_from_h1_to_h4(self) -> None:
        """Gate 2.8 must handle speaker headers using #, ##, ###, and ####."""
        for h_prefix in ("#", "##", "###", "####"):
            recap = f"""{self.base_recap_prefix}
# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

{h_prefix} Alice Smith
- **Database Partitioning and Sharding Architecture**: Alice presented the detailed data partition plan,
explaining that separating high-frequency write operations from analytical reporting is necessary to eliminate lock contention.
- **CDC and Read Model Synchronization**: She demonstrated how event streaming with Change Data Capture will keep downstream read models synchronized with minimal replication lag.
Her recommendations emphasized zero-downtime cutover procedures and automated failover verification before production deployment.

{h_prefix} Bob Jones
- **Operational Risk and Capacity Planning**: Bob focused on production reliability and operational risk during database maintenance.
He highlighted the importance of running synthetic load tests prior to migration and establishing strict latency thresholds.
- **Client SLA Requirements**: Bob emphasized that client SLA requirements mandate sub-100ms response times, making the Aurora cluster the only viable long-term solution.
He confirmed that the architecture team will oversee the migration dry run scheduled for next week.
"""
            ok, _, fails = _check_appendix_completeness(recap, 30)
            self.assertTrue(ok, f"Failed for header prefix '{h_prefix}': {fails}")

    def test_thin_speaker_section_detected_among_deep_sections(self) -> None:
        """When one speaker section is thin among deep sections, Gate 2.8 must fail with specific speaker name."""
        recap = f"""{self.base_recap_prefix}
# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Alice Smith
- **Database Partitioning and Sharding Architecture**: Alice presented the detailed data partition plan,
explaining that separating high-frequency write operations from analytical reporting is necessary to eliminate lock contention.
- **CDC and Read Model Synchronization**: She demonstrated how event streaming with Change Data Capture will keep downstream read models synchronized with minimal replication lag.
Her recommendations emphasized zero-downtime cutover procedures and automated failover verification before production deployment.

### Bob Jones
Agreed with Alice.
"""
        ok, can_heal, fails = _check_appendix_completeness(recap, 30)
        self.assertFalse(ok)
        self.assertTrue(can_heal)
        self.assertTrue(any("Bob Jones" in f and "GATE_2_8_THIN_SPEAKER_SECTION" in f for f in fails))

    def test_duration_boundary_under_15_minutes_fails_without_appendix(self) -> None:
        """Under universal standards, a 10-minute meeting without an Appendix fails Gate 2.8."""
        recap_no_app = self.base_recap_prefix.replace("Duration: ~30m", "Duration: ~10m")
        ok, _, fails = _check_appendix_completeness(recap_no_app, 10)
        self.assertFalse(ok, "Short meeting without Appendix must fail Gate 2.8.")
        self.assertTrue(any("missing mandatory" in f for f in fails))

    def test_duration_boundary_under_15_minutes_passes_with_scaled_appendix(self) -> None:
        """A 10-minute meeting with compliant scaled Appendix passes Gate 2.8."""
        scaled_recap = f"""{self.base_recap_prefix.replace("Duration: ~30m", "Duration: ~10m")}
# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Alice Smith
- **Database Partitioning Architecture**: Alice presented the data partition plan to isolate write operations and eliminate lock contention across services.

### Bob Jones
- **Operational Reliability**: Bob focused on production reliability and operational risk during database maintenance, confirming latency thresholds.
"""
        ok, _, fails = _check_appendix_completeness(scaled_recap, 10)
        self.assertTrue(ok, f"Short meeting with scaled Appendix should pass: {fails}")


if __name__ == "__main__":
    unittest.main()
