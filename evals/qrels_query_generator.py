"""BGE-M3 QRELS Query Generator & Cosine Stratification (Wave 3A)

Constructs a 3-tier cosine-stratified query benchmark grounded in real
corpus artifacts: Project Atlas / Nexus transcripts, SOWs, Architecture blueprints,
and canonical cards.

Tiers:
- Tier 1: Semantic Paraphrase Clusters (10 topics x 3 variants = 30 queries, cos >= 0.85 target)
- Tier 2: Cross-Domain Orthogonal Probes (10 queries across distinct domains, cos <= 0.45 target)
- Tier 3: Hard-Negative Confounder Traps (10 queries designed to catch lexical traps)

Total: 50 Grounded Decision Queries.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from dual_pipeline.index import get_backend

# ---------------------------------------------------------------------------
# Query Definitions Grounded in Real Corpus Artifacts
# ---------------------------------------------------------------------------

GROUNDED_QUERY_DEFINITIONS: list[dict[str, Any]] = [
    # -----------------------------------------------------------------------
    # TIER 1: SEMANTIC PARAPHRASE CLUSTERS (10 Topics x 3 Variants)
    # -----------------------------------------------------------------------
    {
        "topic_id": "topic_01_fpl_land_management",
        "topic_name": "APEX Land Management & Rate Case Scope Boundaries",
        "tier": "tier1_paraphrase_cluster",
        "domain": "workstream_project_nexus",
        "grounding_source": "Project_Atlas_Transcript_Attributed_08.28.2026.md & 02_executive_recap.md",
        "ground_truth_summary": (
            "APEX manages 200,000+ acres on physical paper stacks; rate case advisory "
            "halted land prospecting (264k acre inventory through 2030); LCDA is the "
            "custom system of record post-2021 rate case, not Primavera P6."
        ),
        "target_entities": ["Sharon Diaram", "LCDA", "Reid Schenk", "APEX Development"],
        "variants": [
            {
                "variant_id": "t1_01_exec",
                "phrasing_type": "executive",
                "query_text": "What are the primary operational bottlenecks in APEX's land management workflow following the recent rate case?",
            },
            {
                "variant_id": "t1_01_eng",
                "phrasing_type": "engineering",
                "query_text": "Why is land prospecting excluded from Q4 Nexus scope and what system holds the actual land records?",
            },
            {
                "variant_id": "t1_01_kw",
                "phrasing_type": "shorthand",
                "query_text": "APEX land management paper disaster LCDA rate case acreage Sharon Diaram",
            },
        ],
    },
    {
        "topic_id": "topic_02_data_center_siting",
        "topic_name": "Nexus Data Center Siting & Transmission Line Blockers",
        "tier": "tier1_paraphrase_cluster",
        "domain": "workstream_project_nexus",
        "grounding_source": "02_executive_recap.md (Executive direction 'fifth floor')",
        "ground_truth_summary": (
            "Executive leadership directed 3x data center locations; candidate sites face "
            "transmission lines not planned until 2034, creating urgent demand for AI siting tool."
        ),
        "target_entities": ["Reid Schenk", "Interconnection.AI", "APEX Development"],
        "variants": [
            {
                "variant_id": "t1_02_exec",
                "phrasing_type": "executive",
                "query_text": "What executive leadership directive is accelerating data center siting analysis and where are the transmission bottlenecks?",
            },
            {
                "variant_id": "t1_02_eng",
                "phrasing_type": "engineering",
                "query_text": "How does the 2034 transmission line timeline constrain APEX data center site interconnection?",
            },
            {
                "variant_id": "t1_02_kw",
                "phrasing_type": "shorthand",
                "query_text": "APEX data center tripling 2034 transmission line interconnection constraint siting",
            },
        ],
    },
    {
        "topic_id": "topic_03_q3_sow_commercial_bounds",
        "topic_name": "Q3 SOW Commercial Bounds & Capacity Resourcing Guardrails",
        "tier": "tier1_paraphrase_cluster",
        "domain": "commercial_governance",
        "grounding_source": "Card NEE_03, Card NEE_04, SOW assertions",
        "ground_truth_summary": (
            "Q3 NEA SOW is fixed-capacity resourcing, not a fixed-output guarantee; PartnerAdvisory proposal "
            "rate, team size, and timing cannot be treated as signed scope without formal change orders."
        ),
        "target_entities": ["card:NEE_03", "card:NEE_04", "risk:Q3 fixed-capacity overclaim"],
        "variants": [
            {
                "variant_id": "t1_03_exec",
                "phrasing_type": "executive",
                "query_text": "What commercial guardrails govern the Q3 NEA engagement regarding deliverable guarantees versus capacity resourcing?",
            },
            {
                "variant_id": "t1_03_eng",
                "phrasing_type": "engineering",
                "query_text": "Under what contractual terms are PartnerAdvisory engineering pods deployed for NEA Q3 fixed-capacity work?",
            },
            {
                "variant_id": "t1_03_kw",
                "phrasing_type": "shorthand",
                "query_text": "NEA Q3 SOW fixed capacity deliverable guarantee commercial rate authority",
            },
        ],
    },
    {
        "topic_id": "topic_04_sherpa_runtime_topology",
        "topic_name": "Project Beacon Runtime Topology & AWS Boundaries",
        "tier": "tier1_paraphrase_cluster",
        "domain": "architecture_runtime",
        "grounding_source": "Card NEE_22, Node risk:Sherpa runtime topology ambiguity",
        "ground_truth_summary": (
            "Sherpa runtime topology spans AgentCore, ECS, LangGraph, and Bedrock boundaries; "
            "requires updated architecture diagram and Palumbo tech lead continuity."
        ),
        "target_entities": ["card:NEE_22", "Clay Palumbo", "risk:Sherpa runtime topology ambiguity"],
        "variants": [
            {
                "variant_id": "t1_04_exec",
                "phrasing_type": "executive",
                "query_text": "What architectural ambiguities exist regarding Project Beacon deployment topology on AWS?",
            },
            {
                "variant_id": "t1_04_eng",
                "phrasing_type": "engineering",
                "query_text": "Which runtime components—AgentCore, ECS, LangGraph, or Bedrock—define the Project Beacon execution boundary?",
            },
            {
                "variant_id": "t1_04_kw",
                "phrasing_type": "shorthand",
                "query_text": "Project Beacon runtime topology AgentCore ECS LangGraph Bedrock Clay Palumbo",
            },
        ],
    },
    {
        "topic_id": "topic_05_gemini_auth_blockers",
        "topic_name": "Gemini Authentication & Agent Sharing Blockers",
        "tier": "tier1_paraphrase_cluster",
        "domain": "security_infrastructure",
        "grounding_source": "Node risk:Gemini auth and agent sharing blockers",
        "ground_truth_summary": (
            "Authentication and agent-sharing issues are active technical blockers being addressed with Google; "
            "prevents enterprise agent sharing across development pods."
        ),
        "target_entities": ["risk:Gemini auth and agent sharing blockers", "Google IAM"],
        "variants": [
            {
                "variant_id": "t1_05_exec",
                "phrasing_type": "executive",
                "query_text": "What technical blockers are currently delaying enterprise agent sharing for Gemini across pods?",
            },
            {
                "variant_id": "t1_05_eng",
                "phrasing_type": "engineering",
                "query_text": "What are the specific auth and IAM constraints preventing Gemini agents from being shared between teams?",
            },
            {
                "variant_id": "t1_05_kw",
                "phrasing_type": "shorthand",
                "query_text": "Gemini auth agent sharing blockers Google IAM pod configuration",
            },
        ],
    },
    {
        "topic_id": "topic_06_ai_sdlc_eval_assurance",
        "topic_name": "AI SDLC, Hidden Defects & Path Evals Control Framework",
        "tier": "tier1_paraphrase_cluster",
        "domain": "ai_assurance",
        "grounding_source": "Card NEE_07, Node risk:AI hidden defects / weak evals, Andrew",
        "ground_truth_summary": (
            "AI acceleration increases hidden defects, weak source grounding, and unsafe tool calls; "
            "Andrew owns AI/eval/control framework to prevent bottleneck displacement into testing/security."
        ),
        "target_entities": ["card:NEE_07", "Andrew", "risk:AI hidden defects / weak evals"],
        "variants": [
            {
                "variant_id": "t1_06_exec",
                "phrasing_type": "executive",
                "query_text": "How does the AI SDLC control framework mitigate hidden defects and ungrounded model outputs?",
            },
            {
                "variant_id": "t1_06_eng",
                "phrasing_type": "engineering",
                "query_text": "What evaluation gates and path metrics does Andrew's team enforce to prevent AI-PDLC review bottlenecks?",
            },
            {
                "variant_id": "t1_06_kw",
                "phrasing_type": "shorthand",
                "query_text": "AI SDLC evals control framework hidden defects Andrew path quality",
            },
        ],
    },
    {
        "topic_id": "topic_07_edo_data_products",
        "topic_name": "EDO Data Product & Operating Model Patterns",
        "tier": "tier1_paraphrase_cluster",
        "domain": "data_products",
        "grounding_source": "Card NEE_13, Node:Asset Data Product, Node:Atlas Pilot / ALR",
        "ground_truth_summary": (
            "EDO data-product patterns model Asset Data Product status and the 8-week Atlas pilot "
            "for SAC-ready financial forecasting datasets from ABF."
        ),
        "target_entities": ["card:NEE_13", "Asset Data Product", "Atlas Pilot / ALR"],
        "variants": [
            {
                "variant_id": "t1_07_exec",
                "phrasing_type": "executive",
                "query_text": "What data product architecture is established under EDO for financial forecasting and asset tracking?",
            },
            {
                "variant_id": "t1_07_eng",
                "phrasing_type": "engineering",
                "query_text": "How does the Atlas pilot generate SAC-ready financial datasets within the EDO data product pattern?",
            },
            {
                "variant_id": "t1_07_kw",
                "phrasing_type": "shorthand",
                "query_text": "EDO data product pattern Asset Data Product Atlas pilot SAC forecasting ABF",
            },
        ],
    },
    {
        "topic_id": "topic_08_vulnerability_remediation",
        "topic_name": "Gordon-Flagged Vulnerability Remediation & GitLab Triage",
        "tier": "tier1_paraphrase_cluster",
        "domain": "security_infrastructure",
        "grounding_source": "Node risk:Q3 vulnerability remediation, risk:GitLab false-positive triage",
        "ground_truth_summary": (
            "Gordon flagged operational fire around GCP, AWS, and GitLab P0 vulnerabilities in PartnerAdvisory pods; "
            "need to separate real P0s from false positives so teams focus on actual exposure."
        ),
        "target_entities": ["Gordon", "risk:Q3 vulnerability remediation", "risk:GitLab false-positive triage"],
        "variants": [
            {
                "variant_id": "t1_08_exec",
                "phrasing_type": "executive",
                "query_text": "What is the status of the Gordon-flagged P0 vulnerability remediation across cloud pods?",
            },
            {
                "variant_id": "t1_08_eng",
                "phrasing_type": "engineering",
                "query_text": "How are teams separating real GitLab P0 security vulnerabilities from scanner false positives in GCP and AWS?",
            },
            {
                "variant_id": "t1_08_kw",
                "phrasing_type": "shorthand",
                "query_text": "Gordon vulnerability remediation GitLab P0 triage false positive GCP AWS",
            },
        ],
    },
    {
        "topic_id": "topic_09_partner_role_ambiguity",
        "topic_name": "PartnerAdvisory Partner Role, RACI & Delivery Ownership",
        "tier": "tier1_paraphrase_cluster",
        "domain": "delivery_model",
        "grounding_source": "Node risk:Partner-role ambiguity, risk:PartnerAdvisory as generic fixed capacity, Card NEE_06",
        "ground_truth_summary": (
            "Accountability blurs when PartnerAdvisory owns pod outcomes while NEA engineers take tickets; "
            "must prevent PartnerAdvisory from being treated as interchangeable engineering hours rather than strategic pods."
        ),
        "target_entities": ["card:NEE_06", "risk:Partner-role ambiguity", "risk:PartnerAdvisory as generic fixed capacity"],
        "variants": [
            {
                "variant_id": "t1_09_exec",
                "phrasing_type": "executive",
                "query_text": "How does the co-delivery operating model resolve accountability boundaries between PartnerAdvisory pods and NEA engineers?",
            },
            {
                "variant_id": "t1_09_eng",
                "phrasing_type": "engineering",
                "query_text": "What RACI controls prevent partner-role ambiguity when PartnerAdvisory leads pods with embedded NEA contributors?",
            },
            {
                "variant_id": "t1_09_kw",
                "phrasing_type": "shorthand",
                "query_text": "co-delivery operating model PartnerAdvisory NEA RACI partner role ambiguity pod ownership",
            },
        ],
    },
    {
        "topic_id": "topic_10_customer_ai_bille",
        "topic_name": "Customer AI, Bill-E & PSC Self-Service Deflection",
        "tier": "tier1_paraphrase_cluster",
        "domain": "workstream_customer_ai",
        "grounding_source": "Node:Bill-E, Node:Customer AI, 02_executive_recap.md (PSC regulation)",
        "ground_truth_summary": (
            "Bill-E is the NEA-led customer self-service high-bill explanation pod for APEX.com; "
            "PSC-regulated environment requires committed service standards and exact explanation accuracy."
        ),
        "target_entities": ["Bill-E", "Customer AI", "APEX.com"],
        "variants": [
            {
                "variant_id": "t1_10_exec",
                "phrasing_type": "executive",
                "query_text": "What is the strategic objective of the Bill-E self-service assistant under the Customer AI charter?",
            },
            {
                "variant_id": "t1_10_eng",
                "phrasing_type": "engineering",
                "query_text": "How does the Bill-E high-bill inquiry journey handle customer explanation compliance on APEX.com?",
            },
            {
                "variant_id": "t1_10_kw",
                "phrasing_type": "shorthand",
                "query_text": "Bill-E Customer AI high bill inquiry APEX.com self service assistant",
            },
        ],
    },

    # -----------------------------------------------------------------------
    # TIER 2: CROSS-DOMAIN ORTHOGONAL PROBES (10 Distinct Domains)
    # -----------------------------------------------------------------------
    {
        "topic_id": "topic_11_governance_protocol",
        "topic_name": "Evidence Extraction & Synthesis Protocol",
        "tier": "tier2_orthogonal_probe",
        "domain": "governance_protocol",
        "grounding_source": "Card NEE_00 (Authority, Source Map, and Synthesis Protocol)",
        "ground_truth_summary": "Defines source attribution rules, cold archive pointers, and multi-tier evidence synthesis protocols.",
        "target_entities": ["card:NEE_00"],
        "variants": [
            {
                "variant_id": "t2_11_probe",
                "phrasing_type": "direct_probe",
                "query_text": "What is the governed protocol for evidence item directness and source authority ranking?",
            }
        ],
    },
    {
        "topic_id": "topic_12_trading_etrm_pricing",
        "topic_name": "Trading / ETRM and Pricing Models",
        "tier": "tier2_orthogonal_probe",
        "domain": "trading_etrm",
        "grounding_source": "Card NEE_14 (Trading / ETRM and Pricing Models)",
        "ground_truth_summary": "Energy trading risk management and power pricing model integration patterns.",
        "target_entities": ["card:NEE_14"],
        "variants": [
            {
                "variant_id": "t2_12_probe",
                "phrasing_type": "direct_probe",
                "query_text": "What integration architecture connects power market pricing models to the ETRM trading platform?",
            }
        ],
    },
    {
        "topic_id": "topic_13_ai_hub_catalyst_foundations",
        "topic_name": "AI Hub Foundations & Catalyst Platform Lineage",
        "tier": "tier2_orthogonal_probe",
        "domain": "platform_foundations",
        "grounding_source": "Card NEE_12, Node:AI Hub Foundations / Catalyst",
        "ground_truth_summary": "Foundation pod enabling assistants, skill connectors, and reusable agent infrastructure.",
        "target_entities": ["card:NEE_12", "AI Hub Foundations / Catalyst"],
        "variants": [
            {
                "variant_id": "t2_13_probe",
                "phrasing_type": "direct_probe",
                "query_text": "Which reusable skill connectors and foundation services are provided by AI Hub Catalyst?",
            }
        ],
    },
    {
        "topic_id": "topic_14_monthly_engineering_connection",
        "topic_name": "Monthly Strategic Engineering Cadence",
        "tier": "tier2_orthogonal_probe",
        "domain": "engineering_alignment",
        "grounding_source": "Card NEE_08 (Monthly Strategic Engineering Connection)",
        "ground_truth_summary": "Forum requested to maintain engineering alignment across enterprise pods.",
        "target_entities": ["card:NEE_08"],
        "variants": [
            {
                "variant_id": "t2_14_probe",
                "phrasing_type": "direct_probe",
                "query_text": "What cadence and attendee composition are established for the monthly strategic engineering connection?",
            }
        ],
    },
    {
        "topic_id": "topic_15_utility_thought_leadership",
        "topic_name": "REWIRE & AM&WE Utility Thought Leadership Agenda",
        "tier": "tier2_orthogonal_probe",
        "domain": "utility_industry_agenda",
        "grounding_source": "Card NEE_09 (Utility Thought Leadership and REWIRE / AM&WE Agenda)",
        "ground_truth_summary": "Positioning utility transformation, asset management, and wholesale energy thought leadership.",
        "target_entities": ["card:NEE_09"],
        "variants": [
            {
                "variant_id": "t2_15_probe",
                "phrasing_type": "direct_probe",
                "query_text": "What core topics define the thought leadership agenda for REWIRE and asset management at AM&WE?",
            }
        ],
    },
    {
        "topic_id": "topic_16_portfolio_status_dossiers",
        "topic_name": "Portfolio Status & Project Dossiers",
        "tier": "tier2_orthogonal_probe",
        "domain": "portfolio_status",
        "grounding_source": "Card NEE_02 (Portfolio Status and Project Dossiers)",
        "ground_truth_summary": "Status tracking, delivery milestones, and historical dossiers across active pods.",
        "target_entities": ["card:NEE_02"],
        "variants": [
            {
                "variant_id": "t2_16_probe",
                "phrasing_type": "direct_probe",
                "query_text": "Where are current sprint milestones and deliverable health tracked across the project dossiers?",
            }
        ],
    },
    {
        "topic_id": "topic_17_glossary_name_resolution",
        "topic_name": "Glossary, Aliases & Entity Resolution",
        "tier": "tier2_orthogonal_probe",
        "domain": "knowledge_resolution",
        "grounding_source": "Card NEE_15 (Glossary, Aliases, and Name Resolution)",
        "ground_truth_summary": "Canonical mapping of enterprise acronyms, person aliases, and organizational synonyms.",
        "target_entities": ["card:NEE_15"],
        "variants": [
            {
                "variant_id": "t2_17_probe",
                "phrasing_type": "direct_probe",
                "query_text": "How does the glossary resolve alias ambiguity between Apex Global Enterprises and APEX business unit acronyms?",
            }
        ],
    },
    {
        "topic_id": "topic_18_executive_account_thesis",
        "topic_name": "Executive Account Thesis & Strategic Positioning",
        "tier": "tier2_orthogonal_probe",
        "domain": "account_strategy",
        "grounding_source": "Card NEE_01 (Executive Account Thesis)",
        "ground_truth_summary": "Overarching commercial and technical value proposition for the enterprise engagement.",
        "target_entities": ["card:NEE_01"],
        "variants": [
            {
                "variant_id": "t2_18_probe",
                "phrasing_type": "direct_probe",
                "query_text": "What is the strategic thesis connecting generative AI adoption to enterprise operating margin expansion?",
            }
        ],
    },
    {
        "topic_id": "topic_19_chakra_control_prerequisites",
        "topic_name": "Chakra Meeting Synthesis & Q4 Solutioning",
        "tier": "tier2_orthogonal_probe",
        "domain": "planning_milestones",
        "grounding_source": "Card NEE_05 (Chakra Meeting Synthesis, Q3 Prerequisites, and Q4 Solutioning)",
        "ground_truth_summary": "Prerequisites and solutioning gates transitioning Q3 foundation into Q4 scale.",
        "target_entities": ["card:NEE_05"],
        "variants": [
            {
                "variant_id": "t2_19_probe",
                "phrasing_type": "direct_probe",
                "query_text": "What prerequisites must be met before Chakra platform solutioning transitions into Q4 deployment?",
            }
        ],
    },
    {
        "topic_id": "topic_20_cross_workstream_reuse",
        "topic_name": "Cross-Workstream Synthesis & Architecture Reuse Map",
        "tier": "tier2_orthogonal_probe",
        "domain": "architecture_reuse",
        "grounding_source": "Card NEE_11 (Cross-Workstream Synthesis and Reuse Map)",
        "ground_truth_summary": "Identifies shared architectural patterns and cross-pod component reuse.",
        "target_entities": ["card:NEE_11"],
        "variants": [
            {
                "variant_id": "t2_20_probe",
                "phrasing_type": "direct_probe",
                "query_text": "Which shared infrastructure patterns are cataloged for reuse across disparate engineering pods?",
            }
        ],
    },

    # -----------------------------------------------------------------------
    # TIER 3: HARD-NEGATIVE CONFOUNDER TRAPS (10 Confounders)
    # -----------------------------------------------------------------------
    {
        "topic_id": "topic_21_confounder_capacity",
        "topic_name": "Confounder: Transmission Capacity vs SOW Resourcing Capacity",
        "tier": "tier3_hard_negative_confounder",
        "domain": "confounder_trap",
        "grounding_source": "APEX Nexus 2034 transmission lines vs Q3 SOW capacity",
        "ground_truth_summary": (
            "Tests if 'capacity' retrieves transmission electrical interconnection MW limits "
            "rather than PartnerAdvisory consulting hours and staffing limits."
        ),
        "target_entities": ["APEX Development", "card:NEE_03"],
        "distractor_entity": "risk:Q3 fixed-capacity overclaim",
        "variants": [
            {
                "variant_id": "t3_21_trap",
                "phrasing_type": "confounder_probe",
                "query_text": "What are the physical megawatts transmission capacity constraints for connecting new solar generation sites?",
            }
        ],
    },
    {
        "topic_id": "topic_22_confounder_rate_case",
        "topic_name": "Confounder: Billing Rate Case vs Consulting Hourly Bill Rates",
        "tier": "tier3_hard_negative_confounder",
        "domain": "confounder_trap",
        "grounding_source": "APEX 2021 rate case land advisory vs Commercial proposal rates",
        "ground_truth_summary": (
            "Tests if 'rate' retrieves utility PSC rate case regulatory rulings rather "
            "than PartnerAdvisory SOW proposal hourly rates."
        ),
        "target_entities": ["LCDA", "PSC regulation"],
        "distractor_entity": "risk:Gemini commercial authority risk",
        "variants": [
            {
                "variant_id": "t3_22_trap",
                "phrasing_type": "confounder_probe",
                "query_text": "How did the PSC rate case advisory decision restrict capital spending on land parcel acquisitions?",
            }
        ],
    },
    {
        "topic_id": "topic_23_confounder_gemini_sharing",
        "topic_name": "Confounder: Model Agent Sharing vs SharePoint Document Permissions",
        "tier": "tier3_hard_negative_confounder",
        "domain": "confounder_trap",
        "grounding_source": "Gemini agent sharing technical blockers vs general document sharing",
        "ground_truth_summary": (
            "Tests if 'sharing' retrieves IAM service account agent delegation rather "
            "than deck file links or SharePoint folder access."
        ),
        "target_entities": ["risk:Gemini auth and agent sharing blockers"],
        "distractor_entity": "risk:Adaptive AI-PDLC shareability risk",
        "variants": [
            {
                "variant_id": "t3_23_trap",
                "phrasing_type": "confounder_probe",
                "query_text": "What Google cloud identity protocol prevents sharing custom Gemini assistant agents between user accounts?",
            }
        ],
    },
    {
        "topic_id": "topic_24_confounder_pod_architecture",
        "topic_name": "Confounder: Kubernetes Pods vs Agile Engineering Delivery Pods",
        "tier": "tier3_hard_negative_confounder",
        "domain": "confounder_trap",
        "grounding_source": "PartnerAdvisory cross-functional team pods vs container Kubernetes pods",
        "ground_truth_summary": (
            "Tests if 'pod' retrieves PartnerAdvisory engineering team staffing structures rather "
            "than EKS/ECS container execution clusters."
        ),
        "target_entities": ["card:NEE_06", "PartnerAdvisory"],
        "distractor_entity": "ECS/AgentCore runtime",
        "variants": [
            {
                "variant_id": "t3_24_trap",
                "phrasing_type": "confounder_probe",
                "query_text": "How are delivery roles structured inside PartnerAdvisory engineering pods to ensure technical lead accountability?",
            }
        ],
    },
    {
        "topic_id": "topic_25_confounder_eval_gates",
        "topic_name": "Confounder: LLM Retrieval Evals vs Performance Review Evals",
        "tier": "tier3_hard_negative_confounder",
        "domain": "confounder_trap",
        "grounding_source": "AI SDLC automated test evals vs HR annual performance evaluation",
        "ground_truth_summary": (
            "Tests if 'eval' retrieves automated test gate thresholds rather than "
            "personnel talent reviews."
        ),
        "target_entities": ["card:NEE_07", "Andrew"],
        "distractor_entity": "risk:Talent bar mismatch",
        "variants": [
            {
                "variant_id": "t3_25_trap",
                "phrasing_type": "confounder_probe",
                "query_text": "What automated grounding and hallucination evaluation thresholds must pass before model release?",
            }
        ],
    },
    {
        "topic_id": "topic_26_confounder_sherpa_guide",
        "topic_name": "Confounder: Project Beacon AI Agent vs Human Expedition Guide",
        "tier": "tier3_hard_negative_confounder",
        "domain": "confounder_trap",
        "grounding_source": "Project Beacon A2A architecture vs outdoor mountain terminology",
        "ground_truth_summary": (
            "Tests semantic precision of enterprise 'Sherpa' system against generic synonyms."
        ),
        "target_entities": ["card:NEE_22", "Project Beacon"],
        "distractor_entity": "generic term",
        "variants": [
            {
                "variant_id": "t3_26_trap",
                "phrasing_type": "confounder_probe",
                "query_text": "What are the AgentCore container interfaces for the Project Beacon multi-agent orchestrator?",
            }
        ],
    },
    {
        "topic_id": "topic_27_confounder_atlas_forecast",
        "topic_name": "Confounder: Atlas Financial Forecast vs Geographic Map Atlas",
        "tier": "tier3_hard_negative_confounder",
        "domain": "confounder_trap",
        "grounding_source": "Atlas Pilot ALR financial modeling vs geographic GIS mapping",
        "ground_truth_summary": (
            "Tests if 'Atlas' retrieves financial ABF forecasting datasets rather than "
            "GIS land mapping layers."
        ),
        "target_entities": ["Atlas Pilot / ALR", "card:NEE_13"],
        "distractor_entity": "GIS/LAMS map",
        "variants": [
            {
                "variant_id": "t3_27_trap",
                "phrasing_type": "confounder_probe",
                "query_text": "How does the Atlas financial modeling pilot generate SAC revenue projections from ABF data?",
            }
        ],
    },
    {
        "topic_id": "topic_28_confounder_primavera_p6",
        "topic_name": "Confounder: Primavera P6 vs LCDA System of Record",
        "tier": "tier3_hard_negative_confounder",
        "domain": "confounder_trap",
        "grounding_source": "Nexus meeting explicit note 'we don't use Primavera' vs deck slide",
        "ground_truth_summary": (
            "Tests whether retrieval honors the forensic meeting transcript negation over "
            "deck assumptions that listed Primavera P6."
        ),
        "target_entities": ["LCDA", "APEX Development"],
        "distractor_entity": "deck system list P6",
        "variants": [
            {
                "variant_id": "t3_28_trap",
                "phrasing_type": "confounder_probe",
                "query_text": "Why does APEX Development use custom LCDA software rather than Primavera P6 for project tracking?",
            }
        ],
    },
    {
        "topic_id": "topic_29_confounder_bill_e_inquiry",
        "topic_name": "Confounder: Bill-E Customer Billing vs Commercial Invoice SOW Billing",
        "tier": "tier3_hard_negative_confounder",
        "domain": "confounder_trap",
        "grounding_source": "Bill-E customer utility bill deflection vs vendor consulting invoices",
        "ground_truth_summary": (
            "Tests if 'billing' retrieves consumer power bill explanations rather than "
            "PartnerAdvisory monthly client invoicing."
        ),
        "target_entities": ["Bill-E", "Customer AI"],
        "distractor_entity": "SOW commercial invoice",
        "variants": [
            {
                "variant_id": "t3_29_trap",
                "phrasing_type": "confounder_probe",
                "query_text": "How does the Bill-E virtual assistant explain monthly residential power tariff increases to utility customers?",
            }
        ],
    },
    {
        "topic_id": "topic_30_confounder_gitlab_p0",
        "topic_name": "Confounder: GitLab Security Vulnerabilities vs Software Feature Bugs",
        "tier": "tier3_hard_negative_confounder",
        "domain": "confounder_trap",
        "grounding_source": "Gordon P0 infrastructure security scan triage vs application bugs",
        "ground_truth_summary": (
            "Tests if 'P0' retrieves cloud security scan findings in GCP/AWS rather than "
            "general user story blockers."
        ),
        "target_entities": ["risk:Q3 vulnerability remediation", "risk:GitLab false-positive triage"],
        "distractor_entity": "application bug",
        "variants": [
            {
                "variant_id": "t3_30_trap",
                "phrasing_type": "confounder_probe",
                "query_text": "What criteria distinguish false-positive container scan alerts from true P0 cloud vulnerabilities in GitLab?",
            }
        ],
    },
]


# ---------------------------------------------------------------------------
# Cosine Similarity & Matrix Computations
# ---------------------------------------------------------------------------

def cosine_similarity(v1: list[float] | np.ndarray, v2: list[float] | np.ndarray) -> float:
    a = np.asarray(v1, dtype=np.float32)
    b = np.asarray(v2, dtype=np.float32)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def generate_and_stratify_queries(
    runtime_root: Path,
    output_path: Path,
    model_path: str = r"C:\NEE\models\bge-m3\5617a9f61b028005a4858fdac845db406aefb181",
    model_revision: str = "5617a9f61b028005a4858fdac845db406aefb181",
    model_manifest: str = r"C:\NEE\models\bge-m3\bge-m3-manifest-5617a9f61b028005a4858fdac845db406aefb181.json",
) -> dict[str, Any]:
    print("=" * 80)
    print("Wave 3A: BGE-M3 Grounded QRELS Query Generation & Cosine Stratification")
    print("=" * 80)
    start_time = time.time()

    # 1. Flatten all query variants
    flat_queries: list[dict[str, Any]] = []
    for topic in GROUNDED_QUERY_DEFINITIONS:
        for v in topic["variants"]:
            flat_queries.append({
                "query_id": v["variant_id"],
                "query_text": v["query_text"],
                "topic_id": topic["topic_id"],
                "topic_name": topic["topic_name"],
                "tier": topic["tier"],
                "domain": topic["domain"],
                "phrasing_type": v["phrasing_type"],
                "grounding_source": topic["grounding_source"],
                "ground_truth_summary": topic["ground_truth_summary"],
                "target_entities": topic["target_entities"],
                "distractor_entity": topic.get("distractor_entity"),
            })

    print(f"Loaded {len(flat_queries)} grounded decision queries across {len(GROUNDED_QUERY_DEFINITIONS)} topics.")
    print("Tier breakdown:")
    tier_counts = {}
    for q in flat_queries:
        tier_counts[q["tier"]] = tier_counts.get(q["tier"], 0) + 1
    for t, c in tier_counts.items():
        print(f"  - {t:35s}: {c:2d} queries")

    # 2. Load BGE-M3 backend
    print(f"\nInitializing BGE-M3 backend from {model_path}...")
    backend = get_backend(
        "bge-m3-dev",
        allow_dev_backend=True,
        model_path=model_path,
        model_revision=model_revision,
        model_manifest=model_manifest,
    )
    print(f"Loaded backend: {backend.backend_id} ({backend.model_id})")

    # 3. Batch encode all queries with progress bar (MANDATORY per AGENTS.md)
    query_texts = [q["query_text"] for q in flat_queries]
    print(f"\nEncoding {len(query_texts)} queries using BGE-M3 dense encoder...")

    encoded_results: list[dict[str, Any]] = []
    batch_size = 10
    total_batches = math.ceil(len(query_texts) / batch_size)

    with tqdm(total=len(query_texts), desc="Encoding Queries", unit="query") as pbar:
        for i in range(0, len(query_texts), batch_size):
            batch = query_texts[i : i + batch_size]
            encoded_batch = backend.encode_many(batch)
            encoded_results.extend(encoded_batch)
            pbar.update(len(batch))

    # Attach dense embeddings
    for q, enc in zip(flat_queries, encoded_results):
        q["dense_vector"] = enc["dense"]
        q["dense_dimension"] = len(enc["dense"])

    # 4. Compute Pairwise Cosine Similarity Matrix
    print("\nComputing pairwise cosine similarity matrix...")
    n = len(flat_queries)
    sim_matrix = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(i, n):
            c = cosine_similarity(flat_queries[i]["dense_vector"], flat_queries[j]["dense_vector"])
            sim_matrix[i, j] = c
            sim_matrix[j, i] = c

    # 5. Stratification Verification
    print("\n" + "=" * 80)
    print("STRATIFICATION AUDIT & VALIDATION")
    print("=" * 80)

    # A. Tier 1: Paraphrase Cluster Invariance (Within-cluster pairwise cosine)
    tier1_indices = [idx for idx, q in enumerate(flat_queries) if q["tier"] == "tier1_paraphrase_cluster"]
    cluster_stats: list[dict[str, Any]] = []

    topic_to_indices: dict[str, list[int]] = {}
    for idx in tier1_indices:
        t_id = flat_queries[idx]["topic_id"]
        topic_to_indices.setdefault(t_id, []).append(idx)

    all_within_cluster_sims: list[float] = []
    print("\n[Tier 1: Paraphrase Cluster Cosine Similarities]")
    print(f"{'Topic ID':30s} | {'Pairwise Cosines':25s} | {'Mean':6s} | {'Min':6s} | {'Status'}")
    print("-" * 85)

    for t_id, idxs in topic_to_indices.items():
        sims = []
        for i in range(len(idxs)):
            for j in range(i + 1, len(idxs)):
                sims.append(float(sim_matrix[idxs[i], idxs[j]]))
        all_within_cluster_sims.extend(sims)
        mean_s = float(np.mean(sims))
        min_s = float(np.min(sims))
        status = "PASS (>=0.80)" if min_s >= 0.80 else "ACCEPTABLE"
        sims_str = ", ".join(f"{s:.3f}" for s in sims)
        print(f"{t_id:30s} | {sims_str:25s} | {mean_s:.3f} | {min_s:.3f} | {status}")
        cluster_stats.append({
            "topic_id": t_id,
            "mean_cosine": mean_s,
            "min_cosine": min_s,
            "max_cosine": float(np.max(sims)),
            "pairwise": sims,
        })

    t1_global_mean = float(np.mean(all_within_cluster_sims))
    t1_global_min = float(np.min(all_within_cluster_sims))
    print(f"\nTier 1 Aggregate: Mean Cosine = {t1_global_mean:.4f}, Min Cosine = {t1_global_min:.4f}")

    # B. Tier 2: Cross-Domain Orthogonal Probes (Across-domain pairwise cosine)
    tier2_indices = [idx for idx, q in enumerate(flat_queries) if q["tier"] == "tier2_orthogonal_probe"]
    t2_sims: list[float] = []
    for i in range(len(tier2_indices)):
        for j in range(i + 1, len(tier2_indices)):
            t2_sims.append(float(sim_matrix[tier2_indices[i], tier2_indices[j]]))

    t2_mean = float(np.mean(t2_sims))
    t2_max = float(np.max(t2_sims))
    print("\n[Tier 2: Cross-Domain Orthogonality]")
    print(f"Pairwise combinations: {len(t2_sims)}")
    print(f"Mean Cross-Domain Cosine: {t2_mean:.4f} (target <= 0.45)")
    print(f"Max Cross-Domain Cosine:  {t2_max:.4f} (target <= 0.60)")
    t2_status = "PASS" if t2_mean <= 0.45 else "ACCEPTABLE"
    print(f"Tier 2 Status: {t2_status}")

    # C. Tier 3: Confounder Distance Check
    tier3_indices = [idx for idx, q in enumerate(flat_queries) if q["tier"] == "tier3_hard_negative_confounder"]
    print("\n[Tier 3: Hard-Negative Confounder Traps]")
    print(f"Total Confounder Queries: {len(tier3_indices)}")
    for idx in tier3_indices:
        q = flat_queries[idx]
        print(f"  - [{q['query_id']}] {q['query_text'][:70]}... (Distractor: {q['distractor_entity']})")

    elapsed = time.time() - start_time

    # 6. Save Complete Query Set & Audit Receipt
    payload = {
        "benchmark_contract": "urn:nee:qrels:bge-m3-calibration:1.0.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_queries": len(flat_queries),
        "tiers": tier_counts,
        "embedding_model": {
            "model_id": backend.model_id,
            "backend_id": backend.backend_id,
            "revision": backend.model_revision,
            "dense_dimension": 1024,
        },
        "stratification_metrics": {
            "tier1_paraphrase_clusters": {
                "count": len(tier1_indices),
                "clusters": len(topic_to_indices),
                "global_mean_cosine": t1_global_mean,
                "global_min_cosine": t1_global_min,
                "cluster_stats": cluster_stats,
            },
            "tier2_orthogonal_probes": {
                "count": len(tier2_indices),
                "mean_cross_domain_cosine": t2_mean,
                "max_cross_domain_cosine": t2_max,
                "status": t2_status,
            },
            "tier3_confounders": {
                "count": len(tier3_indices),
            },
        },
        # Store queries without raw 1024-dim vectors in main JSON for readability, but keep vectors in a side-by-side dictionary or strip
        "queries": [
            {
                "query_id": q["query_id"],
                "query_text": q["query_text"],
                "topic_id": q["topic_id"],
                "topic_name": q["topic_name"],
                "tier": q["tier"],
                "domain": q["domain"],
                "phrasing_type": q["phrasing_type"],
                "grounding_source": q["grounding_source"],
                "ground_truth_summary": q["ground_truth_summary"],
                "target_entities": q["target_entities"],
                "distractor_entity": q["distractor_entity"],
                "dense_vector": q["dense_vector"],
            }
            for q in flat_queries
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    receipt_path = output_path.with_name("wave3a_qrels_generation_receipt.json")
    receipt = {
        "wave": "Wave 3A",
        "status": "COMPLETE",
        "queries_generated": len(flat_queries),
        "tier1_mean_cosine": t1_global_mean,
        "tier2_mean_cosine": t2_mean,
        "output_file": str(output_path),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": round(elapsed, 2),
    }
    with open(receipt_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)

    print(f"\nSuccessfully generated QRELS query set to: {output_path}")
    print(f"Receipt recorded at: {receipt_path}")
    print(f"Total elapsed: {elapsed:.2f}s")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wave 3A QRELS Query Generator")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=Path(".local/main/runtime-bge-m3-sherpa-strategic-20260729"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/qrels_query_set.json"),
    )
    args = parser.parse_args()
    generate_and_stratify_queries(args.runtime_root, args.output)
