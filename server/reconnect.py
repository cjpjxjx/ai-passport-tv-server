"""Keeping a session alive while its source is gone.

When the source stops delivering, the device is held on a notice instead of being
dropped: the last picture with "RECONNECTING..." drawn on it, and silent sound so
the device's audio watchdog stays satisfied. Meanwhile the source is restarted on
a worker thread, because starting it blocks for seconds and the silence must keep
flowing. When fresh data is ready the session carries on from it; the content in
between is skipped, not recovered.
"""

from __future__ import annotations

import os
import select
import threading
import time
from dataclasses import dataclass

from . import frames, overlay
from .live import AUDIO_MAX_LOOKAHEAD_MS, LiveChannel, LiveError
from .media import AUDIO_CHUNK_MS, AUDIO_LEAD_MS
from .protocol import (AUDIO_BYTES, VIDEO_CONTINUES, Kind, Packet, json_bytes,
                       send_packet)

# How long a session may stay on the notice before it is ended.
RECONNECT_TIMEOUT_S = float(os.environ.get("TV_RECONNECT_TIMEOUT_S", "60"))
# A source process that is alive but silent this long is replaced.
STALL_RESTART_S = 5.0
# One replacement attempt must produce a prebuffered source within this time.
ATTEMPT_TIMEOUT_S = 20.0
RETRY_DELAY_S = 2.0

_SILENCE = bytes(AUDIO_BYTES)


@dataclass
class WireState:
    """What has gone out on the wire, so the notice and the silence continue it."""

    seq: int
    audio_pts: int
    audio_stamp: int = -AUDIO_CHUNK_MS
    video_stamp: int = -1


def _close_quietly(channel: LiveChannel) -> None:
    try:
        channel.close()
    except Exception:
        pass


def _close_background(channel: LiveChannel) -> None:
    """Closing waits on the decoder, which would stall the silence."""
    threading.Thread(target=_close_quietly, args=(channel,), daemon=True).start()


class Restart:
    """A replacement channel being started on a worker thread."""

    def __init__(self, make):
        self.channel: LiveChannel = make()
        self.error: Exception | None = None
        self.started_at = time.monotonic()
        self._cancelled = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.channel.build_palette()
            self.channel.start()
        except Exception as error:
            self.error = error
        if self.error is not None or self._cancelled:
            _close_quietly(self.channel)

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()

    def abandon(self) -> None:
        self._cancelled = True
        _close_background(self.channel)


def _tell_device(connection, session: int, reason: str) -> None:
    try:
        send_packet(connection, Packet(Kind.ERROR, session, 0, 0,
                                       json_bytes({"reason": reason})))
    except OSError:
        pass


def _send_notice(server, connection, session: int, wire: WireState,
                 shown: bytes | None) -> bytes:
    """Send the notice frame; returns what the device now shows."""
    raw = overlay.reconnect_frame(shown)
    chosen = frames.choose_stripes(raw, shown, 0, 1 << 20)
    parts = frames.pack_stripes(chosen)
    stamp = max(wire.audio_stamp, wire.video_stamp + 1)
    deadline = time.monotonic() + server.frame_deadline_s
    for n, part in enumerate(parts):
        send_packet(connection,
                    Packet(Kind.JPEG, session, wire.seq, stamp, part,
                           VIDEO_CONTINUES if n else 0),
                    deadline=deadline)
        wire.seq += 1
        server.video_sent += 1
    wire.video_stamp = stamp
    return frames.apply_stripes(raw, shown, chosen)


def _send_silence(server, connection, session: int, wire: WireState,
                  origin: float) -> None:
    """Silent sound for every chunk that has come due, paced like real sound."""
    for _ in range(32):
        lookahead = (wire.audio_pts - AUDIO_LEAD_MS) - (time.monotonic() - origin) * 1000
        if lookahead > AUDIO_MAX_LOOKAHEAD_MS:
            break
        if not select.select([], [connection], [], 0)[1]:
            break
        stamp = wire.audio_stamp + AUDIO_CHUNK_MS
        send_packet(connection, Packet(Kind.PCM, session, wire.seq, stamp, _SILENCE))
        wire.audio_stamp = stamp
        wire.audio_pts += AUDIO_CHUNK_MS
        wire.seq += 1
        server.audio_sent += 1


def hold(server, connection, session: int, channel: LiveChannel, wire: WireState,
         origin: float, since: float, restart_reason: str = "") -> LiveChannel | None:
    """Hold the device on the notice until a source has data again.

    Returns the channel to continue with -- `channel` itself if it recovered, or a
    freshly started replacement -- or None if the device left. `wire` is updated
    in place. Raises LiveError once `RECONNECT_TIMEOUT_S` has passed since `since`.
    A non-empty `restart_reason` replaces `channel` immediately.
    """
    deadline = since + RECONNECT_TIMEOUT_S
    restart: Restart | None = None
    attempts = 0
    retry_at = 0.0
    pending = restart_reason
    make = server.make_channel
    try:
        server.phase = "live_reconnect"
        channel.shown = _send_notice(server, connection, session, wire, channel.shown)
        while not server.stop.is_set():
            server.phase = "live_reconnect"
            now = time.monotonic()
            if now > deadline:
                _tell_device(connection, session, "source unavailable")
                raise LiveError(f"source unavailable for {RECONNECT_TIMEOUT_S:.0f} s")
            _send_silence(server, connection, session, wire, origin)

            if restart is None:
                if channel is not None and not pending:
                    if channel.failure() is not None:
                        pending = "decoder_stopped"
                    elif now - since > STALL_RESTART_S:
                        pending = "stalled"
                    elif channel.video_pending() and len(channel.audio) >= 4:
                        return channel
                elif channel is None and not pending:
                    pending = "start_failed"
                if pending and now >= retry_at:
                    attempts += 1
                    server.logger(f"session={session} RECONNECT attempt={attempts} "
                                  f"reason={pending}")
                    if channel is not None:
                        _close_background(channel)
                        channel = None
                    restart = Restart(make)
                    pending = ""
            else:
                failed = ""
                if restart.done and restart.error is not None:
                    failed = type(restart.error).__name__
                elif restart.done and restart.channel.failure() is not None:
                    failed = "decoder_stopped"
                elif now - restart.started_at > ATTEMPT_TIMEOUT_S:
                    failed = "timeout"
                if failed:
                    server.logger(f"session={session} RECONNECT attempt={attempts} "
                                  f"failed={failed}")
                    restart.abandon()
                    restart = None
                    retry_at = now + RETRY_DELAY_S
                elif restart.done and restart.channel.prebuffered():
                    winner, restart = restart.channel, None
                    return winner

            if not server._wait_until(connection, time.monotonic() + 0.01, session):
                return None
        return None
    finally:
        if restart is not None:
            restart.abandon()
