import json
from pathlib import Path

def run():
    out_file = Path("evals/qrels_authentic_benchmark.json")
    data = json.loads(out_file.read_text())
    
    data["summary"]["total_queries"] += 4
    data["summary"]["total_candidates_judged"] += 5
    
    new_queries = [
        {
            "query_id": "q_work_organization_01",
            "query_text": "How did Amit suggest separating the work tracks for DocIQ remediation and enhancements?",
            "topic_id": "topic_work_organization",
            "partition": "dev",
            "judgments": [
                {
                    "record_id": "3ba9d7ab6fffa21a",
                    "blinded_evidence": {
                        "record_id": "3ba9d7ab6fffa21a",
                        "record_type": "source_chunk",
                        "lane": "key_point",
                        "title": "Work organization",
                        "retrieval_text": "- **Work organization — Amit Ayer suggested separating remediation from enhancements.** His proposed Track A grouped staged backfill with DMT confidence alignment. A separate Track B grouped feature enhancements, including the file-upload interface and extraction-related work. This distinction would separate preparation for repository updates from additional functionality. The extraction detail was not developed further in the discussion. These were advisory work-track suggestions; no revised work breakdown, delivery assignment, or release commitment was adopted. [00:16:15–00:16:35]",
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
        },
        {
            "query_id": "q_backfill_validation_01",
            "query_text": "What pilot size was suggested for backfill validation and what were the proposed measurements?",
            "topic_id": "topic_backfill_validation",
            "partition": "dev",
            "judgments": [
                {
                    "record_id": "7e9aeb790992f897",
                    "blinded_evidence": {
                        "record_id": "7e9aeb790992f897",
                        "record_type": "source_chunk",
                        "lane": "key_point",
                        "title": "Backfill validation",
                        "retrieval_text": "- **Backfill validation — Amit Ayer suggested an optional 5,000-document pilot.** Rather than an all-or-nothing backfill, he described smaller pilot batches and offered 5,000 documents through the validation interface as one possibility. The proposed measurements were error calibration and throughput, giving leadership evidence about processing behavior while limiting operational exposure. The number was an example, not an agreed batch size. No pilot execution commitment, acceptance criteria, or measured results were established. [00:16:40–00:17:02]",
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
        },
        {
            "query_id": "q_confidence_routing_01",
            "query_text": "What confidence routing thresholds were proposed for automatic document handling versus human triage?",
            "topic_id": "topic_confidence_routing",
            "partition": "dev",
            "judgments": [
                {
                    "record_id": "331cd75ef785179e",
                    "blinded_evidence": {
                        "record_id": "331cd75ef785179e",
                        "record_type": "source_chunk",
                        "lane": "key_point",
                        "title": "Confidence routing",
                        "retrieval_text": "- **Confidence routing — Amit Ayer suggested automatic handling above a proposed floor.** His advisory example used “Above 90” for automatic handling and lower-confidence results going to the validation interface for human triage, potentially through different queues. The number’s unit was unspecified, and behavior at exactly 90 was not defined. This remained a suggested routing concept rather than an approved threshold or implemented control; the business requirements for write-back remained open. [00:17:56–00:18:12]",
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
        },
        {
            "query_id": "q_portfolio_overlap_01",
            "query_text": "Why was there frustration expressed regarding parallel document-processing initiatives like LENS?",
            "topic_id": "topic_portfolio_overlap",
            "partition": "dev",
            "judgments": [
                {
                    "record_id": "97895f931ecc392e",
                    "blinded_evidence": {
                        "record_id": "97895f931ecc392e",
                        "record_type": "source_chunk",
                        "lane": "key_point",
                        "title": "Portfolio overlap",
                        "retrieval_text": "- **Portfolio overlap — Jack O’Shea reported frustration with DocIQ-like initiatives.** Jack described parallel document-processing efforts elsewhere in the company as frustrating. DocIQ, LENS, and related charters provide the subjects for comparison, but the discussion supplied no named duplicate systems or quantified impact. Amit’s LENS experience concerned confidence metrics and additional data. The substantive question is how the initiatives fit together; consolidation, shared architecture, and changes to project charters remain open. [00:18:27–00:19:39]",
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
    
    # And add the f23c3523e8ae4fe0 to the existing topic_documentum_writeback query, but under a new query ID,
    # or to the same query q_documentum_writeback_01.
    # We'll just create a new query since it's about accountable person.
    
    q_writeback_accountable = {
        "query_id": "q_documentum_writeback_02",
        "query_text": "Who is accountable for aligning the Documentum write-back requirements with DMT and PGD?",
        "topic_id": "topic_documentum_writeback",
        "partition": "dev",
        "judgments": [
            {
                "record_id": "f23c3523e8ae4fe0",
                "blinded_evidence": {
                    "record_id": "f23c3523e8ae4fe0",
                    "record_type": "source_chunk",
                    "lane": "action_item",
                    "title": "Action Ledger: Write-back requirements",
                    "retrieval_text": "| 1 | Action | Align Documentum write-back requirements with DMT and PGD, covering low-confidence fields, duplicate handling, and deletion permissions. Discussed focus: the next one or two sprints. Backfill was tentatively targeted for month-end, subject to readiness. | Jack O’Shea | Not stated | stmt-004, stmt-014–016; 00:04:13–00:05:01, 00:17:13–00:17:52 | In Progress |",
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
    
    new_queries.append(q_writeback_accountable)
    data["summary"]["total_queries"] += 1
    
    data["evaluated_queries"].extend(new_queries)
    out_file.write_text(json.dumps(data, indent=2))
    print(f"Updated {out_file}")

if __name__ == "__main__":
    run()
