"""内容时间、会话时间，以及画面与声音的配对。

内容时间是条目在节目中的位置，取自解码器自身的 pts（见 pts.py），与到达时间无关，
投递延迟不会改变画面对应哪段声音。

两路流出自同一个 ffmpeg 进程、共用一个时钟，偏移为零，每个会话由 `calibrate()` 以
`BASIS_COMMON_DECODE` 记录一次。`ContentTimeline` 保存该偏移并判断画面相对声音是早、晚
还是同步；`SessionClock` 把内容时间映射为设备要求的线上时间戳。

本模块只做时间戳运算，不涉及套接字与 ffmpeg。
"""
from __future__ import annotations



# 两路内容时间相差多少以内算同一时刻：一个音频块，即声音的切分粒度。
DEFAULT_TOLERANCE_MS = 40

# 偏移的来源，随偏移一起记录，见 `calibrate`。
# 服务端使用 `BASIS_COMMON_DECODE`：两路流出自同一 ffmpeg 进程，时钟相同，偏移为零。
BASIS_COMMON_DECODE = "one decode, the decoder's own timestamps"
BASIS_MEDIA_START = "media stream start times"

# 两路时钟关系的确定程度：未建立、由非媒体来源建立、由媒体自身时间戳建立。
# 只有最后一种可以声称已同步；`APPROXIMATE` 仍可用于配对，但不能当作实测。
STATE_UNKNOWN = "unknown"
STATE_APPROXIMATE = "approximate"
STATE_FROM_MEDIA = "from media timestamps"


def state_of_basis(basis: str) -> str:
    """某种来源对应的标定状态；只有来自媒体的两种来源才算已同步。"""
    if basis in (BASIS_COMMON_DECODE, BASIS_MEDIA_START):
        return STATE_FROM_MEDIA
    return STATE_APPROXIMATE


class ContentTimeline:
    """两路流的内容时钟，以及二者的偏移。

    偏移由 `calibrate()` 设置一次后冻结；此前 `offset_ms` 为 None，需要它的配对一律拒绝而不猜测。
    """

    def __init__(self, tolerance_ms: float = DEFAULT_TOLERANCE_MS):
        self.tolerance_ms = float(tolerance_ms)
        # 音频内容时间减视频内容时间。
        self.offset_ms: float | None = None
        # 标定情况说明，写入日志。
        self.calibration_note = "not attempted"
        # 偏移的来源，见 BASIS_* 常量。
        self.basis = ""
        self.recalibrations_refused = 0
        self.state = STATE_UNKNOWN

    # -- 标定 --------------------------------------------------------------

    def calibrate(self, video_start_s: float, audio_start_s: float,
                  basis: str = BASIS_MEDIA_START) -> bool:
        """记录两路流的偏移，只生效一次；重复调用被拒绝并计入 `recalibrations_refused`。

        `audio_start_s`、`video_start_s` 为两路在共同时钟上的首个时间戳；服务端以
        `BASIS_COMMON_DECODE` 传入 0.0。本次设置了偏移返回 True，已设置过返回 False。
        """
        if self.offset_ms is not None:
            self.recalibrations_refused += 1
            return False
        self.offset_ms = (float(audio_start_s) - float(video_start_s)) * 1000.0
        self.basis = basis
        self.state = state_of_basis(basis)
        self.calibration_note = f"calibrated from {basis}"
        return True

    @property
    def calibrated(self) -> bool:
        """两路关系是否来自媒体自身的时间戳；这是声称已同步的依据，配对是否可用看 `usable`。"""
        return self.state == STATE_FROM_MEDIA

    @property
    def usable(self) -> bool:
        """映射是否可用于放置条目；近似值也算可用。"""
        return self.state != STATE_UNKNOWN

    # -- 内容时间 ----------------------------------------------------------



    def audio_in_video_units(self, audio_content_ms: float) -> float:
        """把音频内容时间换算到视频时钟：加上 `offset_ms`，`video_in_audio_units` 为其逆运算。

        同一次解码的偏移为 0，两者都是恒等变换。
        """
        if self.offset_ms is None:
            raise ValueError("the two streams have not been related yet")
        return audio_content_ms + self.offset_ms

    def video_in_audio_units(self, video_content_ms: float) -> float:
        if self.offset_ms is None:
            raise ValueError("the two streams have not been related yet")
        return video_content_ms - self.offset_ms

    # -- 队列所需的判断 ----------------------------------------------------


    def verdict(self, video_content_ms: float | None,
                audio_content_ms: float | None) -> str:
        """判断哪一路更靠前，返回可写入日志的词。

        - `"no-video"` / `"no-audio"`：对应队列为空，无从配对。
        - `"unrelated"`：两路都有内容但偏移未记录，拒绝而不猜测。
        - `"video-early"`：画面比声音靠前，等待。
        - `"video-late"`：画面落后，已错过时机。
        - `"together"`：在容差内。
        """
        if video_content_ms is None:
            return "no-video"
        if audio_content_ms is None:
            return "no-audio"
        if self.offset_ms is None:
            return "unrelated"
        # 为正：画面队首比当前声音新，时机未到，等待；为负：已过时，无法补救。
        delta = video_content_ms - self.audio_in_video_units(audio_content_ms)
        if delta > self.tolerance_ms:
            return "video-early"
        if delta < -self.tolerance_ms:
            return "video-late"
        return "together"


