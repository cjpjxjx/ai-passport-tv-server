"""Byte-rate control for a live session.

The picture's frame rate is fixed for a session; what moves is the byte rate the
frames are fitted into (`ByteRate`). The controller is given what the last window
of sending cost -- the slowest write and the frames given up -- and answers with
a byte rate for `frames.ByteBudget` to spend. It holds no socket, no clock and no
state that outlives a session.
"""

from __future__ import annotations

import os

# Nominal rate used to size the pre-buffer, in frames per second of reserve.
START_FPS = int(os.environ.get("TV_START_FPS", "5"))

# Write times (ms) above which the link is judged to be filling up, and below which it has room.
# The gap is hysteresis: a single threshold would oscillate.
SLOW_WRITE_MS = 250.0
FAST_WRITE_MS = 120.0

# Comfortable windows in a row needed before the rate goes up; down is immediate.
WINDOWS_BEFORE_UP = 2


# Adapting the byte rate is opt-in (TV_ADAPTIVE=1); by default it is held at the target.
ADAPTIVE = os.environ.get("TV_ADAPTIVE", "0") == "1"


# --- Fixed frame rate, variable byte rate -------------------------------------
#
# The sender does not choose how many pictures a second to send. The
# rate is the source's own and is held for the whole session, because a rate that
# moves is a stutter the viewer sees, and a frame that carries fewer changed
# stripes is a softer picture the viewer mostly does not. What moves instead is
# how many bytes the pictures are allowed to cost, and that is the whole job of
# `ByteRate`: it is given what the last window cost and answers with a byte rate
# for `frames.ByteBudget` to spend.
#
# The target is a size per FRAME, and the byte rate follows from the frame rate:
# FRAME_BYTES * fps, so a 25 fps channel is held to 500 kB/s and a 30 fps one to
# 600 kB/s. It is what the session runs at when ADAPTIVE is off, which is the
# default, and every frame is fitted into it (frames.encode_within) rather than
# merely capped by it. TV_RATE_START sets a byte rate directly instead, whatever
# the frame rate.
#
# MIN and MAX only bound the adaptive controller and have no effect in the fixed
# mode. MAX defaults to START because the adaptive mode is not the one being
# tuned; raise it together with TV_ADAPTIVE=1 to let the controller climb.
# Each can be overridden without editing a file.
FRAME_BYTES = int(os.environ.get("TV_FRAME_BYTES", "20000"))
MIN_RATE_BPS = int(os.environ.get("TV_RATE_MIN", "100000"))


def start_rate(fps: int) -> int:
    """The fixed byte rate for a picture at `fps`."""
    explicit = os.environ.get("TV_RATE_START")
    return int(explicit) if explicit else FRAME_BYTES * fps


def max_rate(fps: int) -> int:
    explicit = os.environ.get("TV_RATE_MAX")
    return max(int(explicit), start_rate(fps)) if explicit else start_rate(fps)


RATE_STEP_DOWN = 0.85
RATE_STEP_UP = 1.05


class ByteRate:
    """Decides the picture's byte rate; the picture's frame rate is a constant.

    Signals are the slowest write in the window and the frames the sender itself
    gave up. Down at once, up only after `WINDOWS_BEFORE_UP` windows in a row of
    evidence. A window is only proof of spare capacity if the link was actually being used: a still studio shot sends
    almost nothing, every write in it is instant, and raising the rate on that
    would open the gate for the next cut to spend it all at once. So the rate only
    climbs in a window that used at least 80% of it and still wrote quickly.
    """

    def __init__(self, fps: int, start: int | None = None,
                 minimum: int | None = None, maximum: int | None = None,
                 adaptive: bool = True):
        if fps < 1:
            raise ValueError("a frame rate below one is not a rate")
        if start is None:
            start = start_rate(fps)
            minimum = min(MIN_RATE_BPS, start) if minimum is None else minimum
            maximum = max_rate(fps) if maximum is None else maximum
        minimum = MIN_RATE_BPS if minimum is None else minimum
        maximum = max(start, max_rate(fps)) if maximum is None else maximum
        if not 0 < minimum <= start <= maximum:
            raise ValueError("start rate is outside the range")
        self.fps = fps
        self.rate = start
        self.minimum, self.maximum = minimum, maximum
        self.adaptive = adaptive
        self.bytes = 0
        self.worst_write_ms = 0.0
        self.dropped = 0
        self.window_s = 1.0
        self.reason = "starting" if adaptive else "fixed"
        self._comfortable = 0
        self._dropped_last_window = False

    def observe(self, video_bytes: int, worst_write_ms: float,
                dropped: int = 0, frames: int = 0,
                window_s: float = 1.0) -> int:
        """Take one window's measurements; return the frame rate, which never moves.

        The return value is there so the sender's loop reads the same either way.
        The byte rate is `self.rate` afterwards.
        """
        self.bytes, self.worst_write_ms, self.dropped = video_bytes, worst_write_ms, dropped
        self.window_s = max(0.05, window_s)
        drop_running = bool(dropped) and self._dropped_last_window
        self._dropped_last_window = bool(dropped)
        if not self.adaptive:
            return self.fps
        measured = bool(frames or video_bytes or worst_write_ms > 0.0 or dropped)
        if not measured:
            # An empty window says the source stopped, not that the link has room.
            self._comfortable = 0
            self.reason = "no measurement in the window; holding"
            return self.fps
        achieved = video_bytes / self.window_s
        if worst_write_ms > SLOW_WRITE_MS or drop_running:
            self.reason = ("write was %.0f ms" % worst_write_ms
                           if worst_write_ms > SLOW_WRITE_MS
                           else "dropped %d frame%s, second window running"
                           % (dropped, "" if dropped == 1 else "s"))
            self._comfortable = 0
            if self.rate > self.minimum:
                self.rate = max(self.minimum, int(self.rate * RATE_STEP_DOWN))
            else:
                self.reason += "; already at the floor"
            return self.fps
        if dropped:
            # One isolated loss is what a burst at the working point costs.
            self.reason = "dropped %d frame%s" % (dropped, "" if dropped == 1 else "s")
            return self.fps
        if worst_write_ms < FAST_WRITE_MS and achieved >= self.rate * 0.8:
            self._comfortable += 1
            if self._comfortable >= WINDOWS_BEFORE_UP and self.rate < self.maximum:
                self.rate = min(self.maximum, int(self.rate * RATE_STEP_UP))
                self._comfortable = 0
                self.reason = "used the rate and wrote fast for %d windows" % WINDOWS_BEFORE_UP
                return self.fps
            self.reason = "comfortable (%d/%d)" % (self._comfortable, WINDOWS_BEFORE_UP)
        else:
            self._comfortable = 0
            self.reason = "holding"
        return self.fps

    def describe(self) -> str:
        return (f"fps={self.fps} rate={self.rate // 1000}kB/s "
                f"video={self.bytes // 1000}kB/{self.window_s:.2f}s "
                f"worst_write={self.worst_write_ms:.0f}ms "
                f"dropped={self.dropped} ({self.reason})")
