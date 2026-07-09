#!/bin/bash
# Build a DISTRIBUTABLE, notarized .app + .dmg signed with a real Apple
# Developer ID — the version other people's Macs will open without Gatekeeper
# warnings. Replaces the local self-signed flow (scripts/build.sh) for releases.
#
# ⚠️ REQUIRES an Apple Developer ID (from the Apple Developer Program). Until
#    IT/you provide one, this script can't run — it's staged and ready.
#
# Set these first (ask IT for the values):
#   export DEV_ID="Developer ID Application: Your Company Inc (TEAMID1234)"
#   export NOTARY_PROFILE="whisper-notary"   # a saved notarytool credential:
#     xcrun notarytool store-credentials whisper-notary \
#       --apple-id "you@example.com" --team-id TEAMID1234 \
#       --password "app-specific-password"
#
# Then:  ./scripts/release.sh
set -euo pipefail
cd "$(dirname "$0")/.."

: "${DEV_ID:?set DEV_ID to your 'Developer ID Application: … (TEAMID)' identity}"
: "${NOTARY_PROFILE:?set NOTARY_PROFILE to a saved notarytool credential profile}"

APP="dist/Whisper Dictation.app"
DMG="dist/Whisper-Dictation.dmg"
ENTITLEMENTS="packaging/entitlements.plist"

echo "==> Building (py2app) ..."
source .venv/bin/activate
rm -rf build dist
python setup.py py2app

echo "==> Signing every nested binary with Hardened Runtime ..."
# Sign inner dylibs/.so first (deepest first), then frameworks, then the app.
find "$APP/Contents" \( -name "*.dylib" -o -name "*.so" \) -print0 |
  while IFS= read -r -d '' lib; do
    codesign --force --timestamp --options runtime --sign "$DEV_ID" "$lib"
  done
# The embedded python executable, then the app bundle itself.
codesign --force --timestamp --options runtime --entitlements "$ENTITLEMENTS" \
  --sign "$DEV_ID" "$APP/Contents/MacOS/python" 2>/dev/null || true
codesign --force --timestamp --options runtime --entitlements "$ENTITLEMENTS" \
  --sign "$DEV_ID" "$APP"

echo "==> Verifying signature ..."
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> Building .dmg ..."
rm -f "$DMG"
hdiutil create -volname "Whisper Dictation" -srcfolder "$APP" -ov -format UDZO "$DMG"

echo "==> Notarizing (uploads to Apple; waits) ..."
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait

echo "==> Stapling the ticket ..."
xcrun stapler staple "$DMG"
xcrun stapler staple "$APP"

echo ""
echo "✅ Release ready: $DMG"
echo "   It will open cleanly on any Mac. Share the .dmg or hand it to IT/MDM."
