import sys
from pathlib import Path


def main():
    hooks_file = Path(".agents/hooks.json")
    if not hooks_file.exists():
        return 0

    content = hooks_file.read_text(encoding="utf-8")
    if "(See AGENTS.md" in content:
        print("ERROR: Found corrupted '(See AGENTS.md' string in hooks.json")
        return 1

    print("PASS: No corrupted JSON patterns found.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
