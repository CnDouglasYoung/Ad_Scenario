# -*- coding: utf-8 -*-
"""三个剧本的配置。总预算单位：元。"""
from dataclasses import dataclass, field

U = 100_000


@dataclass
class Script:
    key: str
    name: str
    total_yuan: float = 900.0
    daily_cap_yuan: float = 150.0
    # 从第几天起总预算变化：(day, new_total_yuan)
    budget_change: dict = field(default_factory=dict)
    # 剧本 B：从第几天起启用 ROI 门槛
    roi_gate: dict = field(default_factory=dict)   # {start_day: threshold}
    bonus: bool = False

    def total_units(self, day: int) -> int:
        t = self.total_yuan
        for d, v in sorted(self.budget_change.items()):
            if day >= d:
                t = v
        return int(t * U)

    def roi_threshold(self, day: int):
        th = None
        for d, v in sorted(self.roi_gate.items()):
            if day >= d:
                th = v
        return th


SCRIPTS = {
    "0": Script("0", "稳定对照"),
    "A": Script("A", "预算收紧", budget_change={5: 600.0}),
    "B": Script("B", "回报约束下扩量", roi_gate={4: 0.35}, bonus=True),
}
