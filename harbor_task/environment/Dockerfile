# main 容器：DSH + Python + curl，不含任何数据
FROM node:22-slim AS nodeenv
ARG DSH_VERSION=0.2.1-rc.2
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm config set registry "$NPM_REGISTRY" \
 && npm install -g "@deepseek-ai/dsh@${DSH_VERSION}" \
 && dsh --version

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
# curl 供 agent 使用；走阿里云镜像，避免 deb.debian.org 慢
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
      /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    apt-get update -qq && apt-get install -y -qq --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=nodeenv /usr/local/bin/node /usr/local/bin/node
COPY --from=nodeenv /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && ln -sf /usr/local/lib/node_modules/@deepseek-ai/dsh/lib/bin.js /usr/local/bin/dsh \
 && dsh --version

COPY app/run_day.sh /app/run_day.sh
COPY app/submit.sh /app/submit.sh
RUN chmod +x /app/submit.sh /app/run_day.sh

# 非 root 运行：agent 用 uid 1000；verifier 目录 root 独占
RUN useradd -m -u 1000 -s /bin/bash agent \
 && mkdir -p /logs/agent /logs/verifier /app \
 && chown -R agent:agent /app /logs/agent \
 && chmod 700 /logs/verifier \
 && chmod 711 /logs

WORKDIR /app
USER agent
ENV HOME=/tmp
