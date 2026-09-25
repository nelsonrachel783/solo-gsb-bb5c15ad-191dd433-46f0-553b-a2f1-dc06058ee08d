# 采访逐字稿校时 API
# 构建阶段：编译 C 对齐内核（运行镜像无需保留 gcc 亦可，入口脚本会兜底重编译）
FROM python:3.11-slim AS builder

WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY app/native/align_c.c ./align_c.c
RUN gcc -O3 -fPIC -shared -o libalign.so align_c.c

# 运行阶段
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000

# 入口脚本“启动时安装依赖”，并在缺失内核时即时编译，因此保留 gcc
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY app ./app
COPY --from=builder /build/libalign.so ./app/native/libalign.so

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
port=os.environ.get('PORT','8000'); \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health'%port,timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
