"""py2app build config for Whisper Dictation.

Build the .app:
    source .venv/bin/activate
    pip install py2app
    python setup.py py2app          # full, distributable bundle -> dist/

The Info.plist below is already MDM/signing-ready: stable bundle id, versioning,
LSUIElement (menu-bar only, no Dock icon) and the microphone usage string
Apple requires. To sign + notarize later:
    codesign --deep --force --options runtime --sign "Developer ID Application: <ORG>" \
        "dist/Whisper Dictation.app"
    xcrun notarytool submit ... && xcrun stapler staple "dist/Whisper Dictation.app"
"""

from setuptools import setup

APP = ["app_main.py"]

# List heavy/native packages explicitly so py2app copies them wholesale — its
# static import graph misses the dynamic imports these libraries use.
PACKAGES = [
    "dictation",
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "av",
    "tokenizers",
    "huggingface_hub",
    "sounddevice",
    "_sounddevice_data",  # holds libportaudio.dylib — MUST stay unzipped so dlopen works
    "numpy",
    "rumps",
    "pynput",
    "AVFoundation",  # microphone permission status/request
]

PLIST = {
    "CFBundleName": "Whisper Dictation",
    "CFBundleDisplayName": "Whisper Dictation",
    "CFBundleIdentifier": "io.github.jofer95.whisper-dictation",
    "CFBundleVersion": "1.11.0",
    "CFBundleShortVersionString": "1.11.0",
    "LSMinimumSystemVersion": "11.0",
    "LSUIElement": True,  # menu-bar app: no Dock icon, no main window
    "LSMultipleInstancesProhibited": True,  # never run two copies at once
    "NSMicrophoneUsageDescription": (
        "Whisper Dictation transcribes your speech to text locally on this Mac. "
        "Audio never leaves your device."
    ),
}

OPTIONS = {
    "argv_emulation": False,  # True needs Carbon and breaks pynput on modern macOS
    "packages": PACKAGES,
    "plist": PLIST,
}

setup(
    name="Whisper Dictation",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