class SourceState:
    """区分窗口内没有画面是源停供还是链路拥塞。

    从发送端看两者都表现为空窗口，处置相反。区分依据是内容是否仍在到达：
    源停供时两路计数都不前进；链路拥塞时计数照常前进，发送队列增长而无数据发出。
    """

    #: 内容正常到达，队列在界内。
    FLOWING = "flowing"
    #: 内容已停止到达；该窗口没有可用测量，不调整速率。
    STARVED = "starved"
    #: 内容在到达但队列超界，需要降低速率。
    CONGESTED = "congested"

    def __init__(self, stalled_windows_before_starved: int = 2):
        self.stalled_windows_before_starved = stalled_windows_before_starved
        self._stalled = 0
        self.state = self.FLOWING
        self.note = "start"

    def observe(self, video_advanced: bool, audio_advanced: bool,
                queue_over_bound: bool) -> str:
        """对刚结束的窗口分类。

        `video_advanced`、`audio_advanced` 表示该路是否有产出；`queue_over_bound` 表示发送队列是否超界。
        单个安静窗口不算停供：HLS 源按分片突发送达，连续多个窗口才判定为停供。
        """
        if video_advanced or audio_advanced:
            self._stalled = 0
            if queue_over_bound:
                self.state = self.CONGESTED
                self.note = "content arriving and the queue is over its bound"
            else:
                self.state = self.FLOWING
                self.note = "content arriving"
            return self.state
        self._stalled += 1
        if self._stalled >= self.stalled_windows_before_starved:
            self.state = self.STARVED
            self.note = f"no content for {self._stalled} windows"
        else:
            # 证据不足：这是一个没有测量值的窗口，不能当作 flowing 告知控制器。
            self.state = self.FLOWING if not queue_over_bound else self.CONGESTED
            self.note = f"quiet window {self._stalled}/{self.stalled_windows_before_starved}"
        return self.state

    @property
    def measured(self) -> bool:
        """上一个窗口是否带有可用于速率决策的测量值。"""
        return self.state != self.STARVED


# 映射对一帧画面的判定：
#   PLACED：已得到会话时间戳；
#   IN_HOLE：内容落在发送端丢弃的声音区间内，丢弃该帧；
#   NOT_YET：对应声音尚未取走，保留该帧等待；
#   NO_ANCHOR：尚未取走任何声音，会话时间无定义。
VERDICT_PLACED = "placed"
VERDICT_IN_HOLE = "in-dropped-sound"
VERDICT_NOT_YET = "sound-not-taken-yet"
VERDICT_NO_ANCHOR = "no-sound-taken"


