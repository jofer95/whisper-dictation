# Distributing Whisper Dictation to the team

Goal: **one signed, notarized `.dmg` that any Mac opens with a double-click** —
no building from source, no Gatekeeper warnings.

## 📍 Where we are

| | Status |
|---|---|
| Packaged `.app` (py2app), Hardened Runtime entitlements | ✅ done |
| `scripts/release.sh` — signs every binary, verifies, smoke-tests, builds the `.dmg`, notarizes, staples | ✅ ready |
| Hardened Runtime build boots and runs the engine | ✅ verified with a dry run (`SIGN_ONLY=1`, local cert) |
| Apple Developer Program account | ✅ exists — organization **Your Company Inc.** (the company's legal entity) |
| **Your role in that account** | ⏳ currently **Developer** — creating a *Developer ID* certificate and an App Store Connect API key needs **Admin** (or Account Holder) |
| "Developer ID Application" certificate | ⏳ blocked on the role |
| Notarization credentials (API key) | ⏳ blocked on the role |
| Auto-update | ⏳ not started (see below) |

Until the Developer ID exists, builds are signed with a **self-signed** local
identity (`scripts/build.sh`): they run perfectly on the machine that built
them, but other Macs will refuse to open them.

## 1. Get the role (one message)

Ask the Account Holder / an Admin of the Your Company Inc. Apple Developer account:

> I'm preparing internal distribution of a Mac dictation tool and need to sign
> and notarize it with our Apple Developer account. I'm currently a
> **Developer**; creating a **Developer ID Application** certificate and an
> **App Store Connect API key** (for notarization) requires **Admin**. Could you
> make me Admin in App Store Connect → Users and Access? Nothing gets published
> to the App Store.

**Plan B — no Admin:** an Admin can do the two steps below for you. For the
certificate, keep the private key on *your* Mac: Keychain Access → Certificate
Assistant → *Request a Certificate From a Certificate Authority…* → "Saved to
disk"; send them the `.certSigningRequest`, they upload it under
Certificates → **Developer ID Application** and send back the `.cer`, which you
double-click to install. For notarization, they create the API key and send you
the `.p8` + Key ID + Issuer ID.

## 2. Create the certificate (Admin, ~2 minutes)

1. Xcode → **Settings → Accounts** → add the Apple ID that is on the YourCompany team.
2. Select the team → **Manage Certificates… → + → Developer ID Application**.
3. Check it landed (and copy the exact name):

   ```bash
   security find-identity -v -p codesigning | grep "Developer ID Application"
   # → "Developer ID Application: Your Company Inc. (TEAMID1234)"
   ```

⚠️ Not "Apple Development", "Apple Distribution" or "Mac App Store" — those are
for other things. Back up the certificate **with its private key** (Keychain
Access → export `.p12`); a team only gets a few Developer ID certificates.

## 3. Notarization credentials (Admin, ~2 minutes)

1. [App Store Connect](https://appstoreconnect.apple.com) → **Users and Access →
   Integrations → App Store Connect API** → generate a **Team key** ("Developer"
   access is enough).
2. Download the `.p8` (**only downloadable once**) and note the **Key ID** and
   **Issuer ID**.
3. Save them in the keychain once:

   ```bash
   xcrun notarytool store-credentials whisper-notary \
     --key ~/keys/AuthKey_XXXXXXXXXX.p8 --key-id XXXXXXXXXX --issuer <issuer-uuid>
   ```

An API key (rather than an Apple ID + app-specific password) doesn't expire and
isn't tied to one person, so it can move to CI later.

## 4. Cut a release

```bash
export DEV_ID="Developer ID Application: Your Company Inc. (TEAMID1234)"
export NOTARY_PROFILE="whisper-notary"
./scripts/release.sh
# → dist/Whisper-Dictation.dmg  (signed, notarized, stapled)
```

Before the first real release, the dry run checks everything except Apple's
servers (no Developer ID needed):

```bash
DEV_ID="Whisper Dictation Dev" SIGN_ONLY=1 ./scripts/release.sh
```

Test the `.dmg` on a Mac that has **never** built the app: double-click → drag
to Applications → open. No warning dialog = done.

## 🎯 Rollout

**Phase 1 — notarized `.dmg` (start here).** Share it on Slack / Drive.
Teammates drag it to Applications and open it; each grants Microphone +
Accessibility once (the app guides them).

**Phase 2 — MDM (scale it).** Hand IT the notarized build so they push it via
MDM (Jamf / Kandji / Mosyle / Intune) as a `.pkg`, plus a **PPPC profile** that
pre-approves Microphone + Accessibility for bundle id
`io.github.jofer95.whisper-dictation` and our Team ID. Non-technical folks then
get it installed with permissions already granted.

## 🔄 Updates

- **Today:** each release is a new `.dmg`; installing it over the old app keeps
  settings, dictionary and history (they live in
  `~/Library/Application Support/whisper-dictation-hcp/`). Because the signing
  identity stays the same, macOS keeps the Microphone/Accessibility grants.
- **Next (pending):** in-app auto-update with [Sparkle](https://sparkle-project.org/)
  — needs a place to host an appcast + the `.dmg` (e.g. GitHub Releases) and an
  EdDSA update-signing key. Only worth it once the notarized build exists.
- With MDM, IT pushes new versions instead.

## First-run behavior

The app launches instantly; the speech model (~460 MB for `small`, 1.6 GB for
Large v3 Turbo) downloads in the background on first use only (menu shows ⏳),
then runs fully offline. For a fully air-gapped build we can pre-bundle the
model.
