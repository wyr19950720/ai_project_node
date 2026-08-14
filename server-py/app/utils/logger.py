# server-py/app/utils/logger.py
# 结构化日志：开发环境彩色输出，生产环境 JSON 输出
import json
import sys
from datetime import datetime, timezone

from app.config import config

IS_PROD = config.app.env == "production"

_COLORS = {"info": "\x1b[36m", "warn": "\x1b[33m", "error": "\x1b[31m", "debug": "\x1b[90m"}
_RESET = "\x1b[0m"


def _log(level: str, msg: str, ctx: dict | None = None):
    ctx = ctx or {}
    entry = {"time": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg, **ctx}

    if IS_PROD:
        sys.stdout.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return

    c = _COLORS.get(level, "")
    time_str = entry["time"][11:19]
    ctx_str = f" {json.dumps(ctx, ensure_ascii=False)}" if ctx else ""
    print(f"{c}[{time_str}] {level.upper()} {msg}{ctx_str}{_RESET}")


class _Logger:
    def info(self, msg, ctx=None):
        _log("info", msg, ctx)

    def warn(self, msg, ctx=None):
        _log("warn", msg, ctx)

    def error(self, msg, ctx=None):
        _log("error", msg, ctx)

    def debug(self, msg, ctx=None):
        if not IS_PROD:
            _log("debug", msg, ctx)


logger = _Logger()
