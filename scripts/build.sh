#!/bin/bash
# Build + sign the app in one step. Always signs with the stable identity so
# macOS keeps the Microphone/Accessibility grants across rebuilds.
set -euo pipefail
cd "$(dirname "$0")/.."

source .venv/bin/activate
rm -rf build dist
python setup.py py2app
scripts/sign_selfsigned.sh "dist/Whisper Dictation.app"

echo ""
echo "✅ Built + signed: dist/Whisper Dictation.app"
echo "   Install:  rm -rf '/Applications/Whisper Dictation.app' && cp -R 'dist/Whisper Dictation.app' /Applications/"
