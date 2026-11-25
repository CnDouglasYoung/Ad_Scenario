#!/bin/sh
# 统一提交入口：补上 trial/step/request 身份字段，再 POST /plan。
# 用法：/app/submit.sh /tmp/plan.json   （plan.json 只含 {"groups": {...}}）
set -eu
PLAN_FILE="${1:?需要计划文件}"
: "${TRIAL_ID:?缺少 TRIAL_ID}"
: "${STEP_ID:?缺少 STEP_ID}"
: "${DAY:?缺少 DAY}"
: "${REQUEST_ID:?缺少 REQUEST_ID}"
TOKEN="${TOKEN:-$(cat /tmp/.day_token 2>/dev/null || true)}"
export TOKEN                      # 不导出的话下面的 python 读不到
: "${TOKEN:?缺少 TOKEN}"

python3 - "$PLAN_FILE" <<'PY' > /tmp/submit_body.json
import json, os, sys
raw = json.load(open(sys.argv[1], encoding="utf-8"))
groups = raw.get("groups") or raw
body = {"trial_id": os.environ["TRIAL_ID"], "step_id": os.environ["STEP_ID"],
        "day": int(os.environ["DAY"]), "request_id": os.environ["REQUEST_ID"],
        "token": os.environ["TOKEN"], "plan": {"groups": groups}}
print(json.dumps(body, ensure_ascii=False))
PY

curl -sS -w '\nHTTP_STATUS:%{http_code}\n' -X POST "$ENGINE_URL/plan" \
  -H 'Content-Type: application/json' --data-binary @/tmp/submit_body.json
