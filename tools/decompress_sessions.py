#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把证据目录里的 session.v4.jsonl.zstd 解压成 .jsonl，供上下文抽取使用。

依赖宿主机的 zstd 命令（无需 python 包）。
用法：python3 tools/decompress_sessions.py [evidence_dir]
"""
import glob, os, subprocess, sys


def main(root="evidence"):
    files = sorted(glob.glob(os.path.join(root, "*", "steps", "*", "attempts", "*",
                                          "session.v4.jsonl.zstd")))
    done = skipped = failed = 0
    for src in files:
        dst = src[: -len(".zstd")]
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            skipped += 1
            continue
        try:
            subprocess.run(["zstd", "-d", "-f", "-q", src, "-o", dst], check=True)
            done += 1
        except Exception as e:
            failed += 1
            print(f"[warn] {src}: {e}", file=sys.stderr)
    print(f"会话存档：解压 {done}，已存在 {skipped}，失败 {failed}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "evidence")
