"""
tests/test_copilot_speaker_summary_depth.py

Validates replication of Microsoft Copilot speaker summary depth across:
1. Stage 02 Executive Recap prompt contracts (config and governance)
2. QA Gate 2.8 Appendix completeness and boilerplate rejection
3. Appendix Healer and Offline Synthesizer multi-thematic bullet generation
4. Kannan C NEA interview debrief calibration exemplar compliance
"""

from __future__ import annotations

import unittest

from dual_pipeline.core import ROOT
from dual_pipeline.transcript_qa_core.appendix_healer import heal_output_2_appendix
from dual_pipeline.transcript_qa_core.gate2 import _check_appendix_completeness, run_qa_gate_2


class TestCopilotSpeakerSummaryDepth(unittest.TestCase):
    def setUp(self) -> None:
        self.kannan_calibration_appendix = """# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

#### Kannan
- **Interview Structure and Evaluation Themes**: Kannan provided a detailed walkthrough of both interview rounds. He explained that the first discussion with Rianne focused heavily on understanding how much hands-on engineering work he performs, how artificial intelligence has changed his daily workflow, and how he balances design, coding, and collaboration. He described multiple behavioral scenarios, including how he communicates estimation spikes, escalates risks, and manages project pivots. His interpretation was that the interviewers were assessing flexibility, curiosity, adaptability, and willingness to take on responsibilities outside a narrowly defined role. He highlighted a question about stepping into a platform engineering role when needed as a strong indicator that candidates are expected to operate across disciplines rather than remain specialized.
- **Technical Screening and Problem-Solving Approach**: Kannan explained that the technical round revolved around a deliberately flawed codebase and a failing unit test. Rather than immediately addressing the failing test, he described his methodology of first understanding the broader system by reviewing file structures, documentation, classes, interfaces, and application behavior. After building context, he investigated the failing test, identified that a filtering function had not been implemented correctly, proposed the fix, and then expanded the discussion into broader code-quality concerns. He emphasized identifying structural problems, maintainability issues, and design improvements, demonstrating that the interview evaluated engineering judgment as much as coding ability.
- **Code Quality, Architecture, and Engineering Standards**: Kannan reported that much of the technical discussion focused on how he would guide developers, improve documentation, increase reusability, strengthen testability, and keep code organized. He described questions about code reviews, production readiness, GitHub repository practices, deployment safeguards, and maintaining code integrity. He interpreted these discussions as an effort to determine whether he possessed broad engineering experience spanning development workflows, repository management, testing practices, and operational concerns rather than just implementation skills.
- **AI Tool Usage and Responsible Adoption**: Kannan recalled substantial discussion around AI-assisted development. He shared his experience using several coding assistants and explained that effective AI usage depends heavily on prompt quality. He warned that unclear instructions can generate excessive or inaccurate changes, creating operational risk. To mitigate that risk, he advocated strengthening unit-test coverage whenever AI-generated code is introduced and maintaining strong specifications and documentation to guide development. He described these practices as mechanisms for preserving code integrity and controlling complexity when working with AI-powered engineering tools.
- **Responding to Project Pivots**: When asked how he handled shifting project direction, Kannan outlined a structured approach centered on alignment, analysis, and transparency. He stated that teams must first internally agree that a change in direction is necessary. After alignment, he would investigate multiple alternatives, gather supporting evidence, evaluate possible outcomes, and prepare for client questions. He emphasized communicating options openly, presenting advantages and disadvantages, and ensuring stakeholders understand tradeoffs before decisions are made. His response framed pivots as a disciplined process rather than a reactive event.
- **Technical Exercise Mechanics and Environment**: Kannan explained how the coding assessment was executed through a browser-based shared development environment. Both participants could interact with the code repository, browse files, inspect logs, and review application behavior in real time. He noted that no AI coding assistant was used during the exercise. The environment enabled collaborative observation while still allowing him to demonstrate reasoning, navigation, and debugging skills directly within the shared interface.
- **Interview Advice for Future Candidates**: Kannan offered extensive recommendations for others preparing for similar interviews. He advised candidates to listen carefully before answering, pause to understand the true intent behind questions, and avoid jumping straight to solutions. He believed the interviewers value curiosity, adaptability, learning ability, productivity gains through AI, and thoughtful engineering judgment. For technical exercises, he recommended first understanding the overall system, searching for code smells, identifying architectural weaknesses, examining file structures, and articulating why specific improvements are necessary. He stressed confidence, analytical thinking, and clear communication over rapid coding.
- **Example of Architectural Critique**: To illustrate the type of analysis he performed, Kannan described noticing that application data was repeatedly loaded from a CSV file whenever an API operation occurred. He identified this as inefficient and suggested moving toward a more structured data-storage approach. He explained that while additional context would influence the final architecture choice, his goal was to demonstrate awareness of performance, maintainability, scalability, and design implications rather than focusing solely on repairing the immediate defect.
- **Preparation Support From Prior Team Members**: Kannan explained that discussions with colleagues who had already gone through the process helped him prepare. According to his account, those conversations provided guidance regarding the unit-test exercise and likely behavioral scenarios, giving him a better understanding of what to expect and how to approach the interviews.

#### Amit
- **Deep Investigation of Technical Reasoning**: Amit concentrated on understanding the thinking process behind Kannan's success rather than simply documenting outcomes. He repeatedly asked Kannan to explain his debugging methodology, specifically how he approached a failing unit test, how he narrowed down the root cause, and what reasoning steps occurred between identifying an issue and proposing a solution. His questions focused on extracting a repeatable process that could be used to coach future candidates.
- **Exploration of Client Expectations Around Change**: Amit focused significant attention on the topic of project pivots. He noted that project direction changes occur frequently and sought detailed insight into how Kannan framed discussions around changing scope and approach. His questions encouraged examination of transparency, option analysis, business value, tradeoffs, stakeholder communication, and alignment strategies, helping the broader group understand how those themes were being evaluated.
- **Operational Details of the Technical Assessment**: Amit also pursued practical information about how the technical interview was run. Rather than concentrating only on technical content, he investigated the mechanics of the exercise, including who controlled the environment, whether the candidate interacted directly with code, whether collaborative editing was supported, and what platform was used. His intent was to gain a clearer understanding of the candidate experience and improve preparation efforts.
- **Gathering Advice for Future Placement Success**: Amit referenced the team's relatively limited interview pass rate and asked Kannan for actionable guidance that could improve outcomes for future candidates. Through those questions, he helped surface recommendations around listening carefully, demonstrating curiosity, discussing AI usage thoughtfully, evaluating architecture before coding, and clearly explaining engineering decisions.

#### Dan
- **Context for the Feedback Session**: Dan provided extensive background explaining why the group wanted detailed interview feedback. He described a shift in expectations toward engineers who can operate across multiple disciplines rather than remaining highly specialized. According to his explanation, successful candidates are increasingly expected to function as flexible, full-stack contributors who can adapt to changing project needs and perform effectively across a wide range of responsibilities.
- **Understanding the Interview Evaluation Criteria**: Throughout the conversation, Dan sought clarification on behavioral questions and evaluation areas. He specifically requested more detail around topics such as estimation changes, risk communication, and project pivots. His questions were intended to identify recurring interview themes so that future candidate recommendations could be better aligned with the expectations being communicated during the hiring process.
- **Validation of Successful Interview Behaviors**: After hearing Kannan explain his debugging methodology, Dan openly recognized the approach as a strong example of effective technical reasoning. He appeared particularly interested in the emphasis on understanding system context before attempting fixes, suggesting that thorough analysis and communication were important indicators of success.
- **Future Team Integration and Logistics**: Near the end of the discussion, Dan shifted toward onboarding and team placement considerations. He discussed uncertainty around future assignments and described how travel and collaboration arrangements might work depending on the eventual team placement. His comments focused on integrating Kannan into upcoming workstreams and aligning him with the appropriate engineering group.

#### Stephan
- **AI Questioning During Interviews**: Stephan focused on determining how prominently AI-related topics appeared during the interview process. He specifically investigated whether AI tooling, coding assistants, or AI-driven development practices were evaluated during the screening, seeking to verify whether candidates are systematically assessed on modern AI engineering workflows.
- **Validation of Candidate Strategy and Experience**: Stephan engaged on the practical balance between rapid code fixes and contextual system analysis, confirming that the client evaluates holistic engineering judgment rather than pure typing speed.
"""

    def test_kannan_calibration_appendix_passes_gate_2_8(self) -> None:
        """Kannan C calibration appendix must easily clear Gate 2.8."""
        recap_text = f"""# Subject: Technical Interview Feedback -- NEA CI Prep | 09/14/2026

**Date:** 09/14/2026
**Time:** 03:00 PM - 03:30 PM ET (Duration: ~30m)
**Attendees:**
- PartnerAdvisory: Amit Ayer, Drew Oakley, Cameron Mercer, Tyler McCord, Kannan Chandraprakash

## Meeting Objective
Review NEA interview experience with Kannan Chandraprakash following his successful technical screening with Rianne, identifying key evaluation patterns and debugging methodologies to coach future candidates.

## Key Discussion Points & Findings
### 1. Hands-On Engineering Expectations and AI Assistance
Kannan provided a detailed walkthrough of both interview rounds, noting that the opening discussion with Rianne focused heavily on assessing hands-on engineering capability, daily workflow dynamics, and how artificial intelligence tools are leveraged in practice. The interviewer investigated how much time is actively spent on design, coding, testing, and team interaction. Behavioral questions probed how candidates manage estimation spikes, escalate delivery risks, and execute project pivots. Kannan highlighted that the team sought engineers exhibiting high curiosity, technical adaptability, and a willingness to step into adjacent responsibilities such as platform engineering when needed.

### 2. Context-First Technical Screening and Problem-Solving Methodology
The technical assessment revolved around a deliberately flawed codebase containing a failing unit test. Rather than immediately patching the failing test, Kannan prioritized developing a holistic understanding of the application architecture by reviewing file trees, documentation, class hierarchies, and interface contracts. After establishing clear context, he investigated the broken unit test, diagnosed an incorrectly implemented filtering function, proposed the necessary logical fix, and expanded the conversation into broader code maintainability concerns.

### 3. Code Quality, Architectural Integrity, and Production Safeguards
Substantial technical dialogue centered on how senior engineers mentor developers, improve documentation clarity, increase code reusability, and enforce testability. Kannan addressed questions regarding code review standards, production readiness safeguards, GitHub repository management, and deployment pipelines. The discussion confirmed that Apex Global Enterprises evaluates holistic engineering judgment spanning development workflows, repository governance, and operational reliability rather than isolated coding speed.

### 4. AI Tooling Governance and Spec-Driven Development
Kannan recalled significant discussion regarding AI coding assistants, noting that effective usage depends strictly on prompt quality. Unclear instructions risk introducing chaotic and unverified changes. To preserve codebase integrity, Kannan advocated for updating unit tests for every AI-assisted code modification and maintaining disciplined specification-driven development practices.

### 5. Managing Project Pivots Through Alignment and Options Analysis
When addressing shifting project direction, Kannan outlined a disciplined process emphasizing internal alignment before client communication. Teams must first agree that a pivot is necessary, conduct research across viable alternatives, evaluate trade-offs, and transparently present options with supporting evidence to stakeholders.

## Strategic Synthesis
| # | What We Heard (NEA Operational Friction) | What It Means (Enterprise Architecture Implication) |
| :--- | :--- | :--- |
| 1 | Candidates fail when rushing straight into code patches | Must coach engineers to analyze system architecture and test suites before writing code |
| 2 | NEA requires full-stack flexibility across platforms | Team members must operate across disciplines rather than remaining narrow specialists |

## Next Steps & Action Items
| # | Action Item | Accountable Owner | Responsible Party | Target Timing | Spoken Locator | Status |
|---|---|---|---|---|---|---|
| 1 | Synthesize interview coaching guide based on Kannan's feedback | PartnerAdvisory | Amit Ayer | 09/18/2026 | [22:34] | Confirmed |

## Open Decisions / Blockers
- Final workstream and pod assignment for Kannan pending quarter-end team reallocations.

## Business Meaning and Value Case
- **Business problem:** Low interview pass rates (<50%) constrain delivery scaling across NEA analytics initiatives.
- **Value at stake:** Accelerates team placement and protects account revenue by aligning candidate preparation with verified client expectations.
- **Baseline:** 30-40% interview placement success rate across recent candidate submissions.
- **Target:** >= 70% interview placement success rate through disciplined candidate preparation.
- **Value mechanism:** Institutionalizing Kannan's context-first debugging approach and AI governance principles.
- **Confidence:** High (85%) based on verified interview outcomes.
- **Consequence of delay:** Missed hiring windows and slower ramp for critical analytics workstreams.
- **Evidence:** Spoken testimony from Kannan Chandraprakash and Drew Oakley.

## Executive Decision
- **Executive conclusion:** Standardize interview coaching around context-first code exploration and full-stack adaptability.
- **Decision required:** Formalize technical screening preparation curriculum for upcoming NEA candidates.
- **Recommended choice:** Adopt Kannan's context-gathering checklist and behavioral pivot frameworks.
- **Alternatives rejected or deferred:** Allowing candidates to enter screening without structured architecture walkthroughs.
- **Reason:** Verified success in recent technical screening with staff engineering leadership.
- **Decision owner:** Amit Ayer and Drew Oakley.
- **Decision deadline:** 09/18/2026.
- **Consequence of no decision:** Continued sub-optimal placement rates across engineering roles.
- **Decision status:** Ratified.
- **Evidence:** [04:04], [22:34].

## Immediate Delivery Scope & Release Boundaries
- **Target user:** PartnerAdvisory engineering candidates interviewing with Apex Global Enterprises.
- **Business capability:** Repeatable technical screening preparation curriculum.
- **In scope:** Unit-test debugging methodology, architectural smell identification checklist, AI assistant prompt governance guide, and behavioral pivot framing.
- **Out of scope:** Custom technical screening coding challenges or third-party platform licensing.
- **Systems touched:** Candidate coaching playbooks and team readiness documentation.
- **Systems explicitly not replaced:** Official client interview portals (CodeInterview.io).
- **Required dependencies:** Access to candidate feedback repository.
- **Known constraints:** Time constraints between candidate submission and client screening.
- **First deliverable:** Two-page technical screening coaching playbook.
- **Acceptance test:** Feedback walkthrough completed with next candidate cohort.
- **Exit criterion:** Playbook reviewed and ratified by account leadership.
- **Owner:** Amit Ayer.
- **Decision gate:** Review with Drew Oakley and Cameron Mercer.
- **Success metric:** Improvement in candidate pass rate to >= 65%.
- **Evidence:** Spoken dialogue [04:36]-[28:00].
- **MVP status:** Ready for immediate candidate application.
- **Next required decision:** Schedule onboarding logistics once pod allocation is confirmed.

{self.kannan_calibration_appendix}
"""
        ok, _, fails = _check_appendix_completeness(recap_text, 30)
        self.assertTrue(ok, f"Gate 2.8 failed on Kannan calibration: {fails}")

        res = run_qa_gate_2(recap_text, {"duration_minutes": 30})
        self.assertEqual(res["status"], "PASS")
        self.assertTrue(res["gates"]["Gate_2_8_Appendix_Completeness"]["passed"])
        self.assertEqual(res["failures"], [])

    def test_prompt_contracts_contain_intrinsic_gold_standard(self) -> None:
        """Prompt contracts must define intrinsic SSOT gold standard without external calibration names."""
        stage_02_prompt = ROOT / "config" / "prompts" / "synthesis" / "stages" / "02_executive_recap.md"
        gov_prompt = ROOT / "governance" / "decision_intelligence" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md"
        mirror_prompt = ROOT / "config" / "prompts" / "synthesis" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md"

        self.assertTrue(stage_02_prompt.is_file())
        self.assertTrue(gov_prompt.is_file())
        self.assertTrue(mirror_prompt.is_file())

        stage_02_text = stage_02_prompt.read_text(encoding="utf-8")
        gov_text = gov_prompt.read_text(encoding="utf-8")
        mirror_text = mirror_prompt.read_text(encoding="utf-8")

        self.assertNotIn("Kannan", stage_02_text)
        self.assertNotIn("Kannan", gov_text)
        self.assertIn("Single Source of Truth (SSOT) Gold Standard", stage_02_text)
        self.assertIn("Thematic Architecture", stage_02_text)
        self.assertIn("Thematic Architecture", gov_text)
        self.assertEqual(gov_text, mirror_text)

    def test_gate_2_8_rejects_generic_boilerplate_and_thin_sections(self) -> None:
        """Gate 2.8 must reject generic 3-bullet placeholder text and thin sections."""
        boilerplate_text = """# Subject: Test
**Date:** 09/14/2026
**Time:** 05:00 PM - 05:30 PM ET (Duration: ~30m)
**Attendees:** Dan (PartnerAdvisory), Sarah (NEA)

## Meeting Objective
Test.

## Key Discussion Points & Findings
- Point.

## Next Steps & Action Items
| 1 | Action | PartnerAdvisory | Dan | 09/15/2026 | [01:00] |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Dan - PartnerAdvisory
- **Core Thesis & Strategic Perspective:** Endorsed architectural decoupling.
- **Key Contributions & Discussion Focus:** Discussed integration.
- **Commitments & Next Steps:** Coordinate follow-up.
"""
        ok, can_heal, fails = _check_appendix_completeness(boilerplate_text, {"duration_minutes": 30})
        self.assertFalse(ok)
        self.assertTrue(can_heal)
        self.assertTrue(any("GATE_2_8_BOILERPLATE_DETECTED" in f for f in fails))

        thin_text = """# Subject: Test
**Date:** 09/14/2026
**Time:** 05:00 PM - 05:30 PM ET (Duration: ~30m)
**Attendees:** Dan (PartnerAdvisory), Sarah (NEA)

## Meeting Objective
Test.

## Key Discussion Points & Findings
- Point.

## Next Steps & Action Items
| 1 | Action | PartnerAdvisory | Dan | 09/15/2026 | [01:00] |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Dan - PartnerAdvisory
Too short.
"""
        ok_thin, can_heal_thin, fails_thin = _check_appendix_completeness(thin_text, {"duration_minutes": 30})
        self.assertFalse(ok_thin)
        self.assertTrue(can_heal_thin)
        self.assertTrue(any("GATE_2_8_THIN_SPEAKER_SECTION" in f for f in fails_thin))

    def test_appendix_healer_generates_copilot_depth_bullets(self) -> None:
        """Appendix healer must generate rich multi-thematic bullets from transcript dialogue."""
        raw_transcript = """
[00:00:03] Kannan Chandraprakash: Yeah, the interview went well. First discussion with Rianne focused heavily on hands on engineering work.
[00:01:25] Drew Oakley: Thank you so much. We are keen to learn because we had people who did not make it through.
[00:02:12] Amit Ayer: Amit Ayer, Senior Director. Want to understand what you went through and how you debugged the failing unit test.
[00:05:26] Kannan Chandraprakash: Rianne joined as a developer back in 2012. She asked how much hands-on I do in a day and behavioral questions like how do you propose spikes and pivots.
[00:07:42] Kannan Chandraprakash: The technical round had deliberately unstructured code and a failing unit test. I looked at the code structure first before touching the fix.
[00:10:42] Kannan Chandraprakash: Before looking at the failing test, I wanted to get an idea of what the code is doing. I went through the README, looked at the classes, and found the filter was not implemented.
[00:12:13] Kannan Chandraprakash: For AI tools, prompt quality is critical. If instructions are unclear, it generates excessive changes, so we need strong unit tests to preserve code integrity.
[00:15:19] Kannan Chandraprakash: On project pivots, first internally align that a pivot is needed, then research multiple options, gather data, and transparently present pros and cons to the client.
[00:22:34] Kannan Chandraprakash: For future candidates, listen carefully before answering, do not jump into solutioning, inspect the codebase for code smells, and explain your architectural reasoning.
"""
        base_recap = """# Subject: Technical Interview Feedback -- NEA CI Prep | 09/14/2026
**Date:** 09/14/2026
**Time:** 03:00 PM - 03:30 PM ET (Duration: ~30m)
**Attendees:**
- PartnerAdvisory: Kannan Chandraprakash (Senior Principal), Amit Ayer (Lead Enterprise Architect), Drew Oakley (Account Lead)

## Meeting Objective
Review interview feedback.

## Key Discussion Points & Findings
- Interview feedback reviewed.

## Strategic Synthesis
| # | What We Heard | What It Means |
| :--- | :--- | :--- |
| 1 | Candidate passed | Standardize preparation |

## Immediate Delivery Scope & Release Boundaries
- **Target user:** Candidates.
- **In scope:** Playbook.

## Next Steps & Action Items
| # | Action Item | Accountable Owner | Responsible Party | Target Timing | Spoken Locator |
|---|---|---|---|---|---|
| 1 | Draft playbook | PartnerAdvisory | Amit Ayer | 09/18/2026 | [22:34] |
"""
        healed = heal_output_2_appendix(
            base_recap,
            metadata={"duration_minutes": 30},
            output_1=raw_transcript,
        )

        self.assertIn("# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown", healed)
        self.assertIn("### Kannan Chandraprakash", healed)
        self.assertIn("Discussion Context", healed)
        self.assertIn("Technical Analysis", healed)

        # Must pass Gate 2.8 checks
        ok, _, fails = _check_appendix_completeness(healed, {"duration_minutes": 30})
        self.assertTrue(ok, f"Healed appendix failed Gate 2.8: {fails}")


if __name__ == "__main__":
    unittest.main()
