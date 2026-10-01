# CLAUDE.md

本文档为 Claude Code 提供指导，帮助在开发 **ai-passport-tv-server** 时理解项目上下文、设计原则与开发约束。定位：AI Passport TV 设备的局域网媒体服务器，把直播频道实时转码为设备可绘制的调色板索引画面与 PCM 音频，经 FAV1 协议推流。

无论用户输入包含哪种语言，包括英文代码、报错信息或专业术语，都用简体中文回答和解释；仅当用户明确要求"用英语回答"或"翻译"时才使用其他语言。

## 需求处理流程（强制）

除非用户明确说明是"顺手""小改""直接改"这类不需要确认的琐碎调整，否则收到任何功能性需求时，**必须先完成理解与确认，再动手实现**：

1. **复述需求**：用自己的话简要复述理解到的需求，写清隐含前提与边界：涉及范围、是否要兼容旧数据、是否要动已有行为。需求有歧义或存在多种合理理解时，明确指出分歧点，不要自行选一种理解就往下做。
2. **给出简要计划**：列出打算改动的文件/模块、大致思路，以及是否会触及本文件"核心设计原则"或"关键设计决策"里的约束。计划保持简短，几行到十几行即可。
3. **等待用户确认后才动手实现**，包括写代码、提交代码。用户纠正理解偏差或调整方向时，按新的理解重新给出复述和计划。

> [README.md](README.md) 面向使用者，讲功能、配置与使用方法；本文件面向开发，讲设计决策与操作性约束。两者冲突时以 README.md 为准，并同步修订本文件。
## 项目概述

Python 标准库实现的单客户端局域网服务器，不是公网代理或带完整鉴权的服务。设备按频道名连接，服务端为每个会话启动一个 ffmpeg 进程，同时解码音视频，视频缩放并量化到固定 3-3-2 调色板后按条带 deflate 压缩，音频转 16 kHz 单声道 PCM，按设备音频时钟调度发送。

- **直播转码与会话**：频道表加载、ffmpeg 命令构造、队列与预缓冲，见 [server/live.py](server/live.py)。
- **会话与发送调度**：监听、握手鉴权、直播会话、包调度，见 [server/tv_server.py](server/tv_server.py)。
- **画面编码**：条带切分、增量发送、deflate 压缩、视频包打包，见 [server/frames.py](server/frames.py)。
- **帧率控制**：固定帧率与可选的自适应字节率，见 [server/rate.py](server/rate.py)。

当前处于**持续迭代**阶段，与固件仓库 `ai-passport-tv` 配套使用。

## 核心设计原则（最高优先级）

以下条目来自固件 `208357b` 的记录并与本仓库代码核对，设备不在本仓库，无法在本地验证。

1. **【MUST】固件契约同步修改** —— 下列任一项只能与固件同时修改，否则会话被重置、屏幕显示彩条：调色板 256 项（`frames.PALETTE_ENTRIES`）；画面几何与条带行数（`frames.GEOMETRIES`，默认 320x180、12 行、15 条带，`CONFIG` 带 `stripe_rows` 供设备校验）；单个压缩条带不超过 4096 字节；视频包不超过 `frames.VIDEO_MAX`；`CONFIG` 的 `fps` 为 1..30；频道数与频道 id 长度上限（`live.TV_CHANNEL_MAX`、`live.TV_CHANNEL_ID_MAX`）。

2. **【MUST】设备约 3 秒收不到数据即断开重连** —— 音频断流超过该时限会结束会话。发送以完整 FAV1 包为界，视频大包分片写入（`tv_server.VIDEO_SLICE_BYTES`），视频晚于呈现时刻则丢弃，不得为视频阻塞音频。

3. **【MUST】不依赖设备反馈** —— `HELLO` 之后设备不再上行，服务端无法得知丢包与积压；速率与积压判断只用本地写入耗时（`rate.py`）。

4. **【MUST】零长度条带表示"未变化"** —— 增量发送依赖它（`frames.compress_stripes`、`frames.choose_stripes`）；旧固件遇到零长度条带会结束会话。

