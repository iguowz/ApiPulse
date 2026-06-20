FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖（curl 用于 docker healthcheck）
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY backend/requirements.txt .
# 使用清华镜像源加速国内下载，避免 SSL/网络问题
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 复制源码（包目录 + main.py + test 文件）
COPY config/ ./config/
COPY models/ ./models/
COPY services/ ./services/
COPY api/ ./api/
COPY har_parser/ ./har_parser/
COPY ai_analyzer/ ./ai_analyzer/
COPY dag_engine/ ./dag_engine/
COPY data_factory/ ./data_factory/
COPY monitor/ ./monitor/
COPY knowledge/ ./knowledge/
COPY main.py .

# 日志目录
RUN mkdir -p /app/logs

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
