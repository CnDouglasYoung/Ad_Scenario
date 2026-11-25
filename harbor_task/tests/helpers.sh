write_reward() { echo "${1:-0}" > /logs/verifier/reward.txt; }
pass() { write_reward 1; exit 0; }
fail() { write_reward 0; exit 0; }

# 用 python3 而不是 curl：镜像里一定有 python3，少一个依赖
engine_get() {
  python3 - "$1" "${ENGINE_URL:-http://engine:8080}" <<'PY'
import sys, urllib.request
path, base = sys.argv[1], sys.argv[2]
try:
    print(urllib.request.urlopen(base + path, timeout=20).read().decode())
except Exception as e:
    sys.exit(f"engine_get failed: {e}")
PY
}
