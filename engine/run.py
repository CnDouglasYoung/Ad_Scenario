#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑一个剧本。用法：

    python3 run.py --script 0            # 规则基线
    python3 run.py --script A --quiet
    python3 run.py --script 0 --plans plans.json   # 自定义动作序列
"""
import argparse, json, sys
from data import GROUPS, U, load_conversions
from scripts import SCRIPTS
from replay import Run
from score import score
from baseline import FixedBid


def load_plans(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {int(k): v for k, v in data.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="0", choices=["0", "A", "B"])
    ap.add_argument("--plans", help="自定义动作：{day: {group: {bid, budget}}}")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    conv, _ = load_conversions()
    script = SCRIPTS[args.script]
    run = Run(script=script, conv=conv)
    plans = load_plans(args.plans) if args.plans else None
    policy = FixedBid(script.daily_cap_yuan)

    print(f"剧本 {script.key} · {script.name} · 总预算 ¥{script.total_yuan}")
    print(f"{'日':>3} {'报价':>16} {'赢标':>8} {'点击':>6} {'消耗(¥)':>10} {'累计(¥)':>10}")
    for day in range(1, 8):
        plan = plans[day] if plans else policy.plan(day, run.cum_spend, run.effective_total(day))
        rep = run.play_day(day, plan)
        wins = sum(g["impressions_won"] for g in rep["groups"].values())
        clk = sum(g["clicks"] for g in rep["groups"].values())
        bids = "/".join(str(plan[g]["bid"]) for g in GROUPS)
        print(f"{day:>3} {bids:>16} {wins:>8,} {clk:>6} "
              f"{run.daily_spend[day]/U:>10.2f} {run.cum_spend/U:>10.2f}")
        if run.failed:
            print(f"\n❌ 判负：{run.fail_reason}")
            break

    s = score(run)
    print(f"\n七天点击 {s['clicks']} | 曝光(D4-7) {s['impressions_4_7']:,} | "
          f"消耗 ¥{run.cum_spend/U:.5f}")
    print(f"罚分 {s['penalty']} | 加分 {s['bonus']} | 主分 {s['main_score']}")
    if args.script == "0":
        ok_i = s["impressions_4_7"] == 561_234
        ok_s = run.cum_spend == 69_839_567
        print(f"\n对账：Day4-7 曝光 {s['impressions_4_7']:,} vs 561,234 "
              f"{'✅' if ok_i else '❌'} | 实付 {run.cum_spend:,} vs 69,839,567 "
              f"{'✅' if ok_s else '❌'}")
        if not (ok_i and ok_s):
            sys.exit(1)


if __name__ == "__main__":
    main()
