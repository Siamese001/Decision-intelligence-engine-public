#!/usr/bin/env sh
set -eu

# Resolve repository root or worktree root
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

# Check required hooks in .githooks/
REQUIRED_HOOKS="pre-commit pre-push post-checkout post-commit post-merge commit-msg"
for hook in $REQUIRED_HOOKS; do
  hook_path="$REPO_ROOT/.githooks/$hook"
  if [ ! -f "$hook_path" ]; then
    echo "❌ ERROR: Required repository hook is missing: $hook_path" >&2
    echo "   Remediation: Ensure .githooks/$hook exists in the repository checkout." >&2
    exit 1
  fi
  chmod +x "$hook_path" 2>/dev/null || true
done

if [ -f "$REPO_ROOT/.githooks/_common.sh" ]; then
  chmod +x "$REPO_ROOT/.githooks/_common.sh" 2>/dev/null || true
fi

# Configure core.hooksPath to .githooks
git -C "$REPO_ROOT" config core.hooksPath .githooks

# Verify configuration
CONFIGURED=$(git -C "$REPO_ROOT" config --get core.hooksPath 2>/dev/null || echo "")
if [ "$CONFIGURED" != ".githooks" ]; then
  echo "❌ ERROR: core.hooksPath validation failed. Expected '.githooks', got '$CONFIGURED'." >&2
  echo "   Remediation: Run 'git config core.hooksPath .githooks' manually in $REPO_ROOT." >&2
  exit 1
fi

echo "✅ Repository hooks installed successfully at .githooks (idempotent)."
exit 0
