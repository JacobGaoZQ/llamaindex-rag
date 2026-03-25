# 智能家居问答系统

基于 LlamaIndex 的多模态 RAG 系统，支持 PDF 文档解析与图文智能问答。

## 环境要求

- Python 3.9+
- [DashScope API Key](https://dashscope.aliyun.com/)（通义千问，用于 LLM 和 Embedding）

## 快速开始

### 1. 创建虚拟环境

```bash
python3 -m venv .venv
```

### 2. 安装依赖

```bash
.venv/bin/pip install -r requirements.txt
```

### 3. 配置 API Key

编辑 `run_ui.sh`，将 `QWEN_API_KEY` 替换为你的实际 DashScope API Key：

```bash
export QWEN_API_KEY="your-api-key-here"
```

或在启动前通过环境变量设置：

```bash
export QWEN_API_KEY="your-api-key-here"
```

### 4. 启动应用

```bash
./run_ui.sh
```

应用默认运行在 `http://localhost:8501`，若端口被占用会自动递增。

## 使用说明

1. 将 PDF 文档放入 `./data` 目录
2. 在侧边栏输入 DashScope API Key
3. 点击 **重建索引** 解析文档并构建向量索引（首次使用）
4. 或点击 **加载索引** 加载已有索引
5. 在对话框输入问题，系统将返回文字回答和相关图片

## 项目结构

```
llamaindex-rag/
├── app.py                  # Streamlit Web UI 主入口
├── run_ui.sh               # 启动脚本
├── requirements.txt        # 依赖列表
├── data/                   # 放置 PDF 文档的目录
├── src/
│   ├── pdf_parser.py       # PDF 解析与图片提取
│   └── multimodal_query.py # 多模态 RAG 查询逻辑
├── extracted_images/       # 解析后提取的图片（自动生成）
├── parsed_metadata/        # 解析元数据缓存（自动生成）
└── multimodal_index/       # 向量索引持久化目录（自动生成）
```

## 技术栈

- [Streamlit](https://streamlit.io/) — Web UI 框架
- [LlamaIndex](https://www.llamaindex.ai/) — RAG 核心框架
- [DashScope](https://dashscope.aliyun.com/) — 通义千问 LLM (`qwen-flash`) 和 Embedding (`text-embedding-v4`)
- [LlamaParse](https://cloud.llamaindex.ai/) — 文档解析服务
- PyMuPDF — PDF 处理与图片提取
