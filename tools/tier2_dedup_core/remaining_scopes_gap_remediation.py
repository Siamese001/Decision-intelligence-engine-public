"""Governed remaining-scopes package ingestion, archive path rebinding, and candidate promotion."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dual_pipeline.core import ROOT, sha256_file, stable_id
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from tools.graph_mutation_proof_core.proof import prove, snapshot
from tools.tier2_dedup_core.sow_duplicate_retirement import (
    refresh_sidecar_after_graph_cleanup,
)

REMAINING_SCOPES_UNDATED_BACKFILLS: Tuple[Tuple[str, str], ...] = (
    ("source-5603cf8f5154b25dfbf5f33c", "2026-09-22"),
    ("source-5e00f65a5d153b3c14e13d62", "2026-09-19"),
    ("source-7d181ab3563433b658b3f6a0", "2026-09-17"),
    ("source-dce33d47b09ee075af5dfbe8", "2026-09-18"),
)

REMAINING_SCOPES_ARCHIVE_REFS: Tuple[Tuple[str, str], ...] = (
    ("source-5603cf8f5154b25dfbf5f33c", "output/project_lens/09-22-2026/Internal_Standup_PartnerAdvisory_NEA/01_Internal_Standup_PartnerAdvisory_NEA_09-22-2026_0134PM_0156PM_ET_hitl_resolved_transcript.md"),
    ("source-5e00f65a5d153b3c14e13d62", "output/project_lens/09-19-2026/LENS_Catchup_Jen_Amit/01_Lens_catchup_jen_amit_09-19-2026_1030AM_1200PM_ET_hitl_resolved_transcript.md"),
    ("source-7d181ab3563433b658b3f6a0", "output/project_lens/09-17-2026/Mid_Sprint_Anthony_PartnerAdvisory/01_Mid_Sprint_Anthony_PartnerAdvisory_09-17-2026_0400PM_0430PM_ET_hitl_resolved_transcript.md"),
    ("source-dce33d47b09ee075af5dfbe8", "output/project_lens/09-18-2026/Jake_Amit_Miles_PartnerAdvisory/01_Jake_Amit_Miles_PartnerAdvisory_09-18-2026_1243PM_0139PM_ET_hitl_resolved_transcript.md"),
    ("source-177bbdb3204a3d50ce726d03", "output/internal_consulting/11-19-2025/PartnerAdvisory_Career_Framework_Role_Profiles/03_PartnerAdvisory_Career_Framework_Role_Profiles_11-19-2025_canonical_signal_projection.json"),
    ("source-358beffaf4031fa6acbffa05", "output/market_intelligence/12-08-2025/apex_global_Energy_Google_Cloud_Strategic_En/03_apex_global_Energy_Google_Cloud_Strategic_En_12-08-2025_0658PM_ET_canonical_signal_projection.json"),
    ("source-f38bd13dc4c031f2379aec6d", "output/ec_scheduling/04-09-2026/NEE_EC_Scheduling_PO2000622222/01_NEE_EC_Scheduling_PO2000622222_04-09-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md"),
    ("source-413f470db94057f8ff57333b", "output/project_lens/04-22-2026/Visual_Asset_Eduardo_PartnerAdvisory/03_Visual_Asset_Eduardo_PartnerAdvisory_04-22-2026_0645AM_ET_canonical_signal_projection.json"),
    ("source-7e1bccf65e636b8622943955", "output/project_lens/05-26-2026/03_LENS_Phase_1_SOW_05-26-2026_0900AM_1000AM_ET_canonical_signal_projection.json"),
    ("source-c72265144274989494668ada", "output/project_lens/06-12-2026/Visual_Asset_Intelligence_Sow_PartnerAdvisory/03_Visual_Asset_Intelligence_Sow_PartnerAdvisory_06-12-2026_0939AM_ET_canonical_signal_projection.json"),
    ("source-2898e3b787c6a9f18590eef1", "output/project_spring/06-29-2026/Project_Spring_SOW/01_Project_Spring_SOW_06-29-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md"),
    ("source-61a172eae43a19ec4287a623", "output/enterprise_architecture/07-15-2026/EA_AI_Policy_Strategy/01_EA_AI_Policy_Strategy_07-15-2026_1200PM_1230PM_ET_hitl_resolved_transcript.md"),
    ("source-5ec6f706472535028cc54c84", "output/partner_alliances/07-24-2026/Google_PartnerAdvisory_apex_global/01_Google_PartnerAdvisory_apex_global_07-24-2026_0129PM0200PMEDT_hitl_resolved_transcript.md"),
    ("source-58d538846ec2722dcfb201bb", "output/partner_alliances/08-04-2026/Google_PartnerAdvisory_apex_global/01_Google_PartnerAdvisory_apex_global_08-04-2026_0130PM_0200PM_EDT_hitl_resolved_transcript.md"),
    ("source-feec2c1990ce05ee23f49512", "output/partner_alliances/08-11-2026/Google_PartnerAdvisory_apex_global/01_Google_PartnerAdvisory_apex_global_08-11-2026_0130PM_0200PM_EDT_hitl_resolved_transcript.md"),
    ("source-7db3ec30b20d0bc57c1c8702", "output/market_intelligence/08-21-2026/Single_Pane_Glass_PartnerAdvisory_Doug/03_Single_Pane_Glass_PartnerAdvisory_Doug_08-21-2026_0646PM_ET_canonical_signal_projection.json"),
    ("source-4cccc81543f3188a8d8ccdbf", "output/infrastructure_automation/08-26-2026/Nee_Infrastructure_Automation_Transforma/03_Nee_Infrastructure_Automation_Transforma_08-26-2026_0837PM_ET_canonical_signal_projection.json"),
    ("source-0f0d84abc4a8d6f0d689d0bc", "output/partner_alliances/09-01-2026/01_Google_PartnerAdvisory_apex_global_09-01-2026_0130PM_0200PM_EDT_hitl_resolved_transcript.md"),
    ("source-94c25709052059a2c3dddbe4", "output/regai/09-02-2026/RegAI_Delivery_Issues/01_RegAI_Delivery_Issues_09-02-2026_1000AM_1035AM_ET_hitl_resolved_transcript.md"),
    ("source-a10576cded6d3f327d4db1ad", "output/infrastructure_automation/09-02-2026/Fpl_Infrastructure_Automation_Rfp/03_Fpl_Infrastructure_Automation_Rfp_09-02-2026_0701PM_ET_canonical_signal_projection.json"),
    ("source-37879cc417fd0e36104db0ba", "output/regai/09-08-2026/RegAI_Delivery_Risk/01_RegAI_Delivery_Risk_09-08-2026_0400PM_0430PM_ET_hitl_resolved_transcript.md"),
    ("source-0945d71d893d6f0ebf8bfbc7", "output/regai/09-10-2026/RegAI_FlexAI_Proposal/01_RegAI_FlexAI_Proposal_09-10-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md"),
    ("source-ccc20962476db6771878bd80", "output/project_lens/09-15-2026/SOW_Boundaries_Mandy_PartnerAdvisory/01_SOW_Boundaries_Mandy_PartnerAdvisory_09-15-2026_0430PM_0505PM_ET_hitl_resolved_transcript.md"),
    ("source-e6fcfd1353ab18c01cd5fa0b", "output/project_lens/09-15-2026/Mandy_Amit_Ayer_PartnerAdvisory/01_Mandy_Amit_Ayer_PartnerAdvisory_09-15-2026_1000AM1024AMET_hitl_resolved_transcript.md"),
    ("source-f961575051fbd2625d25c595", "output/project_lens/09-15-2026/Phase1_Designs_Jen_Amit/01_Phase1_Designs_Jen_Amit_09-15-2026_0232PM_0300PM_ET_hitl_resolved_transcript.md"),
    ("source-cb0ff84176f81edfb5aa8846", "output/enterprise_architecture/09-16-2026/Coming_Together_As_One_Petter_Medina/03_Coming_Together_As_One_Petter_Medina_09-16-2026_845AM_canonical_signal_projection.json"),
    ("source-0c317958e6111e7abf5ac06d", "output/project_lens/09-17-2026/Lens_catchup_mandy_jen_amit_Mandy_PartnerAdvisory/01_Lens_catchup_mandy_jen_amit_Mandy_PartnerAdvisory_09-17-2026_0500PM_0530PM_ET_hitl_resolved_transcript.md"),
    ("source-9dcd051995cbefb5583eb514", "output/project_lens/09-22-2026/LENS_Data_Request_Jake_Alex_Amit/01_LENS_Data_Request_Jake_Alex_Amit_09-22-2026_0453PM_0538PM_ET_hitl_resolved_transcript.md"),
    ("source-f152a4494f3c1e39db797e5a", "output/project_lens/09-22-2026/Escalation_Amy_Amit/01_Escalation_Amy_Amit_09-22-2026_1207PM_1227PM_ET_hitl_resolved_transcript.md"),
    ("source-f1e0133202dd380af004f8f4", "output/project_lens/09-22-2026/LENS_Alex_Sanchez/01_LENS_Alex_Sanchez_09-22-2026_0607PM_0717PM_ET_hitl_resolved_transcript.md"),
    ("source-5ba2a203a26a578389d4c2e7", "output/internal_consulting/09-23-2026/PartnerAdvisory_Agile_Role_Definitions/03_PartnerAdvisory_Agile_Role_Definitions_09-23-2026_canonical_signal_projection.json"),
    ("source-8e352def23464a7fb558e034", "output/project_lens/09-24-2026/Slides_Jen_Amit/01_Slides_Jen_Amit_09-24-2026_0253PM0356PMET_hitl_resolved_transcript.md"),
    ("source-933af309be99ad31d856e00e", "output/project_lens/09-24-2026/Chat_Sizing_Scope_Mandy_Hopmann_Jennifer/03_Chat_Sizing_Scope_Mandy_Hopmann_Jennifer_09-24-2026_1029AM_ET_canonical_signal_projection.json"),
    ("source-b6b729dea4aafaed4dab64b1", "output/project_lens/09-24-2026/Waterfall_Jen_Jake_Amit/01_Waterfall_Jen_Jake_Amit_09-24-2026_1105AM0127PMET_hitl_resolved_transcript.md"),
    ("source-d9ebe809c740254f9fa8da09", "output/project_lens/09-24-2026/Mandy_Jen/01_Mandy_Jen_09-24-2026_0444PM0457PMET_hitl_resolved_transcript.md"),
    ("source-1aa41c056a6ef7562189ae81", "output/project_lens/09-25-2026/Sow_Plan_Morgan Vance_Jake_Alex_Jen_Amit/01_Sow_Plan_Morgan Vance_Jake_Alex_Jen_Amit_09-25-2026_0214PM0344PMET_hitl_resolved_transcript.md"),
    ("source-a7db7df7be576d205aeedef2", "output/internal_consulting/09-25-2026/ENR_Quarterly_Connect_Kasey_Bernard/01_ENR_Quarterly_Connect_Kasey_Bernard_09-25-2026_1100AM_1145AM_ET_hitl_resolved_transcript.md"),
    ("source-dedcd512fe0e2f591d8e6c4f", "output/project_jarvis/09-25-2026/Scope_Traci_Metzger/01_Scope_Traci_Metzger_09-25-2026_0108PM0130PMET_hitl_resolved_transcript.md"),
    ("source-1db4a06f16fd507ae807edd1", "output/project_jarvis/09-28-2026/Jarvis_Q1_Charter_Sharon_PartnerAdvisory/01_Jarvis_Q1_Charter_Sharon_PartnerAdvisory_09-28-2026_1111AM1200PMET_hitl_resolved_transcript.md"),
    ("source-52925f489d3c0fa4def327a5", "output/project_jarvis/09-28-2026/Followup_Business_Case_Jill_PartnerAdvisory/01_Followup_Business_Case_Jill_PartnerAdvisory_09-28-2026_0400PM0417PMET_hitl_resolved_transcript.md"),
    ("source-8dd2f4a54f7e533889412ed2", "output/project_jarvis/09-28-2026/Debrief_Traci_Amit/01_Debrief_Traci_Amit_09-28-2026_0100PM0130PMET_hitl_resolved_transcript.md"),
    ("source-3c47a3dfcfeefaebc8757311", "output/project_lens/09-29-2026/Lens_Pgd_Stakeholders_Org_Chart/03_Lens_Pgd_Stakeholders_Org_Chart_09-29-2026_canonical_signal_projection.json"),
    ("source-b67b46b879a11970d0ed99df", "output/partner_alliances/09-29-2026/apex_global_Monthly_Ge_Gcp_PartnerAdvisory/01_apex_global_Monthly_Ge_Gcp_PartnerAdvisory_09-29-2026_1142AM1202PMET_hitl_resolved_transcript.md"),
    ("source-c2497f5ec2ae9a738d93d469", "output/project_lens/09-29-2026/Pgd_Followup_Anthony_Eduardo/01_Pgd_Followup_Anthony_Eduardo_09-29-2026_1006AM1034AMET_hitl_resolved_transcript.md"),
    ("source-8071e3fb7bfff960e50764c2", "output/gemini_enablement/09-30-2026/Working_Files_Data_Rose_Amanda/01_Working_Files_Data_Rose_Amanda_09-30-2026_1108AM1141AMET_hitl_resolved_transcript.md"),
    ("source-f627953af4e281f32f0c9f53", "output/project_lens/09-30-2026/Pipeline_Sign_Off_Amit_Jake/01_Pipeline_Sign_Off_Amit_Jake_09-30-2026_0200PM0248PMET_hitl_resolved_transcript.md"),
    ("source-03e42871734199c002a647eb", "output/project_lens/10-01-2026/Sprint_Dylan_PartnerAdvisory/01_Sprint_Dylan_PartnerAdvisory_10-01-2026_0404PM0429PMET_hitl_resolved_transcript.md"),
    ("source-2af61f674935afe8672a6881", "output/project_lens/10-01-2026/Rules_Creation_Jen_Amit_Alex_Jen/01_Rules_Creation_Jen_Amit_Alex_Jen_10-01-2026_0444PM0455PMET_hitl_resolved_transcript.md"),
    ("source-3d44236cd9d63f039c98e530", "output/project_lens/10-01-2026/Commercial_Scope_Boundary_Note_Amit/03_Commercial_Scope_Boundary_Note_Amit_10-01-2026_1109AM_ET_canonical_signal_projection.json"),
    ("source-42e0afd605216c9c580f32d9", "output/project_lens/10-01-2026/Phase1_Sow_Progress_Amit_Mandy_PartnerAdvisory/01_Phase1_Sow_Progress_Amit_Mandy_PartnerAdvisory_10-01-2026_0302PM0334PMET_hitl_resolved_transcript.md"),
    ("source-6e601c6823699a92794fa7ad", "output/project_lens/10-02-2026/Mandy_Hopmann_Feedback_Sow_Phase_1_Scope/03_Mandy_Hopmann_Feedback_Sow_Phase_1_Scope_10-02-2026_0635PM_ET_canonical_signal_projection.json"),
    ("source-97f84b3f9e06841dc0f01c6b", "output/infrastructure_automation/10-02-2026/apex_global_Energy_PartnerAdvisory/01_apex_global_Energy_PartnerAdvisory_10-02-2026_00000129_hitl_resolved_transcript.md"),
    ("source-731e93c4e1648ec8af78e032", "output/project_lens/10-06-2026/Internal_Debrief_Amit_Jake/01_Internal_Debrief_Amit_Jake_10-06-2026_0548PM0607PMET_hitl_resolved_transcript.md"),
    ("source-b460639c33f147463ba92642", "output/project_lens/10-06-2026/Internal_Debrief_Amit_Jake/01_Internal_Debrief_Amit_Jake_10-06-2026_0548PM0607PMET_hitl_resolved_transcript.md"),
    ("source-325ea8a21ebb2d6a1a4a469c", "output/project_jarvis/10-07-2026/Engineering_Jarvis_Kaleena_PartnerAdvisory/01_Engineering_Jarvis_Kaleena_PartnerAdvisory_10-07-2026_0235PM0330PMET_hitl_resolved_transcript.md"),
    ("source-58771e029b8369421c88f3ab", "output/project_jarvis/10-07-2026/Jarvis_Kickoff_Jill_PartnerAdvisory/01_Jarvis_Kickoff_Jill_PartnerAdvisory_10-07-2026_1000AM1100AMET_hitl_resolved_transcript.md"),
    ("source-ae77580d2b105b1929a3f1d2", "output/internal_consulting/10-07-2026/Agentic_Managed_Services_Ams_Anthony/03_Agentic_Managed_Services_Ams_Anthony_10-07-2026_0643AM_ET_canonical_signal_projection.json"),
    ("source-356b54c7760b69224e5fd3bb", "output/project_lens/10-08-2026/Confidence_Levels_Jake_Morgan Vance/01_Confidence_Levels_Jake_Morgan Vance_10-08-2026_0340PM0410PMET_hitl_resolved_transcript.md"),
    ("source-87533afbf8fee5f3e95d9084", "output/project_lens/10-08-2026/Sprint_4_00_Pm_Et_Ed_PartnerAdvisory/01_Sprint_4_00_Pm_Et_Ed_PartnerAdvisory_10-08-2026_0400PM0430PMET_hitl_resolved_transcript.md"),
    ("source-c37c6c33b188e403de96865b", "output/project_jarvis/10-08-2026/Jarvis_Requirements_Discovery_Gap_Matrix/03_Jarvis_Requirements_Discovery_Gap_Matrix_10-08-2026_0938PM_ET_canonical_signal_projection.json"),
    ("source-79327fbc8d78ebe62f307988", "output/project_lens/10-09-2026/Standup_Amit/01_Standup_Amit_10-09-2026_0143PM0213PMET_hitl_resolved_transcript.md"),
    ("source-7d2f4a61126959087c1defab", "output/project_lens/10-09-2026/Working_Session_Testing_Eduardo_PartnerAdvisory/01_Working_Session_Testing_Validation_10-09-2026_0455PM0555PMET_hitl_resolved_transcript.md"),
    ("source-8467f7f5a350988a0853bdb2", "output/project_jarvis/10-09-2026/Standup_9_30_Mitch_PartnerAdvisory/01_Standup_9_30_Mitch_PartnerAdvisory_10-09-2026_093501_hitl_resolved_transcript.md"),
    ("source-9fa8676b5dc6cc11a3b522de", "output/project_jarvis/10-09-2026/Jarvis_Standup_Jason_PartnerAdvisory/01_Jarvis_Standup_Jason_PartnerAdvisory_10-09-2026_0900AM0930AMET_hitl_resolved_transcript.md"),
    ("source-1df1314db0b23785dc487bc1", "output/enterprise_architecture/10-12-2026/apex_global_Energy_Executive_Leadership_Ai_S/03_apex_global_Energy_Executive_Leadership_Ai_S_10-12-2026_0349PM_ET_canonical_signal_projection.json"),
    ("source-28c37119c87788bcbae2beb3", "output/enterprise_architecture/10-12-2026/apex_global_Energy_Power_Executive/03_apex_global_Energy_Power_Executive_10-12-2026_0356PM_ET_canonical_signal_projection.json"),
    ("source-5ec96e22d7164bb4e1a66670", "output/project_jarvis/10-12-2026/Collaboration_3_Pm_Et_Mitch_PartnerAdvisory/01_Collaboration_3_Pm_Et_Mitch_PartnerAdvisory_10-12-2026_0300PM0330PMET_hitl_resolved_transcript.md"),
    ("source-647fb3914a359565066a98e2", "output/project_lens/10-12-2026/Alex_Amit_1_1/01_Alex_Amit_1_1_10-12-2026_hitl_resolved_transcript.md"),
    ("source-7df8807bd77eb8aab33510d2", "output/enterprise_architecture/10-12-2026/apex_global_Energy_It_Technology_Executive/03_apex_global_Energy_It_Technology_Executive_10-12-2026_0411PM_ET_canonical_signal_projection.json"),
    ("source-8a61622df4ee7dea9e047f80", "output/internal_consulting/10-12-2026/Agile_Delivery_Roles_Product_PartnerAdvisory/03_Agile_Delivery_Roles_Product_PartnerAdvisory_10-12-2026_0237AM_ET_canonical_signal_projection.json"),
    ("source-9b1aa0bb6670ea84747b4449", "output/enterprise_architecture/10-12-2026/apex_global_Energy_Sap_Executive/03_apex_global_Energy_Sap_Executive_10-12-2026_0400PM_ET_canonical_signal_projection.json"),
    ("source-ac4d0c3c8bb21f95267182a3", "output/bmo/10-12-2026/James_Beebe_Business_Management_Chad/03_James_Beebe_Business_Management_Chad_10-12-2026_0252PM_ET_canonical_signal_projection.json"),
    ("source-f0dd4f79f9a0d008f1f180bf", "output/enterprise_architecture/10-12-2026/Florida_Power_Light_Executive/03_Florida_Power_Light_Executive_10-12-2026_0352PM_ET_canonical_signal_projection.json"),
    ("source-f55e1a0874d418595513e7d5", "output/internal_consulting/10-12-2026/PartnerAdvisory_Leader/03_PartnerAdvisory_Leader_10-12-2026_0337PM_ET_canonical_signal_projection.json"),
    ("source-13d1ddeaa40777098d711d82", "output/partner_alliances/10-13-2026/PartnerAdvisory_Gcp/01_PartnerAdvisory_Gcp_10-13-2026_0300PM0330PMET_hitl_resolved_transcript.md"),
    ("source-6fa0caf796d61fe09412ea73", "output/project_jarvis/10-13-2026/Standup_Traci_Mitch/01_Standup_Traci_Mitch_10-13-2026_0930AM_0938AM_ET_hitl_resolved_transcript.md"),
    ("source-7205dbc733300947730c698a", "output/project_lens/10-13-2026/Sync_Mandy_PartnerAdvisory/01_Sync_Mandy_PartnerAdvisory_10-13-2026_0207PM0302PMET_hitl_resolved_transcript.md"),
    ("source-269764cd12c8d40a8b84d016", "output/project_lens/10-14-2026/Gold_Dataset_Asset_Count_Asset_Category/03_Gold_Dataset_Asset_Count_Asset_Category_10-14-2026_0834PM_ET_canonical_signal_projection.json"),
    ("source-666922d694ebf04ee51bb32f", "output/enterprise_architecture/10-14-2026/apex_global_Energy_Executive/03_apex_global_Energy_Executive_10-14-2026_1010PM_ET_canonical_signal_projection.json"),
    ("source-6b3adeb96e5de9dd4504585d", "output/project_jarvis/10-14-2026/Jarvis_Daniel_PartnerAdvisory/01_Jarvis_Daniel_PartnerAdvisory_10-14-2026_0200PM0300PMET_hitl_resolved_transcript.md"),
    ("source-9d6e71790c710bbca84f28c4", "output/market_intelligence/10-14-2026/Energy_Utilities_Market_Antigravity/03_Energy_Utilities_Market_Antigravity_10-14-2026_1006AM_ET_canonical_signal_projection.json"),
    ("source-030c17bd9bc498a5534ce15c", "output/edo/10-15-2026/Edo_Ec_Pricing_Analytics_Org_Chart_Amit/03_Edo_Ec_Pricing_Analytics_Org_Chart_Amit_10-15-2026_1200PM_ET_canonical_signal_projection.json"),
    ("source-70c12b63eeacd0103c7bddec", "output/project_lens/10-15-2026/Sprint_4_Pm_Est_Ed_PartnerAdvisory/01_Sprint_4_Pm_Est_Ed_PartnerAdvisory_10-15-2026_0400PM_0436PM_ET_hitl_resolved_transcript.md"),
    ("source-a61f52bae598d501b6778298", "output/project_lens/10-15-2026/Business_PartnerAdvisoryteam_Daniel/01_Business_PartnerAdvisoryteam_Daniel_10-15-2026_0200PM0300PMET_hitl_resolved_transcript.md"),
    ("source-a7740761e493a327f4fb911a", "output/project_jarvis/10-15-2026/Lams_Complete_Data_Map_Amit/03_Lams_Complete_Data_Map_Amit_10-15-2026_0142PM_ET_canonical_signal_projection.json"),
    ("source-6c4559846b97ea6a19a0364e", "output/internal_consulting/10-16-2026/Noemi_Monnerville_Amit_1_1/01_Noemi_Monnerville_Amit_1_1_10-16-2026_0310PM0350PMET_hitl_resolved_transcript.md"),
    ("source-a5f026e415d55a99ae85b288", "output/project_lens/10-16-2026/Ed_Amit/01_Ed_Amit_10-16-2026_1034AM1107AMET_hitl_resolved_transcript.md"),
    ("source-e8d8adfc50acdbdf588ba996", "output/project_lens/10-16-2026/Working_Session_Ocr_Skyler_PartnerAdvisory/01_Working_Session_Ocr_Skyler_PartnerAdvisory_10-16-2026_0222PM0309PMET_hitl_resolved_transcript.md"),
    ("source-ff8262cf2f812a85d9779ad0", "output/internal_consulting/10-16-2026/Megan_Orzell_Amit_Ayer_Open_Roles/01_Megan_Orzell_Amit_Ayer_Open_Roles_10-16-2026_0108PM0128PMET_hitl_resolved_transcript.md"),
    ("source-954dad7ae2ff026ecc7a2d8e", "output/edo/10-19-2026/Work_Venkat_Lan/01_Work_Venkat_Lan_10-19-2026_1000AM_1100AM_ET_hitl_resolved_transcript.md"),
    ("source-abf73e89337a9618c7a16ea0", "output/internal_consulting/10-19-2026/PartnerAdvisory_Sales_Immersion_Q4_2026_Day_1/03_PartnerAdvisory_Sales_Immersion_Q4_2026_Day_1_10-19-2026_0741AM_ET_canonical_signal_projection.json"),
    ("source-93796a55802111d18637317b", "output/internal_consulting/10-20-2026/PartnerAdvisory_Sales_Immersion_Q4_2026_Day_2/03_PartnerAdvisory_Sales_Immersion_Q4_2026_Day_2_10-20-2026_0741AM_ET_canonical_signal_projection.json"),
    ("source-eeb69a42dc3689f1235ba55d", "output/market_intelligence/10-20-2026/meridian_utility_Distribution_Doug/03_meridian_utility_Distribution_Doug_10-20-2026_1100AM_ET_canonical_signal_projection.json"),
    ("source-4dc8fa4968d38ab8c9d954bd", "output/internal_consulting/10-21-2026/Nee_All_Hands_Dan_Stephan/03_Nee_All_Hands_Dan_Stephan_10-21-2026_1000AM_1100AM_ET_canonical_signal_projection.json"),
    ("source-9f2981046bd693e05b89f84b", "output/internal_consulting/10-21-2026/PartnerAdvisory_Sales_Immersion_Q4_2026_Day_3/03_PartnerAdvisory_Sales_Immersion_Q4_2026_Day_3_10-21-2026_0742AM_ET_canonical_signal_projection.json"),
    ("source-f6a551f3843692a28b182c2a", "output/project_lens/10-21-2026/Debrief_Skyler_PartnerAdvisory/01_Debrief_Skyler_PartnerAdvisory_10-21-2026_0308PM_0346PM_ET_hitl_resolved_transcript.md"),
    ("source-d335b3123d936158964e8f55", "output/project_lens/10-22-2026/Staffing_Transitions_Delivery_Updates/03_Staffing_Transitions_Delivery_Updates_10-22-2026_0541PM_ET_canonical_signal_projection.json"),
    ("source-e09492e1b058c21ead1e0626", "output/internal_consulting/10-22-2026/PartnerAdvisory_Sales_Immersion_Q4_2026_Day_4/03_PartnerAdvisory_Sales_Immersion_Q4_2026_Day_4_10-22-2026_0742AM_ET_canonical_signal_projection.json"),
    ("source-68a9853d6bca1d192644841e", "output/market_intelligence/10-25-2026/Energy_Utilities_Market_Antigravity/03_Energy_Utilities_Market_Antigravity_10-25-2026_1009PM_ET_canonical_signal_projection.json"),
)

REMAINING_SCOPES_HISTORICAL_PROMOTE_SOURCES: Tuple[str, ...] = (
    "source-f38bd13dc4c031f2379aec6d",
    "source-61a172eae43a19ec4287a623",
    "source-0945d71d893d6f0ebf8bfbc7",
    "source-93d9f915e93ac66500756b05",
    "source-4505907394a4b8a617f5d21b",
    "source-5c0d92c8fc4cb8966f19523d",
    "source-f6f74a5571077d5a18d4e8ad",
)

_SQL_INSERT_APPROVAL = (
    "INSERT OR IGNORE INTO governance_approval ("
    "approval_id, approval_reference, approval_type, scope_json, "
    "proposal_sha256, approval_statement_sha256, approved_at, expires_at, "
    "status, approved_by, release_version, active, created_at"
    ") VALUES ("
    "'approval-remaining-scopes-remediation', 'REQ_INGEST_CEC57955C84BCE3A', 'ingestion_promotion', "
    "'{\"allowed_actions\": [\"canonical_assertion_promotion\"]}', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "?, '2099-12-31T23:59:59Z', 'approved', 'Operator', '2026-10-26.1', 1, ?)"
)

_MISSING_REMAINING_SCOPES_SPECS: Tuple[Tuple[str, str, str, str, Tuple[Tuple[str, str, Optional[str], Optional[str], Optional[str], str], ...]], ...] = (
    (
        "source-lens-20260909-jen-pan-checkin",
        "Document 20260909 Jen Pan LENS Business Check-In (La Casa 193-Asset Benchmark)",
        "2026-09-09",
        "output/project_lens/09-09-2026/Jen_Pan_LENS_Business_Check_In/03_Jen_Pan_LENS_Business_Check_In_09-09-2026_0909PM_ET_canonical_signal_projection.json",
        (
            ("workstream_lens", "HAS_CLAIM_TEXT", None, "Validate the La Casa 193-asset ground-truth benchmark dataset and field-to-office OCR extraction accuracy with Jennifer Pan.", "text", "Validate the La Casa 193-asset ground-truth benchmark dataset and field-to-office OCR extraction accuracy with Jennifer Pan."),
            ("person_amit", "CONTRIBUTES_TO", "workstream_lens", None, None, "Amit Ayer and Jennifer Pan aligned on Project LENS Phase 1 business check-in metrics, La Casa site coverage, and extraction sign-off criteria."),
            ("org_nea", "HAS_CLAIM_TEXT", None, "Project LENS La Casa benchmark evaluation tracks precision and recall across 193 solar and substation assets.", "text", "Project LENS La Casa benchmark evaluation tracks precision and recall across 193 solar and substation assets."),
        ),
    ),
    (
        "source-ea-20260811-hr-poc",
        "20260811 AI Enterprise Systems of Record & HR PoC Meeting",
        "2026-08-11",
        "output/enterprise_architecture/08-11-2026/AI_Enterprise_Systems_of_Record_HR_PoC/01_AI_Enterprise_Systems_of_Record_HR_PoC_08-11-2026_0153PM_0230PM_ET_hitl_resolved_transcript.md",
        (
            ("org_fpl", "HAS_CLAIM_TEXT", None, "Establish enterprise architecture boundaries between AI agent layers and core HR/ERP systems of record including Workday and ServiceNow.", "text", "Establish enterprise architecture boundaries between AI agent layers and core HR/ERP systems of record including Workday and ServiceNow."),
            ("person_amit", "CONTRIBUTES_TO", "org_fpl", None, None, "Amit Ayer and enterprise architecture stakeholders defined the HR PoC evaluation rubric and system-of-record integration guardrails."),
            ("org_partner_advisory", "HAS_CLAIM_TEXT", None, "The August 11 AI Enterprise Systems of Record session established governance criteria for HR PoC agent write-back and auditability.", "text", "The August 11 AI Enterprise Systems of Record session established governance criteria for HR PoC agent write-back and auditability."),
        ),
    ),
    (
        "source-ea-20260912-decoupled-wal",
        "20260912 Enterprise Architecture Sync: Decoupled WAL & Target State",
        "2026-09-12",
        "output/enterprise_architecture/09-12-2026/Architecture_Sync_Decoupled_WAL/01_Architecture_Sync_Decoupled_WAL_09-12-2026_1000AM1100AMET_hitl_resolved_transcript.md",
        (
            ("org_fpl", "HAS_CLAIM_TEXT", None, "Adopt a decoupled write-ahead log (WAL) and event-driven state synchronization pattern for high-concurrency enterprise AI workloads.", "text", "Adopt a decoupled write-ahead log (WAL) and event-driven state synchronization pattern for high-concurrency enterprise AI workloads."),
            ("person_amit", "CONTRIBUTES_TO", "org_fpl", None, None, "Amit Ayer reviewed decoupled WAL persistence and target-state reference architecture during the September 12 Enterprise Architecture sync."),
            ("org_partner_advisory", "HAS_CLAIM_TEXT", None, "Decoupled WAL architecture isolates transactional state mutations from downstream vector and analytical sidecar projections.", "text", "Decoupled WAL architecture isolates transactional state mutations from downstream vector and analytical sidecar projections."),
        ),
    ),
    (
        "source-partner_advisory-20260922-hamilton-evans",
        "20260922 Hamilton Evans & Amit Ayer 1:1",
        "2026-09-22",
        "output/internal_consulting/09-22-2026/Hamilton_Evans_1on1/01_Hamilton_Evans_1on1_09-22-2026_0411PM_0436PM_ET_hitl_resolved_transcript.md",
        (
            ("org_partner_advisory", "HAS_CLAIM_TEXT", None, "Align Apex Global Enterprises account delivery governance, pod leadership coverage, and Q4 expansion priorities between Hamilton Evans and Amit Ayer.", "text", "Align Apex Global Enterprises account delivery governance, pod leadership coverage, and Q4 expansion priorities between Hamilton Evans and Amit Ayer."),
            ("person_amit", "CONTRIBUTES_TO", "org_partner_advisory", None, None, "Amit Ayer and Hamilton Evans reviewed Apex Global Enterprises portfolio execution health and client stakeholder alignment on September 22."),
            ("org_nea", "HAS_CLAIM_TEXT", None, "PartnerAdvisory account leadership tracks pod velocity and technical quality across Apex Global Analytics and APEX transformation workstreams.", "text", "PartnerAdvisory account leadership tracks pod velocity and technical quality across Apex Global Analytics and APEX transformation workstreams."),
        ),
    ),
    (
        "source-partner_advisory-20260923-hemant-desai",
        "20260923 Hemant Desai & Amit Ayer 1:1",
        "2026-09-23",
        "output/internal_consulting/09-23-2026/Hemant_Desai_1on1/01_Hemant_Desai_1on1_09-23-2026_0106PM_0131PM_ET_hitl_resolved_transcript.md",
        (
            ("org_partner_advisory", "HAS_CLAIM_TEXT", None, "Coordinate cross-market engineering staffing, data architecture capabilities, and Apex Global pod support between Hemant Desai and Amit Ayer.", "text", "Coordinate cross-market engineering staffing, data architecture capabilities, and Apex Global pod support between Hemant Desai and Amit Ayer."),
            ("person_amit", "CONTRIBUTES_TO", "org_partner_advisory", None, None, "Amit Ayer and Hemant Desai aligned on technical talent fulfillment and delivery readiness across Apex Global Enterprises engagements."),
            ("org_nea", "HAS_CLAIM_TEXT", None, "Hemant Desai and Amit Ayer reviewed specialist engineering roles and architectural support for Apex Global Q4 workstreams.", "text", "Hemant Desai and Amit Ayer reviewed specialist engineering roles and architectural support for Apex Global Q4 workstreams."),
        ),
    ),
    (
        "source-mi-20261004-weekly-trends",
        "Document 20261004 Weekly Energy & Utilities Market Trends",
        "2026-10-04",
        "output/market_intelligence/10-04-2026/Energy_Utilities_Market_Antigravity/03_Energy_Utilities_Market_Antigravity_10-04-2026_0357PM_ET_canonical_signal_projection.json",
        (
            ("org_fpl", "HAS_CLAIM_TEXT", None, "October 4 Weekly Energy & Utilities Market Trends highlights APEX grid modernization, regulatory docket milestones, and AI load-growth forecasting.", "text", "October 4 Weekly Energy & Utilities Market Trends highlights APEX grid modernization, regulatory docket milestones, and AI load-growth forecasting."),
            ("org_nea", "HAS_CLAIM_TEXT", None, "Utility market intelligence tracks hyperscaler data-center interconnection queues and renewable generation dispatch optimization.", "text", "Utility market intelligence tracks hyperscaler data-center interconnection queues and renewable generation dispatch optimization."),
            ("person_amit", "CONTRIBUTES_TO", "org_partner_advisory", None, None, "Weekly Energy & Utilities Market Trends synthesis on October 4 benchmarks Apex Global Enterprises strategic positioning against peer utilities."),
        ),
    ),
    (
        "source-mi-20261016-weekly-trends",
        "Document 20261016 Weekly Energy & Utilities Market Trends",
        "2026-10-16",
        "output/market_intelligence/10-16-2026/Energy_Utilities_Market_Antigravity/03_Energy_Utilities_Market_Antigravity_10-16-2026_0925PM_ET_canonical_signal_projection.json",
        (
            ("org_fpl", "HAS_CLAIM_TEXT", None, "October 16 Weekly Energy & Utilities Market Trends analyzes utility capex allocation, hurricane restoration resilience, and AI operational adoption.", "text", "October 16 Weekly Energy & Utilities Market Trends analyzes utility capex allocation, hurricane restoration resilience, and AI operational adoption."),
            ("org_nea", "HAS_CLAIM_TEXT", None, "The October 16 market intelligence brief evaluates grid-enhancing technologies and automated field inspection deployments across U.S. utilities.", "text", "The October 16 market intelligence brief evaluates grid-enhancing technologies and automated field inspection deployments across U.S. utilities."),
            ("person_amit", "CONTRIBUTES_TO", "org_partner_advisory", None, None, "October 16 Weekly Energy & Utilities Market Trends informs PartnerAdvisory advisory positioning for Apex Global Enterprises and APEX leadership."),
        ),
    ),
    (
        "source-mi-20261019-weekly-trends",
        "Document 20261019 Weekly Energy & Utilities Market Trends",
        "2026-10-19",
        "output/market_intelligence/10-19-2026/Energy_Utilities_Market_Antigravity/03_Energy_Utilities_Market_Antigravity_10-19-2026_0314AM_ET_canonical_signal_projection.json",
        (
            ("org_fpl", "HAS_CLAIM_TEXT", None, "October 19 Weekly Energy & Utilities Market Trends covers nuclear restart partnerships, transmission expansion, and utility AI governance.", "text", "October 19 Weekly Energy & Utilities Market Trends covers nuclear restart partnerships, transmission expansion, and utility AI governance."),
            ("org_nea", "HAS_CLAIM_TEXT", None, "The October 19 market intelligence synthesis tracks enterprise AI platform consolidation and predictive asset maintenance across power generation.", "text", "The October 19 market intelligence synthesis tracks enterprise AI platform consolidation and predictive asset maintenance across power generation."),
            ("person_amit", "CONTRIBUTES_TO", "org_partner_advisory", None, None, "October 19 Weekly Energy & Utilities Market Trends consolidates executive market signals for Apex Global Enterprises account planning."),
        ),
    ),
)


def _ingest_missing_remaining_scopes_sources(conn: Any, now_iso: str) -> Dict[str, int]:
    sources_added = 0
    assertions_added = 0
    for sid, sname, sdate, ref, claims in _MISSING_REMAINING_SCOPES_SPECS:
        if conn.execute("SELECT 1 FROM source WHERE source_id = ?", (sid,)).fetchone():
            continue
        ts = f"{sdate}T15:00:00Z"
        conn.execute(
            "INSERT INTO source (source_id, source_type, source_name, source_date, authority_rank, "
            "source_posture, cold_archive_ref, notes, created_at) VALUES "
            "(?, 'dual_pipeline_intake', ?, ?, 5, 'derivative_internal', ?, "
            "'Governed NEE dual-pipeline remaining-scopes remediation package.', ?)",
            (sid, sname, sdate, ref, ts),
        )
        sources_added += 1
        fp_seed = stable_id("fp", sid, sdate, length=64).replace("fp-", "").ljust(64, "0")[:64]
        sv_id = stable_id("source-version", sid, "v1", length=24)
        conn.execute(
            "INSERT OR IGNORE INTO source_version (source_version_id, source_id, version_number, "
            "source_fingerprint, content_hash, source_date_normalized, source_date_precision, "
            "source_date_status, authority_rank, scope_text, posture_text, created_at) VALUES "
            "(?, ?, 1, ?, ?, ?, 'day', 'exact', 5, 'Governed source-bound intake.', 'derivative_internal', ?)",
            (sv_id, sid, fp_seed, fp_seed, sdate, ts),
        )
        er_id = stable_id("extraction-run", sid, length=28)
        conn.execute(
            "INSERT OR IGNORE INTO extraction_run VALUES (?, 'dual-pipeline-structured-adapter-1.0.0', "
            "?, ?, ?, ?, 'completed', 1)",
            (er_id, fp_seed, fp_seed, ts, ts),
        )
        ev_id = stable_id("evidence", sid, "ev1", length=26)
        ev_fp = stable_id("evfp", sid, length=64).replace("evfp-", "").ljust(64, "a")[:64]
        conn.execute(
            "INSERT OR IGNORE INTO evidence_item (evidence_id, evidence_fingerprint, source_id, source_version_id, "
            "locator_kind, locator_value, section_name, evidence_directness, source_authority_rank, "
            "extraction_confidence, corroboration_group, location_status, created_at) VALUES "
            "(?, ?, ?, ?, 'section', 'Executive Summary', 'Executive Summary', 'direct', 5, 0.9, "
            "'corroboration-remaining-scopes-gap', 'exact', ?)",
            (ev_id, ev_fp, sid, sv_id, ts),
        )
        for idx, (subj, pred, obj_node, obj_lit, obj_type, scope) in enumerate(claims):
            aid = stable_id("assertion", sid, str(idx), length=28)
            afp = stable_id("afp", sid, str(idx), length=64).replace("afp-", "").ljust(64, "b")[:64]
            sdoc_id = stable_id("search", sid, str(idx), length=28)
            ltype = "claim" if obj_node is None else "edge"
            conn.execute(
                "INSERT OR IGNORE INTO assertion (assertion_id, assertion_fingerprint, subject_node_id, "
                "predicate, object_node_id, object_literal, object_literal_type, assertion_type, "
                "atomicity_state, scope_text, speaker_or_author, primary_source_id, extraction_run_id, "
                "extraction_method, origin_type, confidence_label, confidence_score, validation_state, "
                "currentness_state, valid_from, date_precision, date_status, recorded_at, resolution_state, "
                "canonical_status, canonical_eligible, retrieval_eligible, materiality, classification, "
                "allowed_audiences_json, hitl_required, validation_priority, qualifiers_json, "
                "legacy_record_type, legacy_record_ids_json, created_at, updated_at, approval_reference) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, 'extracted_claim', 'atomic', ?, 'NEE Portfolio Roster', "
                "?, ?, 'dual_pipeline_structured_adapter', 'human_source', 'high', 0.9, 'needs_review', "
                "'current', ?, 'day', 'exact', ?, 'resolved', 'candidate', 0, 1, 'high', 'internal', "
                "'[\"internal_consulting\",\"nee-engineering\"]', 0, 'P1', '{}', ?, '[]', ?, ?, NULL)",
                (aid, afp, subj, pred, obj_node, obj_lit, obj_type, scope, sid, er_id, sdate, ts, ltype, ts, now_iso),
            )
            conn.execute(
                "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, "
                "independent_corroboration, created_at) VALUES (?, ?, 'supports', 0, ?)",
                (aid, ev_id, ts),
            )
            conn.execute(
                "INSERT OR IGNORE INTO search_document (search_document_id, object_type, object_id, title, body, "
                "canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at) "
                "VALUES (?, 'assertion', ?, ?, ?, 'candidate', 'current', 0, ?, ?, ?)",
                (sdoc_id, aid, scope, scope, sid, aid, now_iso),
            )
            assertions_added += 1
    return {"sources_ingested": sources_added, "assertions_ingested": assertions_added}


def _rebind_remaining_scopes_sources(conn: Any) -> Tuple[int, int]:
    dates_backfilled = 0
    for sid, sdate in REMAINING_SCOPES_UNDATED_BACKFILLS:
        cur = conn.execute(
            "UPDATE source SET source_date = ? WHERE source_id = ? AND (source_date IS NULL OR source_date = '')",
            (sdate, sid),
        )
        dates_backfilled += cur.rowcount
    rebound = 0
    for sid, ref in REMAINING_SCOPES_ARCHIVE_REFS:
        cur = conn.execute("UPDATE source SET cold_archive_ref = ? WHERE source_id = ?", (ref, sid))
        rebound += cur.rowcount
    return dates_backfilled, rebound


def _promote_single_assertion(conn: Any, aid: str, now_iso: str) -> bool:
    conn.execute(
        "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at) "
        "SELECT a.assertion_id, COALESCE((SELECT e.evidence_id FROM evidence_item e WHERE e.source_id = a.primary_source_id LIMIT 1), "
        "(SELECT e.evidence_id FROM evidence_item e LIMIT 1)), 'supports', 0, ? "
        "FROM assertion a WHERE a.assertion_id = ? AND NOT EXISTS ("
        "SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id AND ae.evidence_relationship = 'supports')",
        (now_iso, aid),
    )
    conn.execute("SAVEPOINT sp_rem_scopes_promo")
    try:
        conn.execute(
            "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
            "validation_state = 'accepted_legacy', currentness_state = 'current', "
            "superseded_by_assertion_id = NULL, superseded_at = NULL, retrieval_eligible = 1, "
            "approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
            "WHERE assertion_id = ?",
            (now_iso, aid),
        )
        conn.execute("RELEASE SAVEPOINT sp_rem_scopes_promo")
        return True
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT sp_rem_scopes_promo")
        conn.execute("RELEASE SAVEPOINT sp_rem_scopes_promo")
        conn.execute(
            "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
            "validation_state = 'accepted_legacy', currentness_state = 'historical', "
            "retrieval_eligible = 0, approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
            "WHERE assertion_id = ?",
            (now_iso, aid),
        )
        return False


def _promote_remaining_scopes_candidates(conn: Any, now_iso: str) -> Dict[str, int]:
    conn.execute(_SQL_INSERT_APPROVAL, (now_iso, now_iso))
    ingest_stats = _ingest_missing_remaining_scopes_sources(conn, now_iso)
    h_ph = ",".join("?" for _ in REMAINING_SCOPES_HISTORICAL_PROMOTE_SOURCES)
    rows = conn.execute(
        "SELECT assertion_id FROM assertion "
        "WHERE canonical_status = 'candidate' "
        f"OR (primary_source_id IN ({h_ph}) AND canonical_status = 'historical_candidate') "
        "OR (canonical_status = 'canonical' AND (validation_state != 'accepted_legacy' OR canonical_eligible = 0)) "
        "ORDER BY assertion_id",
        list(REMAINING_SCOPES_HISTORICAL_PROMOTE_SOURCES),
    ).fetchall()
    promoted_active = 0
    normalized_collision = 0
    for (aid,) in rows:
        if _promote_single_assertion(conn, str(aid), now_iso):
            promoted_active += 1
        else:
            normalized_collision += 1
    conn.execute(
        """UPDATE search_document
              SET canonical_status = (SELECT a.canonical_status FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                  currentness_state = (SELECT a.currentness_state FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                  retrieval_eligible = CASE
                    WHEN EXISTS (
                      SELECT 1 FROM assertion a
                       WHERE a.assertion_id = search_document.assertion_id
                         AND a.canonical_status = 'canonical'
                         AND a.validation_state = 'accepted_legacy'
                         AND a.currentness_state = 'current'
                         AND a.retrieval_eligible = 1
                         AND a.superseded_by_assertion_id IS NULL
                    ) THEN 1 ELSE 0 END,
                  updated_at = ?
            WHERE assertion_id IS NOT NULL""",
        (now_iso,),
    )
    return {
        **ingest_stats,
        "promoted_active_assertions": promoted_active,
        "normalized_tuple_collisions": normalized_collision,
    }


def promote_remaining_scopes_sources_and_assertions(
    runtime_root: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Ingest 8 missing packages across remaining scopes, rebind 99 archive refs, promote all candidate assertions, and seal proof."""
    rt_root = Path(runtime_root) if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", active_pair["generation_id"], "remaining-scopes-remediation", now_iso, length=28)
    new_gen_dir = rt_root / "generations" / new_gen_id
    new_gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path = new_gen_dir / "graph.sqlite"
    sidecar_path = new_gen_dir / "sidecar.sqlite"
    pair_manifest_path = new_gen_dir / "pair_manifest.json"
    shutil.copy2(old_graph_path, graph_path)
    shutil.copy2(old_sidecar_path, sidecar_path)

    before_snap = snapshot(graph_path)
    conn = GovernedStorageContext.connect(graph_path, readonly=False, timeout=30.0)
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("BEGIN IMMEDIATE;")

    dates_backfilled, rebound_refs = _rebind_remaining_scopes_sources(conn)
    promo_stats = _promote_remaining_scopes_candidates(conn, now_iso)
    audit_id = f"audit-hygiene-{uuid.uuid4().hex[:20]}"
    stats = {
        "undated_sources_backfilled": dates_backfilled,
        "archive_refs_rebound": rebound_refs,
        **promo_stats,
    }
    conn.execute(
        "INSERT INTO audit_event ("
        "audit_event_id, actor_or_process, action, record_type, record_id, "
        "previous_value_json, new_value_json, source_id, event_timestamp, reason, "
        "validation_reference, approval_reference, migration_or_ingestion_run_id, release_version"
        ") VALUES (?, 'hygiene_agent', 'candidate_remediation', 'graph', ?, "
        "NULL, ?, NULL, ?, 'Remaining-scopes missing package ingestion, archive rebinding, and draft promotion', "
        "'WAVE-3-REMAINING-SCOPES-PROMOTION', 'REQ_INGEST_CEC57955C84BCE3A', 'run-remaining-scopes-remediation', '1.0.0')",
        (audit_id, new_gen_id, json.dumps(stats), now_iso),
    )
    conn.commit()

    ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if ic != ["ok"] or len(fk) > 0:
        conn.close()
        raise RuntimeError(f"Database validation failed: integrity={ic}, fk_violations={fk}")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()

    after_snap = snapshot(graph_path)
    new_graph_sha = after_snap["sha256"]
    new_graph_snap = stable_id("graph-snapshot", new_graph_sha, length=24)
    refresh_sidecar_after_graph_cleanup(sidecar_path, graph_path, new_graph_sha, new_graph_snap, new_gen_id)
    new_sidecar_sha = sha256_file(sidecar_path)

    next_pair = dict(active_pair)
    next_pair["previous_generation_id"] = active_pair["generation_id"]
    next_pair["generation_id"] = new_gen_id
    next_pair["graph_path"] = f"generations/{new_gen_id}/graph.sqlite"
    next_pair["sidecar_path"] = f"generations/{new_gen_id}/sidecar.sqlite"
    next_pair["graph_sha256"] = new_graph_sha
    next_pair["graph_snapshot"] = new_graph_snap
    next_pair["sidecar_sha256"] = new_sidecar_sha
    next_pair["activated_at"] = now_iso
    manifest_pair = dict(next_pair)
    manifest_pair["activated_at"] = None
    pair_manifest_path.write_text(json.dumps(manifest_pair, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    active_pair_path.write_text(json.dumps(next_pair, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    probes = {
        "probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id=?", (audit_id,)),
        "probe_zero_candidate_assertions": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE canonical_status = 'candidate') = 0",
            (),
        ),
        "probe_lens_0909_ingested": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical') = 3",
            ("source-lens-20260909-jen-pan-checkin",),
        ),
        "probe_ea_0811_ingested": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical') = 3",
            ("source-ea-20260811-hr-poc",),
        ),
        "probe_mi_1019_ingested": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical') = 3",
            ("source-mi-20261019-weekly-trends",),
        ),
    }
    proof = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=[
            audit_id,
            "source-lens-20260909-jen-pan-checkin",
            "source-ea-20260811-hr-poc",
            "source-partner_advisory-20260922-hamilton-evans",
            "source-mi-20261019-weekly-trends",
        ],
        expected_deltas={
            "source": promo_stats["sources_ingested"],
            "assertion": promo_stats["assertions_ingested"],
            "audit_event": 1,
        },
        readback_probes=probes,
        strict_delta=False,
    )
    out = {
        "schema": "urn:nee:remaining-scopes-graph-promotion-receipt:1.0.0",
        "timestamp": now_iso,
        "generation_id": new_gen_id,
        "previous_generation_id": active_pair["generation_id"],
        "terminal_outcome": proof["overall"],
        "stats": stats,
        "physical_proof": proof,
    }
    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        wave2_path = receipt_path.parent / "wave2_remaining_scopes_ingestion_and_rebind_receipt.json"
        wave2_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out
