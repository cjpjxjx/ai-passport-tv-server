"""发送时序常量与固定调色板。"""

from __future__ import annotations

import os

from . import frames

# CONFIG 中声明的名义帧率，不是直播频道的实际发送帧率。设备只校验其合理（1..30），
# 不按它调度：每帧自带时间戳，按音频时钟呈现。直播频道的帧率在启动时由 ffprobe 探测
# （`LiveChannel.fps`）并在整个会话内保持；显式设置 `TV_FPS` 则覆盖探测、强制一个帧率。
FPS = int(os.environ.get("TV_FPS", "25"))
DURATION_MS = 10000
AUDIO_CHUNK_MS = 40
# 音频发送提前量：发送端领先墙钟多少，决定稳态下设备音频队列的深度。
# 见 CLAUDE.md 关键设计决策 5。可用环境变量调整。
AUDIO_LEAD_MS = int(os.environ.get("TV_AUDIO_LEAD_MS", "320"))
# 视频发送提前量，与音频独立设置：音频提前量决定设备队列深度，视频提前量只决定帧在自己
# 的时隙之前多早放出；视频提前量过大会与音频挤在一起、丢帧增多。
VIDEO_LEAD_MS = int(os.environ.get("TV_VIDEO_LEAD_MS", "200"))
START_DELAY_MS = 200


def default_palette() -> bytes:
    """直播画面索引所指的固定 3-3-2 调色板。

    步长 36 与 85 同 ffmpeg 的 AV_PIX_FMT_RGB8，量化到 RGB565 后 256 项全部一致。
    """
    return frames.palette_bytes(bytes(
        value
        for k in range(256)
        for value in (36 * ((k >> 5) & 7), 36 * ((k >> 2) & 7), 85 * (k & 3))
    ))
