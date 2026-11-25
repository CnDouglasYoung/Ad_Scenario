# 容器化运行

## 构建与运行

```bash
cd .scratch/ad-scenario
docker compose -f docker/compose.yaml build
docker compose -f docker/compose.yaml run --rm engine --script 0   # 或 A / B
```

## 国内网络：基础镜像拉不下来时

Docker Hub 常常连不上，先从镜像站拉再打回原名：

```bash
docker pull docker.m.daocloud.io/library/python:3.12-slim
docker tag docker.m.daocloud.io/library/python:3.12-slim python:3.12-slim
```

## 数据完整性

```bash
docker run --rm --entrypoint sh \
  -v "$PWD/pkg:/data:ro" -v "$PWD/docker:/docker:ro" \
  -e AD_PKG=/data -w /app/engine ad-scenario-engine:latest /docker/smoke.sh
```

逐天校验 `manifest.json` 里的 `sha256_uncompressed`，七天全 OK 才继续。

## 设计

- 数据**不进镜像**，运行时只读挂载到 `/data`（镜像 177MB，数据 383MB 分离）
- `network_mode: none`：引擎全程离线，杜绝实现层偷偷补数据
- 入口先检查 `manifest.json` 是否存在

## 模型价格表

`Dockerfile.harbor` 会把 `model_prices.json` 烤进镜像（关闭运行期联网拉取）。
该文件体积大、不入库，构建前请先运行：

```bash
bash work/fetch_prices.sh
```
