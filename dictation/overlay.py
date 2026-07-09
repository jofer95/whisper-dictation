"""Floating recording overlay — visual proof the mic is actually capturing.

A borderless, non-activating NSPanel that floats above every app (like
WisperFlow's pill). Four selectable styles:

  * cream  — the original hand-drawn cream pill.
  * glass  — Liquid Glass: a real NSVisualEffectView frost that adapts to
             light/dark, with a blue→purple gradient waveform.
  * hud    — Dark HUD glass (Control-Center vibe), white waveform.
  * aurora — glass with a slowly rotating conic color sheen behind the bars.

A live level history scrolls across the bars (flat = no audio, moving = capture
works). When recording stops the pill flips to a spinner while transcribing,
then hides.

Everything visual runs on the main thread; finish() is the one call that may
come from the transcription worker, so it hops to the main thread first.
"""

import math

import objc
from AppKit import (
    NSAppearance,
    NSAppearanceNameVibrantDark,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSGradient,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTimer,
    NSView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectMaterialPopover,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)

from .log import get_logger

log = get_logger()

PANEL_W, PANEL_H = 60.0, 32.0
BARS = 9
BAR_W, GAP = 3.0, 3.0
FPS = 30.0
GAIN = 6.0            # speech peaks ~0.05–0.4; scale so talking nearly fills the pill
BOTTOM_MARGIN = 24.0  # px above the screen bottom
VOICE_ON = 0.03       # peak above which we treat the stream as catching your voice

STYLES = ("cream", "glass", "hud", "aurora")
DEFAULT_STYLE = "glass"

# Palette
_CREAM = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.969, 0.953, 0.914, 0.96)
_INK = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.10, 0.10, 1.0)
_GREY = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.62, 0.62, 1.0)
_WHITE = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.97, 0.98, 1.0, 1.0)
_WHITE_DIM = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.45)
_GLASS_A = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.49, 0.69, 1.0, 1.0)   # #7db0ff
_GLASS_B = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.69, 0.49, 1.0, 1.0)   # #b07cff
_GLASS_GRAD = NSGradient.alloc().initWithStartingColor_endingColor_(_GLASS_A, _GLASS_B)
_GLASS_ACCENT = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.60, 0.55, 1.0, 1.0)


class _WaveView(NSView):
    """Draws the waveform bars (and, for the cream style, the pill shell too).
    For glass styles the background is an NSVisualEffectView underneath, so this
    view stays transparent and only paints bars."""

    def initWithFrame_(self, frame):
        self = objc.super(_WaveView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._levels = [0.0] * BARS
        self._level_fn = None
        self._timer = None
        self._active = False
        self._mode = "wave"
        self._spin = 0.0
        self._style = DEFAULT_STYLE
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
        self.stopAnimating()
        window = self.window()
        if window is not None:
            window.orderOut_(None)

    def tick_(self, _timer):
        if self._mode == "loading":
            self._spin = (self._spin - 12.0) % 360.0
            self.setNeedsDisplay_(True)
            return
        level = 0.0
        if self._level_fn is not None:
            try:
                level = float(self._level_fn())
            except Exception:
                level = 0.0
        if level >= VOICE_ON:
            self._active = True
        self._levels = self._levels[1:] + [level]
        self.setNeedsDisplay_(True)

    # ---- style-aware colors ----
    def _spinner_color(self):
        if self._style == "cream":
            return _INK
        if self._style == "glass":
            return _GLASS_ACCENT
        return _WHITE  # hud, aurora

    def _fill_bar(self, path):
        """Fill one bar honoring the current style + active state."""
        if self._style == "glass":
            if self._active:
                _GLASS_GRAD.drawInBezierPath_angle_(path, 90.0)
            else:
                _WHITE_DIM.setFill(); path.fill()
            return
        if self._style in ("hud", "aurora"):
            (_WHITE if self._active else _WHITE_DIM).setFill()
            path.fill()
            return
        # cream
        (_INK if self._active else _GREY).setFill()
        path.fill()

    def drawRect_(self, _rect):
        if self._style == "cream":
            self._draw_cream_shell()

        if self._mode == "loading":
            self._draw_spinner()
            return

        span = BARS * BAR_W + (BARS - 1) * GAP
        x = (PANEL_W - span) / 2.0
        mid = PANEL_H / 2.0
        max_h = PANEL_H - 12.0
        for level in self._levels:
            norm = min(1.0, level * GAIN)
            bar_h = max(2.0, norm * max_h)
            bar = NSMakeRect(x, mid - bar_h / 2.0, BAR_W, bar_h)
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar, BAR_W / 2.0, BAR_W / 2.0)
            self._fill_bar(path)
            x += BAR_W + GAP

    def _draw_cream_shell(self):
        pill = NSMakeRect(1.5, 1.5, PANEL_W - 3, PANEL_H - 3)
        radius = (PANEL_H - 3) / 2.0
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(pill, radius, radius)
        _CREAM.setFill()
        path.fill()
        (_INK if (self._mode == "loading" or self._active) else _GREY).setStroke()
        path.setLineWidth_(2.0)
        path.stroke()

    def _draw_spinner(self):
        r = PANEL_H / 2.0 - 9.0
        center = (PANEL_W / 2.0, PANEL_H / 2.0)
        arc = NSBezierPath.bezierPath()
        arc.setLineWidth_(2.5)
        arc.setLineCapStyle_(1)
        arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
            center, r, self._spin, self._spin + 270.0)
        self._spinner_color().setStroke()
        arc.stroke()


