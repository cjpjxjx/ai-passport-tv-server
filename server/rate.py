"""直播会话的字节率控制。

帧率在会话内固定，变化的只有帧所适配的字节率（`ByteRate`）。控制器接收上一窗口的
发送开销（最慢写入与放弃的帧数），给出供 `frames.ByteBudget` 使用的字节率；
不持有 socket、时钟或跨会话状态。
"""

from __future__ import annotations

import os

# 预缓冲按此名义帧率折算储备量。
START_FPS = int(os.environ.get("TV_START_FPS", "5"))

# 写入耗时（毫秒）：高于 SLOW 判定链路在积压，低于 FAST 判定有余量；两者之间为滞回区间。
SLOW_WRITE_MS = 250.0
FAST_WRITE_MS = 120.0

# 连续多少个宽松窗口后才上调字节率；下调立即生效。
WINDOWS_BEFORE_UP = 2


# 自适应字节率默认关闭，TV_ADAPTIVE=1 开启。
ADAPTIVE = os.environ.get("TV_ADAPTIVE", "0") == "1"


# --- 帧率固定，字节率可变 ---
#
# 目标按帧计：字节率 = FRAME_BYTES * fps，自适应关闭时会话固定使用它；
# TV_RATE_START 直接指定字节率，不随帧率变化。
# MIN 与 MAX 只限定自适应范围；MAX 默认等于起始值，需配合 TV_ADAPTIVE=1 调高才会上探。
FRAME_BYTES = int(os.environ.get("TV_FRAME_BYTES", "20000"))
MIN_RATE_BPS = int(os.environ.get("TV_RATE_MIN", "100000"))


def start_rate(fps: int) -> int:
    """`fps` 下的固定字节率。"""
    explicit = os.environ.get("TV_RATE_START")
    return int(explicit) if explicit else FRAME_BYTES * fps


def max_rate(fps: int) -> int:
    explicit = os.environ.get("TV_RATE_MAX")
    return max(int(explicit), start_rate(fps)) if explicit else start_rate(fps)


RATE_STEP_DOWN = 0.85
RATE_STEP_UP = 1.05


class ByteRate:
    """决定画面字节率，帧率恒定。

    依据窗口内最慢写入与发送端自己放弃的帧数：立即下调，连续
    `WINDOWS_BEFORE_UP` 个窗口宽松后才上调。上调还要求窗口用掉至少 80% 的字节率，
    静止画面几乎不发数据，写入再快也不能证明链路有余量。
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
        """记录一个窗口的测量值，返回恒定的帧率；字节率见 `self.rate`。"""
        self.bytes, self.worst_write_ms, self.dropped = video_bytes, worst_write_ms, dropped
        self.window_s = max(0.05, window_s)
        drop_running = bool(dropped) and self._dropped_last_window
        self._dropped_last_window = bool(dropped)
        if not self.adaptive:
            return self.fps
        measured = bool(frames or video_bytes or worst_write_ms > 0.0 or dropped)
        if not measured:
            # 空窗口表示源停供，不代表链路有余量。
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
            # 孤立的一次丢帧属于工作点附近的正常突发。
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
