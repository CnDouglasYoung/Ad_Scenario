#!/bin/bash
# day7 验收（协议 v2）：以引擎回执为准，不再靠日期推断
set -u
source /tests/helpers.sh

umask 077
rm -f /logs/verifier/reward.txt
trap '[ -f /logs/verifier/reward.txt ] || echo 0 > /logs/verifier/reward.txt' EXIT

echo "[debug] step=day7 day=7"
if ! state=$(engine_get /state 2>/tmp/err.txt); then
  echo "[debug] engine_get 失败:"; cat /tmp/err.txt; fail
fi
printf '%s' "$state" > /logs/verifier/state.json
echo "[debug] state: ${state:0:160}"

done_through=$(printf '%s' "$state" | python3 -c "import sys,json;print(json.load(sys.stdin)['completed_through'])")
phase=$(printf '%s' "$state" | python3 -c "import sys,json;print(json.load(sys.stdin)['phase'])")
echo "[debug] completed_through=$done_through (期望 7)  phase=$phase (期望 FROZEN 或 COMMITTED)"
[ "$done_through" -eq 7 ] || fail
case "$phase" in FROZEN|COMMITTED) ;; *) fail ;; esac

if ! receipt=$(engine_get "/audit/receipt?day=7" 2>/dev/null); then
  echo "[debug] 没有提交回执"; fail
fi
printf '%s' "$receipt" > /logs/verifier/receipt.json
commit_id=$(printf '%s' "$receipt" | python3 -c "import sys,json;print(json.load(sys.stdin).get('commit_id') or '')")
echo "[debug] commit_id=${commit_id}"
[ -n "$commit_id" ] || fail

cp /logs/agent/perm-probe.txt /logs/verifier/perm-probe.txt 2>/dev/null || true
engine_get "/report?day=7" > /logs/verifier/report.json 2>/dev/null || true

if ! engine_get /score > /logs/verifier/score.json 2>/dev/null; then
  echo "[debug] score 获取失败"; fail
fi
rw=$(python3 -c "
import json
d = json.load(open('/logs/verifier/score.json'))
if d.get('evidence_failed'): raise SystemExit('evidence_failed')
sc = d['score']; BASE = {'0': 613.0, 'A': 503.0, 'B': 613.0}
script = (d.get('summary') or {}).get('script') or ''
if script not in BASE: raise SystemExit('unknown condition')
print('%.6f' % (sc['main_score'] / BASE[script]))
")
echo "[debug] 归一化主分=$rw"
write_reward "$rw"
exit 0

pass
