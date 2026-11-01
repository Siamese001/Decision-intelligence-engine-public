import json
from pathlib import Path

def run():
    out_file = Path("evals/qrels_authentic_benchmark.json")
    data = json.loads(out_file.read_text())
    
    data["summary"]["total_queries"] += 3
    data["summary"]["total_candidates_judged"] += 3
    
    new_queries = [
        {
            "query_id": "q_neet_telemetry_01",
            "query_text": "What telemetry sources are used by APEX_TRANS Insights Hub for wildfire and smoke detection?",
            "topic_id": "topic_neet_telemetry",
            "partition": "dev",
            "judgments": [
                {
                    "record_id": "90af388366530162",
                    "blinded_evidence": {
                        "record_id": "90af388366530162",
                        "record_type": "source_chunk",
                        "lane": "key_point",
                        "title": "SaaS Telemetry Sources",
                        "retrieval_text": "- **SaaS Telemetry Sources:** Pano AI optical smoke cameras (REST API), TechnoSilva 5-day wildfire risk heatmaps (REST/GIS API), DTN Weather & Lightning radar (REST/WMS API).",
                        "source_id": "02_NEET_Insights_Hub_Tech_Services_Group_Interview_09-11-2026_0336PM_0437PM_ET_executive_recap.md"
                    },
                    "multi_dimensional_label": {
                        "evidence_relevance": {
                            "grade": 2,
                            "label": "Highly Relevant",
                            "support_type": "direct_answer"
                        }
                    },
                    "provenance": "expert_adjudicated",
                    "review_status": "RESOLVED"
                }
            ]
        },
        {
            "query_id": "q_navigator_usage_01",
            "query_text": "Who is responsible for checking if Navigator is already deployed in the enterprise area?",
            "topic_id": "topic_navigator_usage",
            "partition": "dev",
            "judgments": [
                {
                    "record_id": "9337155bbd2dbd41",
                    "blinded_evidence": {
                        "record_id": "9337155bbd2dbd41",
                        "record_type": "source_chunk",
                        "lane": "action_item",
                        "title": "Investigate Navigator Usage",
                        "retrieval_text": "| 1 | Action | Investigate whether Navigator or a similar project-management capability is already used in the referenced enterprise area and report the finding. | Taylor Mercer | TBD | 00:03:30–00:04:00 | Confirmed |",
                        "source_id": "02_Jarvis_Q1_Charter_Sharon_PartnerAdvisory_09-28-2026_1111AM_1200PM_ET_executive_recap.md"
                    },
                    "multi_dimensional_label": {
                        "evidence_relevance": {
                            "grade": 1,
                            "label": "Partially Relevant",
                            "support_type": "partial_support"
                        }
                    },
                    "provenance": "expert_adjudicated",
                    "review_status": "RESOLVED"
                }
            ]
        },
        {
            "query_id": "q_hr_pii_ownership_01",
            "query_text": "What gaps were identified regarding PII and HR workflows, and who is meeting to resolve them?",
            "topic_id": "topic_hr_pii_ownership",
            "partition": "dev",
            "judgments": [
                {
                    "record_id": "c35258dfb9cd0a43",
                    "blinded_evidence": {
                        "record_id": "c35258dfb9cd0a43",
                        "record_type": "source_chunk",
                        "lane": "key_point",
                        "title": "HR 360 PII Ownership",
                        "retrieval_text": "- **Dan surfaced PII and HR 360 ownership gaps so HRIT handoffs can be defined before complex workflows move forward.** Drew Oakley distinguished simple workflows from cases that route, update, or associate employee and performance information. He identified PII handling as an unresolved concern and connected the discussion to capabilities developed through HR 360. Dan stated that he intended to meet with John Andersen (NEA Engineering Manager) later on 09/29/2026 to clarify what NEA would continue owning, what HRIT would own, and how product handoffs should work. Anuj Chokshi said the parties were close but still coordinating. [11:47:15–11:48:30; stmt-13–stmt-15]",
                        "source_id": "02_apex_global_Monthly_Ge_Gcp_PartnerAdvisory_09-29-2026_1142AM1202PMET_executive_recap.md"
                    },
                    "multi_dimensional_label": {
                        "evidence_relevance": {
                            "grade": 2,
                            "label": "Highly Relevant",
                            "support_type": "direct_answer"
                        }
                    },
                    "provenance": "expert_adjudicated",
                    "review_status": "RESOLVED"
                }
            ]
        }
    ]
    
    data["evaluated_queries"].extend(new_queries)
    out_file.write_text(json.dumps(data, indent=2))
    print(f"Updated {out_file}")

if __name__ == "__main__":
    run()
