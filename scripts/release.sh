#!/bin/bash
# Build a DISTRIBUTABLE, notarized .app + .dmg signed with a real Apple
# Developer ID — the version other people's Macs open without Gatekeeper
# warnings. Replaces the local self-signed flow (scripts/build.sh) for releases.
#
# Needs (see DISTRIBUTION.md for how to get them — Admin role required):
#   export DEV_ID="Developer ID Application: Your Company Inc. (TEAMID1234)"
#   export NOTARY_PROFILE="whisper-notary"   # saved once with:
#     xcrun notarytool store-credentials whisper-notary \
#       --key ~/keys/AuthKey_XXXXXXXXXX.p8 --key-id XXXXXXXXXX --issuer <issuer-uuid>
#
# Then:  ./scripts/release.sh
#
# Dry run without the Developer ID (checks the Hardened Runtime build launches;
# skips the .dmg + notarization):
#   DEV_ID="Whisper Dictation Dev" SIGN_ONLY=1 ./scripts/release.sh
set -euo pipefail
cd "$(dirname "$0")/.."

: "${DEV_ID:?set DEV_ID to your 'Developer ID Application: … (TEAMID)' identity}"
SIGN_ONLY="${SIGN_ONLY:-0}"
if [[ "$SIGN_ONLY" != "1" ]]; then
  : "${NOTARY_PROFILE:?set NOTARY_PROFILE to a saved notarytool credential profile}"
fi

APP="dist/Whisper Dictation.app"
DMG="dist/Whisper-Dictation.dmg"
ENTITLEMENTS="packaging/entitlements.plist"
TS="--timestamp"
[[ "$SIGN_ONLY" == "1" ]] && TS="--timestamp=none"   # no Apple timestamp server for local certs

echo "==> Building (py2app) ..."
source .venv/bin/activate
rm -rf build dist
# On-device AI cleanup helper (Swift, Xcode 26+). Lands in Resources/aicleanup;
# the Mach-O loop below signs it with Hardened Runtime like everything else.
packaging/aicleanup/build.sh
python setup.py py2app >/dev/null

sign() { codesign --force $TS --options runtime --sign "$DEV_ID" "$@"; }

echo "==> Signing every nested Mach-O binary with Hardened Runtime ..."
# Notarization rejects ANY unsigned Mach-O, not just *.so / *.dylib (py2app also
# ships executables and extension-less libraries). Deepest paths first.
count=0
while IFS= read -r f; do
  if file -b "$f" | grep -q "Mach-O"; then
    sign "$f"
    count=$((count + 1))
  fi
done < <(find "$APP/Contents" -type f ! -path "*/_CodeSignature/*" \
           | awk '{ print length, $0 }' | sort -rn | cut -d' ' -f2-)
echo "    signed $count binaries"

# Embedded frameworks as bundles, then the bundled python + the app itself
# (those two carry the entitlements: microphone + library validation off).
find "$APP/Contents/Frameworks" -maxdepth 1 -name "*.framework" -print0 2>/dev/null |
  while IFS= read -r -d '' fw; do sign "$fw"; done
for exe in "$APP/Contents/MacOS/"*; do
  sign --entitlements "$ENTITLEMENTS" "$exe"
done
sign --entitlements "$ENTITLEMENTS" "$APP"

echo "==> Verifying signature ..."
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -d --entitlements - "$APP" 2>/dev/null | grep -q audio-input \
  || { echo "entitlements missing on the app"; exit 1; }

echo "==> Smoke test (Hardened Runtime build boots + runs the engine) ..."
"$APP/Contents/MacOS/Whisper Dictation" --self-test | grep "SELF-TEST OK"

if [[ "$SIGN_ONLY" == "1" ]]; then
  echo ""
  echo "✅ Signed (no notarization): $APP"
  exit 0
fi

echo "==> Building .dmg ..."
rm -f "$DMG"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"      # drag-to-install
hdiutil create -volname "Whisper Dictation" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
sign "$DMG"

echo "==> Notarizing (uploads to Apple; waits) ..."
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait

echo "==> Stapling the ticket ..."
xcrun stapler staple "$DMG"
xcrun stapler staple "$APP"
spctl --assess --type open --context context:primary-signature -v "$DMG"

echo ""
echo "✅ Release ready: $DMG"
echo "   It opens cleanly on any Mac. Share the .dmg or hand it to IT/MDM."