class SessionClock:
    """把内容时间按分段映射为线上时间。

    内容时间是条目在节目中的位置，不从 0 开始，随源而变。会话时间是在收听中的位置，从 0 开始，
    每收一个块恰好前进一个块长；设备要求音频时间戳严格等于上一个加一个块长。

    发送端丢弃的声音没有被听到，占内容时间而不占会话时间，所以映射是分段的：每次不连续就开始
    一个新段，段内会话时间与内容时间平行。

    画面时间戳取画面自身的内容位置换算到会话时间。时间戳正确是必要条件而非充分条件：
    实际绘制时刻还取决于发送、缓冲、丢帧与设备播放。本模块不测量观众看到与听到的偏差，
    `diagnostics()` 也不是这种测量。

    `audio_items` 为已取走的音频块数，发送端用它控制节奏。
    """

    __slots__ = ("chunk_ms", "tolerance_ms", "audio_items", "video_items", "last_video_ms",
                 "pairs", "residual_sum_ms", "residual_worst_ms",
                 "content_gap_ms", "audio_gaps", "frames_unplaceable",
                 "frames_in_hole", "last_verdict",
                 "_segments", "_prev_audio_content")

    def __init__(self, chunk_ms: float, tolerance_ms: float = 350.0):
        if chunk_ms <= 0:
            raise ValueError("chunk_ms must be positive")
        self.chunk_ms = float(chunk_ms)
        # 画面的会话位置允许领先该时刻声音位置的幅度，数值与发送端配对容差相同，用于 `placement` 的越界守卫。
        self.tolerance_ms = float(tolerance_ms)
        self.audio_items = 0
        self.video_items = 0
        self.last_video_ms = -1
        # 配对时画面内容相对声音的差值统计，为正表示画面靠前。
        self.pairs = 0
        self.residual_sum_ms = 0.0
        self.residual_worst_ms = 0.0
        # 源跳过的内容时长（毫秒）与次数。
        self.content_gap_ms = 0
        self.audio_gaps = 0
        # 映射无法放置的帧数（落入丢弃区间或时机未到）。
        self.frames_unplaceable = 0
        # 其中落入丢弃区间的帧数；只有这部分是损失，其余只是等待。
        self.frames_in_hole = 0
        self.last_verdict = VERDICT_NO_ANCHOR
        # 每段为（内容起点，会话起点，内容终点），终点不含；最新一段仍在接收音频，终点为 None。
        # 终点使丢弃区间内的内容不会沿前一段外推。
        self._segments: list[tuple[float, float, float | None]] = []
        self._prev_audio_content: float | None = None


    @property
    def anchored(self) -> bool:
        """是否已取走过声音；此前会话时间无意义。"""
        return bool(self._segments)

    def session_of(self, content_ms: float) -> float | None:
        """内容时刻对应的会话时间，没有则返回 None。

        段内：会话时间与内容时间平行。间隙内：声音已被发送端丢弃，会话中没有对应时间，返回 None。
        第一段之前：向后延伸第一段，结果可能为负，由 `placement()` 钳制为 0。
        """
        if not self._segments:
            return None
        first = self._segments[0]
        if content_ms < first[0]:
            # 早于会话第一个声音：向后延伸第一段。
            return float(content_ms) - first[0] + first[1]
        for start_content, start_session, end_content in self._segments:
            if start_content <= content_ms and (end_content is None
                                                or content_ms < end_content):
                # 落在本段内；最新一段没有终点，只向前延伸。
                return float(content_ms) - start_content + start_session
        # 不在任何一段内：位于已结束段与下一段之间，即发送端丢弃的声音，没有会话时刻。
        return None

    def audio(self, content_ms: float) -> int:
        """返回一个音频块的会话时间戳并计为已取走。

        时间戳逐块连续（设备要求）；源的不连续只改变内容映射，并计入 `content_gap_ms`。
        """
        content_ms = float(content_ms)
        # 本块的会话时间，先于修改映射计算：新段从本块的会话时间开始。
        value = int(round(self.audio_items * self.chunk_ms))
        if self._prev_audio_content is None:
            # 锚点：会话的第一个声音，会话时间为 0。
            self._segments = [(content_ms, float(value), None)]
        else:
            step = content_ms - self._prev_audio_content
            if step > self.chunk_ms * 1.5:
                missed = int(round(step - self.chunk_ms))
                self.content_gap_ms += missed
                self.audio_gaps += 1
                # 在实际收到的声音末尾关闭旧段，避免间隙内的内容沿旧段外推。
                previous = self._segments[-1]
                self._segments[-1] = (previous[0], previous[1],
                                      self._prev_audio_content + self.chunk_ms)
                # 从本块的会话时间开始新段。
                self._segments.append((content_ms, float(value), None))
        self._prev_audio_content = content_ms
        self.audio_items += 1
        return value

    def placement(self, content_ms: float,
                  pending_audio_ms: float | None = None) -> tuple[str, int | None]:
        """判定画面的位置，返回（判定，会话时间戳或 None）。

        `content_ms` 为画面内容时刻，须已换算到声音时钟（见 `ContentTimeline.video_in_audio_units`）。
        `pending_audio_ms` 为发送端下一个将取的音频块的内容位置（队首），用于在该块被取走之前
        提前关闭当前段、发现间隙。

        `IN_HOLE` 是损失，丢弃该帧；`NOT_YET` 是等待，该帧留在队列中。
        没有锚点时无会话时间，时间戳为 None。
        时间戳严格递增（设备拒绝不前进的视频时间戳），相同则加 1 毫秒；钳制为不小于 0。
        """
        self.last_verdict = VERDICT_NO_ANCHOR
        if not self.anchored:
            return VERDICT_NO_ANCHOR, None
        # 当前段的前沿，以及下一个声音是否接续它；队首在被取走前就可见，因此能提前发现间隙。
        edge = None
        if self._prev_audio_content is not None and pending_audio_ms is not None:
            edge = self._prev_audio_content + self.chunk_ms
            if pending_audio_ms <= edge:
                # 下一个声音接续本段，段直接延伸。
                edge = None
        if edge is not None and content_ms >= edge:
            if content_ms < pending_audio_ms:
                # 落入发送端丢弃的声音，没有可用的会话时间。
                self.frames_unplaceable += 1
                self.frames_in_hole += 1
                self.last_verdict = VERDICT_IN_HOLE
                return VERDICT_IN_HOLE, None
            # 连下一个声音也已越过，时机未到，保留该帧。
            self.last_verdict = VERDICT_NOT_YET
            return VERDICT_NOT_YET, None
        place = self.session_of(content_ms)
        if place is not None and place > (self.audio_items * self.chunk_ms
                                          + self.tolerance_ms):
            # 第二道守卫，针对尚不可见的间隙：间隙之后的声音被取走前，此刻配对的帧会沿仍未关闭的段跨过空洞。
            # 单向判断：画面可以落后于已取走的声音（声音比画面靠前一个队列深度），但不能超前；
            # 超前的余量取配对容差，因为队首尚未取走。
            self.frames_unplaceable += 1
            self.last_verdict = VERDICT_NOT_YET
            return VERDICT_NOT_YET, None
        if place is None:
            # 内容落入丢弃区间，没有会话时刻；此处若发出时间戳，会使下面的严格递增钳制超过恢复帧。
            self.frames_unplaceable += 1
            self.frames_in_hole += 1
            self.last_verdict = VERDICT_IN_HOLE
            return VERDICT_IN_HOLE, None
        value = int(round(place))
        if value < 0:
            value = 0
        if value <= self.last_video_ms:
            value = self.last_video_ms + 1
        self.last_video_ms = value
        self.video_items += 1
        self.last_verdict = VERDICT_PLACED
        return VERDICT_PLACED, value

    def video(self, content_ms: float,
              pending_audio_ms: float | None = None) -> int | None:
        """只返回画面的会话时间戳，无则 None；发送端需要区分丢弃与等待，用 `placement()`。"""
        return self.placement(content_ms, pending_audio_ms)[1]

    def note_pair(self, video_content_ms: float, audio_content_ms: float) -> None:
        """记录配对时画面内容相对声音的差值（毫秒），为正表示画面靠前；两个参数在同一时钟上。

        该差值反映两个队列的深度差，不是唇音偏差：恒定表示画面队列多存了这么多内容，需要关注的是它持续漂移。
        """
        residual = float(video_content_ms) - float(audio_content_ms)
        self.pairs += 1
        self.residual_sum_ms += residual
        if abs(residual) > abs(self.residual_worst_ms):
            self.residual_worst_ms = residual

    def diagnostics(self) -> str:
        """周期报告用的一行统计。"""
        mean = self.residual_sum_ms / self.pairs if self.pairs else 0.0
        return (f"audio={self.audio_items} video={self.video_items}"
                f" pair={self.pairs}"
                f" drift={mean:+.0f}ms worst={self.residual_worst_ms:+.0f}ms"
                f" gaps={self.audio_gaps}/{self.content_gap_ms}ms"
                f" unplaceable={self.frames_unplaceable}"
                f" hole={self.frames_in_hole}")
