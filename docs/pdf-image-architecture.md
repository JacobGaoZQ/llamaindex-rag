# PDF 图片识别、提取、缓存架构文档

## 1. 架构概览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PDF 图片处理架构                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │  PDF 输入    │───▶│  MinerU      │───▶│  Markdown    │                   │
│  └──────────────┘    │  解析器      │    └──────────────┘                   │
│         │            └──────────────┘           │                           │
│         │                   │                   │                           │
│         ▼                   ▼                   ▼                           │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │ 图片提取     │    │ 图片去重     │    │ 图片描述     │                   │
│  │ - 位图      │    │ - MD5 哈希   │    │ - Qwen-VL   │                   │
│  │ - 矢量图    │    │ - IoU 重叠   │    │ - OCR 文本   │                   │
│  └──────────────┘    └──────────────┘    └──────────────┘                   │
│         │                   │                   │                           │
│         ▼                   ▼                   ▼                           │
│  ┌──────────────────────────────────────────────────────┐                  │
│  │                    缓存层                             │                  │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐      │                  │
│  │  │ 转换缓存   │  │ 描述缓存   │  │ 向量索引   │      │                  │
│  │  │ SHA256     │  │ MD5        │  │ 持久化     │      │                  │
│  │  └────────────┘  └────────────┘  └────────────┘      │                  │
│  └──────────────────────────────────────────────────────┘                  │
│                              │                                              │
│                              ▼                                              │
│  ┌──────────────────────────────────────────────────────┐                  │
│  │                  多模态 RAG 系统                      │                  │
│  │         (文本 + 图片 联合检索与问答)                   │                  │
│  └──────────────────────────────────────────────────────┘                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2. 核心模块

### 2.1 PDF 解析层 (MinerU)

**文件**: `src/mineru_converter.py`

MinerU 是当前的 PDF 解析方案，通过调用 `magic-pdf` CLI 工具实现高质量的 PDF 到 Markdown 转换。

```python
class StableMinerUConverter:
    """
    基于 MinerU (magic-pdf) 的稳定 PDF 转换器
    支持缓存机制，避免重复处理相同 PDF
    """

    def convert(self, pdf_path: str) -> Tuple[str, List[TOCItem], List[ImageInfo], Dict]:
        """
        转换 PDF 为 Markdown

        Returns:
            - markdown_content: Markdown 文本内容
            - toc: 目录项列表
            - images: 图片信息列表
            - doc_info: 文档元数据
        """
```

**MinerU 输出结构**:
```
output/
└── {pdf_name}/
    ├── {pdf_name}.md          # Markdown 内容
    ├── {pdf_name}_content_list.json  # 内容结构列表
    └── images/                # 提取的图片
        ├── page_1_img_1.jpeg
        ├── page_2_img_1.jpeg
        └── ...
```

**content_list.json 结构**:
```json
[
  {
    "type": "text",
    "text": "段落文本内容",
    "page_number": 1
  },
  {
    "type": "image",
    "img_path": "images/page_1_img_1.jpeg",
    "page_number": 1,
    "bbox": [x0, y0, x1, y1]
  },
  {
    "type": "table",
    "text": "表格内容",
    "page_number": 2
  }
]
```

### 2.2 图片提取

MinerU 自动处理图片提取：

- **位图提取**: 从 PDF 中提取嵌入的 JPEG、PNG 等位图
- **矢量图渲染**: 将矢量图形渲染为图片
- **位置信息**: 记录每张图片的页码和边界框 (bbox)
- **自动去重**: MinerU 内部已实现一定程度的去重

**图片信息数据结构**:
```python
@dataclass
class ImageInfo:
    image_id: str           # 唯一标识符
    file_path: str          # 图片文件路径
    page_num: int           # 所在页码
    bbox: Tuple[float, ...] # 边界框坐标 (x0, y0, x1, y1)
    width: int              # 图片宽度
    height: int             # 图片高度
    image_type: str = "bitmap"  # 图片类型
    caption: str = ""       # 图片标题/说明
```

### 2.3 图片去重机制

尽管 MinerU 有内部去重，系统仍实现了额外的去重层：

