#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 端：只通过 HTTP 与引擎交互，本地没有任何数据文件。

    python3 agent_loop.py --policy rule
    python3 agent_loop.py --policy llm --model deepseek-chat

策略可替换：rule（规则基线）/ llm（OpenAI 兼容接口）。
"""
import argparse, json, os, sys, time, datetime, urllib.request, urllib.error

ENGINE = os.environ.get("ENGINE_URL", "http://engine:8080")


def api(path, payload=None, timeout=300):
    url = ENGINE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_engine(tries=60):
    for i in range(tries):
        try:
            api("/health", timeout=3); return
        except Exception:
            time.sleep(1)
    sys.exit("引擎未就绪")


# ---------------- 策略 ----------------
def rule_policy(state, report, day):
    """规则基线：固定报价，额度按剩余预算均分。"""
    bids = {"ex1_336x280": 90, "ex1_728x90": 90, "ex2_728x90": 100, "ex3_1000x90": 100}
    cap = int(state["daily_cap_yuan"] * 100_000)
    quota = min(cap, state["remaining_units"] // (8 - day))
    base, rem = divmod(quota, 4)
    groups = {}
    for i, g in enumerate(state["groups"]):
        groups[g] = {"bid": bids[g], "budget": base + (1 if i < rem else 0)}
    return groups


PROMPT = """你是广告投放操盘手。今天是第 {day} 天（共 7 天），总预算 {total} 元，日上限 {cap} 元。

规则：
- 4 个广告位组：{groups}
- 每组每天给一个「出价 bid」和「额度 budget」（整数，1 元 = 100000 单位）
- 出价严格高于市场成交价才赢；一价组按出价付费，二价组按成交价付费
- 额度按报价预留：该组最多赢 budget/bid 次曝光
- 当日花费超过 {cap} 元会扣分；累计超过总预算直接判负

昨日报表（如果没有就是冷启动，只能盲投）：
{report}

只输出 JSON，不要解释：
{{"groups": {{"ex1_336x280": {{"bid": 90, "budget": 3000000}}, ... }}}}
"""


def llm_policy(state, report, day, model, base_url, api_key, temperature=0.3):
    prompt = PROMPT.format(day=day, total=state["total_budget_yuan"], cap=state["daily_cap_yuan"],
                           groups=", ".join(state["groups"]),
                           report=json.dumps(report.get("groups", {}), ensure_ascii=False)[:3000])
    body = {"model": model, "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.loads(r.read())
    text = out["choices"][0]["message"]["content"]
    s, e = text.find("{"), text.rfind("}")
    plan = json.loads(text[s:e + 1])
    return {g: {"bid": int(plan["groups"][g]["bid"]),
                "budget": int(plan["groups"][g]["budget"])} for g in state["groups"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", choices=["rule", "llm"], default="rule")
    ap.add_argument("--model", default=os.environ.get("MODEL_NAME", "deepseek-chat"))
    ap.add_argument("--base-url", default=os.environ.get("MODEL_BASE_URL", "https://api.deepseek.com/v1"))
    ap.add_argument("--api-key", default=os.environ.get("MODEL_API_KEY", ""))
    args = ap.parse_args()

    wait_engine()
    api("/reset", {})          # 每次开跑前重置引擎里的 run
    state = api("/state")
    print(f"[agent] script={state['script']} 预算 {state['total_budget_yuan']} 元 策略={args.policy}")
    print(f"{'日':>3}{'预算(¥)':>10}{'点击':>7}{'消耗(¥)':>10}")

    trajectory = []
    for day in range(1, 8):
        state = api("/state")
        report = api(f"/report?day={day}")
        if args.policy == "rule":
            groups = rule_policy(state, report, day)
        else:
            if not args.api_key:
                sys.exit("缺少 MODEL_API_KEY")
            groups = llm_policy(state, report, day, args.model, args.base_url, args.api_key)
        res = api("/plan", {"day": day, "groups": groups})
        rep = res["report"]
        print(f"{day:>3}{groups['ex3_1000x90']['budget']/100000:>10.1f}"
              f"{rep['daily_total']['clicks']:>7}{rep['daily_total']['spend']/100000:>10.2f}")
        trajectory.append({"day": day, "state": state, "plan": groups,
                           "report": rep, "failed": res.get("failed", False)})
        if res.get("failed"):
            print(f"\n❌ 判负：{res['reason']}")
            break

    final = api("/score")
    s = final["score"]
    print(f"\n七天点击 {s['clicks']} | 曝光(D4-7) {s['impressions_4_7']:,} | "
          f"罚分 {s['penalty']} | 加分 {s['bonus']} | 主分 {s['main_score']}")
    # 规则基线在三个剧本下的已知结果（本地引擎复算得出）
    EXPECT = {"0": (613, 561_234), "A": (503, 397_408), "B": (613, 561_234)}
    exp = EXPECT.get(state["script"])
    ok = exp is not None and (s["clicks"], s["impressions_4_7"]) == exp
    print(f"对账（规则基线）：{'✅ 与本地一致' if ok else '❌ 不一致'}"
          + ("" if exp is None else f"（期望 {exp[0]} 点击 / {exp[1]:,} 曝光）"))

    # 落盘：一份可复算的运行记录
    os.makedirs("/work/runs", exist_ok=True)
    rid = f"{args.policy}_{state['script']}_{datetime.datetime.now():%Y%m%d-%H%M%S}"
    out = {"run_id": rid, "policy": args.policy, "script": state["script"],
           "model": args.model if args.policy == "llm" else None,
           "trajectory": trajectory, "score": s, "summary": final["summary"]}
    with open(f"/work/runs/{rid}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"运行记录 → /work/runs/{rid}.json")


if __name__ == "__main__":
    main()
