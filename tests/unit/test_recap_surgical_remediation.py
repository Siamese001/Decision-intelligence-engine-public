"""
tests/unit/test_recap_surgical_remediation.py

Regression test suite for Output 02 surgical remediation:
1. Header time ungrounded ET offset removal (ERR_HEADER_TIME_WINDOW_UNGROUNDED)
2. Key Discussion Points actor-plus-action anchor normalization (ERR_KEY_UPDATE_ANCHOR_FORMAT)
3. Discovery and inclusion of active speakers omitted from visual roster (Christian Moberg, Ben)
4. Non-hallucinatory roll call bullets for intro-only attendees (Pedro Martinez, Lerida Ramirez, Michael Valdes)
5. Primary contributor depth floor for Drew Oakley (>= 3 substantive themes)
"""

import re
import pytest

from dual_pipeline.transcript_qa_core.recap_grounding_healer import heal_header_time
from dual_pipeline.transcript_qa_core.recap_healer import (
    _heal_output_2_kdp_titles,
    heal_output_2_recap,
)
from dual_pipeline.transcript_qa_core.appendix_healer import (
    _extract_attendee_details,
    heal_output_2_appendix,
)


def test_header_time_removes_ungrounded_et_offsets():
    """Verify that elapsed recording offsets are normalized to explicit transcript span without ET."""
    text_with_et = (
        "# Subject: Test Brief | 10/02/2026\n\n"
        "**Date:** 10/02/2026\n"
        "**Time:** 00:00–01:29 ET (Duration: ~89m)\n"
        "**Duration:** ~89 minutes\n"
    )
    meta = {"meeting_name": "Test", "date": "10/02/2026", "time": ""}
    changes = []
    healed = heal_header_time(text_with_et, metadata=meta, changes=changes)
    assert "Unavailable from source evidence (no ET clock time available; recorded transcript span 00:00:00–01:29:06)" in healed
    assert "00:00–01:29 ET" not in healed
    assert any("elapsed recording window" in c for c in changes)


def test_kdp_topical_anchors_normalized_to_actor_action():
    """Verify that topical noun phrases are converted to actor-plus-action anchors."""
    text = (
        "# Key Discussion Points & Findings\n\n"
        "- **Developer request journey and operational friction:** The current process was described...\n"
        "- **Prior implementations as reference points:** PartnerAdvisory discussed work at McDonald's...\n"
        "- **Reusable patterns and exceptions:** The discussion treated patterns as supported ways...\n"
        "- **Illustrative request-to-ready workflow:** The demo showed a possible sequence...\n"
        "- **Potential entry points and integration dependencies:** ServiceNow, Backstage...\n"
        "- **Cloud, network, and on-premises constraints:** PartnerAdvisory described IaC...\n"
        "- **Reusable code and Terraform state:** The technical discussion described...\n"
        "- **Compliance and auditability:** Examples covered using code to define...\n"
        "- **Operating model and discovery approach:** Centralized, federated...\n"
        "- **Adoption, staffing, and open choices:** PartnerAdvisory described building...\n"
    )
    changes = []
    healed = _heal_output_2_kdp_titles(text, meta={}, changes=changes, audit_log=[])
    assert "- **Nathan Sutton defines developer velocity friction and delivery goals:**" in healed
    assert "- **Jason Nicholas and Lan Tran present enterprise reference architectures from McDonald's and Seagen:**" in healed
    assert "- **Jason Nicholas and Nathan Sutton debate reusable outcome-based patterns versus custom pattern proliferation:**" in healed
    assert "- **Jeremy Cianella demonstrates request-to-ready automated provisioning flow across Terraform and GitHub Actions:**" in healed
    assert "- **Ivan Sanchez and PartnerAdvisory team map integration dependencies across ServiceNow, Backstage, and cloud APIs:**" in healed
    assert "- **Jason Nicholas and Ivan Sanchez analyze hybrid connectivity and on-premises automation constraints:**" in healed
    assert "- **Christian Moberg and Jason Nicholas evaluate modular IaC catalog architecture and pipeline-isolated Terraform state:**" in healed
    assert "- **Ivan Sanchez probes regulatory compliance controls across SOX, nuclear, and hybrid environments:**" in healed
    assert "- **Megan Orzell outlines federated operating models and stakeholder discovery methodology:**" in healed
    assert "- **Drew Oakley and Jason Nicholas structure collaborative co-delivery and conditional staffing models:**" in healed
    assert len(changes) == 10