```python
# MD5 哈希去重
def calculate_image_hash(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()

# IoU (Intersection over Union) 区域重叠检测
def calculate_iou(bbox1: tuple, bbox2: tuple) -> float:
    """计算两个边界框的重叠程度"""
    x0_1, y0_1, x1_1, y1_1 = bbox1
    x0_2, y0_2, x1_2, y1_2 = bbox2

    inter_x0 = max(x0_1, x0_2)
    inter_y0 = max(y0_1, y0_2)
    inter_x1 = min(x1_1, x1_2)
    inter_y1 = min(y1_1, y1_2)

    if inter_x1 <= inter_x0 or inter_y1 <= inter_y0:
        return 0.0

    inter_area = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
    area1 = (x1_1 - x0_1) * (y1_1 - y0_1)
    area2 = (x1_2 - x0_2) * (y1_2 - y0_2)
    union_area = area1 + area2 - inter_area

    return inter_area / union_area if union_area > 0 else 0.0
```

**去重策略**:
- 首先使用 MD5 哈希检测完全相同的图片
- 然后使用 IoU > 0.8 检测区域重叠的重复图片

## 3. 图片描述生成

### 3.1 视觉模型 (`src/image_descriptor.py`)

```python
@dataclass
class ImageDescription:
    image_id: str
    description: str       # 详细图片描述
    keywords: List[str]    # 3-5 个关键词标签
    category: str          # 图片分类
    ocr_text: Optional[str]  # OCR 提取文本
    confidence: float      # 置信度
```

**支持的图片分类**:
- `流程图` - 流程图、时序图
- `示意图` - 架构图、原理图
- `实物图` - 照片、实物图像
- `界面截图` - UI 截图、软件界面
- `表格图表` - 数据表格、统计图表
- `其他` - 其他类型

### 3.2 Qwen-VL 提示词模板

```python
IMAGE_DESCRIPTION_PROMPT = """
请详细描述这张图片的内容，并按以下JSON格式返回：
{
    "description": "详细描述图片内容，包括主要元素、布局、颜色、文字等",
    "keywords": ["关键词1", "关键词2", "关键词3"],
    "category": "图片分类（流程图/示意图/实物图/界面截图/表格图表/其他）",
    "ocr_text": "图片中包含的文字内容（如果有）",
    "confidence": 0.95
}
"""
```

### 3.3 并发处理

```python
# 使用线程池并发处理图片描述
with ThreadPoolExecutor(max_workers=3) as executor:
    futures = []
    for image_info in images:
        future = executor.submit(
            self._generate_single_description,
            image_info
        )
        futures.append(future)
```

## 4. 缓存架构

### 4.1 缓存层级

