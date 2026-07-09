#!/bin/bash
# Fast-test a pull request end to end: check it out, install any new deps,
# build + sign, install to /Applications, and launch it.
#
# Usage:  ./scripts/test-pr.sh <pr-number>      e.g.  ./scripts/test-pr.sh 1
#
# Go back to your own version afterwards with:
#   git checkout main && ./scripts/build.sh && \
#     cp -R "dist/Whisper Dictation HCP.app" /Applications/
set -euo pipefail
cd "$(dirname "$0")/.."

PR="${1:?usage: ./scripts/test-pr.sh <pr-number>}"

echo "==> Checking out PR #$PR ..."
gh pr checkout "$PR"

echo "==> Installing any new dependencies ..."
source .venv/bin/activate
pip install -q -r requirements.txt

echo "==> Building + signing ..."
./scripts/build.sh

echo "==> Installing + launching ..."
pkill -f "Whisper Dictation HCP" 2>/dev/null || true
rm -rf "/Applications/Whisper Dictation HCP.app"
cp -R "dist/Whisper Dictation HCP.app" /Applications/
open "/Applications/Whisper Dictation HCP.app"

echo ""
echo "✅ Running PR #$PR."
echo "   Back to your version:  git checkout main && ./scripts/build.sh"
