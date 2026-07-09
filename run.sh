#!/bin/bash
# Launch the dictation menu-bar app from its own virtualenv, regardless of the
# directory this script is invoked from.
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
exec python -m dictation "$@"
