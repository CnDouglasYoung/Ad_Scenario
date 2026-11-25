# 投放场景回放引擎 —— 最小镜像（纯标准库，无第三方依赖）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AD_PKG=/data

WORKDIR /app
COPY engine/ /app/engine/
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# 数据不进镜像，运行时只读挂载到 /data（约 380MB，映像保持轻量）
VOLUME ["/data", "/work"]
WORKDIR /app/engine

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["--script", "0"]
