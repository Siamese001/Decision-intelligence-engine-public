"""
tools/verify_card_boundaries.py

Verifies Wave 2 Governed Knowledge Cards Parity & Disjointness Bounds.
Checks that NEE_02, NEE_06, NEE_10, and NEE_15 explicitly reflect:
1. Jen Pan as PartnerAdvisory Delivery Lead / Engagement PM on Project LENS.
2. Explicit disjointness and boundary isolating Jen Pan from Project Beacon.
3. Rule 4 Strict Workstream Disjointness in NEE_15.
Produces an immutable runtime receipt: reports/wave2_cards_boundary_receipt.json.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("verify_card_boundaries")


def verify_cards(cards_dir: Path, output_receipt: Path) -> dict:
    nee_10_path = cards_dir / "NEE_10_Stakeholders_Champions_Org_Credentialing_Open_Questions.md"
    nee_15_path = cards_dir / "NEE_15_Glossary_Aliases_and_Name_Resolution.md"
    nee_02_path = cards_dir / "NEE_02_Portfolio_Status_and_Project_Dossiers.md"
    nee_06_path = cards_dir / "NEE_06_CoDelivery_CoHydrated_Operating_Model.md"

    for p in [nee_10_path, nee_15_path, nee_02_path, nee_06_path]:
        if not p.is_file():
            raise FileNotFoundError(f"Card file not found: {p}")

    nee_10_text = nee_10_path.read_text(encoding="utf-8")
    nee_15_text = nee_15_path.read_text(encoding="utf-8")
    nee_02_text = nee_02_path.read_text(encoding="utf-8")
    nee_06_text = nee_06_path.read_text(encoding="utf-8")

    results = {
        "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "cards_checked": [str(p.name) for p in [nee_10_path, nee_15_path, nee_02_path, nee_06_path]],
        "checks": {},
        "pass": False,
    }

    # Check 1: NEE_10 has Jen Pan with LENS only and zero role on Sherpa
    check_1 = "Jen Pan" in nee_10_text and "Project LENS only" in nee_10_text and "zero presence or role on Project Beacon" in nee_10_text
    results["checks"]["nee_10_jen_pan_isolated"] = check_1

    # Check 2: NEE_15 has Jen Pan in canonical table and strict disjointness rule
    check_2a = "Jen Pan" in nee_15_text and "Project LENS only" in nee_15_text and "Zero involvement in Project Beacon" in nee_15_text
    check_2b = "Strict Workstream Disjointness" in nee_15_text and "Cross-workstream entity aggregation is strictly forbidden" in nee_15_text
    results["checks"]["nee_15_jen_pan_canonical"] = check_2a
    results["checks"]["nee_15_disjointness_rule"] = check_2b

    # Check 3: NEE_02 has Jen Pan under LENS and Sherpa notes Jen Pan exclusion
    check_3a = "Jen Pan (PartnerAdvisory Delivery Lead / PM — Project LENS only" in nee_02_text
    check_3b = "Jen Pan has zero presence, role, or involvement on Project Beacon" in nee_02_text
    results["checks"]["nee_02_lens_roster"] = check_3a
    results["checks"]["nee_02_sherpa_exclusion"] = check_3b

    # Check 4: NEE_06 has Jen Pan in LENS pod with zero Sherpa role
    check_4 = "Jen Pan (PartnerAdvisory Delivery Lead / PM — Project LENS only; zero Project Beacon role)" in nee_06_text
    results["checks"]["nee_06_lens_pod"] = check_4

    all_passed = (
        check_1
        and check_2a
        and check_2b
        and check_3a
        and check_3b
        and check_4
    )
    results["pass"] = bool(all_passed)

    output_receipt.parent.mkdir(parents=True, exist_ok=True)
    output_receipt.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Receipt written to {output_receipt}")
    print(f"Wave 2 Card Boundaries Verification: {'PASS' if all_passed else 'FAIL'}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Wave 2 Knowledge Card boundaries.")
    parser.add_argument(
        "--cards-dir",
        type=Path,
        default=REPO_ROOT / "cards",
        help="Path to cards directory",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=REPO_ROOT / "reports" / "wave2_cards_boundary_receipt.json",
        help="Path to output receipt JSON",
    )
    args = parser.parse_args()
    results = verify_cards(args.cards_dir, args.receipt)
    return 0 if results["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
