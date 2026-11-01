import argparse
import hashlib
import hmac
import json
import sys
from pathlib import Path

# Mock Ed25519 Secret Key for local deterministic gating
# In production, this resides in a secure hardware enclave or KMS
MOCK_SECRET_KEY = b"operator-enclave-secret-key"

def generate_signature(payload: str) -> str:
    """Simulates an Ed25519 signature using HMAC-SHA256 for deterministic gating."""
    return hmac.new(MOCK_SECRET_KEY, payload.encode('utf-8'), hashlib.sha256).hexdigest()

def verify_ledger(ledger_path: str) -> bool:
    path = Path(ledger_path)
    if not path.exists():
        print(f"Error: Ledger file {ledger_path} not found.")
        return False

    with open(path, 'r') as f:
        ledger = json.load(f)

    decisions = ledger.get("decisions", [])
    if not decisions:
        print("Warning: Ledger is empty.")

    for entry in decisions:
        alr_id = entry.get("alr_id")
        resolution = entry.get("resolution")
        claimed_sig = entry.get("signature")

        # Reconstruct the canonical payload
        canonical_payload = f"{alr_id}:{resolution}"
        expected_sig = generate_signature(canonical_payload)

        if not hmac.compare_digest(claimed_sig, expected_sig):
            print(f"Cryptographic Trust Violation: Signature mismatch for {alr_id}")
            return False

    print(f"Cryptographic verification passed. All {len(decisions)} [OPERATOR VERIFIED] seals are authentic.")
    return True

def main():
    parser = argparse.ArgumentParser(description="Govern HITL Cryptographic Signatures")
    parser.add_argument("--verify", action="store_true", help="Verify the signatures in the ledger")
    parser.add_argument("--ledger-path", type=str, default="hitl_decision_ledger.json", help="Path to the ledger JSON")
    args = parser.parse_args()

    if args.verify:
        if verify_ledger(args.ledger_path):
            sys.exit(0)
        else:
            sys.exit(1)

if __name__ == "__main__":
    main()