class WaveformOverlay:
    """Show/hide a floating recording pill. `level_fn` returns the 0..1 mic level."""

    def __init__(self, level_fn, style=DEFAULT_STYLE):
        self._level_fn = level_fn
        self._style = style if style in STYLES else DEFAULT_STYLE
        self._panel = None
        self._view = None

    def set_style(self, style):
        """Switch the look. Rebuilds the panel on next show()."""
        if style not in STYLES or style == self._style:
            return
        self._style = style
        # Drop the current panel so the new style is built fresh next time.
        was_visible = self._panel is not None and self._panel.isVisible()
        self.hide()
        self._panel = None
        self._view = None
        if was_visible:
            self.show()

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
        view._style = self._style

        if self._style == "cream":
            panel.setContentView_(view)
        else:
            effect = self._build_glass(rect)
            if self._style == "aurora":
                self._add_aurora(effect, rect)
            effect.addSubview_(view)
            panel.setContentView_(effect)

        self._panel, self._view = panel, view

    def _build_glass(self, rect):
        material = (NSVisualEffectMaterialHUDWindow if self._style == "hud"
                    else NSVisualEffectMaterialPopover)
        effect = NSVisualEffectView.alloc().initWithFrame_(rect)
        effect.setMaterial_(material)
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(NSVisualEffectStateActive)
        if self._style == "hud":
            try:
                effect.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameVibrantDark))
            except Exception:
                pass
        effect.setWantsLayer_(True)
        layer = effect.layer()
        layer.setCornerRadius_(PANEL_H / 2.0)
        layer.setMasksToBounds_(True)
        layer.setBorderWidth_(1.0)
        layer.setBorderColor_(
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.30).CGColor())
        return effect

    def _add_aurora(self, effect, rect):
        """Insert a slowly rotating conic color sheen behind the bars."""
        try:
            from Quartz import CAGradientLayer, CABasicAnimation
            aurora = NSView.alloc().initWithFrame_(rect)
            aurora.setWantsLayer_(True)
            grad = CAGradientLayer.layer()
            grad.setType_("conic")
            # Oversize + center so rotation never reveals a corner.
            d = max(PANEL_W, PANEL_H) * 2.0
            grad.setFrame_(NSMakeRect((PANEL_W - d) / 2.0, (PANEL_H - d) / 2.0, d, d))
            cols = [
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.43, 0.91, 1.0, 1.0).CGColor(),
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.66, 0.55, 0.98, 1.0).CGColor(),
                NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.48, 0.78, 1.0).CGColor(),
                NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.83, 0.43, 1.0).CGColor(),
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.43, 0.91, 1.0, 1.0).CGColor(),
            ]
            grad.setColors_(cols)
            aurora.layer().addSublayer_(grad)
            aurora.layer().setOpacity_(0.55)
            aurora.layer().setCornerRadius_(PANEL_H / 2.0)
            aurora.layer().setMasksToBounds_(True)
            spin = CABasicAnimation.animationWithKeyPath_("transform.rotation")
            spin.setFromValue_(0.0)
            spin.setToValue_(2.0 * math.pi)
            spin.setDuration_(7.0)
            spin.setRepeatCount_(float("inf"))
            grad.addAnimation_forKey_(spin, "spin")
            effect.addSubview_(aurora)
        except Exception:
            log.exception("aurora sheen unavailable; using plain glass")

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
        try:
            if self._view is not None:
                self._view.setLoading()
        except Exception:
            log.exception("could not switch overlay to loading")

    def finish(self):
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
