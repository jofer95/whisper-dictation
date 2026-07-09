"""Insert transcribed text into whatever field currently has focus.

Two strategies:

  * "paste" (default): put the text on the clipboard and send Cmd+V, then
    restore the previous clipboard. This is fast and — crucially for Spanish —
    inserts accented characters (á é í ó ú ñ ¿ ¡) reliably, which per-character
    synthetic typing often mangles.

  * "type": synthesize each keystroke with pynput. Slower and less robust with
    accents, but useful in the rare app that ignores paste.

Both require macOS Accessibility permission for the host process.
"""

import threading
import time

from pynput import keyboard

# NSPasteboard comes from pyobjc, which rumps already depends on — no new deps.
from AppKit import NSPasteboard, NSPasteboardTypeString

from .log import get_logger

log = get_logger()


class TextInjector:
    def __init__(self, mode: str = "paste", type_delay: float = 0.0025):
        self.mode = mode
        self.type_delay = type_delay
        self._kb = keyboard.Controller()

    def insert(self, text: str) -> None:
        if not text:
            return
        try:
            if self.mode == "type":
                self._insert_by_typing(text)
            else:
                self._insert_by_paste(text)
            log.info(f"injected {len(text)} chars via {self.mode}")
        except Exception:
            # Almost always missing Accessibility permission.
            log.exception("text injection failed (grant Accessibility permission?)")
            raise

    def _insert_by_typing(self, text: str) -> None:
        for ch in text:
            try:
                self._kb.type(ch)
                time.sleep(self.type_delay)
            except Exception:
                # A single unsupported glyph shouldn't abort the whole insert.
                continue

    def _insert_by_paste(self, text: str) -> None:
        pb = NSPasteboard.generalPasteboard()
        previous = pb.stringForType_(NSPasteboardTypeString)

        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)

        with self._kb.pressed(keyboard.Key.cmd):
            self._kb.press("v")
            self._kb.release("v")

        # Restore the user's prior clipboard shortly after the paste registers.
        def _restore():
            time.sleep(0.4)
            if previous is not None:
                pb.clearContents()
                pb.setString_forType_(previous, NSPasteboardTypeString)

        threading.Thread(target=_restore, daemon=True).start()