5. **【MUST】内容时间戳取自解码器自身的 pts** —— 不用到达时间，不用产出计数推算；`showinfo` 行序号与已读条目数不符时拒绝继续（`pts.Timestamps`、`pts.Misaligned`）。

6. **【MUST】频道地址只来自频道表白名单，来源只接受私有 IPv4** —— `LiveChannel` 拒绝表外 URL；默认监听 `0.0.0.0`，`serve()` 对非私有来源地址（`tv_server.local_ipv4`）的连接立即关闭，该过滤不可去掉；无 TLS，不做公网端口转发。

## 通用开发约束

- **【MUST】** 保持代码简洁易懂，避免过度设计和不必要的抽象；不擅自添加用户未要求的功能。
- **【MUST】** 用户提出需求时，先阅读 [README.md](README.md) 和相关模块代码，理解当前项目目标、架构与实现方式。
- **【MUST】** 功能/架构发生变化后同步更新 README.md 与本文件，避免文档与代码脱节。
- **【MUST】** 新增功能时创建新模块或函数，不要把逻辑全部塞进已有的大文件或大函数。
- **【MUST】** 项目特性按用户需求分阶段实现，不需要一次性把 README.md 里列出的所有特性都做完。
- **【MUST】** 处理需求前先查"架构说明"定位到相关模块，用 grep/关键字搜索确认涉及范围，
  只读取真正相关的文件；`tv_server.py`、`live.py` 体量大，只按函数名读取局部。
  "架构说明"和"关键设计决策"没有覆盖到、确实无法判断影响范围时，再扩大搜索或直接询问用户。
- **【MUST】** 密码、密钥、token、API secret 等敏感信息严禁出现在上下文、代码、注释、文档和命令中；
  需要引用时一律用占位符（如 `${API_KEY}`、`***`），或在命令中通过环境变量注入；
  不主动读取凭证文件内容，也不接受把真实凭证粘贴进对话。

## 代码风格

- **【MUST】** 不在代码注释、日志输出、控制台输出中使用 emoji 表情及特殊符号；README.md 与文档中可以使用。
- **【MUST】** 网络请求、外部进程调用、文件读写等操作须捕获异常并记录日志，失败要可见，不静默吞掉；单个会话失败不中断监听循环。
- **【MUST】** 异常信息、日志不包含 token、HELLO 载荷与对端数据。
- **【SHOULD】** 使用类型注解，文件头保留 `from __future__ import annotations`。
- **【SHOULD】** 函数和类添加简短的 docstring，注释密度与同文件其它同类条目保持一致。
- **【SHOULD】** 新增第三方依赖前先评估必要性；运行只依赖标准库，`numpy` 为可选依赖，同步更新 [requirements.txt](requirements.txt)。
- **【MUST】** 面向用户的控制台提示用简体中文，代码注释与 docstring 用英文。
- **【MUST】** `.py`、`.md` 用 UTF-8，换行符用 LF。

## 文档规范

README.md、CLAUDE.md 及代码注释遵守：

- 只客观描述功能、现状、使用方法与约束，不强调"做了哪些修改/优化"、不解释"为什么这么改"；代码注释、commit message、对话回复同理。确需长期保留原因的，写进"关键设计决策"章节。
- 篇幅精简：一句话能说清楚的不写第二句，不铺垫背景、不重复自证。
- **篇幅纪律**：CLAUDE.md 上限 200 行，每次改完检查总行数；超出时压缩已有条目，不放宽上限。
- **不记录可直接查询的现状**：一条命令就能拿到的内容不写进文档，写"用什么命令查"。
- **只写现状，不写沿革**：不写"X 原为 Y""曾判断为""本次""第 N 次调整"这类叙述；对象删除或替换后不再提及。
- **保留结论，不保留过程**：只写当前结论与取舍逻辑；同类信息只在一处展开，别处一行指回；新信息覆盖旧条目，不新增"变更记录"。
- **落笔自检**：写每一行前自问，读者据此能做出什么决定或操作？答不上来就删。
- **直接写规则**：用祈使句写"做什么""不做什么"，不为规则补举例、强调语气或说明性文字。
- "关键设计决策"只记录当前结论与必要的取舍/风险点，不归因到人，不写审批日期；具体数字大幅精简。
- 已实测的信息与推算/转述/未验证的信息必须显式区分。
- 使用中文编写，代码块、表格、列表等不同元素之间用空行隔开，合理缩进。
- 中文与半角英文字符之间加一个半角空格；中文与阿拉伯数字之间同样加一个半角空格，全文统一。

