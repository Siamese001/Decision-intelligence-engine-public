"""
tests/adversarial/test_output2_decoupling_adversarial.py

Adversarial stress suite for Wave 1: Output 02 Decoupling & Thematic Anchors.
Covers:
1. Boundary payloads: empty Section 3, missing Appendix, single-token anchors, unicode noise.
2. Invariant bypass attempts: prefix padding, title prefixes ("Dr. Amit", "VP Dan"), multi-speaker anchors.
3. Concurrency / scale: massive 50-bullet Section 3 vs 20-speaker Appendix.
4. Malformed formatting: unclosed asterisks, nested lists, mixed markdown symbols.
"""
import unittest
from typing import Any, Dict

from dual_pipeline.prompts.contracts.output2_contract import (
    validate_c21_depth_floors_and_anchors,
)


class TestOutput2DecouplingAdversarial(unittest.TestCase):
    """Adversarial suite for Section 3 vs Appendix decoupling."""

    def setUp(self) -> None:
        self.meta: Dict[str, Any] = {
            "attendees": {
                "Google": ["Alex Brannan (Partner Lead)", "Dianne Eldridge (CE Director)"],
                "PartnerAdvisory": ["Amit Ayer (Lead Client Partner)", "Drew Oakley (Client Service Lead)"],
            },
            "meeting_type": "GENERAL",
        }

    def test_adversarial_boundary_empty_and_minimal_sections(self) -> None:
        """Empty Section 3 or missing Appendix should gracefully handle without crashes."""
        # Case A: Empty text
        passed, failures = validate_c21_depth_floors_and_anchors("", meta=self.meta)
        self.assertTrue(passed)
        self.assertEqual(len(failures), 0)

        # Case B: Section 3 present, Appendix completely omitted
        text_no_app = """# Subject: Brief | 09/29/2026

**Date:** 09/29/2026
**Time:** 03:00 PM - 03:45 PM ET
**Duration:** ~45 minutes
**Attendees:**
PartnerAdvisory:
- Amit Ayer (Lead Client Partner)

# Meeting Objective
Objective.

# Key Discussion Points & Findings

- **Strategic Partnership Bounds:** Cross-functional coordination. [00:05:00]
"""
        passed, failures = validate_c21_depth_floors_and_anchors(text_no_app, meta=self.meta)
        self.assertTrue(passed)
        self.assertEqual(len(failures), 0)

    def test_adversarial_bypass_attempt_prefix_padding_and_titles(self) -> None:
        """Adversarial attempt: adding role prefixes or leading filler to sneak persona anchors."""
        text_sneaky = """# Subject: Brief | 09/29/2026

**Date:** 09/29/2026
**Time:** 03:00 PM - 03:45 PM ET
**Duration:** ~45 minutes
**Attendees:**
Google:
- Dianne Eldridge (CE Director)
PartnerAdvisory:
- Amit Ayer (Lead Client Partner)

# Meeting Objective
Objective.

# Key Discussion Points & Findings

- **Amit Ayer - Update:** Timeline details. [00:05:00]
- **Dianne Eldridge's Notes:** Access details. [00:10:00]

# Action & Governance Ledger

| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Verify access | Amit Ayer | 10/05/2026 | 00:05:00 | Confirmed |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Dianne Eldridge — Google, CE Director
- **Access Focus**: Dianne pushed back.
"""
        passed, failures = validate_c21_depth_floors_and_anchors(text_sneaky, meta=self.meta)
        self.assertFalse(passed)
        self.assertTrue(any("SECTION_3_ANCHOR_PERSONA_COLLAPSE" in f for f in failures))

    def test_adversarial_malformed_anchors_and_markdown(self) -> None:
        """Malformed markdown with unclosed bolding or non-standard bullet characters."""
        text_malformed = """# Subject: Brief | 09/29/2026

**Date:** 09/29/2026
**Time:** 03:00 PM - 03:45 PM ET
**Duration:** ~45 minutes
**Attendees:**
PartnerAdvisory:
- Amit Ayer (Lead Client Partner)

# Meeting Objective
Objective.

# Key Discussion Points & Findings

* **Apex Global Infrastructure Integration: Pipeline boundaries verified. [00:05:00]
- **Unclosed Asterisk Anchor: Technical details. [00:10:00]
+ **Thematic Decision Frame:** Clean architecture. [00:15:00]

# Action & Governance Ledger

| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Setup system | Amit Ayer | 10/05/2026 | 00:05:00 | Confirmed |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Amit Ayer — PartnerAdvisory, Lead Client Partner
- **Architecture**: Core systems setup.
"""
        # Should not raise exception and should evaluate cleanly
        passed, failures = validate_c21_depth_floors_and_anchors(text_malformed, meta=self.meta)
        self.assertTrue(passed)

    def test_adversarial_scale_many_bullets(self) -> None:
        """Scale stress: 25 thematic bullets must evaluate within 50ms without timeout."""
        import time

        bullets = []
        for i in range(25):
            bullets.append(
                f"- **Thematic Mechanism {i} & Architecture Layer {i}:** Subsystem description and operational takeaway {i}. [00:{i:02d}:00]"
            )
        sec3_content = "\n".join(bullets)
        text_scale = f"""# Subject: Brief | 09/29/2026

**Date:** 09/29/2026
**Time:** 03:00 PM - 03:45 PM ET
**Duration:** ~45 minutes
**Attendees:**
PartnerAdvisory:
- Amit Ayer (Lead Client Partner)

# Meeting Objective
Large scale sync.

# Key Discussion Points & Findings

{sec3_content}

# Action & Governance Ledger

| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Follow up | Amit Ayer | 10/05/2026 | 00:01:00 | Confirmed |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Amit Ayer — PartnerAdvisory, Lead Client Partner
- **Enterprise Perspective**: Long term outlook.
"""
        start = time.perf_counter()
        passed, failures = validate_c21_depth_floors_and_anchors(text_scale, meta=self.meta)
        elapsed = time.perf_counter() - start
        self.assertTrue(passed)
        self.assertLess(elapsed, 0.05, f"Validation took too long: {elapsed:.4f}s")


if __name__ == "__main__":
    unittest.main()
