#!/bin/bash
# Sign an .app with a STABLE, trusted self-signed identity so macOS TCC
# permissions (Microphone, Accessibility) persist across rebuilds — unlike
# ad-hoc signing, whose identity changes every build and forces re-granting.
#
# Idempotent: creates + trusts the cert once (prompts for your login password
# that one time), then just re-signs on subsequent runs.
#
# Usage: scripts/sign_selfsigned.sh "dist/Whisper Dictation.app"
#
# NOTE: self-signed DEV identity, trusted only on THIS Mac. For company MDM
# distribution, swap it for a Developer ID / org cert + notarization — the
# codesign command is otherwise identical.
set -euo pipefail

APP="${1:?usage: sign_selfsigned.sh <app-path>}"
CERT_CN="Whisper Dictation Dev"
LOGIN_KC="$HOME/Library/Keychains/login.keychain-db"
OPENSSL="/usr/bin/openssl"   # system LibreSSL -> p12 compatible with `security import`

if security find-identity -v -p codesigning | grep -q "$CERT_CN"; then
  echo "Trusted signing identity '$CERT_CN' already present — reusing it."
else
  echo ">>> Creating + trusting self-signed identity '$CERT_CN'."
  echo ">>> macOS will ask for your login password ONCE to trust it."
  TMP="$(mktemp -d)"
  cat > "$TMP/cs.cnf" <<EOF
[req]
distinguished_name=dn
x509_extensions=v3
prompt=no
[dn]
CN=$CERT_CN
[v3]
basicConstraints=critical,CA:false
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
EOF
  "$OPENSSL" req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "$TMP/cs.key" -out "$TMP/cs.crt" -config "$TMP/cs.cnf"
  "$OPENSSL" pkcs12 -export -inkey "$TMP/cs.key" -in "$TMP/cs.crt" \
    -out "$TMP/cs.p12" -passout pass:temp -name "$CERT_CN"

  # Import key+cert (accessible to codesign with no per-sign prompt), then trust
  # the cert for the code-signing policy in the user domain.
  security import "$TMP/cs.p12" -k "$LOGIN_KC" -P temp -A -T /usr/bin/codesign
  security add-trusted-cert -r trustRoot -p codeSign -k "$LOGIN_KC" "$TMP/cs.crt"
  rm -rf "$TMP"
fi

echo "Signing $APP ..."
# --deep doesn't descend into Resources: sign the Swift helper explicitly, BEFORE
# the bundle (signing it afterwards would break the app's resource seal).
HELPER="$APP/Contents/Resources/aicleanup"
if [ -f "$HELPER" ]; then
  codesign --force -s "$CERT_CN" "$HELPER"
fi
codesign --deep --force -s "$CERT_CN" "$APP"
codesign --verify --deep --strict "$APP" && echo "signature verifies ✅"
codesign -dvv "$APP" 2>&1 | grep -iE "Authority=|Identifier=" || true
