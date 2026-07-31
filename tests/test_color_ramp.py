"""Tests for the recording-duration colour ramp."""

import pytest

from src.overlay.color_ramp import BLUE, GREEN, RED, progress_color


def test_starts_blue():
    assert progress_color(0.0) == pytest.approx(BLUE)


def test_green_at_the_midpoint():
    assert progress_color(0.5) == pytest.approx(GREEN)


def test_red_at_the_warn_threshold():
    assert progress_color(1.0) == pytest.approx(RED)


@pytest.mark.parametrize("fraction", [-1.0, -0.01])
def test_clamps_below_zero(fraction):
    assert progress_color(fraction) == pytest.approx(BLUE)


@pytest.mark.parametrize("fraction", [1.01, 5.0])
def test_clamps_above_one(fraction):
    """Past the warn threshold the bar stays red rather than wrapping."""
    assert progress_color(fraction) == pytest.approx(RED)


def test_ramp_is_continuous():
    """No visible jump as the bar crosses the blue->green->red handover."""
    steps = [progress_color(i / 100.0) for i in range(101)]
    for previous, current in zip(steps, steps[1:]):
        for before, after in zip(previous, current):
            assert abs(after - before) < 0.05


def test_every_component_stays_in_range():
    for i in range(21):
        for component in progress_color(i / 20.0):
            assert 0.0 <= component <= 1.0
