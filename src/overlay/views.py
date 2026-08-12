"""AppKit drawing for the overlay window."""

import math
import time

import objc
from AppKit import NSView, NSColor, NSBezierPath, NSGraphicsContext
from Foundation import NSMakeRect, NSMakePoint, NSObject

from src.overlay.animation import AnimationLibrary
from src.overlay.color_ramp import progress_color
from src.overlay.state import read_state

try:
    import Quartz
except ImportError:
    Quartz = None

# Half of the clips' 220px source width, so they land 1:1 on a Retina
# screen — no resampling, no softness.
CLIP_WIDTH = 110.0
CLIP_HEIGHT = CLIP_WIDTH * 3 / 4      # every clip is cropped to 4:3
# Space kept for the duration bar. Reserved in every mode, not just while
# recording, so the clip sits at exactly the same spot throughout — this is
# a small badge, and a picture that jumps on each mode change draws the eye
# to the window instead of to Data.
BAR_RESERVE = 22.0
# The duration bar's geometry, shared so the processing dots can sit on the
# bar's own centre line rather than on a number that drifts away from it.
BAR_X = 20.0
BAR_Y = 14.0
BAR_HEIGHT = 6.0
STRIP_CENTRE_Y = BAR_Y + BAR_HEIGHT / 2
# Bracketing the bar's 6pt height: the dots shrink below it and swell above,
# so the two states read as the same element doing a different job.
DOT_RADIUS = 4.5
DOT_SPACING = 16.0
DOT_MIN_ALPHA = 0.30

RECORD_DOT_RGB = (1.0, 0.25, 0.20)
SPINNER_RGB = (0.17, 0.83, 0.75)
PANEL_RGB = (0.08, 0.08, 0.10)
BORDER_RGB = (0.30, 0.30, 0.35)
TRACK_RGB = (0.25, 0.25, 0.30)


def _fill(rgb, alpha):
    NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgb, alpha).setFill()


def _stroke(rgb, alpha):
    NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgb, alpha).setStroke()


