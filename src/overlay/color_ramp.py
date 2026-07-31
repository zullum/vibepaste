"""Colour ramp for the recording-duration progress bar.

Blue while there's plenty of headroom, green in the middle, red as the
recording approaches the length where it should be split into parts.

Kept free of PyObjC so it can be unit tested without a UI process.
"""

BLUE = (0.25, 0.55, 1.00)
GREEN = (0.20, 0.85, 0.45)
RED = (1.00, 0.25, 0.20)


def _lerp(start, end, fraction):
    return tuple(a + (b - a) * fraction for a, b in zip(start, end))


def progress_color(fraction):
    """RGB for a progress fraction.

    Args:
        fraction: 0.0 at the start of a recording, 1.0 at the warn threshold.
            Values outside [0, 1] are clamped.

    Returns:
        (r, g, b) floats in 0..1 — blue at 0.0, green at 0.5, red at 1.0.
    """
    fraction = min(1.0, max(0.0, fraction))
    if fraction <= 0.5:
        return _lerp(BLUE, GREEN, fraction / 0.5)
    return _lerp(GREEN, RED, (fraction - 0.5) / 0.5)
