#!/usr/bin/env python3
"""逐行读取标准输入，加本地时间戳后写入 LOGDIR/<name>-YYYYMMDD.log，每天一个文件。
LOGDIR 默认为 <repo>/logs，可用 AV_LOG_DIR 或 --dir 覆盖。

    python -u -m server.tv_server live --channel <id> --bind <addr> 2>&1 | python tools/log_stamp.py server [--dir LOGDIR]

排查时在服务端日志中搜索：STATE_CHANGE、Traceback、ProtocolError、LiveError、
channel_audio_empty、"source=starved"。
"""
import datetime as dt
import os
import sys


def log_dir(argv: list[str]) -> str:
    if "--dir" in argv:
        path = argv[argv.index("--dir") + 1]
    else:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.environ.get("AV_LOG_DIR") or os.path.join(repo, "logs")
    os.makedirs(path, exist_ok=True)
    return path


def main() -> int:
    name = sys.argv[1]
    directory = log_dir(sys.argv)
    f, day = None, None
    for raw in sys.stdin.buffer:
        now = dt.datetime.now()
        if day != now.date():
            if f:
                f.close()
            day = now.date()
            f = open(os.path.join(directory, f"{name}-{day:%Y%m%d}.log"), "a", encoding="utf-8", errors="replace")
        f.write(f"{now:%Y-%m-%d %H:%M:%S.%f}"[:-3] + " " + raw.decode("utf-8", "replace").rstrip() + "\n")
        f.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
