# TextMirror 后端 Dockerfile（多阶段：编译工具链不进运行镜像）

# ---- 构建层：编译安装 Python 依赖 ----
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1

# 注入可靠DNS（覆盖容器默认DNS，彻底解决构建时DNS解析超时）
COPY resolv.conf /etc/resolv.conf

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt \
        -i https://mirrors.aliyun.com/pypi/simple/ \
        --timeout 60

# ---- 运行层 ----
FROM python:3.11-slim

LABEL maintainer="TextMirror Team"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 运行期系统依赖：curl 供健康检查、antiword 供 .doc 文本提取
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl antiword && \
    rm -rf /var/lib/apt/lists/*

COPY resolv.conf /etc/resolv.conf
COPY --from=builder /install /usr/local

# 复制项目代码
COPY . .

# 入口脚本置于 /usr/local/bin，避免开发环境挂载 ./backend:/app 时被覆盖
RUN cp docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh && \
    chmod +x /usr/local/bin/docker-entrypoint.sh && \
    useradd --uid 1000 --create-home appuser && \
    chown -R appuser:appuser /app
# 最小权限运行；上传目录为宿主机挂载，部署前需一次性
#   chown -R 1000:1000 /opt/UploadFile/textMirrorUpload
USER appuser
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# 暴露端口
EXPOSE 3020

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:3020/api/v1/health || exit 1

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3020", "--workers", "2"]
