# -*- coding: utf-8 -*-
"""三个对账点 + 一个回归：隐藏计价开关对消耗的影响。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import GROUPS, U, load_conversions, iter_day
from scripts import SCRIPTS
from replay import Run
from score import score, roi_4_7
from baseline import FixedBid, BIDS


def run_baseline(key):
    conv, _ = load_conversions()
    run = Run(script=SCRIPTS[key], conv=conv)
    pol = FixedBid()
    for d in range(1, 8):
        run.play_day(d, pol.plan(d, run.cum_spend, run.effective_total(d)))
    return run


def test_script0_impressions():
    assert score(run_baseline("0"))["impressions_4_7"] == 561_234


def test_script0_spend():
    assert run_baseline("0").cum_spend == 69_839_567


def test_script0_clicks():
    assert run_baseline("0").total_clicks == 613


def test_no_hidden_pricing_reproduces_old_numbers():
    """关掉 Day3/6 隐藏计价 → 613 点击 / 66,177,492 单位（= 旧规格的 ¥661.77）"""
    conv, _ = load_conversions()
    cum = clicks = 0
    for day in range(1, 8):
        pol = FixedBid()
        plan = pol.plan(day, cum, SCRIPTS["0"].total_units(day))
        quota = {g: plan[g]["budget"] for g in GROUPS}
        mw = {g: quota[g] // BIDS[g] for g in GROUPS}
        win = {g: 0 for g in GROUPS}
        for g, p, cl, cv in iter_day(day, conv):
            if win[g] >= mw[g] or BIDS[g] <= p:
                continue
            win[g] += 1
            cum += BIDS[g] if g.startswith("ex1") else p
            clicks += int(cl)
    assert (clicks, cum) == (613, 66_177_492)


def test_scriptA_clicks():
    assert run_baseline("A").total_clicks == 503


def test_roi_gate_loose():
    roi, _, _ = roi_4_7(run_baseline("B"))
    assert roi > 0.35
