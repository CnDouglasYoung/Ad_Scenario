#!/bin/sh
# 一天的 DSH 调用（协议 v2）。
# 身份由适配器注入：TRIAL_ID / STEP_ID / DAY / REQUEST_ID / TOKEN
# 模型配置走单份 overlay；stdout 直通 exec，由宿主采集。
set -eu
INSTR=$(cat "$1")

# 令牌写文件：DSH 的工具子进程会过滤掉 TOKEN 这类环境变量名
if [ -n "${TOKEN:-}" ]; then
  umask 077; printf '%s' "$TOKEN" > /tmp/.day_token
fi

mkdir -p /logs/agent
{
  echo "day=${DAY:-} step=${STEP_ID:-} trial=${TRIAL_ID:-} attempt=${ATTEMPT_ID:-}"
  echo "uid=$(id -u) user=$(id -un)"
  for d in /tests /logs/verifier; do
    if ls "$d" >/dev/null 2>&1; then echo "READABLE $d"; else echo "DENIED   $d"; fi
  done
} >> /logs/agent/perm-probe.txt 2>&1

MODEL_NAME="${DSH_MODEL_NAME:-${MODEL_NAME:-}}"
MODEL_BASE_URL="${DSH_MODEL_BASE_URL:-${MODEL_BASE_URL:-}}"
MODEL_API_KEY="${DSH_MODEL_API_KEY:-${MODEL_API_KEY:-}}"
echo "生效模型 MODEL_NAME=${MODEL_NAME:-} MODEL_BASE_URL=${MODEL_BASE_URL:-}" >> /logs/agent/perm-probe.txt

PATCH=""
if [ -n "${MODEL_NAME:-}" ]; then
  cat > /tmp/dsh-overlay.yml <<YML
- id: llm-deepseek
  config:
    baseURL: ${MODEL_BASE_URL}
    apiKeyEnv: DSH_MODEL_API_KEY
    maxTokens: ${MODEL_MAX_TOKENS:-32768}
    models:
      - id: ${MODEL_NAME}
        name: ${MODEL_NAME}
        contextWindow: ${MODEL_CONTEXT_WINDOW:-200000}
        maxTokens: ${MODEL_MAX_TOKENS:-32768}

- id: agent-default-model
  config:
    provider: deepseek-official
    model: ${MODEL_NAME}
YML
  PATCH="--patch /tmp/dsh-overlay.yml"
  [ -n "${MODEL_API_KEY:-}" ] && export DSH_MODEL_API_KEY="$MODEL_API_KEY"
fi

if [ -n "${DSH_SESSION_ID:-}" ]; then
  echo "[run_day] 续接 ${DSH_SESSION_ID} step=${STEP_ID:-}" >&2
  exec dsh --profile headless $PATCH --json --session-id "$DSH_SESSION_ID" "$INSTR"
else
  echo "[run_day] 新建会话 step=${STEP_ID:-}" >&2
  exec dsh --profile headless $PATCH --json "$INSTR"
fi
