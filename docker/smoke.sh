#!/bin/sh
# 在容器内跑三个剧本 + 核对三个对账点。用法：sh docker/smoke.sh
set -eu
fail=0
for s in 0 A B; do
  echo "=== 剧本 $s ==="
  python3 run.py --script "$s" | tail -4 || fail=1
done
echo
echo "=== 数据完整性 ==="
python3 - <<'PY'
import hashlib, json, os
m = json.load(open(os.path.join(os.environ.get("AD_PKG","/data"), "manifest.json")))
bad = 0
for d in m["days"]:
    p = os.path.join(os.environ.get("AD_PKG","/data"), d["member"])
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    ok = h == d["sha256_uncompressed"]
    bad += 0 if ok else 1
    print(f"day_{d['day']:02d} {'OK' if ok else 'MISMATCH'}")
print("全部一致" if not bad else f"{bad} 天不一致")
raise SystemExit(1 if bad else 0)
PY
exit $fail