```
┌─────────────────────────────────────────────────────────┐
│                    缓存层级架构                          │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Level 1: PDF 转换缓存                                   │
│  ├─ 位置: ./output/convert_cache/                       │
│  ├─ 键: SHA256(pdf_content)                             │
│  ├─ 值: {markdown, toc, images, metadata}               │
│  └─ TTL: 永久（文件存在即有效）                          │
│                                                         │
│  Level 2: 图片描述缓存                                   │
│  ├─ 位置: ./image_desc_cache/                           │
│  ├─ 键: MD5(image_content)                              │
│  ├─ 值: ImageDescription (JSON)                         │
│  └─ TTL: 永久                                           │
│                                                         │
│  Level 3: 向量索引缓存                                   │
│  ├─ 位置: ./unified_index/                              │
│  ├─ 键: source_pdf_hash                                 │
│  ├─ 值: VectorStoreIndex + metadata                     │
│  └─ TTL: 需验证源文件哈希                                │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 4.2 缓存实现

#### 4.2.1 PDF 转换缓存 (`src/mineru_converter.py`)

```python
class StableMinerUConverter:
    def __init__(self, cache_dir: str = "./output/convert_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_key(self, pdf_path: str) -> str:
        """基于 PDF 内容生成缓存键"""
        with open(pdf_path, "rb") as f:
            content = f.read()
        return hashlib.sha256(content).hexdigest()

    def _get_from_cache(self, cache_key: str) -> Optional[Dict]:
        """从缓存获取转换结果"""
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return None
```

#### 4.2.2 图片描述缓存 (`src/image_descriptor.py`)

```python
class ImageDescriptor:
    def __init__(self, cache_dir: str = "./image_desc_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, ImageDescription] = {}
        self._load_cache()

    def _get_cache_key(self, image_path: str) -> str:
        """基于图片内容生成缓存键"""
        with open(image_path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()

    def _load_cache(self):
        """加载所有缓存的描述"""
        for cache_file in self.cache_dir.glob("*.json"):
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                desc = ImageDescription(**data)
                self._cache[desc.image_id] = desc
```

### 4.3 缓存失效策略

```python
def validate_cache(self, cached_data: Dict, pdf_path: str) -> bool:
    """验证缓存是否有效"""
    # 1. 检查源 PDF 是否变更
    current_hash = self._get_cache_key(pdf_path)
    if cached_data.get("pdf_hash") != current_hash:
        return False

    # 2. 检查图片文件是否仍然存在
    for img in cached_data.get("images", []):
        if not Path(img["path"]).exists():
            return False

    return True
```

## 5. 数据处理流程

### 5.1 完整处理流程

```
PDF 文件输入
    │
    ▼
┌─────────────────┐
│ 1. PDF 转换     │◄─── 检查转换缓存 (SHA256)
│    (MinerU)     │      命中则跳过
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 2. 图片提取     │───▶ MinerU 自动提取
│                 │───▶ 保存到输出目录
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 3. 图片去重     │───▶ MD5 哈希去重
│                 │───▶ IoU 区域重叠检测
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 4. 图片描述     │◄─── 检查描述缓存 (MD5)
│    (Qwen-VL)    │      命中则跳过
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 5. 图片-文本关联 │───▶ 基于 Markdown 引用关联
│                 │───▶ 插入图片引用标记
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 6. 构建索引     │───▶ 文本向量化
│                 │───▶ 图片元数据索引
└────────┬────────┘
         │
         ▼
    多模态 RAG 系统
```

### 5.2 图片-文本关联

```python
def _associate_images_to_sections(
    self, sections: List[Section], images: List[ImageInfo], markdown_content: str
) -> List[Section]:
    """根据 Markdown 内容中的图片引用，将图片关联到对应章节"""

    # 建立图片文件路径到 ImageInfo 的映射
    path_to_image = {}
    for img in images:
        path_to_image[img.file_path] = img
        path_to_image[Path(img.file_path).name] = img

    # 扫描每个 section 的内容，查找其中引用的图片
    img_ref_pattern = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')

    for section in sections:
        refs = img_ref_pattern.findall(section.content)
        for ref_path in refs:
            ref_name = Path(ref_path).name
            img = path_to_image.get(ref_path) or path_to_image.get(ref_name)
            if img and img not in section.images:
                section.images.append(img)

    # 未关联的图片分配给第一个章节
    associated_ids = {img.image_id for s in sections for img in s.images}
    unassociated = [img for img in images if img.image_id not in associated_ids]
    if unassociated and sections:
        sections[0].images.extend(unassociated)

    return sections
```

## 6. 多模态索引与检索

### 6.1 索引结构 (`src/unified_pipeline.py`)

```python
@dataclass
class Section:
    """Markdown 章节"""
    section_id: str
    level: int
    title: str
    content: str
    page_num: int
    anchor: str
    images: List[ImageInfo] = field(default_factory=list)
    image_descriptions: List[ImageDescription] = field(default_factory=list)
```

### 6.2 增强文本构建

```python
# 构建增强文本：章节内容 + 图片描述
enhanced_text = section.content
image_desc_text = []

for desc in section.image_descriptions:
    if desc.description:
        image_desc_text.append(f"【图片描述】{desc.description}")
    if desc.keywords:
        image_desc_text.append(f"【关键词】{', '.join(desc.keywords)}")

if image_desc_text:
    enhanced_text += "\n\n" + "\n".join(image_desc_text)
```

### 6.3 检索流程

```python
def query(self, query_text: str, similarity_threshold: float = 0.3) -> Dict[str, Any]:
    """执行多模态查询"""

    # 1. 检索文本节点
    retriever = self.index.as_retriever(similarity_top_k=20)
    retrieved_nodes = retriever.retrieve(query_text)

    # 2. 分类节点
    text_nodes = []
    image_desc_nodes = []

    for node in retrieved_nodes:
        if node.score and node.score >= similarity_threshold:
            node_type = node.node.metadata.get("node_type", "text")
            if node_type == "image_description":
                image_desc_nodes.append(node)
            else:
                text_nodes.append(node)

    # 3. 收集相关图片
    all_images = []
    seen_image_ids = set()

    for node in text_nodes:
        image_ids_str = node.node.metadata.get("image_ids", "")
        image_ids = [i for i in image_ids_str.split(",") if i]
        for img_id in image_ids:
            if img_id not in seen_image_ids:
                # 添加图片信息
                all_images.append(img_data)
                seen_image_ids.add(img_id)

    return {
        "text": answer_text,
        "images": all_images,
        "image_descriptions": [...],
        "confidence": confidence,
    }
```

## 7. 配置参数

### 7.1 图片处理配置

```python
IMAGE_CONFIG = {
    # 尺寸过滤
    "min_image_size": 50,          # 最小图片尺寸（像素）
    "max_image_ratio": 0.9,        # 最大页面占比

    # 去重阈值
    "iou_threshold": 0.8,          # IoU 重叠阈值

    # 并发控制
    "max_workers": 3,              # 图片描述并发数
    "batch_size": 10,              # 批处理大小

    # 缓存路径
    "convert_cache_dir": "./output/convert_cache",
    "image_desc_cache_dir": "./image_desc_cache",
    "vector_index_dir": "./unified_index"
}
```

### 7.2 视觉模型配置

```python
VISION_MODEL_CONFIG = {
    "model": "qwen-vl-max",        # 或 "qwen-vl-plus"
    "api_key": "DASHSCOPE_API_KEY",
    "max_retries": 3,
    "timeout": 60
}
```

### 7.3 MinerU 配置

```python
MINERU_CONFIG = {
    "lang": "ch",                  # 语言: ch (中文) / en (英文)
    "output_dir": "./output",
    "cache_dir": "./output/convert_cache"
}
```

## 8. 文件目录结构

```
project/
├── src/
│   ├── unified_pipeline.py        # 统一处理管道（主流程）
│   ├── mineru_converter.py        # MinerU 转换器
│   └── image_descriptor.py        # 图片描述生成
├── output/
│   ├── convert_cache/             # PDF 转换缓存
│   │   └── {sha256_hash}.json
│   └── images/                    # 提取的图片
│       └── {pdf_name}/
│           └── images/
│               ├── page_1_img_1.jpeg
│               └── ...
├── image_desc_cache/              # 图片描述缓存
│   └── {md5_hash}.json
├── unified_index/                 # 向量索引缓存
│   ├── docstore.json
│   ├── index_store.json
│   └── vector_store.json
└── docs/
    └── pdf-image-architecture.md  # 本文档
```

## 9. 依赖服务

| 服务 | 用途 | 配置项 |
|------|------|--------|
| MinerU (magic-pdf) | PDF 解析与图片提取 | 本地 CLI 工具 |
| DashScope | 视觉模型 (Qwen-VL) 和 Embedding | `DASHSCOPE_API_KEY` |

## 10. 性能优化

1. **多级缓存**：转换缓存 → 描述缓存 → 向量索引缓存
2. **并发处理**：图片描述生成使用线程池（max_workers=3）
3. **增量更新**：仅处理新增或变更的图片
4. **懒加载**：向量索引按需加载

## 11. 主流程入口

```python
# src/unified_pipeline.py

class UnifiedRAGSystem:
    """
    统一 RAG 系统

    完整流程：
    PDF -> MinerU Markdown -> 章节 -> 图文关联 -> 图片描述 -> 索引 -> 查询
    """

    def __init__(
        self,
        qwen_api_key: str,
        persist_dir: str = "./unified_index",
        image_output_dir: str = "./output/images",
        llm_model: str = "qwen-flash",
        embedding_model: str = "text-embedding-v4",
        vl_model: str = "qwen-vl-max",
        verbose: bool = True,
        lang: str = "ch",
        cache_dir: str = "./output/convert_cache",
    ):
        # 初始化 MinerU 转换器
        self.pdf_converter = StableMinerUConverter(
            image_output_dir=image_output_dir,
            verbose=verbose,
            lang=lang,
            cache_dir=str(self.cache_dir),
        )
        # 初始化图片描述器
        self.image_descriptor = ImageDescriptor(
            api_key=qwen_api_key,
            model=vl_model
        )
```
