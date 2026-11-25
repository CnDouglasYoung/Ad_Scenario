# -*- coding: utf-8 -*-
"""核心回放引擎。

口径（与《场景规格 v3》§3.2/§3.3 一致）：
- 组预算按「报价」预留：当日该组赢标数上限 = 预算 // 报价
- 赢标：报价严格大于历史成交价（平价按输）
- 扣费：一价组 = 报价；二价组 = 成交价；
        隐藏计价日（Day 3、6）= max(成交价, ceil(0.8 × 报价))
- 日上限：软，超出只记账（用于扣分），不阻断
- 总预算：硬，逐笔检查；某笔扣费使累计超过「当时生效」的总预算 → 立即判负，且该笔不入账
- 余额按实际扣费更新
"""
from dataclasses import dataclass, field
from data import GROUPS, FIRST_PRICE, DATES, U, iter_day


def ceil08(bid: int) -> int:
    """ceil(0.8 × bid)，整数运算，无浮点。"""
    return (4 * bid + 4) // 5


@dataclass
class GroupDay:
    group: str
    bid: int = 0
    budget: int = 0
    max_wins: int = 0
    considered: int = 0        # 参与过竞价（未被上限拦截）
    wins: int = 0
    spend: int = 0
    clicks: int = 0
    conversions: int = 0
    available: int = 0
    prices: list = field(default_factory=list)

    def pct(self, q: float) -> int:
        if not self.prices:
            return 0
        v = sorted(self.prices)
        return v[min(len(v) - 1, int(len(v) * q))]

    def as_report(self) -> dict:
        roi = None
        if self.spend > 0:
            roi = round((self.conversions * 12 * U - self.spend) / self.spend, 4)
        return {
            "your_bid": self.bid, "budget": self.budget,
            "impressions_available": self.available,
            "impressions_won": self.wins,
            "win_rate": round(self.wins / self.available, 4) if self.available else None,
            "spend_units": self.spend,
            "clicks": self.clicks, "conversions": self.conversions, "roi": roi,
            "price_p10": self.pct(0.10), "price_p50": self.pct(0.50),
            "price_p90": self.pct(0.90), "price_max": self.prices and max(self.prices) or 0,
        }


@dataclass
class Run:
    script: object
    conv: dict
    days: dict = field(default_factory=dict)     # day -> {group: GroupDay}
    daily_spend: dict = field(default_factory=dict)
    cum_spend: int = 0
    failed: bool = False
    fail_reason: str = ""
    fail_day: int = 0
    total_clicks: int = 0

    def effective_total(self, day: int) -> int:
        return self.script.total_units(day)

    # 方案 B：预算下调后若已超支，冻结后续投放，但不判负，跑完按实际结算
    frozen: bool = False
    frozen_from: int | None = None

    def play_day(self, day: int, plan: dict) -> dict:
        """plan: {group: {"bid": int, "budget": int}}；返回当日台账。

        方案 B：预算下调（剧本 A 的第 5 天）后，若累计花费已超过新预算，
        **不判负**，但本日起冻结投放——不再产生任何扣费，跑完按实际结算。
        """
        total_now = self.effective_total(day)
        if not self.frozen and self.cum_spend > total_now:
            self.frozen = True
            self.frozen_from = day

        gd = {}
        for g in GROUPS:
            spec = plan.get(g) or {}
            bid = int(spec.get("bid", 0))
            bud = int(spec.get("budget", 0))
            gd[g] = GroupDay(group=g, bid=bid, budget=bud,
                             max_wins=(bud // bid) if bid > 0 else 0)
        self.days[day] = gd

        if self.frozen:
            self.daily_spend[day] = 0
            return {"day": day, "groups": {g: c.as_report() for g, c in gd.items()},
                    "frozen": True,
                    "note": (f"预算已降至 ¥{total_now / U:.0f}，"
                             f"而此前已花费 ¥{self.cum_spend / U:.2f}；"
                             f"自 Day {day} 起冻结投放，不再产生扣费")}

        day_spend = 0
        for g, p, clicked, is_conv in iter_day(day, self.conv):
            cell = gd[g]
            cell.available += 1
            cell.prices.append(p)
            if cell.wins >= cell.max_wins:      # 达到预留上限 → 该组当日不再赢标
                continue
            if cell.bid <= p:                    # 平价按输
                continue
            cell.considered += 1
            if g in FIRST_PRICE:
                cost = cell.bid
            elif day in (3, 6):
                cost = max(p, ceil08(cell.bid))
            else:
                cost = p
            # 总预算：硬约束，逐笔检查，超额不入账
            # 必须把「当天已发生的」day_spend 一起算进来，否则同日后续竞价会忽略此前消耗
            if self.cum_spend + day_spend + cost > self.effective_total(day):
                self.failed = True
                self.fail_day = day
                self.fail_reason = (f"Day {day} {g}: 扣费 {cost} 会使累计 "
                                    f"{self.cum_spend + day_spend + cost} "
                                    f"超过总预算 {self.effective_total(day)}")
                break
            cell.wins += 1
            cell.spend += cost
            cell.clicks += int(clicked)
            cell.conversions += int(is_conv)
            day_spend += cost

        self.cum_spend += day_spend
        self.daily_spend[day] = day_spend
        self.total_clicks += sum(c.clicks for c in gd.values())
        return {"day": day, "groups": {g: c.as_report() for g, c in gd.items()}}

    # ---------- 汇总 ----------
    def summary(self) -> dict:
        return {
            "script": self.script.key,
            "failed": self.failed,
            "fail_reason": self.fail_reason,
            "frozen": self.frozen,
            "frozen_from": self.frozen_from,
            "clicks": self.total_clicks,
            "spend_units": self.cum_spend,
            "spend_yuan": round(self.cum_spend / U, 5),
            "per_day": {d: {"clicks": sum(c.clicks for c in g.values()),
                            "spend": self.daily_spend.get(d, 0),
                            "wins": sum(c.wins for c in g.values())}
                        for d, g in self.days.items()},
        }
