#!/bin/bash
# Compile the Foundation Models helper (Swift) into packaging/aicleanup/build/aicleanup.
# Needs Xcode 26+ (macOS 26 SDK). Apple silicon only — the on-device model
# doesn't exist on Intel. setup.py bundles the binary into the .app's Resources.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build
SDK="$(xcrun --sdk macosx --show-sdk-path)"
xcrun --sdk macosx swiftc -O -parse-as-library \
  -target arm64-apple-macos26.0 -sdk "$SDK" \
  main.swift -o build/aicleanup
echo "built packaging/aicleanup/build/aicleanup"
