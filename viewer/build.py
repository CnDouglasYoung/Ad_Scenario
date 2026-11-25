#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 /work/runs/*.json 渲染成一个静态看板。"""
import glob, json, os, sys, html

RUNS = sys.argv[1] if len(sys.argv) > 1 else "/work/runs"
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(RUNS, "index.html")


def rows(run):
    for t in run["trajectory"]:
        d = t["day"]; rep = t["report"]; tot = rep.get("daily_total", {})
        yield (d, rep, tot)


def main():
    files = sorted(glob.glob(os.path.join(RUNS, "*.json")))
    if not files:
        sys.exit(f"{RUNS} 里没有 run 记录")
    cards = []
    for f in files:
        run = json.load(open(f, encoding="utf-8"))
        s = run["score"]
        tr = "".join(
            f"<tr><td>{d}</td><td>{rep.get('groups',{}).get('ex3_1000x90',{}).get('clicks','')}</td>"
            f"<td>{tot.get('spend',0)/100000:.2f}</td>"
            f"<td>{tot.get('clicks','')}</td>"
            f"<td>{'⚠️' if tot.get('over_cap') else ''}</td></tr>"
            for d, rep, tot in rows(run))
        cards.append(f"""
        <section>
          <h2>{html.escape(run['run_id'])}</h2>
          <p class="kpi">主分 <b>{s['main_score']}</b> · 点击 {s['clicks']} · 曝光(D4-7) {s['impressions_4_7']:,}
             · 罚分 {s['penalty']} · 加分 {s['bonus']}</p>
          <table><thead><tr><th>日</th><th>ex3 点击</th><th>消耗(¥)</th><th>当日点击</th><th>超日限</th></tr></thead>
          <tbody>{tr}</tbody></table>
        </section>""")
    doc = f"""<!doctype html><meta charset="utf-8"><title>投放场景 · 运行看板</title>
<style>body{{font:14px/1.6 system-ui,sans-serif;max-width:860px;margin:40px auto;padding:0 16px;color:#222}}
h1{{font-size:20px}} h2{{font-size:15px;margin-top:28px;color:#0b5}} .kpi{{color:#555}}
table{{border-collapse:collapse;width:100%;margin-top:8px;font-size:13px}}
th,td{{border-bottom:1px solid #eee;padding:6px 8px;text-align:right}} th:first-child,td:first-child{{text-align:left}}
th{{color:#666;font-weight:600}}</style>
<h1>投放场景 · 运行看板</h1><p class="kpi">{len(files)} 次运行</p>{''.join(cards)}"""
    open(OUT, "w", encoding="utf-8").write(doc)
    print("写好了", OUT)


if __name__ == "__main__":
    main()
