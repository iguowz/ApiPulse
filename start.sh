#!/usr/bin/env bash
# ApiPulse 一键启动脚本
# 用法: ./start.sh [--docker|--local]   默认 --local

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── 命令行参数 ──────────────────────────────────────────
MODE="${1:---local}"
case "$MODE" in
  --docker|-d) MODE=docker ;;
  --local|-l)  MODE=local ;;
  --help|-h)
    echo "ApiPulse 启动脚本"
    echo "  ./start.sh --docker   Docker Compose 启动 (前端:3000 后端:8000)"
    echo "  ./start.sh --local    本地开发启动 (需手动 mongo/redis/minio)"
    exit 0 ;;
esac

# ── Docker 模式 ─────────────────────────────────────────
if [ "$MODE" = "docker" ]; then
  echo "=== ApiPulse Docker 启动 ==="
  cd "$ROOT"
  docker compose up -d
  echo "前端: http://localhost:3000"
  echo "后端: http://localhost:8000"
  echo "MinIO 控制台: http://localhost:9001"
  exit 0
fi

# ── 本地开发模式 ────────────────────────────────────────
echo "=== ApiPulse 本地开发模式 ==="

# 1. 检查 Python 依赖
if ! python -c "import motor" 2>/dev/null; then
  echo "→ 安装 Python 依赖..."
  pip install -r "$ROOT/backend/requirements.txt"
fi

# 2. 检查前端依赖
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "→ 安装前端依赖..."
  cd "$ROOT/frontend" && npm install
fi

# 3. 检查基础设施 (MongoDB / Redis / MinIO)
echo "→ 检查基础设施服务..."
for svc in mongod redis-server minio; do
  if ! pgrep -q "$svc"; then
    echo "⚠ 警告: $svc 未运行，请确保 MongoDB / Redis / MinIO 已启动"
    echo "  可使用 Docker 快速启动基础设施:"
    echo "  docker compose -f $ROOT/docker-compose.yml up -d mongo redis minio"
  fi
done

# 4. 启动后端
echo "→ 启动后端 (端口 8000)..."
cd "$ROOT" && python main.py &
BACKEND_PID=$!

# 5. 启动前端 (Vite dev server)
echo "→ 启动前端 (端口 5173)..."
cd "$ROOT/frontend" && npx vite --host &
FRONTEND_PID=$!

# 6. 清理
trap "echo '停止服务...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT EXIT

echo "前端: http://localhost:5173"
echo "后端: http://localhost:8000"
echo "按 Ctrl+C 停止"

wait
