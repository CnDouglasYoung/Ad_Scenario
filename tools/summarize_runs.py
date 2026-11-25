#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总跑批结果：按 (剧本, 模型) 输出 n / 均值 / 极差 / 明细，并给逐 run 明细。
用法：python3 tools/summarize_runs.py 'work/harbor-jobs/m5r*' [--json out.json]
"""
import glob, json, os, re, statistics, sys

BASE = {"0": 613.0, "A": 503.0, "B": 613.0}


def collect(patterns):
    rows = []
    for pat in patterns:
        for d in sorted(glob.glob(pat)):
            tag = os.path.basename(d.rstrip("/"))
            m = re.search(r"-(ds|glm)-([0AB])", tag)
            if not m:
                continue
            model, script = m.group(1), m.group(2)
            sc = glob.glob(f"{d}/**/steps/day7/verifier/score.json", recursive=True)
            if not sc:
                continue
            try:
                doc = json.load(open(sc[0], encoding="utf-8"))
            except Exception:
                continue
            s, sm = doc["score"], doc["summary"]
            rows.append({
                "tag": tag, "model": model, "script": script,
                "clicks": s["clicks"], "spend_yuan": round(sm["spend_yuan"], 2),
                "penalty": round(s["penalty"], 2), "main_score": round(s["main_score"], 2),
                "reward": round(s["main_score"] / BASE.get(sm["script"], 613.0), 3),
                "frozen": bool(sm.get("frozen")),
                "frozen_from": sm.get("frozen_from"),
            })
    return rows


def main():
    args = [a for a in sys.argv[1:] if a != "--json"]
    if "--json" in sys.argv:
        args = args[:-1]                       # 去掉 --json 后面的输出路径
    rows = collect(args or ["work/harbor-jobs/m5r*"])
    if not rows:
        sys.exit("没有找到已完成的 run")
    groups = {}
    for r in rows:
        groups.setdefault((r["script"], r["model"]), []).append(r)
    print("| 剧本 | 模型 | n | 均值 | 极差 | 明细 |")
    print("|---|---|---:|---:|---:|---|")
    for k in sorted(groups):
        v = [r["reward"] for r in groups[k]]
        print(f"| {k[0]} | {k[1]} | {len(v)} | {statistics.mean(v):.3f} | "
              f"{max(v) - min(v):.3f} | {', '.join(f'{x:.3f}' for x in v)} |")
    print()
    print("| run | 剧本 | 模型 | 点击 | 花费 | 罚分 | 主分 | 归一 | 冻结 |")
    print("|---|---|---|---:|---:|---:|---:|---:|---|")
    for r in sorted(rows, key=lambda x: (x["script"], x["model"], x["reward"])):
        fz = f"day{r['frozen_from']}" if r["frozen"] else ""
        print(f"| {r['tag']} | {r['script']} | {r['model']} | {r['clicks']} | "
              f"¥{r['spend_yuan']} | {r['penalty']} | {r['main_score']} | {r['reward']:.3f} | {fz} |")
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n已写出 {out}")


if __name__ == "__main__":
    main()
