"""Timing constants shared by both senders, and the fixed palette."""

from __future__ import annotations

import os

from . import frames

# Frames a second the server aims to send. Overridable from the environment so
# that finding the rate this device can actually hold does not mean editing a
# tracked file between every run -- the measurements are worth comparing, and
# they are not comparable if the thing being measured changes underneath them.
#
# Both ends read this figure for different purposes: the server paces by it, and
# the device only checks that it is sane (main/av_player.c, json_between) because
# every frame carries its own timestamp. So it can be raised or lowered freely
# from here without rebuilding the firmware.
#
# The frame rate this server ANNOUNCES in CONFIG, and the rate the pre-generated
# media is made at. It is a nominal figure, not what a live channel is sent at.
#
# The device checks it for sanity (1 to 30) and does not pace by it -- every frame
# carries its own timestamp and is scheduled against the audio clock -- so CONFIG
# can go out before the source has been looked at. A live channel's own rate is
# found when it starts (`LiveChannel.fps`, from ffprobe) and is then held for the
# whole session: ffmpeg is asked for it, the timeline counts in it and the sender
# paces by it. `TV_FPS` set explicitly overrides that probe and forces one rate.
FPS = int(os.environ.get("TV_FPS", "25"))
DURATION_MS = 10000
AUDIO_CHUNK_MS = 40
# Send leads, defined here because both the pre-generated scheduler and the live
# sender must use the same pair and this module is the one they both import.
# They are equal by construction: the merge sends whichever stream's slot comes
# due first, and because audio may lead the wall clock by a lookahead, a shorter
# video lead placed the video slot behind the audio slot for every iteration and
# starved video for the entire session.
# How far ahead of the wall clock the sender runs. This, and not the lookahead,
# is what sets how much audio sits in the device's queue at a steady state: the
# sender releases a chunk when its due time arrives, so the queue holds this
# much and the lookahead only caps how far a catch-up may run past it.
#
# Two hundred was too near the edge. One task reads both streams, so a picture
# packet in flight is time in which nothing drains the queue, and a 240x180
# frame is one packet of about 13 KB that takes some 220 ms to cross -- longer
# than the 200 ms the queue held. Measured: every underrun was preceded by a
# ten-second interval with the sound full, and the gap at the failure was 305 ms
# every time, which is the device's threshold and not a variable quantity.
#
# Three hundred and twenty is where the sweep landed, and it is better than the
# figure it replaces on every count rather than merely no worse. Over 240
# seconds each: at 200 ms, ten underruns of the sound and frames dropped; at
# 260, none and a median of 10.0 frames; at 320, none, a median of 10.8 and not
# one dropped frame; at 400, none but 39 dropped and the median back to 9.0.
# Past a point the deeper lead is the picture's problem rather than the sound's,
# because a chunk released long before its due time occupies the socket that the
# frame needed.
#
# Overridable so the depth can be swept without a rebuild.
AUDIO_LEAD_MS = int(os.environ.get("TV_AUDIO_LEAD_MS", "320"))
# The picture keeps the lead it had, and separating the two is the point of
# this line. They were equal, and equal leads are what make the merge alternate
# evenly -- that property is wanted and is tested. But they do not have to be
# *this* value: the sound's lead is what sets the device's queue depth, while
# the picture's lead only decides how far ahead of its own slot a frame is
# released. Raising both with one number re-released the picture 200 ms earlier,
# which bunched it against the sound and cost more frames than it saved --
# measured, 58 dropped in 300 seconds against 9, while the underruns went to
# zero. The picture wants the old lead; the sound wants the deeper one.
VIDEO_LEAD_MS = int(os.environ.get("TV_VIDEO_LEAD_MS", "200"))
START_DELAY_MS = 200


def default_palette() -> bytes:
    """The fixed 3-3-2 palette the live picture's indices refer to.

    The 36 and 85 steps are the ones ffmpeg uses for AV_PIX_FMT_RGB8, and they
    match it for all 256 entries once quantised to RGB565. Without a palette the
    device would be handed indices with nothing to look them up in and draw black.
    """
    return frames.palette_bytes(bytes(
        value
        for k in range(256)
        for value in (36 * ((k >> 5) & 7), 36 * ((k >> 2) & 7), 85 * (k & 3))
    ))
