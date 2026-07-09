# Distributing Whisper Dictation to the team

Goal: **one signed `.app` that any Mac opens with a double-click** — no building
from source, no Gatekeeper warnings.

## The one thing that unlocks it: an Apple Developer ID

Today the app is *self-signed*, so it only opens cleanly on the machine that
built it. To run on anyone else's Mac it must be **signed with an Apple
Developer ID** and **notarized** by Apple. Everything else is ready.

## ✅ Ask IT for

1. **Apple Developer Program — Organization** account. If your company has one, ask
   for a **"Developer ID Application" certificate** and the **Team ID**.
2. **Notarization credentials** for that account: an App Store Connect API key,
   or an Apple ID + app-specific password (used once via `notarytool
   store-credentials`).
3. Which **MDM** the company uses (Jamf / Kandji / Mosyle / Intune) — for the
   later, zero-friction rollout.
4. Any **security / IP / open-source policy** on internal tools, and whether this
   should live in a **company GitHub org** rather than a personal account.

## 🎯 Recommended rollout

**Phase 1 — Notarized `.dmg` (start here).** Needs only the Developer ID (step
1–2 above), no MDM. Run `./scripts/release.sh` → get a notarized
`Whisper-Dictation.dmg` → share it (Slack / Drive / internal page).
Teammates drag it to Applications and open it. Each grants Microphone +
Accessibility once (the app guides them). Best for a pilot / small team.

**Phase 2 — MDM (scale it).** When it catches on, hand IT the notarized build so
they push it via MDM as a `.pkg` **and** apply a **PPPC profile** that
pre-approves Microphone + Accessibility (keyed to our Team ID / bundle id
`io.github.jofer95.whisper-dictation`). Then non-technical folks get it
auto-installed with permissions already granted — zero friction.

## 🔧 How to cut a release (once the Developer ID exists)

```bash
# one-time: save notarization credentials
xcrun notarytool store-credentials whisper-notary \
  --apple-id "you@example.com" --team-id TEAMID1234 \
  --password "app-specific-password"

export DEV_ID="Developer ID Application: Your Company Inc (TEAMID1234)"
export NOTARY_PROFILE="whisper-notary"
./scripts/release.sh          # builds, signs (hardened runtime), notarizes, staples, makes the .dmg
```

Signing details live in [`packaging/entitlements.plist`](packaging/entitlements.plist)
(microphone + library-validation for the bundled Python) and
[`scripts/release.sh`](scripts/release.sh).

## First-run behavior

The app launches instantly; the speech model (~150 MB) downloads in the
background on first run only (menu shows ⏳ "loading model…"), then runs fully
offline forever. For a **fully air-gapped** build we can pre-bundle the model —
just ask.

## What's already done vs. pending

- ✅ Packaged `.app` (py2app), Hardened-Runtime entitlements, release script, `.dmg` step, PPPC guidance.
- ⏳ Pending: the Apple Developer ID (from IT) → then `release.sh` produces the distributable build.
