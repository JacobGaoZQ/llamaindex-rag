#!/bin/bash
# 启动多模态 RAG 系统 Web UI

# 设置 API Key（请替换为你的实际 API Key）
export QWEN_API_KEY="${QWEN_API_KEY:-xxxxxx}"

# 禁用 Streamlit 使用统计
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# 查找可用端口
PORT=8501
while lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; do
    PORT=$((PORT + 1))
done

echo "使用端口: $PORT"

# 启动 Streamlit
.venv/bin/streamlit run app.py --server.port $PORT --server.address 0.0.0.0
