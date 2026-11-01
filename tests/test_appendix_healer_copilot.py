"""Unit tests for Copilot-style Appendix Healer and Synthesizer."""
import re

from dual_pipeline.transcript_qa_core.appendix_healer import heal_output_2_appendix
from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer


def test_heal_output_2_appendix_with_dialogue():
    f2 = """# Subject: Project LENS Catchup | 09/14/2026

Date: 09/14/2026
Time: 05:02 PM - 05:39 PM ET
Attendees:
- Apex Global Enterprises (NEA): harper Hoffman (Product & Business Lead)
- PartnerAdvisory: Amit Ayer (Lead Enterprise Architect)

---

## Meeting Objective
Review Project LENS progress.
"""
    output_1 = """# Forensic Transcript
[00:00:10] harper Hoffman: As a user I could not experience that. We need to be able to preview the rules on sample images before approving anything.
[00:01:20] Amit Ayer: We can introduce a six-row modal in the UI to give immediate visibility into the transformations.
"""
    meta = {
        "duration_minutes": 37,
        "roster": [
            {"name": "harper Hoffman", "role": "Product & Business Lead", "org": "Apex Global Enterprises (NEA)"},
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "org": "PartnerAdvisory"},
        ],
    }
    healed = heal_output_2_appendix(f2, metadata=meta, output_1=output_1)
    assert "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown" in healed
    assert "# harper Hoffman - Apex Global Enterprises (NEA), Product & Business Lead" in healed
    assert "# Amit Ayer - PartnerAdvisory, Lead Enterprise Architect" in healed
    assert (
        "**Operational Engagement & Discussion Priorities**:" in healed
        or "Operational Engagement & Discussion Priorities" in healed
    )
    assert "We need to be able to preview the rules" in healed or "six-row modal" in healed


def test_heal_output_2_appendix_offline_role_synthesis():
    f2 = """# Subject: Project LENS Catchup | 09/14/2026

Date: 09/14/2026
Time: 05:02 PM - 05:39 PM ET
Attendees:
- Apex Global Enterprises (NEA): harper Hoffman (Product & Business Lead), Brett Gavagni (Architecture & Systems Lead)
- PartnerAdvisory: Jennifer Pan (Lead Engineer / Solution Builder), Amit Ayer (Lead Enterprise Architect)

---

## Meeting Objective
Review Project LENS progress.
"""
    meta = {
        "duration_minutes": 37,
        "roster": [
            {"name": "harper Hoffman", "role": "Product & Business Lead", "org": "Apex Global Enterprises (NEA)"},
            {"name": "Brett Gavagni", "role": "Architecture & Systems Lead", "org": "Apex Global Enterprises (NEA)"},
            {"name": "Jennifer Pan", "role": "Lead Engineer / Solution Builder", "org": "PartnerAdvisory"},
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "org": "PartnerAdvisory"},
        ],
    }
    healed = heal_output_2_appendix(f2, metadata=meta)
    assert "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown" in healed
    assert "# harper Hoffman - Apex Global Enterprises (NEA), Product & Business Lead" in healed
    assert "# Brett Gavagni - Apex Global Enterprises (NEA), Architecture & Systems Lead" in healed
    assert "# Jennifer Pan - PartnerAdvisory, Lead Engineer / Solution Builder" in healed
    assert "# Amit Ayer - PartnerAdvisory, Lead Enterprise Architect" in healed
    assert "Stakeholder Representation & Scope" in healed
    assert "Attributable Dialogue Record" in healed
    # Verify no generic boilerplate bullets
    assert "**Core Thesis & Strategic Perspective:**" not in healed
    # Verify word count per speaker section is substantive
    for name in ["harper Hoffman", "Brett Gavagni", "Jennifer Pan", "Amit Ayer"]:
        m = re.search(rf"# {re.escape(name)}[^\n]*([\s\S]*?)(?=(?:\n# [A-Z]|\Z))", healed)
        assert m is not None
        words = m.group(1).split()
        assert len(words) >= 20


def test_heal_output_2_appendix_replaces_degenerate_boilerplate():
    f2_degenerate = """# Subject: Test Meeting | 09/14/2026

Date: 09/14/2026
Time: 10:00 AM ET (Duration: ~30m)
Attendees:
- Apex Global Enterprises (NEA): harper Hoffman (Product Lead)

---

## Meeting Objective
Review.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### harper Hoffman (Apex Global Enterprises (NEA), Product Lead)
- **Core Thesis & Strategic Perspective:** Endorsed architectural decoupling and disciplined governance boundaries to ensure project execution stability.
- **Key Contributions & Discussion Focus:** Evaluated critical system dependencies, affirmed data lineage standards, and verified delivery readiness criteria.
- **Commitments & Next Steps:** Confirmed ownership of technical validation milestones and committed to supporting subsequent review gates.
"""
    meta = {
        "duration_minutes": 30,
        "roster": [
            {"name": "harper Hoffman", "role": "Product Lead", "org": "Apex Global Enterprises (NEA)"},
        ],
    }
    healed = heal_output_2_appendix(f2_degenerate, metadata=meta)
    assert "- **Core Thesis & Strategic Perspective:** Endorsed architectural decoupling" not in healed
    assert "# harper Hoffman - Apex Global Enterprises (NEA), Product Lead" in healed
    assert "Stakeholder Representation & Scope" in healed
    assert "Attributable Dialogue Record" in healed


def test_offline_synthesizer_deep_recap_appendix():
    synth = OfflineTranscriptSynthesizer()
    meta = {
        "meeting_name": "Project LENS Catchup",
        "date": "09/14/2026",
        "time": "05:02 PM - 05:39 PM",
        "duration_minutes": 37,
        "roster": [
            {"name": "harper Hoffman", "role": "Product & Business Lead", "org": "Apex Global Enterprises (NEA)"},
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "org": "PartnerAdvisory"},
        ],
    }
    recap = synth.synthesize_recap("prompt", user_content_or_metadata=meta)
    assert "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown" in recap
    assert "# harper Hoffman - Apex Global Enterprises (NEA), Product & Business Lead" in recap
    assert "**Core Thesis & Strategic Perspective:**" not in recap
    assert "Stakeholder Representation & Scope" in recap
    assert "Attributable Dialogue Record" in recap