## 架构说明

数据流：`channels.txt` → `live.py`（ffmpeg 解码）→ `frames.py`（条带压缩）→ `tv_server.py`（调度与发送）→ 设备。

- **[server/tv_server.py](server/tv_server.py)** —— `AVServer.serve`（监听与单客户端接入）、`_live_session`（直播会话：CONFIG、PALETTE、帧发送）、`_pace_live`（发送调度循环）、`load_token` / `authenticate`（令牌鉴权）、`main`（唯一子命令 `live`）。涉及命令行参数、令牌、握手、发送节奏时看这里。
- **[server/protocol.py](server/protocol.py)** —— `Kind`、`Packet`、`send_packet`、`receive_packet`：24 字节包头、类型、长度上限、整包超时。涉及线协议字段与限长时看这里。
- **[server/frames.py](server/frames.py)** —— `GEOMETRIES`、`compress_stripes`、`choose_stripes`、`encode_within`、`pack_stripes`、`input_options`、`palette_bytes`：几何、条带、增量、字节预算、ffmpeg 输入参数。涉及画面格式、包大小、压缩时看这里。
- **[server/live.py](server/live.py)** —— `parse_channels` / `load_channels`（频道表）、`LiveChannel`（单频道会话：两路队列、预缓冲、对齐）、`source_command`（ffmpeg 命令）、`probe_source_fps`。涉及频道表、ffmpeg 参数、队列与预缓冲、音画对齐时看这里。
- **[server/timeline.py](server/timeline.py)** —— `ContentTimeline`、`SessionClock`、`SourceState`：内容时间轴与源状态判断。
- **[server/pts.py](server/pts.py)** —— `Timestamps`、`Reader`：解析 ffmpeg `showinfo` / `ashowinfo` 输出的 pts。
- **[server/rate.py](server/rate.py)** —— `ByteRate`：字节率与自适应，无 IO。涉及字节预算、自适应时看这里。
- **[server/perceptual.py](server/perceptual.py)** —— `encode_within`：帧超出字节目标时的感知取舍，依赖 numpy。
- **[server/media.py](server/media.py)** —— 时序常量（`FPS`、`AUDIO_LEAD_MS`、`VIDEO_LEAD_MS`、`START_DELAY_MS`）与固定调色板 `default_palette`。
- **[server/netident.py](server/netident.py)** —— `lan_address`、`local_name`、`describe`：启动时打印设备应填写的地址与 `.local` 名称。
- **[tools/log_stamp.py](tools/log_stamp.py)** —— 给标准输入逐行加时间戳并按天写日志。

运行时调参环境变量均以 `TV_` 开头，用 `grep -n 'environ.get("TV_' server/*.py` 查看名称与默认值。

## 关键设计决策

以下结论取自代码注释中的实测记录；标注"旧几何"的数字在当前 320x180 几何下未重测。

### 1. 固定调色板、关闭抖动

画面量化到 ffmpeg 固定 3-3-2 调色板（`LiveChannel.build_palette`），不按频道取样自适应调色板；ffmpeg 加 `-sws_dither none`。自适应调色板会在后续画面超出取样色域时偏色；抖动是噪声，会使最坏帧从约 23 KB 增至约 31 KB（实测五个频道）。固定调色板的代价是帧更大、帧率更低。

### 2. 条带独立压缩，deflate 级别 6

条带独立压缩使面板可边收边刷，设备只需一个条带大小的缓冲。级别 6 比级别 1 每帧少约 9.4% 字节（旧几何实测）。

### 3. 视频包目标 12288 字节

一帧拆成多个包在旧几何下实测更差（欠载、重置增多）；单包过大则读取期间音频无人读取。目标值取中间（`TV_PACKET_TARGET`），协议上限 `VIDEO_MAX` 为硬限制。

