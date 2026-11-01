import json
from pathlib import Path

def run():
    out_file = Path("evals/qrels_authentic_benchmark.json")
    data = json.loads(out_file.read_text())
    
    data["summary"]["total_queries"] += 3
    data["summary"]["total_candidates_judged"] += 5
    
    new_queries = [
        {
            "query_id": "q_documentum_writeback_01",
            "query_text": "What are the unresolved write-back rules for Documentum and how do they impact the backfill?",
            "topic_id": "topic_documentum_writeback",
            "partition": "dev",
            "judgments": [
                {
                    "record_id": "a589209fa8fde383",
                    "blinded_evidence": {
                        "record_id": "a589209fa8fde383",
                        "record_type": "source_chunk",
                        "lane": "key_point",
                        "title": "Documentum Write-Back Rules",
                        "retrieval_text": "- **Repository controls — Jack O’Shea identified unresolved write-back rules.** The business requirements still need to establish whether low-confidence fields can enter Documentum, when model output should be withheld, and how duplicate documents should be handled. Permission to delete documents is also unresolved. These questions affect changes to existing repository records, not just extraction accuracy. The immediate process concern is defining what writing back actually entails, including the testing needed to support it. [00:04:13–00:04:37; 00:17:13–00:17:52]",
                        "source_id": "02_Dociq_Jack_Amit_Jack_PartnerAdvisory_10-01-2026_1107AM1128AMET_executive_recap.md"
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
                },
                {
                    "record_id": "03df53028080108e",
                    "blinded_evidence": {
                        "record_id": "03df53028080108e",
                        "record_type": "source_chunk",
                        "lane": "key_point",
                        "title": "Documentum Backfill Expectations",
                        "retrieval_text": "- **Documentum readiness — Jack O’Shea reset backfill expectations.** Backfill means writing properties to documents already stored in Documentum. PGD leadership had expected an imminent start, but more consistent model performance and clearer document-handling requirements are prerequisites. Jack reported that leadership understood both the progress and the remaining complexity after the preceding review. Model consistency remains an open readiness dependency; no separate remediation assignment or completion date was committed in this session. [00:03:11–00:05:01]",
                        "source_id": "02_Dociq_Jack_Amit_Jack_PartnerAdvisory_10-01-2026_1107AM1128AMET_executive_recap.md"
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
            "query_id": "q_delivery_frictions_01",
            "query_text": "What are the observed delivery frictions on DocIQ and what is the plan for product-led executive communication?",
            "topic_id": "topic_delivery_frictions",
            "partition": "dev",
            "judgments": [
                {
                    "record_id": "3a25d8d4009954d5",
                    "blinded_evidence": {
                        "record_id": "3a25d8d4009954d5",
                        "record_type": "source_chunk",
                        "lane": "key_point",
                        "title": "DocIQ Scope and Accountability",
                        "retrieval_text": "- **Scope and accountability — Jack O’Shea acknowledged Apex Global-driven delivery friction.** The Q2 project vision had changed substantially through Q3 and Q4, with scope creep and insufficient product direction contributing to difficulty. Jack balanced that acknowledgment with satisfaction in the team’s work. Both attendees supported product-led executive communication, with engineers providing technical context rather than carrying commitments beyond their control. The intended change concerns who leads and owns the message, while preserving useful engineering participation. [00:05:18–00:05:56; 00:09:39–00:14:40]",
                        "source_id": "02_Dociq_Jack_Amit_Jack_PartnerAdvisory_10-01-2026_1107AM1128AMET_executive_recap.md"
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
                },
                {
                    "record_id": "ef7a267694733dad",
                    "blinded_evidence": {
                        "record_id": "ef7a267694733dad",
                        "record_type": "source_chunk",
                        "lane": "key_point",
                        "title": "DocIQ Engineering Focus",
                        "retrieval_text": "- **Engineering focus — Amit Ayer clarified the team’s broader technical strengths.** Capabilities extend beyond data work to front-end development, software engineering, extraction, embeddings, and classification. Additional technical-lead capacity and stronger product oversight are intended to reduce competing coordination demands. Both attendees connected delivery problems to optimistic planning and changing direction, rather than a lack of technical ability. Clearer requirements and earlier escalation of dependencies were the practical improvements discussed for protecting engineering delivery. [00:06:10–00:09:33; 00:13:40–00:14:40]",
                        "source_id": "02_Dociq_Jack_Amit_Jack_PartnerAdvisory_10-01-2026_1107AM1128AMET_executive_recap.md"
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
            "query_id": "q_model_economics_01",
            "query_text": "What is the cost of scaling DocIQ and what architectural changes were proposed to improve economics?",
            "topic_id": "topic_model_economics",
            "partition": "dev",
            "judgments": [
                {
                    "record_id": "732e0825c023014a",
                    "blinded_evidence": {
                        "record_id": "732e0825c023014a",
                        "record_type": "source_chunk",
                        "lane": "key_point",
                        "title": "DocIQ Model Economics",
                        "retrieval_text": "- **Model economics — Amit Ayer flagged the cost of scaling DocIQ.** Amit recalled model-cost figures declining from nearly $1 to approximately $0.10; the measurement basis and document volume were unspecified. These remain approximate recollections rather than a verified per-document baseline or savings calculation. He also reported greater phase-two attention to sustainable human review, retraining, confidence governance, and image caching. That design attention connects extraction performance with operating cost, while implementation and measured benefits remain unverified. [00:14:56–00:15:37]",
                        "source_id": "02_Dociq_Jack_Amit_Jack_PartnerAdvisory_10-01-2026_1107AM1128AMET_executive_recap.md"
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
