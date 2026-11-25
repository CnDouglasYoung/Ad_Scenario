#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 DSH 跑完整七天：一条连续会话，每个剧本重开。

    python3 dsh_runner.py --days 7

与 Harbor 适配器共用同一套调用约定：
- Day 1 不带 --session-id，从事件流里抓 sessionId 落盘
- Day 2..7 带 --session-id 续接（同一剧本内保持记忆）
- 每步事件流 append 到 /logs/agent/dsh.jsonl
"""
import argparse, json, os, re, subprocess, sys, urllib.request

ENGINE = os.environ.get("ENGINE_URL", "http://engine:8080")
SIDFILE = "/tmp/.dsh_session"
LOGFILE = "/logs/agent/dsh.jsonl"

INSTR = """你是广告投放操盘手，今天是第 {day} 天（共 7 天）。

先取今天的报表：
  curl -s "$ENGINE_URL/report?day={day}"

然后决定四个广告位组今天的出价 bid 和额度 budget（整数，1 元 = 100000 单位），
用 POST 提交（必须返回 200，否则今天算失败）：
  curl -s -X POST "$ENGINE_URL/plan" -H 'Content-Type: application/json' -d '<你的 JSON>'

JSON 形状：
{{"day": {day}, "groups": {{"ex1_336x280": {{"bid": 90, "budget": 3000000}},
  "ex1_728x90": {{"bid": 90, "budget": 3000000}},
  "ex2_728x90": {{"bid": 100, "budget": 3000000}},
  "ex3_1000x90": {{"bid": 100, "budget": 3000000}}}}}}

规则：每次曝光一个广告位，出价最高者得；一价组按最高价结算，二价组按次高价结算；日超 ¥150 扣分；
累计超总预算判负。提交成功后只回复 SUBMITTED，不要重复提交。
"""


def run_dsh(text, sid=None):
    cmd = ["dsh", "--profile", "headless", "--json"]
    if sid:
        cmd += ["--session-id", sid]
    cmd.append(text)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    out = p.stdout or ""
    os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(out)
    return out, p.stderr or ""


def session_id(events):
    for line in events.splitlines()[:3]:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") == "session":
            return e.get("sessionId")
    return None


def final_text(events):
    txt = ""
    for line in events.splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") == "final":
            txt = e.get("text", "")
    return txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    # 每次开跑前重置引擎
    try:
        req = urllib.request.Request(ENGINE + "/reset", data=b"{}",
                                     headers={"Content-Type": "application/json",
                                              "X-Admin-Token": os.environ.get("ADMIN_TOKEN", "dev")},
                                     method="POST")
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        print("[dsh] reset 失败:", e)

    sid = None
    if os.path.exists(SIDFILE):
        sid = open(SIDFILE).read().strip() or None
        print(f"[dsh] 续接会话 {sid}")

    for day in range(1, args.days + 1):
        out, err = run_dsh(INSTR.format(day=day), sid)
        if sid is None:
            sid = session_id(out)
            if sid:
                open(SIDFILE, "w").write(sid)
                print(f"[dsh] 新会话 {sid}")
        try:
            state = json.loads(urllib.request.urlopen(ENGINE + "/state", timeout=30).read())
        except Exception as e:
            print("  引擎不可达:", e); break
        print(f"[day {day}] 引擎 day={state['day']} 已花 ¥{state['spent_units']/100000:.2f} "
              f"| 回复 {final_text(out)[:40]!r}")
        if state["day"] != day + 1:
            print(f"  ⚠️ 引擎天数异常：期望 {day+1}，实际 {state['day']}（可能有重复提交）")
        if state.get("day") == 1 and day == 7:
            break
        if err.strip():
            print("  stderr:", err.strip().splitlines()[-1][:120])


if __name__ == "__main__":
    main()