### 4. 发送缓冲 16384

`TV_SEND_BUFFER` 升到 65536 时发送队列堆到 64 KB，帧率从 5.0 降到 2.2 fps（实测）；瓶颈是链路而非窗口。

### 5. 音频提前量 320 ms，视频 200 ms

两者独立设置。音频提前量决定设备端队列深度；240 秒扫描中 200 ms 出现 10 次欠载，320 ms 无欠载无丢帧，400 ms 丢帧 39 个（实测）。视频提前量加到 320 ms 会与音频挤在一起，丢帧显著增多。

### 6. 帧率默认固定，自适应为可选

`TV_ADAPTIVE` 默认关闭，字节率固定；开启后 `ByteRate` 按写入耗时调整字节率，帧率不变。直播帧率取自 ffprobe 探测的源帧率，`TV_FPS` 显式设置时覆盖探测。

### 7. 一个 ffmpeg 进程产出音视频，经回环 TCP 输出

一次解码同时产出两路载荷，pts 天然同一时间轴。两路输出走 `tcp://127.0.0.1:<port>` 而非管道：Windows 的 `subprocess.Popen` 不支持 `pass_fds`。

### 8. 无令牌也可运行

未配置令牌时不校验 HELLO 内容，仅检查 HELLO 形状，并在控制台提示同网段设备均可收看。配置令牌后严格校验。

## 配置与凭证约束

- **【MUST】** 配对令牌通过 `--token-file` 或环境变量 `TV_PAIRING_TOKEN` 注入，二者不可同时使用；**绝不入库**，**也绝不进入上下文、命令与文档**；文档中只出现占位符。
- **【MUST】** 令牌文件须为进程用户拥有的普通文件，权限 0400 或 0600，不得为符号链接。
- **【MUST】** `.gitignore` 保持忽略 `.local/`、日志与运行时生成物；`channels.txt` 不放带凭证的地址。新增配置项时同步更新 README.md。

## 运行与部署

需要 Python 3.10+（实测 3.10.11）与 PATH 中的 `ffmpeg`、`ffprobe`；工作目录或仓库根需有 `channels.txt`，也可用 `TV_CHANNELS_FILE` 指定。

```bash
python -m server.tv_server live --channel <频道 id>
python -m server.tv_server live --channel <频道 id> --bind <局域网 IPv4> --port 8096 --token-file <令牌文件>
python -u -m server.tv_server live --channel <频道 id> 2>&1 | python tools/log_stamp.py server
```

没有 Docker 部署。频道表只在启动时读取一次，修改后重启服务。

### 测试与检查

没有配置测试、lint、类型检查或构建流程；发送节奏与画面格式的改动用真实设备验证。

## 实现状态

- **已实现**：直播转码与 FAV1 推流、令牌鉴权、频道表、增量条带、可选的自适应帧率与感知取舍。
- **范围**：只支持直播频道，没有预生成素材与本地视频文件导入。
- **已知缺陷**：`load_token` 使用 Windows 不存在的 `os.O_NONBLOCK` 与 `os.getuid`，Windows 上 `--token-file` 不可用（实测）。

## 开发执行检查清单

### 需求确认阶段

- [ ] 已按"需求处理流程"复述需求、给出简要计划，并等到用户确认；琐碎调整除外

### 编码阶段

- [ ] 是否遵循"核心设计原则"中的约束，涉及固件契约的改动是否同步到固件
- [ ] 外部进程 / 网络调用是否捕获异常，不中断监听循环
- [ ] 敏感信息未出现在日志、异常信息、提交内容、代码、注释、文档或命令中
- [ ] 新增配置项是否同步更新 README.md

### 验证阶段

- [ ] `live` 子命令能启动，设备连接后画面与声音正常
- [ ] 涉及发送节奏、画面格式的改动用真实设备验证

### 提交阶段

- [ ] 功能变更时更新 README.md，设计原则或约束变更时更新本文件，总行数不超过 200
- [ ] 提交信息简明扼要，只说动机/影响范围，不逐条罗列改动细节
