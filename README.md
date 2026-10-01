# ai-passport-tv-server

AI Passport TV 设备的局域网媒体服务器：把直播频道（HLS 等）实时转码为调色板索引画面与 16 kHz 单声道 PCM，经 FAV1 协议推送给设备。单设备、仅限可信局域网，不是公网代理，无 TLS。

## 环境要求

- Python 3.10+（实测 3.10.11），运行只依赖标准库。
- `ffmpeg`、`ffprobe` 在 PATH 中。
- 可选依赖见 [requirements.txt](requirements.txt)：`numpy`（帧超出字节目标时的感知取舍）。
- 设备固件须与服务端的画面几何、调色板等参数一致，见 [CLAUDE.md](CLAUDE.md)"核心设计原则"。

## 快速开始

在仓库根目录运行：

```bash
python -m server.tv_server live --channel ch000
```

- 不指定 `--bind` 时监听所有网卡（`0.0.0.0`），并打印设备上应填写的地址；设备端优先填 `.local` 名称，路由器重新分配 IP 后无需修改。非私有网段（10/8、172.16/12、192.168/16、127/8）来源的连接会被立即关闭。
- 关闭运行窗口即停止服务，设备画面中断。
- 每个设备连接独立启动一个 ffmpeg 进程，换台即设备以新频道名重新连接；转码失败只结束该会话，服务继续监听。
- 同一时间只服务一个已连接设备，其它连接被立即关闭。

带完整参数：

```bash
python -m server.tv_server live --channel ch000 --bind 192.168.1.20 --port 8096 --token-file <令牌文件>
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--channel` | 必填 | 默认频道 id，取自频道表；设备未指定频道时使用 |
| `--bind` | `0.0.0.0` | 监听地址，须为 `0.0.0.0` 或私有 IPv4 |
| `--port` | 8096 | 监听端口 |
| `--token-file` | 无 | 配对令牌文件，见下文 |
| `--ffmpeg` | `ffmpeg` | ffmpeg 可执行文件路径 |
| `--media` | `both` | `audio` 或 `video` 仅发送单路，用于诊断 |

## 频道表

`channels.txt` 每行一个频道，`#` 开头为注释：

```text
id | 显示名 | 地址 [| User-Agent]
```

- `id` 为 1..15 个可打印 ASCII 字符，不可重复；显示名不可为空；地址以 `http://` 或 `https://` 开头。
- 顺序即设备上换台的顺序；设备最多接收 128 个频道，超出部分被忽略。
- 查找顺序：环境变量 `TV_CHANNELS_FILE` → 当前目录 `channels.txt` → 仓库根目录 `channels.txt`；都没有时使用内置的 4 个公开频道。
- 只在启动时读取一次，修改后重启服务。
- 仓库自带的 `channels.txt` 地址来自第三方聚合列表，可用性与版权未经核实，仅用于家庭局域网测试。

## 配对令牌

令牌为 16..128 个可打印 ASCII 字符，应使用随机值；不要写进命令参数、日志或仓库。

- `--token-file <文件>`：文件须为当前用户拥有的普通文件，权限 0400 或 0600，不得为符号链接；Windows 上暂不可用。
- 环境变量 `TV_PAIRING_TOKEN`：与 `--token-file` 二选一。
- 都不设置时不校验令牌，同一网络内的设备都可收看，启动时会打印提示。

设备在 HELLO 中携带令牌，比对使用 `hmac.compare_digest`，失败时不输出令牌或收到的载荷。

## 环境变量

常用调参项如下，完整列表用 `grep -n 'environ.get("TV_' server/*.py` 查看：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TV_CHANNELS_FILE` | 空 | 频道表路径 |
| `TV_PAIRING_TOKEN` | 空 | 配对令牌 |
| `TV_GEOMETRY` | `320x180` | 画面几何，须与固件一致 |
| `TV_FPS` | `25` | 显式设置时覆盖直播源帧率探测 |
| `TV_ADAPTIVE` | `0` | `1` 启用自适应字节率 |
| `TV_DELTA` | `1` | `0` 关闭增量条带 |
| `TV_PERCEPTUAL` | `1` | `0` 关闭感知取舍 |
| `TV_PACKET_TARGET` | `12288` | 视频包目标字节数 |
| `TV_SEND_BUFFER` | `16384` | socket 发送缓冲字节数 |
| `TV_AUDIO_LEAD_MS` | `320` | 音频提前发送量 |
| `TV_VIDEO_LEAD_MS` | `200` | 视频提前发送量 |
| `TV_TRACEBACK` | 空 | `1` 在启动失败时打印异常栈 |

## 线协议

每个 TCP 包为 24 字节网络序包头（`!4sBBHIIII`）加载荷，字段依次为 magic（`FAV1`）、version、flags、type、session、seq、pts_ms、payload_length。

| type | 名称 | 方向 | 载荷 |
| --- | --- | --- | --- |
| 1 | HELLO | 设备到服务端 | JSON：`version`、`token`、可选 `channel` |
| 2 | CONFIG | 服务端到设备 | JSON：几何、`fps`、采样参数、提前量、`channel`、`channel_list` |
| 3 | PCM | 服务端到设备 | 1280 字节，40 ms 一包 |
| 4 | 视频（代码中为 `Kind.JPEG`） | 服务端到设备 | 一段连续条带，见下 |
| 5 | END | 双向 | 无 |
| 6 | ERROR | 保留 | 服务端出错时直接关闭连接，不发送 |
| 7 | PALETTE | 服务端到设备 | 256 项调色板，位于 CONFIG 之后、首帧之前 |

- HELLO 之后设备不再上行媒体或控制包；非法包头、长度、会话或 JSON 直接断开，不搜索下一个 magic。
- `HELLO.channel` 缺省或不在频道表中时使用 `--channel` 指定的频道。
- `CONFIG.channels` 是音频声道数，必须保持数字；频道列表放在独立的 `channel_list`（每项 `{id, name}`）。
- 视频包载荷：`[u8 起始条带][u8 条带数][u16 长度 × 条带数][各条带 deflate 数据]`，长度为大端；长度为 0 表示该条带未变化。flags 的 `0x01` 表示续接同一帧的后续条带包。
- 画面为每像素 1 字节的调色板索引，按条带独立压缩；默认 320x180，每条带 12 行，共 15 条带。
- 各类型的长度上限见 `server/protocol.py`。

## 日志

```bash
python -u -m server.tv_server live --channel ch000 2>&1 | python tools/log_stamp.py server
```

按天写入 `<日志目录>/server-YYYYMMDD.log`，目录由 `--dir`、环境变量 `AV_LOG_DIR` 或默认的仓库下 `logs` 目录决定。
