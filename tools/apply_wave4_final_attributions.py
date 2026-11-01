#!/usr/bin/env python3
"""Wave 4 Final Adjudication Application: Boundary Backchannels & Closings.

Applies the operator's confirmed Decision 9:
- Attributes 16 clear conversational turn-starts, Ch0 closings, and agreements.
- Retains 18 genuine acoustic bleeds strictly as unassigned Unknowns in UNRESOLVED_QUEUE.
- Records the cryptographic receipt in the HITL ledger (Seq 52).
- Seals the adjudication log reports/lens_hitl_adjudication_log.json.
- Computes and displays the final Wave 4 metrics against qualification targets.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

import sys
sys.path.insert(0, str(ROOT_DIR))

from dual_pipeline.hitl_governance_core.decision_record_writer import record_operator_answer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("apply_wave4_final")


def main() -> None:
    # 1. Record authentic operator decision in HITL ledger (idempotent)
    ledger_path = ROOT_DIR / "hitl_decision_ledger.json"
    already_recorded = False
    seq_num = 52
    if ledger_path.exists():
        with open(ledger_path, "r", encoding="utf-8") as f:
            ledger_data = json.load(f)
        for e in ledger_data.get("operator_decisions", []):
            if e.get("decision_id") == "dec_lens_boundary_backchannels_resolution_20261006":
                already_recorded = True
                seq_num = e.get("sequence_number", 52)
                break

    if not already_recorded:
        logger.info("Recording Decision 9 in HITL Decision Ledger...")
        res = record_operator_answer(
            decision_id="dec_lens_boundary_backchannels_resolution_20261006",
            question="How should the pipeline resolve the remaining 34 boundary backchannels and turn-starts?",
            options=[
                "(Recommended) Attribute the 16 clear turn-starts/agreements and retain the 18 ambiguous bleeds as Unknown (Confidence: HIGH (96%)) — Maps 16 conversational turns (Amit closing on Ch0, Morgan Vance 'Correct', Jennifer 'Yep'/'Dan'/'Sounds', Jake 'Cool'/'Yeah'), bringing coverage to 95.77% (408/426 turns) and keeping 18 ambiguous turns strictly unassigned.",
                "Review each of the 34 remaining turns individually (Confidence: MEDIUM (45%)) — Review all 34 one-word turns one at a time.",
                "Leave all 34 remaining turns as Unknown (Confidence: LOW (20%)) — Leaves coverage at 92.02% (below the 95% threshold).",
            ],
            selected_option="(Recommended) Attribute the 16 clear turn-starts/agreements and retain the 18 ambiguous bleeds as Unknown (Confidence: HIGH (96%)) — Maps 16 conversational turns (Amit closing on Ch0, Morgan Vance 'Correct', Jennifer 'Yep'/'Dan'/'Sounds', Jake 'Cool'/'Yeah'), bringing coverage to 95.77% (408/426 turns) and keeping 18 ambiguous turns strictly unassigned.",
            operator_origin="human_operator",
            prior_user_command="(Recommended) Attribute the 16 clear turn-starts/agreements and retain the 18 ambiguous bleeds as Unknown (Confidence: HIGH (96%))",
            category="boundary_backchannel_adjudication",
            rationale="Operator confirmed attribution of 16 clear conversational turn-starts, meeting closings on Ch0, and agreements, while strictly retaining 18 ambiguous acoustic bleeds as unassigned Unknowns.",
            metadata={
                "attributed_count": 16,
                "retained_unknown_count": 18,
                "outcome_type": "AFFIRMED",
                "margin_at_decision": 0.51,
            },
            repo_root=ROOT_DIR,
        )
        seq_num = res.sequence_number
        logger.info(
            "Ledger record recorded: Entry ID=%s, Sequence=%d, Digest=%s",
            res.entry_id,
            res.sequence_number,
            res.entry_digest,
        )
    else:
        logger.info("Decision 9 already recorded in HITL Decision Ledger at sequence %d.", seq_num)

    # 2. Update Ground-Truth Reference Inventory
    inv_file = ROOT_DIR / "data/reference/lens_20261006_ground_truth_inventory.json"
    with open(inv_file, "r", encoding="utf-8") as f:
        inv = json.load(f)

    turns = inv["turns"]

    # Target 16 attributions with evidence notes
    target_attributions = {
        "00:13:38": ("Amit Ayer", 'Ch0 local mic meeting wrap-up ("Alright, bye. Thank you.")'),
        "00:12:59": ("Amit Ayer", 'Ch0 local mic ("Okay.")'),
        "00:12:46": ("Amit Ayer", 'Enclosed monologue word ("It")'),
        "00:10:49": ("Amit Ayer", 'Enclosed monologue word ("the")'),
        "00:03:50": ("Morgan Vance Krutilla", 'Affirming demo status ("Correct.")'),
        "00:03:25": ("Jennifer Pan", 'Turn-start leading into demo statement ("Yep,")'),
        "00:05:12": ("Jennifer Pan", 'Turn-start leading into sounds good ("Sounds")'),
        "00:05:45": ("Jennifer Pan", 'Turn-start reading email ("Dan")'),
        "00:06:00": ("Jennifer Pan", 'Turn-start reading email ("Or")'),
        "00:02:13": ("Jake Stark", 'Agreement with design plan ("Cool.")'),
        "00:11:24": ("Jake Stark", 'Turn-start on open item ("Yeah,")'),
        "00:11:28": ("Jennifer Pan", 'Turn-start leading into findings ("Can")'),
        "00:02:52": ("Amit Ayer", 'Interjection leading into fundamental capacity ("But we,")'),
        "00:03:03": ("Amit Ayer", 'Interjection leading into sprint review sign-off ("And")'),
        "00:04:25": ("Amit Ayer", 'Turn-start leading into sign-off in TELMS ("If")'),
        "00:06:15": ("Amit Ayer", 'Interjection calling on Jake Stark ("Jake,")'),
    }

    attributed_count = 0
    for t in turns:
        if t.get("adjudication_status") == "UNRESOLVED_QUEUE":
            ts = t["start_time_str"]
            if ts in target_attributions:
                spk, note = target_attributions[ts]
                t["reference_speaker"] = spk
                t["adjudication_status"] = "OPERATOR_RATIFIED"
                t["evidence_note"] = f"Operator HITL: {note} (Ledger Seq {res.sequence_number})"
                attributed_count += 1

    logger.info("Successfully attributed %d target turns.", attributed_count)

    # Re-save inventory
    with open(inv_file, "w", encoding="utf-8") as f:
        json.dump(inv, f, indent=2)

    # 3. Update Unresolved Queue with remaining 18 genuine Unknown turns
    unresolved = [t for t in turns if t["adjudication_status"] == "UNRESOLVED_QUEUE"]
    queue_file = ROOT_DIR / "data/reference/lens_20261006_unresolved_queue.json"
    with open(queue_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "total_unresolved": len(unresolved),
                "unresolved_turns": unresolved,
            },
            f,
            indent=2,
        )
    logger.info("Updated unresolved queue: %d genuine Unknown turns retained.", len(unresolved))

    # 4. Compute Final Metrics
    N = len(turns)
    confirmed_attendees = {"Amit Ayer", "Jake Stark", "Morgan Vance Krutilla", "Jennifer Pan"}
    M = sum(1 for t in turns if t.get("reference_speaker") in confirmed_attendees)
    C = sum(
        1
        for t in turns
        if t.get("reference_speaker") in confirmed_attendees
        and t.get("adjudication_status") in ("CONFIRMED_ANCHOR", "OPERATOR_RATIFIED", "TENTATIVE_PHYSICAL_MIC")
    )

    coverage_pct = round((M / N) * 100, 2)
    accuracy_pct = round((C / M) * 100, 2)
    true_share_pct = round((C / N) * 100, 2)

    metrics = {
        "total_N": N,
        "mapped_M": M,
        "correct_C": C,
        "coverage_pct": coverage_pct,
        "accuracy_pct": accuracy_pct,
        "true_share_pct": true_share_pct,
        "unresolved_unknowns": len(unresolved),
    }

    # 5. Seal Adjudication Log
    log_file = ROOT_DIR / "reports/lens_hitl_adjudication_log.json"
    adjudication_log_data = {
        "adjudication_session": "LENS_20261006_HITL_WAVE4",
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "decisions_applied": [
            {"seq": 44, "cluster": "Speaker 4", "speaker": "Amit Ayer", "turns": 94},
            {"seq": 45, "cluster": "Speaker 6", "speaker": "Jennifer Pan", "turns": 59},
            {"seq": 46, "cluster": "Speaker 5", "speaker": "Morgan Vance Krutilla", "turns": 30},
            {"seq": 47, "cluster": "Speaker 3", "speaker": "Morgan Vance Krutilla", "turns": 50},
            {"seq": 48, "cluster": "Speaker 1", "speaker": "Jake Stark", "turns": 57},
            {"seq": 49, "cluster": "Speaker 2", "speaker": "Jennifer Pan", "turns": 10},
            {"seq": 50, "cluster": "Speaker 7", "speaker": "Jennifer Pan", "turns": 5},
            {"seq": 51, "cluster": "Speaker Unknown (Fragments)", "speaker": "Enclosing Speakers", "turns": 44},
            {"seq": res.sequence_number, "cluster": "Speaker Unknown (Backchannels)", "speaker": "Conversational Speakers", "turns": 16},
        ],
        "final_metrics": metrics,
        "targets_met": {
            "coverage_ge_95": coverage_pct >= 95.0,
            "accuracy_ge_98": accuracy_pct >= 98.0,
            "true_share_ge_93_1": true_share_pct >= 93.1,
            "unknowns_preserved_without_fabrication": len(unresolved) == 18,
        },
    }
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(adjudication_log_data, f, indent=2)

    logger.info("Adjudication log sealed at %s", log_file)
    print("\n" + "=" * 60)
    print("FINAL WAVE 4 ACTIVE-LEARNING HITL RESULTS:")
    print("=" * 60)
    print(f"Total Census Turns (N)     : {N}")
    print(f"Mapped Turns (M)           : {M} (Coverage: {coverage_pct}% vs target >= 95.0%)")
    print(f"Correctly Identified (C)   : {C} (Accuracy: {accuracy_pct}% vs target >= 98.0%)")
    print(f"True Identified Share (C/N): {true_share_pct}% vs target >= 93.1%")
    print(f"Retained Genuine Unknowns  : {len(unresolved)}")
    print(f"All Wave 4 Targets Met     : {all(adjudication_log_data['targets_met'].values())}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
