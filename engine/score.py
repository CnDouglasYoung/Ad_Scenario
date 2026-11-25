# -*- coding: utf-8 -*-
"""评分：主分 = 七天点击 − 日超限罚分 + 剧本 B 的曝光加分。"""
from data import U

LAMBDA = 2.0            # 次点击 / 元（标定：最便宜日 1 元 ≈ 1.374 次点击）
BASE_IMPR = 561_234     # Day4-7 赢标曝光基线（已复算）
BONUS_MAX = 15.0        # 最多加 15 个等价点击
BONUS_FULL_AT = 0.10    # 超过基线 10% 加满


def daily_overage_penalty(run) -> float:
    cap = int(run.script.daily_cap_yuan * U)
    over = sum(max(0, s - cap) for s in run.daily_spend.values())
    return LAMBDA * over / U


def impressions_4_7(run) -> int:
    return sum(c.wins for d, g in run.days.items() if 4 <= d <= 7 for c in g.values())


def roi_4_7(run):
    spend = sum(s for d, s in run.daily_spend.items() if d >= 4)
    convs = sum(c.conversions for d, g in run.days.items() if d >= 4 for c in g.values())
    if spend == 0:
        return None, convs, spend
    return (convs * 12 * U - spend) / spend, convs, spend


def bonus_b(run) -> tuple:
    """返回 (加分, 是否达标, Day4-7 曝光, ROI)。"""
    imp = impressions_4_7(run)
    roi, convs, spend = roi_4_7(run)
    if roi is None or roi < 0.35:
        return 0.0, False, imp, roi
    gain = (imp - BASE_IMPR) / (BONUS_FULL_AT * BASE_IMPR)
    return BONUS_MAX * min(1.0, max(0.0, gain)), True, imp, roi


def score(run) -> dict:
    pen = daily_overage_penalty(run)
    bonus, ok, imp, roi = (bonus_b(run) if run.script.bonus else (0.0, None, impressions_4_7(run), None))
    main = 0.0 if run.failed else run.total_clicks - pen + bonus
    return {"clicks": run.total_clicks, "penalty": round(pen, 2), "bonus": round(bonus, 2),
            "bonus_qualified": ok, "impressions_4_7": imp, "roi_4_7": roi,
            "main_score": round(main, 2), "failed": run.failed}
