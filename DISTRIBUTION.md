# Distributing Whisper Dictation

Goal: **one signed, notarized `.dmg` that any Mac opens with a double-click** —
no building from source, no Gatekeeper warnings.

## How signing works here

| Build | Command | Opens on |
|---|---|---|
| Local / personal | `./scripts/build.sh` — signs with a **self-signed** identity it creates once | only the Mac that built it |
| Distributable | `./scripts/release.sh` — **Developer ID** + Hardened Runtime + notarization + `.dmg` | any Mac |

`release.sh` signs every nested binary (py2app ships many), verifies the
signature and entitlements, smoke-tests the Hardened Runtime build, builds a
drag-to-Applications `.dmg`, notarizes it with Apple and staples the ticket.
The Hardened Runtime build has been verified to boot and run the engine.

## What you need

An [Apple Developer Program](https://developer.apple.com/programs/) membership
(individual or organization) and, in it, a role that can create **Developer ID**
certificates and App Store Connect API keys — **Account Holder or Admin**. With
the plain *Developer* role you won't see *Integrations* in App Store Connect and
can't create a Developer ID certificate; ask an Admin to raise your role or to do
steps 1–2 for you.

> **Keeping your private key on your own Mac when someone else creates the
> certificate:** Keychain Access → Certificate Assistant → *Request a Certificate
> From a Certificate Authority…* → "Saved to disk". The Admin uploads that
> `.certSigningRequest` under Certificates → **Developer ID Application** and
> sends you back the `.cer`; double-click it to install.

## 1. Developer ID certificate (~2 minutes)

1. Xcode → **Settings → Accounts** → add your Apple ID.
2. Select the team → **Manage Certificates… → + → Developer ID Application**.
3. Copy its exact name:

   ```bash
   security find-identity -v -p codesigning | grep "Developer ID Application"
   # → "Developer ID Application: Your Name or Company (TEAMID1234)"
   ```

⚠️ Not "Apple Development", "Apple Distribution" or "Mac App Store" — those are
for other things. Back up the certificate **with its private key** (Keychain
Access → export `.p12`); a team only gets a few Developer ID certificates.

## 2. Notarization credentials (~2 minutes)

1. [App Store Connect](https://appstoreconnect.apple.com) → **Users and Access →
   Integrations → App Store Connect API** → generate a **Team key** ("Developer"
   access is enough).
2. Download the `.p8` (**only downloadable once**) and note the **Key ID** and
   **Issuer ID**.
3. Save them in your keychain once:

   ```bash
   xcrun notarytool store-credentials whisper-notary \
     --key ~/keys/AuthKey_XXXXXXXXXX.p8 --key-id XXXXXXXXXX --issuer <issuer-uuid>
   ```

An API key (rather than an Apple ID + app-specific password) doesn't expire and
isn't tied to one person, so it can move to CI later.

## 3. Cut a release

```bash
export DEV_ID="Developer ID Application: Your Name or Company (TEAMID1234)"
export NOTARY_PROFILE="whisper-notary"
./scripts/release.sh
# → dist/Whisper-Dictation.dmg  (signed, notarized, stapled)
```

Dry run without a Developer ID — checks everything except Apple's servers:

```bash
DEV_ID="Whisper Dictation Dev" SIGN_ONLY=1 ./scripts/release.sh
```

Test the `.dmg` on a Mac that has **never** built the app: double-click → drag
to Applications → open. No warning dialog = done.

## Rolling it out

**Small team:** share the notarized `.dmg`. People drag it to Applications and
open it; each grants Microphone + Accessibility once (the app guides them).

**Managed Macs (MDM — Jamf, Kandji, Mosyle, Intune…):** push the notarized build
as a `.pkg` together with a **PPPC profile** that pre-approves Microphone and
Accessibility for bundle id `io.github.jofer95.whisper-dictation` and your Team
ID. If you ship your own fork, change `CFBundleIdentifier` in `setup.py` first.

## Updates

- **Today:** each release is a new `.dmg`; installing it over the old app keeps
  settings, dictionary and history (`~/Library/Application Support/whisper-dictation/`).
  With the same signing identity, macOS keeps the Microphone/Accessibility grants.
- **Planned:** in-app auto-update with [Sparkle](https://sparkle-project.org/)
  (appcast + `.dmg` hosted on GitHub Releases, EdDSA update-signing key).

## First-run behavior

The app launches instantly; the speech model (~460 MB for `small`, 1.6 GB for
Large v3 Turbo) downloads in the background on first use only (menu shows ⏳),
then runs fully offline. For a fully air-gapped build, pre-bundle the model.
