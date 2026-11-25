#!/bin/sh
set -eu
# 任何子命令都先做一次数据完整性检查（三天文件存在即可，完整校验见 smoke.sh）
if [ ! -f "$AD_PKG/manifest.json" ]; then
  echo "缺少数据包：$AD_PKG/manifest.json" >&2
  exit 2
fi
exec python3 run.py "$@"