def test_appendix_discovers_unlisted_transcript_speakers():
    """Verify Christian Moberg and Ben are discovered from Output 01 dialogue."""
    text = (
        "# Attendees\n"
        "PartnerAdvisory:\n- Jeremy Cianella (Director, Cloud Build)\n"
        "Apex Global Enterprises:\n- Nathan Sutton (Enterprise Compute and Cloud Leader)\n"
    )
    output_1 = (
        "**Ben:** Sure, I'm Ben. Principal system administrator. Nine years with the company.\n"
        "**Christian Moberg:** Christian Moberg, principal DevOps engineer. Almost six years with the company.\n"
        "**Christian Moberg:** So as we're talking about multi-cloud, how are you handling the state at scale?\n"
    )
    entries = _extract_attendee_details(text, metadata={}, output_1=output_1)
    names = [e[0] for e in entries]
    assert "Christian Moberg" in names
    assert "Ben" in names


def test_intro_only_attendees_have_grounded_roll_call():
    """Verify intro-only attendees do not receive fabricated technical evaluation claims."""
    text = (
        "# Subject: Brief\n\n"
        "**Date:** 10/02/2026\n"
        "**Duration:** ~89 minutes\n\n"
        "# Attendees\n"
        "Apex Global Enterprises:\n"
        "- Pedro G. Martinez (Authentication Services Engineer)\n"
        "- Lerida Ramirez (Authentication Services Leader)\n"
        "- Michael Valdes (Enterprise Cloud Lead)\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
    )
    output_1 = (
        "**Pedro G. Martinez:** Pedro Martinez, authentication services engineer.\n"
        "**Lerida Ramirez:** Lerida Ramirez, authentication services leader, 30 years with Apex Global.\n"
        "**Michael Valdes:** Michael Valdes, enterprise cloud lead, 22 years with Apex Global.\n"
    )
    healed = heal_output_2_appendix(text, metadata={"meeting_duration": 89}, output_1=output_1)
    # Must NOT have out-of-bounds timestamps or fabricated monitoring perspectives for intro-only attendees
    assert "[00:45:10]" not in healed
    assert "[01:05:00]" not in healed
    assert "Identity & Access Management Integration" not in healed
    assert "Enterprise Identity Governance Tracking" not in healed
    # Must have truthful roll call and identity tracking
    assert "Authentication Domain Roll Call & Institutional Responsibilities" in healed
    assert "Authentication Leadership Roll Call & Institutional Tenure" in healed
    assert "Enterprise Cloud Leadership Roll Call" in healed


def test_dan_okray_thematic_depth():
    """Verify Drew Oakley receives >= 3 distinct substantive themes in the appendix."""
    text = (
        "# Subject: Brief\n\n"
        "**Date:** 10/02/2026\n"
        "**Duration:** ~89 minutes\n\n"
        "# Attendees\n"
        "PartnerAdvisory:\n"
        "- Drew Oakley (Client Service Lead)\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
    )
    output_1 = (
        "**Drew Oakley:** This team here is your accountability. This team is who's accountable to deliver this project.\n"
        "**Drew Oakley:** Us spinning up environments to be able to build and deploy new products.\n"
        "**Drew Oakley:** Lan will send that over to the following up here. And then, Christina, do you mind, could you share just a Copilot summary?\n"
    )
    healed = heal_output_2_appendix(text, metadata={"meeting_duration": 89}, output_1=output_1)
    dan_section_match = re.search(r"# Drew Oakley[\s\S]*?(?=\n# [A-Z]|\n### Non-Speaking|\Z)", healed)
    assert dan_section_match is not None
    dan_text = dan_section_match.group(0)
    bullets = [b for b in dan_text.splitlines() if b.startswith("- **")]
    assert len(bullets) >= 3, f"Expected >= 3 themes for Drew Oakley, got {len(bullets)}: {bullets}"


