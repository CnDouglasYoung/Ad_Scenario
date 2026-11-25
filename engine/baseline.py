# -*- coding: utf-8 -*-
"""规则基线 fixed-bid：报价固定，额度按「剩余 ÷ 剩余天数」匀速推进。

与《场景规格 v3》§6.5 的伪代码逐行对应，用于复算两个对账点。
"""
from data import GROUPS, U

BIDS = {"ex1_336x280": 90, "ex1_728x90": 90, "ex2_728x90": 100, "ex3_1000x90": 100}


class FixedBid:
    """每日产出 {group: {bid, budget}}。"""

    def __init__(self, daily_cap_yuan=150.0):
        self.cap = int(daily_cap_yuan * U)

    def plan(self, day: int, cum_spend: int, total_units: int) -> dict:
        remaining = max(0, total_units - cum_spend)
        quota = min(self.cap, remaining // (8 - day))
        base, rem = divmod(quota, 4)
        alloc = {g: base for g in GROUPS}
        for i in range(rem):
            alloc[GROUPS[i]] += 1
        return {g: {"bid": BIDS[g], "budget": alloc[g]} for g in GROUPS}
