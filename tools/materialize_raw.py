#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把宿主采集的 chunk 流还原成 DSH 的原始事件 JSONL。

evidence/<trial>/steps/<step>/attempts/<id>/stream.jsonl 里每行是
{"stream": "...", "text": "..."}；把 text 按顺序拼起来就是 DSH 的 stdout。
还原结果写在同目录的 dsh.jsonl（只保留能解析为 JSON 的行）。
"""
import glob, json, os, sys


def materialize(stream_path: str) -> tuple[int, int]:
    texts = []
    for line in open(stream_path, encoding="utf-8", errors="ignore"):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        texts.append(rec.get("text", ""))
    raw = "".join(texts)
    out_path = os.path.join(os.path.dirname(stream_path), "dsh.jsonl")
    n_ok = n_bad = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except Exception:
                n_bad += 1
                continue
            f.write(line + "\n")
            n_ok += 1
    return n_ok, n_bad


def main(root="evidence"):
    total_ok = total_bad = 0
    for s in sorted(glob.glob(os.path.join(root, "*", "steps", "*", "attempts", "*", "stream.jsonl"))):
        ok, bad = materialize(s)
        total_ok += ok
        total_bad += bad
        print(f"{s.replace(root+'/','')}: {ok} 条事件" + (f"（丢弃 {bad} 行非 JSON）" if bad else ""))
    print(f"\n合计 {total_ok} 条事件，丢弃 {total_bad} 行")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "evidence")