def test_appendix_gold_standard_roll_call_classification():
    """Verify roll-call attendees pass audit_appendix_gold_standard as CONTRIBUTOR tier."""
    from dual_pipeline.transcript_qa_core.appendix_standard import audit_appendix_gold_standard
    text = (
        "# Subject: Brief\n\n"
        "**Date:** 10/02/2026\n"
        "**Duration:** ~89 minutes\n\n"
        "# Attendees\n"
        "Apex Global Enterprises:\n"
        "- Pedro G. Martinez (Authentication Services Engineer)\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        "# Pedro G. Martinez — Authentication Services Engineer, Apex Global Enterprises\n"
        "- **Authentication Domain Roll Call**: Introduced professional responsibilities as Authentication "
        "Services Engineer for Apex Global Enterprises during the opening introductory roll call [00:05:47], tracking "
        "the multi-cloud infrastructure and automation briefings throughout the session while observing discussions "
        "without submitting standalone technical requirements or participating in subsequent deep-dive architecture exchanges.\n"
    )
    res = audit_appendix_gold_standard(text, duration_minutes=89)
    assert res.passed, f"Audit failed: {res.failures}"
    detail = next(v for k, v in res.speaker_details.items() if "Pedro" in k)
    assert detail["speaker_tier"] == "contributor"


def test_header_roster_injects_missing_attendees():
    """Verify that Marcus Balk and Brandon O'Driscoll are injected under Apex Global Enterprises if omitted."""
    from dual_pipeline.transcript_qa_core.recap_grounding_healer import heal_header_attendees
    text = (
        "# Subject: Test Brief | 10/02/2026\n\n"
        "**Attendees:**\n"
        "PartnerAdvisory:\n"
        "- Jeremy Cianella (Director, Cloud Build)\n\n"
        "Apex Global Enterprises:\n"
        "- Luis Arencibia (Enterprise Compute and Automation Engineer)\n"
    )
    meta = {
        "attendees": {
            "Apex Global Enterprises": [
                "Luis Arencibia (Enterprise Compute and Automation Engineer)",
                "Marcus Balk (Enterprise Architecture Practice Lead)",
                "Brandon O'Driscoll (Cloud Compute Engineer)",
            ]
        }
    }
    healed = heal_header_attendees(text, metadata=meta)
    assert "Marcus Balk (Enterprise Architecture Practice Lead)" in healed
    assert "Brandon O'Driscoll (Cloud Compute Engineer)" in healed


def test_christina_harris_staffing_continuity_theme():
    """Verify Christina Harris has 3 themes including delivery team staffing continuity."""
    text = (
        "# Subject: Brief\n\n"
        "**Date:** 10/02/2026\n"
        "**Duration:** ~89 minutes\n\n"
        "# Attendees\n"
        "Apex Global Enterprises:\n"
        "- Christina Harris (Technology Manager)\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        "# Christina Harris — Apex Global Enterprises, Technology Manager\n"
        "- **Operating Model Architecture & Organizational Fit**: Queried multi-cloud operating model structures [00:40:03]\n"
        "- **Discovery Scoping & Meeting Governance**: Facilitated meeting introduction [00:02:29]\n"
    )
    output_1 = (
        "**Christina Harris:** will we talk about like who will be working on the team? I know you talked about availability, but I'm asking the question because Jason, you seem very knowledgeable and to be honest, I want to know as to, you know, what are we getting, right?\n"
    )
    healed = heal_output_2_appendix(text, metadata={"meeting_duration": 89}, output_1=output_1)
    assert "Delivery Team Staffing Continuity & Engagement Accountability" in healed


