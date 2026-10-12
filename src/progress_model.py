"""Byte-weighted install progress and ETA, shared by both flash backends.

Both engines report how much of the image set has been written, in bytes:

* SP Flash Tool's console prints
  ``62% of image data has been sent (441.62M of 711.46M)`` — its own total is
  the authoritative size of what it decided to write (it auto-loads every
  image the scatter enables, including partitions the package's file list may
  not mention), so this module prefers that total over a sum computed here;
* MTKClient reports its position inside each partition, and the app knows the
  size of every file it hands it.

Neither number is a time estimate. The app used to derive one by assuming
progress arrives evenly (``elapsed * remaining / done``), which is wrong in
exactly the case users notice: the big images. ``userdata`` can be 3 GB and
``system`` 400 MB-1 GB, so a bar sitting at "half done" can still have most of
its wall-clock time ahead of it.

``ThroughputEta`` answers the only question the card asks — how long is left —
from what this install has actually been measured doing: bytes written per
second over a short sliding window, smoothed so a stalled partition read does
not throw the number around.
"""

from __future__ import annotations

import time

# Long enough to smooth bursty USB writes, short enough to follow a real
# change in speed (a 3 GB userdata after eight small partitions).
WINDOW_SECONDS = 25.0

# Three samples means two intervals: a rate exists, and it is not one
# back-to-back pair that may have measured a partition boundary rather than
# the write itself.
MIN_SAMPLES = 3

# Weight of each new measurement in the smoothed rate. Low enough that a
# single slow interval does not move the estimate much.
_SMOOTHING = 0.35


class ThroughputEta:
    """Seconds remaining, measured from this install's own write rate.

    ``note(done, total)`` is called with the absolute byte position and the
    total the tool itself reports; it returns whole seconds left, or ``None``
    while no honest answer exists yet (too few samples, no movement, or
    nothing left to write). ``None`` is the signal for the UI to keep showing
    the elapsed time instead of inventing a number.

    A pause shows as a *longer* estimate rather than as no estimate — that is
    what a pause means — and only a quiet stretch longer than twice the window
    makes the answer unknown again.
    """

    def __init__(self, window: float = WINDOW_SECONDS, min_samples: int = MIN_SAMPLES,
                 clock=time.monotonic):
        self._window = float(window)
        self._min_samples = max(2, int(min_samples))
        self._clock = clock
        self._samples: list = []
        self._rate = 0.0

    # -- lifecycle ----------------------------------------------------------
    def reset(self) -> None:
        """Forget every measurement (a new write phase, or a new attempt)."""
        self._samples = []
        self._rate = 0.0

    # -- measurements -------------------------------------------------------
    def note(self, done, total):
        """Record the tool's position. Returns seconds remaining, or ``None``."""
        try:
            done = float(done)
            total = float(total)
        except (TypeError, ValueError):
            return None
        if total <= 0 or done < 0:
            return None
        now = self._clock()

        # A position that moved backwards is a new image stream (the tool
        # restarted its own count, or a partition was retried): the old rate
        # describes a different transfer, so start the window again.
        if self._samples and done < self._samples[-1][1]:
            self._samples = []

        self._samples.append((now, done))
        cutoff = now - self._window
        while len(self._samples) > self._min_samples and self._samples[0][0] < cutoff:
            self._samples.pop(0)
        if len(self._samples) > self._min_samples * 40:
            # A window that is never time-pruned (a clock that hardly moves)
            # still must not grow without bound.
            self._samples = self._samples[-self._min_samples * 40:]

        if len(self._samples) < self._min_samples:
            return None
        if now - self._samples[0][0] > self._window * 2:
            # Everything still here predates the window: the transfer has been
            # quiet long enough that the last measured rate describes a
            # different moment. Say nothing rather than guess from it.
            return None
        first_t, first_done = self._samples[0]
        span = now - first_t
        moved = done - first_done
        if span <= 0 or moved <= 0:
            return None
        measured = moved / span
        self._rate = measured if self._rate <= 0 else (
            self._rate + _SMOOTHING * (measured - self._rate)
        )
        remaining = total - done
        if remaining <= 0:
            return 0
        return max(0, int(round(remaining / self._rate)))

    @property
    def rate(self) -> float:
        """Smoothed bytes per second, 0.0 until a rate has been measured."""
        return self._rate


def percent_of(done, total, start: float = 0.0, span: float = 100.0) -> int:
    """The byte fraction of ``total`` expressed inside a slice of the bar.

    Kept separate from :class:`ThroughputEta` because the install bar is not a
    single 0-100 scale: preparation and the download agent have their own
    slices, and only writing fills the wide middle one.
    """
    try:
        total = float(total)
        done = float(done)
    except (TypeError, ValueError):
        return int(round(start))
    if total <= 0:
        return int(round(start))
    ratio = min(1.0, max(0.0, done / total))
    return int(round(start + span * ratio))
