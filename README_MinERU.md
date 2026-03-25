# MinerU RAG 系统

基于 MinerU 的高效 PDF 到 RAG 系统，支持完整的图文检索功能。

## 主要特性

### ✅ 稳定的 MinerU 集成
- 使用 MinerU CLI 官方接口，避免内部 API 变更风险
- 自动提取 PDF 目录结构、文本内容和图片
- 支持位图和矢量图形提取

### ✅ 完整的图文信息
- **页码信息**: 每张图片都包含准确的页码位置
- **空间位置**: 图片的精确边界框坐标 (bbox)
- **尺寸信息**: 图片宽度和高度
- **内容描述**: 使用通义千问-VL 自动生成图片描述和关键词

### ✅ 智能章节组织
- 自动识别 PDF 目录结构
- 按章节组织内容，便于检索
- 图文精准关联到对应章节

### ✅ 多文档统一索引
- 支持同时处理多个 PDF 文档
- 统一的向量索引，跨文档检索
- 每个节点包含文档来源信息

### ✅ 高效检索系统
- 基于 LlamaIndex 的向量检索
- 支持文本和图片描述混合检索
- 相似度阈值过滤
- 置信度评分

## 系统架构

```
PDF 文件
    ↓ (MinerU 转换)
Markdown + 图片 + content_list.json
    ↓ (解析和结构化)
章节化文档 (带图文关联)
    ↓ (生成图片描述)
增强文档 (文本 + 图片描述)
    ↓ (构建向量索引)
RAG 索引
    ↓ (查询检索)
智能问答结果
```

## 安装依赖

```bash
pip install -r requirements.txt
```

确保已安装 MinerU：
```bash
pip install mineru[core]
```

## 使用方法

### 1. 环境变量设置

```bash
export QWEN_API_KEY="your_dashscope_api_key"
```

### 2. 命令行使用

```bash
# 构建知识库
python main_unified.py --build

# 指定特定 PDF
python main_unified.py --build --pdf ./data/document.pdf

# 交互式查询
python main_unified.py

# 单次查询
python main_unified.py --query "文档的主要内容是什么？"
```

### 3. Python API 使用

```python
from src.mineru_converter import convert_pdf_with_mineru
from src.unified_pipeline import UnifiedRAGSystem

# 1. 转换单个 PDF
processed_doc = convert_pdf_with_mineru(
    pdf_path="./data/document.pdf",
    output_dir="./output",
    image_dir="./output/images",
    generate_descriptions=True,
    qwen_api_key="your_api_key"
)

# 2. 构建 RAG 系统
rag_system = UnifiedRAGSystem(
    qwen_api_key="your_api_key",
    persist_dir="./index",
    image_output_dir="./images"
)

# 3. 处理多个文档
docs = []
for pdf_file in ["doc1.pdf", "doc2.pdf"]:
    doc = rag_system.process_pdf(pdf_file, generate_descriptions=True)
    docs.append(doc)

# 4. 构建统一索引
rag_system.build_index(docs)

# 5. 查询
result = rag_system.query("请解释文档中的关键技术")
print(result["text"])
print(f"置信度: {result['confidence']:.2%}")
```

### 4. Web UI 使用

```bash
streamlit run app.py
```

## 输出文件结构

```
output/
├── document.md              # 转换后的 Markdown 文件
├── images/                  # 提取的图片
│   ├── document_img_000.png
│   ├── document_img_001.png
│   └── ...
└── index/                   # 向量索引
    ├── docstore.json
    ├── index_store.json
    ├── vector_store.json
    └── document_metadata.json
```

## 核心组件

### 1. `StableMinerUConverter`
- 使用 MinerU CLI 进行 PDF 转换
- 从 content_list.json 补充图片页码和位置信息
- 重写图片路径为统一格式

### 2. `MarkdownParser`
- 解析 Markdown 内容为章节结构
- 根据标题层级组织内容

### 3. `UnifiedRAGSystem`
- 处理多个文档
- 构建统一的向量索引
- 支持图文混合检索

### 4. `ImageDescriptor`
- 使用通义千问-VL 生成图片描述
- 提取关键词和分类信息
- 支持批量处理和缓存

## 性能优化

1. **批量图片描述**: 使用线程池并发生成图片描述
2. **结果缓存**: 图片描述自动缓存，避免重复处理
3. **增量处理**: 支持单独处理新增文档
4. **内存优化**: 流式处理大文档

## 故障排除

### MinerU 相关问题

1. **CLI 命令未找到**:
   ```bash
   pip install mineru[core]
   ```

2. **转换超时**:
   - 增加超时时间（在代码中修改 subprocess timeout）
   - 检查 PDF 文件是否损坏

3. **图片提取不完整**:
   - 确保安装了 Pillow
   - 检查图片格式支持

### API 相关问题

1. **API Key 错误**:
   ```bash
   export QWEN_API_KEY="正确的API密钥"
   ```

2. **请求频率限制**:
   - 降低 `max_workers` 参数
   - 增加请求间隔

## 测试

运行完整测试套件：
```bash
python test_mineru_rag.py
```

## 许可证

MIT License