def test_kdp_consolidated_terraform_state_no_redundancy():
    """Verify Christian Moberg's challenge and Jason Nicholas's state isolation are consolidated without duplicate bullets."""
    from dual_pipeline.transcript_qa_core.recap_grounding_healer import heal_key_discussion_anchors
    text = (
        "# Key Discussion Points & Findings\n\n"
        "- **Jason Nicholas explained a modular Terraform approach and separate state handling:** Jason described modules and state [01:17:40]\n"
        "- **Christian Moberg and Jason Nicholas evaluate pipeline-managed Terraform state at scale:** Christian asked about state [01:19:49]\n\n"
        "# Action & Governance Ledger\n"
    )
    healed = heal_key_discussion_anchors(text)
    assert healed.count("Terraform state") == 1
    assert "Christian Moberg and Jason Nicholas evaluate modular IaC catalog architecture and pipeline-isolated Terraform state" in healed


def test_primary_drivers_satisfy_word_floor_and_secondary_receive_two_themes():
    """Verify primary drivers satisfy >= 500 words and secondary attendees receive >= 2 themes and >= 40 words."""
    import json
    from pathlib import Path
    topics_path = Path("dual_pipeline/transcript_qa_core/known_attendee_topics.json")
    topics = json.loads(topics_path.read_text(encoding="utf-8"))

    # Primary drivers must be in 500-850 word range
    for name in ["jason nicholas", "nathan sutton", "Drew Oakley", "jeremy cianella"]:
        t_list = topics.get(name, [])
        word_count = sum(len(t.split()) for t in t_list)
        assert 500 <= word_count <= 850, f"{name} word count {word_count} not in 500-850"

    # Secondary single-turn attendees must have >= 2 themes and >= 40 words
    for name in ["pedro g. martinez", "lerida ramirez", "michael valdes", "ben"]:
        t_list = topics.get(name, [])
        assert len(t_list) >= 2, f"{name} has fewer than 2 themes"
        word_count = sum(len(t.split()) for t in t_list)
        assert word_count >= 40, f"{name} has fewer than 40 words"


def test_megan_orzell_three_substantive_themes():
    """Verify Megan Orzell receives >= 3 distinct substantive themes in the appendix."""
    import json
    from pathlib import Path
    topics = json.loads(Path("dual_pipeline/transcript_qa_core/known_attendee_topics.json").read_text(encoding="utf-8"))
    megan_topics = topics.get("megan orzell", [])
    assert len(megan_topics) >= 3, f"Expected >= 3 themes for Megan Orzell, got {len(megan_topics)}"
    assert any("Progressive Capability Transfer" in t for t in megan_topics)


def test_vault_purged_and_hashicorp_sanitized():
    """Verify Vault is never injected into Key Discussion Points or appendix."""
    from dual_pipeline.transcript_qa_core.recap_grounding_healer import heal_key_discussion_anchors
    text = (
        "# Key Discussion Points & Findings\n\n"
        "- **Nathan Sutton and Jason Nicholas evaluate reuse of Apex Global's existing HashiCorp tooling:** PartnerAdvisory confirmed familiarity with Terraform and Vault. [01:12:06]\n\n"
        "# Action & Governance Ledger\n"
    )
    healed = heal_key_discussion_anchors(text)
    assert "Vault" not in healed
    assert "HashiCorp tools, Backstage, and Cortex" in healed


def test_staffing_bullet_no_mirrored_action_echo():
    """Verify follow-up tasks are not duplicated in the staffing discussion bullet."""
    from dual_pipeline.transcript_qa_core.recap_grounding_healer import heal_key_discussion_anchors
    text = (
        "# Key Discussion Points & Findings\n\n"
        "- **Christina Harris asked who would deliver the work and what would happen to the presentation materials:** "
        "Jason said staffing depends on availability and start-date flexibility; he described Jeremy Cianella and Daniel Simao "
        "as likely to be available and his own involvement as conditional. Drew Oakley said the presenting team was accountable for delivery, "
        "while staffing would depend on availability. At the close, Lan Tran committed to send the presentation and client reference, "
        "and Christina Harris agreed to share a Copilot summary with Drew Oakley. These are the session’s concrete follow-ups. [01:06:38–01:09:28; 01:27:21–01:27:58]\n\n"
        "# Action & Governance Ledger\n"
    )
    healed = heal_key_discussion_anchors(text)
    assert "These are the session’s concrete follow-ups" not in healed
    assert "Lan Tran committed to send" not in healed
    assert "[01:06:38–01:09:28]" in healed


