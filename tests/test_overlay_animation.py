"""Tests for the Data reaction clips shown in the overlay."""

import pytest

from src.overlay.animation import (
    CLIPS_DIR, LISTENING, POOLS, PROCESSING, RECORDING, SMILING, Animation,
    AnimationLibrary,
)
from src.overlay.state import apply_command, read_state


class FakeAnimation(Animation):
    def __init__(self, count=3, delay=0.1):
        super().__init__(list(range(count)), [delay] * count)


# -- the pools ----------------------------------------------------------

def test_every_pooled_clip_actually_exists():
    """A name that ships without its file falls back to the drawn dots, so
    the animation would silently never appear."""
    missing = [
        name for pool in POOLS.values() for name in pool
        if not (CLIPS_DIR / f"{name}.webp").exists()
    ]

    assert missing == []


def test_no_clip_appears_in_two_pools():
    names = [name for pool in POOLS.values() for name in pool]

    assert len(names) == len(set(names))


def test_listening_and_smiling_do_not_overlap():
    assert set(LISTENING).isdisjoint(SMILING)
    assert set(RECORDING) == set(LISTENING) | set(SMILING)


def test_processing_pool_is_populated():
    assert len(PROCESSING) >= 5


def test_recording_smiles_about_one_time_in_four():
    """The ratio is the requirement, and it must come from the stated chance
    rather than from how many files happen to be in each list — an earlier
    version let list lengths decide and smiled more often than not."""
    library = AnimationLibrary()
    smiles = sum(
        1 for _ in range(400)
        if library._names_for("record")[0] in SMILING
    )

    assert 0.15 < smiles / 400 < 0.35, f"smiled {smiles / 4:.0f}% of the time"


def test_the_smile_ratio_is_independent_of_pool_sizes():
    """Adding clips to a pool must not change how often you see a smile."""
    library = AnimationLibrary(smile_chance=0.0)

    assert all(library._names_for("record")[0] in LISTENING
               for _ in range(50))


def test_always_smiling_is_reachable():
    library = AnimationLibrary(smile_chance=1.0)

    assert all(library._names_for("record")[0] in SMILING
               for _ in range(50))


# -- frame timing -------------------------------------------------------

def test_the_first_frame_shows_first():
    assert FakeAnimation().frame_at(0.0) == 0


def test_frames_advance_with_their_own_delays():
    animation = FakeAnimation(count=3, delay=0.1)

    assert animation.frame_at(0.05) == 0
    assert animation.frame_at(0.15) == 1
    assert animation.frame_at(0.25) == 2


def test_the_clip_loops_rather_than_freezing_on_the_last_frame():
    animation = FakeAnimation(count=3, delay=0.1)

    assert animation.frame_at(0.35) == 0
    assert animation.frame_at(0.45) == 1


def test_total_duration_is_the_sum_of_the_frame_delays():
    assert FakeAnimation(count=4, delay=0.25).duration == pytest.approx(1.0)


# -- picking ------------------------------------------------------------

def test_a_missing_clips_folder_yields_no_animation(tmp_path):
    """Supported outcome, not a crash: the overlay falls back to the dots."""
    library = AnimationLibrary(directory=tmp_path)

    assert library.pick("record") is None


def test_an_unknown_mode_yields_no_animation():
    assert AnimationLibrary().pick("nonsense") is None


def test_a_real_clip_loads_and_is_animated():
    library = AnimationLibrary()
    animation = library.pick("record")

    assert animation is not None, "no recording clip could be decoded"
    assert len(animation.frames) > 1, "clip is a still image"
    assert animation.duration > 0


def test_loading_the_same_clip_twice_reuses_the_decode():
    library = AnimationLibrary()
    name = RECORDING[0]

    first = library._load(name)
    second = library._load(name)

    assert first is second


def test_the_cache_does_not_grow_without_bound():
    """The longest clip is 90 frames; holding every clip would keep the lot
    rasterised for no benefit."""
    library = AnimationLibrary(cache_size=2)
    for name in RECORDING[:4]:
        library._load(name)

    assert len(library._cache) <= 2


# -- modes --------------------------------------------------------------

def test_hide_is_obeyed():
    apply_command("processing")

    apply_command("hide")

    assert read_state()["mode"] == "hidden"


def test_pasting_has_no_mode_of_its_own():
    """Delivering the text ends the run; there is no celebration state."""
    apply_command("processing")

    apply_command("paste")

    assert read_state()["mode"] == "processing", "unknown command changed mode"
    apply_command("hide")


def test_each_mode_change_asks_for_a_fresh_clip():
    apply_command("hide")
    before = read_state()["generation"]

    apply_command("record 60 120")

    assert read_state()["generation"] > before
    apply_command("hide")
