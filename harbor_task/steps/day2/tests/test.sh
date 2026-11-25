#!/bin/bash
# day2 验收（协议 v2）：以引擎回执为准，不再靠日期推断
set -u
source /tests/helpers.sh

umask 077
rm -f /logs/verifier/reward.txt
trap '[ -f /logs/verifier/reward.txt ] || echo 0 > /logs/verifier/reward.txt' EXIT

echo "[debug] step=day2 day=2"
if ! state=$(engine_get /state 2>/tmp/err.txt); then
  echo "[debug] engine_get 失败:"; cat /tmp/err.txt; fail
fi
printf '%s' "$state" > /logs/verifier/state.json
echo "[debug] state: ${state:0:160}"

done_through=$(printf '%s' "$state" | python3 -c "import sys,json;print(json.load(sys.stdin)['completed_through'])")
phase=$(printf '%s' "$state" | python3 -c "import sys,json;print(json.load(sys.stdin)['phase'])")
echo "[debug] completed_through=$done_through (期望 2)  phase=$phase (期望 FROZEN 或 COMMITTED)"
[ "$done_through" -eq 2 ] || fail
case "$phase" in FROZEN|COMMITTED) ;; *) fail ;; esac

if ! receipt=$(engine_get "/audit/receipt?day=2" 2>/dev/null); then
  echo "[debug] 没有提交回执"; fail
fi
printf '%s' "$receipt" > /logs/verifier/receipt.json
commit_id=$(printf '%s' "$receipt" | python3 -c "import sys,json;print(json.load(sys.stdin).get('commit_id') or '')")
echo "[debug] commit_id=${commit_id}"
[ -n "$commit_id" ] || fail

cp /logs/agent/perm-probe.txt /logs/verifier/perm-probe.txt 2>/dev/null || true
engine_get "/report?day=2" > /logs/verifier/report.json 2>/dev/null || true

pass
