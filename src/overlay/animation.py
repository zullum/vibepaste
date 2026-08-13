"""Animated Data reactions for the overlay.

macOS decodes animated WebP natively through ImageIO — no extra dependency,
no bundled decoder — and hands back per-frame delays, so the clips play at
the speed they were authored at rather than at the overlay's tick rate.

The pools are grouped by *what is on screen*, not by filename, because the
filenames lie: `starting_2` is a celebration and `starting_3` is a smile.

How often you get a smile while recording is a stated ratio, not a side
effect of how many files happen to sit in each list. An earlier version let
list lengths decide, and adding clips quietly changed the feel — with four
smiles against three listening clips, a "plain random" pick smiled more
often than not.
"""

import logging
import random
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import Quartz
    from Foundation import NSURL
except ImportError:  # not macOS
    Quartz = None
    NSURL = None

CLIPS_DIR = Path(__file__).resolve().parents[2] / "assets" / "data"

# The files are named for what is on screen, checked by watching every clip
# end to end. They were not always: two clips called `listening` broke into
# a broad grin, which is why an honest 25% smile chance still smiled more
# than half the time. If a clip is ever added, watch it before filing it —
# the middle frame is not enough to tell.
LISTENING = [
    "data_listening_1", "data_listening_2", "data_listening_3",
]
SMILING = [
    "data_smiling_1", "data_smiling_2", "data_smiling_3", "data_smiling_4",
    "data_smiling_5", "data_smiling_6",
]
PROCESSING = [
    "data_processing_1", "data_processing_2", "data_processing_3",
    "data_processing_4", "data_processing_5", "data_processing_6",
    "data_processing_7", "data_processing_8",
]

# One recording in four smiles. Change this number to change the feel;
# adding or removing clips will not.
SMILE_CHANCE = 0.25

RECORDING = LISTENING + SMILING
POOLS = {"record": RECORDING, "processing": PROCESSING}

# Frames are decoded lazily by ImageIO but rasterised on draw, and the
# longest clip is 90 frames. Keeping every clip alive would hold the lot in
# memory for no benefit; a handful covers the ones in current rotation.
CACHE_SIZE = 6
DEFAULT_DELAY = 0.1


class Animation:
    """One decoded clip: its frames and how long each is shown."""

    def __init__(self, frames, durations):
        self.frames = frames
        self.durations = durations
        self.duration = sum(durations)

    @classmethod
    def load(cls, path):
        """Decode a clip, or None if macOS will not read it."""
        if Quartz is None:
            return None
        source = Quartz.CGImageSourceCreateWithURL(
            NSURL.fileURLWithPath_(str(path)), None
        )
        if source is None:
            return None
        frames, durations = [], []
        for index in range(Quartz.CGImageSourceGetCount(source)):
            image = Quartz.CGImageSourceCreateImageAtIndex(source, index, None)
            if image is None:
                continue
            frames.append(image)
            durations.append(_delay_of(source, index))
        if not frames:
            return None
        return cls(frames, durations)

    def frame_at(self, elapsed):
        """The frame showing `elapsed` seconds in, looping forever."""
        if self.duration <= 0:
            return self.frames[0]
        remaining = elapsed % self.duration
        for frame, delay in zip(self.frames, self.durations):
            if remaining < delay:
                return frame
            remaining -= delay
        return self.frames[-1]


def _delay_of(source, index):
    properties = Quartz.CGImageSourceCopyPropertiesAtIndex(source, index, None)
    try:
        webp = dict(dict(properties).get("{WebP}", {}))
        delay = float(webp.get("DelayTime", DEFAULT_DELAY))
    except Exception:
        return DEFAULT_DELAY
    # A zero delay means "as fast as possible", which here would be a blur.
    return delay if delay > 0 else DEFAULT_DELAY


class AnimationLibrary:
    """Picks a random clip per mode and remembers the recent ones."""

    def __init__(self, directory=CLIPS_DIR, pools=None, cache_size=CACHE_SIZE,
                 smile_chance=SMILE_CHANCE):
        self.directory = Path(directory)
        self.pools = pools if pools is not None else POOLS
        self.cache_size = cache_size
        self.smile_chance = smile_chance
        self._cache = OrderedDict()
        self._unusable = set()

    def _names_for(self, mode):
        """Candidate clips for a mode, most likely first.

        Recording draws from two pools at a stated ratio rather than from
        one merged list, so the chance of a smile is a decision rather than
        an accident of how many files are in each.
        """
        if mode == "record":
            listening, smiling = list(LISTENING), list(SMILING)
            random.shuffle(listening)
            random.shuffle(smiling)
            if random.random() < self.smile_chance:
                return smiling + listening   # fall back if none can be read
            return listening + smiling
        names = list(self.pools.get(mode, ()))
        random.shuffle(names)
        return names

    def pick(self, mode):
        """A random clip for this mode, or None if none can be loaded.

        None is a supported answer, not a failure: the overlay falls back to
        the drawn dots, so a missing assets folder costs the animation and
        nothing else.
        """
        for name in self._names_for(mode):
            animation = self._load(name)
            if animation is not None:
                return animation
        return None

    def _load(self, name):
        if name in self._cache:
            self._cache.move_to_end(name)
            return self._cache[name]
        if name in self._unusable:
            return None
        path = self.directory / f"{name}.webp"
        animation = Animation.load(path) if path.exists() else None
        if animation is None:
            self._unusable.add(name)   # complain once, not every frame
            logger.warning(f"Overlay animation unavailable: {path}")
            return None
        self._cache[name] = animation
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return animation
