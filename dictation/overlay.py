"""Floating waveform overlay — visual proof the mic is actually capturing.

A borderless, non-activating NSPanel that floats above every app (like
WisperFlow's pill). A single custom view draws the cream pill and scrolls a live
level history, so flat bars = no audio (wrong mic / no permission) and moving
bars = capture is working. Once recording stops, the same pill flips to a
spinning "loading" state while the audio is transcribed, then hides.

Everything visual runs on the main thread: show()/begin_loading()/hide() are
called from the app's start/stop (which only fire on the run loop) and the
redraw is driven by an NSTimer on the main run loop. finish() is the one call
that may come from the transcription worker thread, so it hops to the main
thread before touching the window. The audio thread only writes a single float
level on the recorder; we read it here.
"""

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTimer,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSMakeRect

from .log import get_logger

log = get_logger()

PANEL_W, PANEL_H = 60.0, 32.0
BARS = 9
BAR_W, GAP = 3.0, 3.0
FPS = 30.0
# Speech peaks sit around 0.05–0.4; scale so normal talking nearly fills the
# pill. ponytail: fixed gain, expose a knob if mics vary too much.
GAIN = 6.0
BOTTOM_MARGIN = 24.0  # px above the screen bottom
# Peak above which we consider the stream to be actually catching your voice
# (above ambient hiss). ponytail: calibration knob — raise if noise trips it.
VOICE_ON = 0.03

_CREAM = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.969, 0.953, 0.914, 0.96)
_INK = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.10, 0.10, 1.0)
_GREY = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.62, 0.62, 1.0)


class _WaveView(NSView):
    """Draws the pill + a scrolling bar history. Owns its own redraw timer."""

    def initWithFrame_(self, frame):
        self = objc.super(_WaveView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._levels = [0.0] * BARS
        self._level_fn = None
        self._timer = None
        self._active = False  # grey until the mic actually catches your voice
        self._mode = "wave"   # "wave" while recording, "loading" while transcribing
        self._spin = 0.0      # spinner angle (degrees) for the loading state
        return self

    def startAnimating(self):
        self._levels = [0.0] * BARS
        self._active = False
        self._mode = "wave"
        if self._timer is None:
            self._timer = (
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    1.0 / FPS, self, b"tick:", None, True
                )
            )

    def setLoading(self):
        self._mode = "loading"
        self.setNeedsDisplay_(True)

    def stopAnimating(self):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def finishOnMain(self):
        """Stop + order the pill out, always on the main thread (finish() routes
        the transcription worker here via performSelectorOnMainThread)."""
        self.stopAnimating()
        window = self.window()
        if window is not None:
            window.orderOut_(None)

    def tick_(self, _timer):
        if self._mode == "loading":
            self._spin = (self._spin - 12.0) % 360.0   # clockwise
            self.setNeedsDisplay_(True)
            return
        level = 0.0
        if self._level_fn is not None:
            try:
                level = float(self._level_fn())
            except Exception:
                level = 0.0
        if level >= VOICE_ON:
            self._active = True  # latch: once your voice lands, stay "normal"
        self._levels = self._levels[1:] + [level]
        self.setNeedsDisplay_(True)

    def drawRect_(self, _rect):
        loading = self._mode == "loading"
        color = _INK if (loading or self._active) else _GREY
        pill = NSMakeRect(1.5, 1.5, PANEL_W - 3, PANEL_H - 3)
        radius = (PANEL_H - 3) / 2.0
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(pill, radius, radius)
        _CREAM.setFill()
        path.fill()
        color.setStroke()
        path.setLineWidth_(2.0)
        path.stroke()

        if loading:
            self._draw_spinner()
            return

        color.setFill()
        span = BARS * BAR_W + (BARS - 1) * GAP
        x = (PANEL_W - span) / 2.0
        mid = PANEL_H / 2.0
        max_h = PANEL_H - 12.0
        for level in self._levels:
            norm = min(1.0, level * GAIN)
            bar_h = max(2.0, norm * max_h)
            bar = NSMakeRect(x, mid - bar_h / 2.0, BAR_W, bar_h)
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar, BAR_W / 2.0, BAR_W / 2.0
            ).fill()
            x += BAR_W + GAP

    def _draw_spinner(self):
        # A 270° arc that rotates — the universal "loading" look, sized to the pill.
        r = PANEL_H / 2.0 - 9.0
        center = (PANEL_W / 2.0, PANEL_H / 2.0)
        arc = NSBezierPath.bezierPath()
        arc.setLineWidth_(2.5)
        arc.setLineCapStyle_(1)  # round caps
        arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
            center, r, self._spin, self._spin + 270.0)
        _INK.setStroke()
        arc.stroke()


class WaveformOverlay:
    """Show/hide a floating waveform pill. `level_fn` returns the current 0..1 mic level."""

    def __init__(self, level_fn):
        self._level_fn = level_fn
        self._panel = None
        self._view = None

    def _build(self):
        rect = NSMakeRect(0, 0, PANEL_W, PANEL_H)
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setIgnoresMouseEvents_(True)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        view = _WaveView.alloc().initWithFrame_(rect)
        view._level_fn = self._level_fn
        panel.setContentView_(view)
        self._panel, self._view = panel, view

    def _center_bottom(self):
        screen = NSScreen.mainScreen()
        if screen is None:
            return
        vf = screen.frame()
        x = vf.origin.x + (vf.size.width - PANEL_W) / 2.0
        y = vf.origin.y + BOTTOM_MARGIN
        self._panel.setFrameOrigin_((x, y))

    def show(self):
        try:
            if self._panel is None:
                self._build()
            self._center_bottom()
            self._panel.orderFrontRegardless()
            self._view.startAnimating()
        except Exception:
            log.exception("could not show waveform overlay")

    def begin_loading(self):
        """Flip the visible pill to the spinning loading state (main thread)."""
        try:
            if self._view is not None:
                self._view.setLoading()
        except Exception:
            log.exception("could not switch overlay to loading")

    def finish(self):
        """Hide the pill once transcription is done. Safe to call from the
        worker thread — it hops to the main thread before touching the window."""
        try:
            if self._view is not None:
                self._view.performSelectorOnMainThread_withObject_waitUntilDone_(
                    b"finishOnMain", None, False)
        except Exception:
            log.exception("could not finish waveform overlay")

    def hide(self):
        try:
            if self._view is not None:
                self._view.stopAnimating()
            if self._panel is not None:
                self._panel.orderOut_(None)
        except Exception:
            log.exception("could not hide waveform overlay")
