import hashlib
import json
import sys
from pathlib import Path

from huggingface_hub import snapshot_download


def hash_dir(path: Path) -> str:
    hasher = hashlib.sha256()
    for f in sorted(path.rglob("*")):
        if f.is_file():
            hasher.update(f.relative_to(path).as_posix().encode())
            with f.open("rb") as fp:
                while chunk := fp.read(8192):
                    hasher.update(chunk)
    return hasher.hexdigest()

def main():
    repos = [
        "mlx-community/whisper-large-v3-mlx",  # noqa: SSOT_EXEMPT
        "pyannote/speaker-diarization-3.1",  # noqa: SSOT_EXEMPT
        "pyannote/segmentation-3.0",
    ]
    receipt = {}
    for repo in repos:
        try:
            # We want to capture the pin without network if possible
            path = snapshot_download(repo_id=repo, local_files_only=True)
            rev_path = Path(path).parent.parent / "refs" / "main"
            if rev_path.exists():
                rev = rev_path.read_text().strip()
            else:
                rev = Path(path).name # The snapshot dir name is usually the rev
            digest = hash_dir(Path(path))
            receipt[repo] = {"revision": rev, "snapshot_sha256": digest}
        except Exception as e:
            print(f"Failed to capture {repo}: {e}", file=sys.stderr)
            sys.exit(1)

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "acoustic_pin_capture_receipt.json"
    out_path.write_text(json.dumps(receipt, indent=2))
    print(f"Wrote {out_path}")

if __name__ == "__main__":
    main()
