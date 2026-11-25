# -*- coding: utf-8 -*-
"""数据层：读取共享包，流式产出每日事件。

金额全程整数单位：1 元 = 100,000 单位（见 manifest.amounts）。
"""
import json, os

U = 100_000                      # 1 元 = 100,000 单位
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.environ.get("AD_PKG") or os.path.join(ROOT, "pkg")   # 容器内挂载到 /data

GROUPS = ["ex1_336x280", "ex1_728x90", "ex2_728x90", "ex3_1000x90"]
FIRST_PRICE = {"ex1_336x280", "ex1_728x90"}
DATES = {1: "2013-06-06", 2: "2013-06-07", 3: "2013-06-08", 4: "2013-06-09",
         5: "2013-06-10", 6: "2013-06-11", 7: "2013-06-12"}


def load_manifest():
    with open(os.path.join(PKG, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def load_conversions():
    """模拟转化：{event_id: day}"""
    with open(os.path.join(PKG, "simulation", "roi_v1.json"), encoding="utf-8") as f:
        roi = json.load(f)
    out = {}
    for day, ids in roi["conversion_event_ids_by_day"].items():
        for e in ids:
            out[e] = int(day)
    return out, roi


def iter_day(day, conv):
    """按文件行序（时间升序）产出 (group, price_units, clicked, is_conv)。

    price 为 None 时跳过（manifest 允许缺值）。
    """
    path = os.path.join(PKG, "history", f"day_{day:02d}.jsonl")
    with open(path, encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            p = o["paying_price_cpm_units"]
            if p is None:
                continue
            yield o["group_id"], int(p), bool(o["clicked"]), o["event_id"] in conv
