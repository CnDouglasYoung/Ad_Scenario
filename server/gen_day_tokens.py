#!/usr/bin/env python3
"""生成本轮使用的 7 枚每日提交令牌（本文件生成物不随仓库分发）。

    python3 server/gen_day_tokens.py           # 写入 server/day_tokens.json
    python3 server/gen_day_tokens.py --print   # 只打印

令牌只用于「当天只能提交一次」的协议校验；真正的信息边界由引擎的步骤闸门
保证（未开放日期无法提交、报表返回 403），令牌泄露不足以让 Agent 提前拿到
未来信息。
"""
import json
import secrets
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "day_tokens.json"


def main() -> None:
    tokens = {str(d): f"t{d}-{secrets.token_hex(4)}" for d in range(1, 8)}
    if "--print" in sys.argv:
        print(json.dumps(tokens, indent=1))
        return
    OUT.write_text(json.dumps(tokens, indent=1), encoding="utf-8")
    print(f"已写入 {OUT}")


if __name__ == "__main__":
    main()