class OverlayView(NSView):
    """Draws recording dots + duration bar, or the transcription spinner."""

    def initWithFrame_(self, frame):
        self = objc.super(OverlayView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.phase = 0.0
        self.library = AnimationLibrary()
        self.clip = None
        self.clip_key = None
        self.clip_started = 0.0
        return self

    def tick_(self, timer):
        self.phase += 0.15
        if self.phase > math.tau:
            self.phase -= math.tau
        self.setNeedsDisplay_(True)

    def isOpaque(self):
        return False

    def drawRect_(self, rect):
        state = read_state()
        if state["mode"] == "hidden":
            return

        self._draw_panel(rect)
        drew_clip = self._draw_clip(rect, state)
        if state["mode"] == "record":
            if not drew_clip:
                self._draw_dots(rect)
            self._draw_progress_bar(rect, state)
        elif state["mode"] == "processing":
            if not drew_clip:
                self._draw_spinner(rect)
            else:
                # Same strip the duration bar uses, so "still working" shows
                # up where you are already looking for progress.
                self._draw_dots(rect, centre_y=STRIP_CENTRE_Y,
                                radius_base=DOT_RADIUS, spacing=DOT_SPACING)
        elif not drew_clip:
            self._draw_spinner(rect)

    # -- the Data reactions ---------------------------------------------

    def _draw_clip(self, rect, state):
        """Draw the animation for this mode. False if there isn't one.

        Returning False is a normal outcome, not an error: without the
        assets the overlay falls back to the drawn dots and spinner, so a
        missing folder costs the animation and nothing else.
        """
        key = (state["mode"], state["generation"])
        if key != self.clip_key:
            self.clip_key = key
            self.clip = self.library.pick(state["mode"])
            self.clip_started = time.monotonic()
        if self.clip is None or Quartz is None:
            return False

        elapsed = time.monotonic() - self.clip_started
        x = (rect.size.width - CLIP_WIDTH) / 2
        y = BAR_RESERVE + (rect.size.height - BAR_RESERVE - CLIP_HEIGHT) / 2
        box = NSMakeRect(x, y, CLIP_WIDTH, CLIP_HEIGHT)

        context = NSGraphicsContext.currentContext()
        context.saveGraphicsState()
        # Clipped to a rounded rect so the still frames sit inside the panel
        # rather than over its corners.
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            box, 6, 6
        ).addClip()
        Quartz.CGContextDrawImage(
            context.CGContext(), box, self.clip.frame_at(elapsed)
        )
        context.restoreGraphicsState()
        return True

    def _draw_panel(self, rect):
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, rect.size.width, rect.size.height), 12, 12
        )
        _fill(PANEL_RGB, 0.92)
        path.fill()
        _stroke(BORDER_RGB, 0.80)
        path.setLineWidth_(1.0)
        path.stroke()

    def _draw_dots(self, rect, centre_y=None, radius_base=5, spacing=18,
                   rgb=RECORD_DOT_RGB):
        """Three pulsing dots. Placement is a parameter so the same drawing
        serves both the full-panel fallback and the narrow status strip."""
        center_x = rect.size.width / 2
        center_y = (rect.size.height - 24) if centre_y is None else centre_y

        for index in range(3):
            # Mirrored, not a travelling wave: the outer two share a phase so
            # the trio is symmetric at every instant. A left-to-right wave
            # swings the bright mass ±9pt around the centre, which is what
            # made these read as badly centred against the progress bar.
            phase = self.phase - abs(index - 1) * 0.7
            scale = 0.5 + 0.5 * math.sin(phase)
            # Floor rather than fade to nothing. Letting two of the three
            # vanish left the visible weight swinging left and right, which
            # is what made a symmetric trio look badly centred.
            alpha = DOT_MIN_ALPHA + (1.0 - DOT_MIN_ALPHA) * scale
            x = center_x + (index - 1) * spacing

            glow = radius_base * (1.0 + scale * 0.8)
            _fill(rgb, alpha * 0.25)
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x - glow, center_y - glow, glow * 2, glow * 2)
            ).fill()

            dot = radius_base * (0.7 + scale * 0.5)
            _fill(rgb, alpha)
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x - dot, center_y - dot, dot * 2, dot * 2)
            ).fill()

    def _draw_progress_bar(self, rect, state):
        elapsed = time.monotonic() - state["started_at"]
        fraction = elapsed / max(1.0, state["warn_seconds"])

        bar_width = rect.size.width - BAR_X * 2
        bar_height = BAR_HEIGHT
        x, y = BAR_X, BAR_Y

        _fill(TRACK_RGB, 0.85)
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x, y, bar_width, bar_height), 3, 3
        ).fill()

        if fraction >= 1.0:
            # Past the warn threshold: full red bar, pulsing so it's obvious.
            alpha = 0.65 + 0.35 * abs(math.sin(self.phase))
            fill_width = bar_width
        else:
            alpha = 1.0
            fill_width = bar_width * fraction

        if fill_width > 1:
            _fill(progress_color(fraction), alpha)
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, y, fill_width, bar_height), 3, 3
            ).fill()

    def _draw_spinner(self, rect):
        cx, cy = rect.size.width / 2, rect.size.height / 2
        radius, width = 13.0, 3.5

        track = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - radius, cy - radius, radius * 2, radius * 2)
        )
        _stroke(BORDER_RGB, 0.5)
        track.setLineWidth_(width)
        track.stroke()

        arc = NSBezierPath.bezierPath()
        start = math.degrees(self.phase)
        arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            NSMakePoint(cx, cy), radius, start, start + 240.0, False
        )
        _stroke(SPINNER_RGB, 1.0)
        arc.setLineWidth_(width)
        arc.setLineCapStyle_(1)
        arc.stroke()


class Ticker(NSObject):
    """NSTimer target: advances the animation and drives window visibility."""

    def initWithDriver_view_(self, driver, view):
        self = objc.super(Ticker, self).init()
        if self is None:
            return None
        self.driver = driver
        self.view = view
        return self

    def tick_(self, timer):
        self.driver.tick()
        self.view.tick_(timer)
