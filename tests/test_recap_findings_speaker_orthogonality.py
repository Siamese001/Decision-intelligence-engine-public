import json
import os

OUTPUT_DIR = ".worktrees/feat_adversarial_review_findings_speaker_overlap/output/project_lens/10-01-2026/Phase1_Sow_Progress_Amit_Mandy_PartnerAdvisory"

def test_anti_echo_and_visual_grounding():
    o1_path = os.path.join(OUTPUT_DIR, "01_Phase1_Sow_Progress_Amit_Mandy_PartnerAdvisory_10-01-2026_0302PM0334PMET_hitl_resolved_transcript.md")
    o2_path = os.path.join(OUTPUT_DIR, "02_Phase1_Sow_Progress_Amit_Mandy_PartnerAdvisory_10-01-2026_0302PM0334PMET_executive_recap.md")
    o3_path = os.path.join(OUTPUT_DIR, "03_Phase1_Sow_Progress_Amit_Mandy_PartnerAdvisory_10-01-2026_0302PM0334PMET_canonical_signal_projection.json")

    with open(o1_path) as f:
        o1_text = f.read()
    with open(o2_path) as f:
        o2_text = f.read()
    with open(o3_path) as f:
        o3_data = json.load(f)

    # 2. Enforce Visual Collateral Grounding in Output 1
    assert "Rules Preview UI demonstrated" in o1_text
    assert "Expanded Golden Dataset Metrics detailed" in o1_text
    assert "LENS Dashboard Wireframe explained verbally" in o1_text
    assert "sow_scope_clarification.md" in o1_text

    # 3. Enforce Visual Collateral Grounding in Output 2 Exhibits
    assert "verbal walkthrough of the Rules Preview UI" in o2_text
    assert "spoken metrics regarding the Golden Dataset" in o2_text
    assert "verbal explanation of the granular rejected reason codes" in o2_text
    assert "sow_scope_clarification.md" in o2_text

    # 4. Enforce Entity Upserts in Output 3
    entities = {e["id"]: e for e in o3_data["graph_mutations"]["entity_upserts"]}
    assert "system:lens_dashboard" in entities
    assert "system:rules_preview_ui" in entities
    assert "policy:sow_contract_4600027256" in entities

    # 5. Enforce Action Commitments in Output 3
    assert len(o3_data.get("graph_mutations", {}).get("action_updates", [])) >= 5

    # 6. Anti-Echo Orthogonality (Output 2)
    sec2_start = o2_text.find("# Key Discussion Points & Findings")
    sec5_start = o2_text.find("# Appendix: Detailed Stakeholder Perspectives")
    assert sec2_start != -1 and sec5_start != -1
    action_table_start = o2_text.find("# Action & Governance Ledger")
    action_table_end = sec5_start
    action_table_content = o2_text[action_table_start:action_table_end]
    assert "For Finding 6, the maintainability meeting remained proposed" not in action_table_content
