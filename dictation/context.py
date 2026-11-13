"""What is around the cursor right now — via the Accessibility API, the same
permission we already need for pasting. No screenshots, nothing stored.

Wispr Flow calls this "context awareness": it lets us put a space before the
dictation when you're continuing a sentence, lowercase "And" mid-sentence, drop
the final full stop when inserting in the middle of a line, never paste into a
password field, and notice when there is no text box at all.

Every call is bounded by a short AX messaging timeout: a hung app must never
stall dictation. Any failure simply means "unknown" and we fall back to the
plain behaviour.
"""

from dataclasses import dataclass

from .log import get_logger

log = get_logger()

AX_TIMEOUT = 0.25          # seconds per AX call
CONTEXT_CHARS = 3

_ERR_NO_VALUE = -25212     # kAXErrorNoValue: nothing has keyboard focus
TEXT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"}
NON_TEXT_ROLES = {
    "AXWindow", "AXApplication", "AXButton", "AXList", "AXOutline", "AXTable",
    "AXImage", "AXMenuBar", "AXMenu", "AXMenuItem", "AXRow", "AXCell",
    "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXSlider", "AXTabGroup",
}


@dataclass
class Context:
    bundle_id: str | None = None
    role: str | None = None
    secure: bool = False
    text_field: bool | None = None   # True / False / None = can't tell
    before: str | None = None        # chars just before the cursor (None = unknown)
    after: str | None = None         # chars just after the selection


def _get(el, attr):
    from ApplicationServices import AXUIElementCopyAttributeValue
    err, value = AXUIElementCopyAttributeValue(el, attr, None)
    return err, value


def _string_for_range(el, loc, length):
    from ApplicationServices import (AXUIElementCopyParameterizedAttributeValue,
                                     AXValueCreate, kAXValueCFRangeType)
    if length <= 0:
        return ""
    rng = AXValueCreate(kAXValueCFRangeType, (loc, length))
    err, value = AXUIElementCopyParameterizedAttributeValue(el, "AXStringForRange", rng, None)
    if err == 0 and value is not None:
        return str(value)
    return None


def _bundle_for_pid(pid):
    try:
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        return app.bundleIdentifier() if app is not None else None
    except Exception:
        return None


def focused_context() -> Context:
    ctx = Context()
    try:
        from ApplicationServices import (AXUIElementCreateSystemWide, AXUIElementGetPid,
                                         AXUIElementSetMessagingTimeout, AXValueGetValue,
                                         kAXValueCFRangeType)
        system = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(system, AX_TIMEOUT)

        err, app = _get(system, "AXFocusedApplication")
        if err == 0 and app is not None:
            perr, pid = AXUIElementGetPid(app, None)
            if perr == 0:
                ctx.bundle_id = _bundle_for_pid(pid)

        err, el = _get(system, "AXFocusedUIElement")
        if err == _ERR_NO_VALUE:
            ctx.text_field = False
            return ctx
        if err != 0 or el is None:
            return ctx
        AXUIElementSetMessagingTimeout(el, AX_TIMEOUT)

        _e, role = _get(el, "AXRole")
        _e, subrole = _get(el, "AXSubrole")
        ctx.role = str(role) if role else None
        ctx.secure = (subrole == "AXSecureTextField")

        err, rng_val = _get(el, "AXSelectedTextRange")
        rng = None
        if err == 0 and rng_val is not None:
            ok, rng = AXValueGetValue(rng_val, kAXValueCFRangeType, None)
            if not ok:
                rng = None

        if ctx.role in TEXT_ROLES or rng is not None:
            ctx.text_field = True
        elif ctx.role in NON_TEXT_ROLES:
            ctx.text_field = False

        if rng is not None and not ctx.secure:
            if isinstance(rng, tuple):       # pyobjc returns CFRange as (loc, len)
                loc, length = int(rng[0]), int(rng[1])
            else:
                loc, length = int(rng.location), int(rng.length)
            start = max(0, loc - CONTEXT_CHARS)
            ctx.before = _string_for_range(el, start, loc - start)
            ctx.after = _string_for_range(el, loc + length, CONTEXT_CHARS)
    except Exception:
        log.exception("reading accessibility context failed")
    return ctx
