#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 iPinYou training1st 抽取 3 天真实数据，作为「参考市场概况」。

对局期是 2013-06-06~06-12（training2nd），参考数据来自 2013-03-11~03-13，
**周期较远**，只作参考。本 benchmark 不设「前 30 天」窗口。

    python3 tools/build_reference_prior.py /mnt/diskstation/download/ipinyou.contest.dataset.zip
"""
import bz2, io, json, os, sys, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prehistory_lib as L

ZIP = sys.argv[1] if len(sys.argv) > 1 else "/mnt/diskstation/download/ipinyou.contest.dataset.zip"
DAYS = ["20130311", "20130312", "20130313"]
PREFIX = "ipinyou.contest.dataset/training1st"
WANT = {("1", "336", "280"), ("1", "728", "90"), ("2", "728", "90"), ("3", "1000", "90")}
GROUP_OF = {("1", "336", "280"): "ex1_336x280", ("1", "728", "90"): "ex1_728x90",
            ("2", "728", "90"): "ex2_728x90", ("3", "1000", "90"): "ex3_1000x90"}


def pct(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def read_lines(zf, name):
    with zf.open(name) as fh:
        data = bz2.BZ2File(io.BytesIO(fh.read()))
        for raw in data:
            yield raw.decode("utf-8", "ignore").rstrip("\n")


def main():
    agg = {}
    with zipfile.ZipFile(ZIP) as zf:
        names = set(zf.namelist())
        for d in DAYS:
            clicks, convs = set(), set()
            clk_name = f"{PREFIX}/clk.{d}.txt.bz2"
            if clk_name in names:
                for line in read_lines(zf, clk_name):
                    clicks.add(line.split("\t", 1)[0])
            conv_name = f"{PREFIX}/conv.{d}.txt.bz2"
            if conv_name in names:
                for line in read_lines(zf, conv_name):
                    convs.add(line.split("\t", 1)[0])
            imp_name = f"{PREFIX}/imp.{d}.txt.bz2"
            if imp_name not in names:
                print(f"缺 {imp_name}", file=sys.stderr)
                continue
            date = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            for line in read_lines(zf, imp_name):
                f = line.split("\t")
                if len(f) < 22:
                    continue
                key = (f[8], f[13], f[14])
                if key not in WANT:
                    continue
                g = GROUP_OF[key]
                try:
                    pay = int(f[20])
                except Exception:
                    continue
                a = agg.setdefault((date, g), {"impressions": 0, "clicks": 0, "convs": 0,
                                               "spend": 0, "prices": []})
                a["impressions"] += 1
                a["spend"] += pay
                a["prices"].append(pay)
                bid_id = f[0]
                if bid_id in clicks:
                    a["clicks"] += 1
                if bid_id in convs:
                    a["convs"] += 1
            print(f"{date}: 累计 {len(agg)} 个 (日,组)", flush=True)

    rows = []
    for (date, g), a in sorted(agg.items()):
        ps = sorted(a["prices"])
        rows.append({
            "date": date, "group_id": g,
            "impressions": a["impressions"],
            "clicked_impressions": a["clicks"],
            "historical_conversions": a["convs"],
            "historical_spend_units": a["spend"],
            "historical_roi": None,
            "price_p50_units": pct(ps, .5),
            "price_p90_units": pct(ps, .9),
            "availability": "reference_prior",
        })

    doc = {
        "start_date": rows[0]["date"], "end_date": rows[-1]["date"],
        "observed_day_count": len({r["date"] for r in rows}),
        "provenance": {
            "synthetic": False,
            "source": "iPinYou training1st (advertiser 355673)",
            "period": "2013-03-11 ~ 2013-03-13",
            "distance": "距对局期 2013-06-06~06-12 约 3 个月",
            "usage": "仅作参考，不代表对局期行情；不得称为「前 30 天」",
            "caveat": "不同广告主、不同月份，价格水平可能有系统性差异",
        },
        "rows": rows,
    }
    out = os.path.join("pkg", "reference_prior.json")
    json.dump(doc, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"写出 {len(rows)} 行（{doc['observed_day_count']} 天 × 4 组）→ {out}")
    for r in rows[:4]:
        print(" ", r["date"], r["group_id"], "曝光", r["impressions"], "点击", r["clicked_impressions"],
              "中位价", r["price_p50_units"])


if __name__ == "__main__":
    main()
