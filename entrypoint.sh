#!/bin/sh
# 容器启动脚本：启动时安装 Python 依赖，并确保 C 对齐内核已编译。
set -e

APP_DIR="${APP_DIR:-/app}"
NATIVE_DIR="${APP_DIR}/app/native"
SRC="${NATIVE_DIR}/align_c.c"
LIB="${NATIVE_DIR}/libalign.so"

echo "[entrypoint] 安装/校验 Python 依赖 ..."
python -m pip install --no-cache-dir -r "${APP_DIR}/requirements.txt"

if [ ! -f "${LIB}" ]; then
    echo "[entrypoint] 未发现预编译对齐内核，使用 gcc 编译 ..."
    gcc -O3 -fPIC -shared -o "${LIB}" "${SRC}"
else
    echo "[entrypoint] 使用预编译对齐内核 ${LIB}"
fi

echo "[entrypoint] 启动 uvicorn（${HOST}:${PORT}）..."
exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" \
    ${UVICORN_EXTRA_ARGS:-}
