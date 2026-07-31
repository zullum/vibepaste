"""AppKit drawing for the overlay window."""

import math
import time

import objc
from AppKit import NSView, NSColor, NSBezierPath
from Foundation import NSMakeRect, NSMakePoint, NSObject

from src.overlay.color_ramp import progress_color
from src.overlay.state import read_state

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
        if state["mode"] == "record":
            self._draw_dots(rect)
            self._draw_progress_bar(rect, state)
        else:
            self._draw_spinner(rect)

    def _draw_panel(self, rect):
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, rect.size.width, rect.size.height), 12, 12
        )
        _fill(PANEL_RGB, 0.92)
        path.fill()
        _stroke(BORDER_RGB, 0.80)
        path.setLineWidth_(1.0)
        path.stroke()

    def _draw_dots(self, rect):
        radius_base = 5
        spacing = 18
        center_x = rect.size.width / 2
        center_y = rect.size.height - 24

        for index in range(3):
            phase = self.phase - (index * 0.7)
            scale = 0.5 + 0.5 * math.sin(phase)
            alpha = max(0.0, 0.4 + 0.6 * math.sin(phase))
            x = center_x + (index - 1) * spacing

            glow = radius_base * (1.0 + scale * 0.8)
            _fill(RECORD_DOT_RGB, alpha * 0.25)
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x - glow, center_y - glow, glow * 2, glow * 2)
            ).fill()

            dot = radius_base * (0.7 + scale * 0.5)
            _fill(RECORD_DOT_RGB, alpha)
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x - dot, center_y - dot, dot * 2, dot * 2)
            ).fill()

    def _draw_progress_bar(self, rect, state):
        elapsed = time.monotonic() - state["started_at"]
        fraction = elapsed / max(1.0, state["warn_seconds"])

        bar_width = rect.size.width - 40
        bar_height = 6
        x, y = 20, 14

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
