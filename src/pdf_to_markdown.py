"""
PDF 转 Markdown 处理器（带图片支持）
使用 LlamaParse 转换 PDF 为 Markdown，并保留图片信息
"""
import os
import json
import base64
import hashlib
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

from llama_parse import LlamaParse
from llama_index.core import Document, VectorStoreIndex, Settings
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.embeddings.dashscope import DashScopeEmbedding
from llama_index.llms.dashscope import DashScope


@dataclass
class ImageReference:
    """图片引用信息"""
    image_id: str
    file_path: str
    page_num: int
    bbox: tuple  # (x0, y0, x1, y1)
    caption: str = ""
    original_url: str = ""  # 原始 URL


@dataclass
class ProcessedImageDoc:
    """处理后的文档"""
    source_file: str
    markdown_content: str
    images: List[ImageReference]
    image_dir: str
    metadata: Dict[str, Any]


class PDFToMarkdownConverter:
    """
    PDF 转 Markdown 转换器
    使用 LlamaParse 处理 PDF，并提取图片信息
    """

    def __init__(
        self,
        llama_cloud_api_key: str,
        image_output_dir: str = "./images",
        verbose: bool = True,
    ):
        """
        初始化转换器

        Args:
            llama_cloud_api_key: LlamaCloud API Key
            image_output_dir: 图片输出目录
            verbose: 是否显示详细日志
        """
        self.api_key = llama_cloud_api_key
        self.image_output_dir = Path(image_output_dir)
        self.image_output_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.image_counter = 0

        # 初始化 LlamaParse（启用图片提取）
        self.parser = LlamaParse(
            api_key=self.api_key,
            result_type="markdown",
            verbose=verbose,
            invalidate_cache=False,
            fast_mode=False,
            skip_diagonal_text=False,
            page_separator="\n\n---\n\n",
            split_by_page=True,
        )

    def convert(
        self,
        pdf_path: str,
        output_dir: Optional[str] = None,
        extract_images: bool = True,
    ) -> ProcessedImageDoc:
        """
        转换 PDF 为 Markdown

        Args:
            pdf_path: PDF 文件路径
            output_dir: Markdown 输出目录
            extract_images: 是否使用 PyMuPDF 提取图片

        Returns:
            ProcessedImageDoc: 处理后的文档
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"文件不存在: {pdf_path}")

        if self.verbose:
            print(f"[转换] {pdf_path.name} -> Markdown...")

        # 1. 使用 LlamaParse 解析文本
        documents = self.parser.load_data(str(pdf_path))

        # 2. 处理文档
        markdown_content, images = self._process_documents(documents, pdf_path.stem)

        # 3. 使用 PyMuPDF 提取图片（如果启用）
        if extract_images:
            if self.verbose:
                print("  [PyMuPDF] 提取图片...")
            images = self._extract_images_with_pymupdf(str(pdf_path), pdf_path.stem)
            # 在 Markdown 中插入图片引用
            markdown_content = self._insert_image_references(
                markdown_content, images
            )

        # 4. 保存 Markdown 文件
        if output_dir:
            output_path = Path(output_dir) / f"{pdf_path.stem}.md"
            output_path.write_text(markdown_content, encoding="utf-8")
            if self.verbose:
                print(f"[保存] {output_path}")

        # 收集元数据
        metadata = {
            "conversion_time": datetime.now().isoformat(),
            "source_file": str(pdf_path),
            "total_pages": len(documents),
            "total_images": len(images),
            "image_dir": str(self.image_output_dir),
        }

        return ProcessedImageDoc(
            source_file=str(pdf_path),
            markdown_content=markdown_content,
            images=images,
            image_dir=str(self.image_output_dir),
            metadata=metadata,
        )

    def _extract_images_with_pymupdf(
        self, pdf_path: str, doc_name: str
    ) -> List[ImageReference]:
        """
        使用 PyMuPDF 提取图片（包括位图和矢量图形）

        优化策略：
        1. 主要使用 page.get_images() 提取PDF原生嵌入图片（最可靠）
        2. 使用 page.get_drawings() 提取矢量图形（图表、流程图等）
        3. 使用 xref 去重避免同一图片在同一页面的重复
        4. 使用 IoU 检测避免区域重叠

        Args:
            pdf_path: PDF 文件路径
            doc_name: 文档名称

        Returns:
            图片引用列表
        """
        import fitz

        images = []
        processed_hashes = set()
        processed_regions = []  # (page_num, bbox)

        doc = fitz.open(pdf_path)

        for page_num in range(len(doc)):
            page = doc[page_num]
            processed_xrefs = set()  # 本页已处理的 xref

            # 1. 提取嵌入的位图图片（主要方法）
            image_list = page.get_images(full=True)
            for img_index, img_info in enumerate(image_list):
                try:
                    xref = img_info[0]

                    # 跳过同一页面中重复出现的相同图片（如页眉页脚logo）
                    if xref in processed_xrefs:
                        continue
                    processed_xrefs.add(xref)

                    base_image = doc.extract_image(xref)
                    if not base_image:
                        continue

                    image_data = base_image["image"]
                    image_ext = base_image.get("ext", "png")

                    # 过滤小图片（提高阈值）
                    width = img_info[2]
                    height = img_info[3]
                    if width < 50 or height < 50:
                        continue

                    # 获取图片位置
                    img_rects = page.get_image_rects(xref)
                    if not img_rects:
                        continue
                    bbox = tuple(img_rects[0])

                    # 检查区域重叠
                    if self._is_region_overlapping(page_num + 1, bbox, processed_regions):
                        continue

                    # 内容哈希去重
                    image_hash = hashlib.md5(image_data).hexdigest()
                    if image_hash in processed_hashes:
                        continue
                    processed_hashes.add(image_hash)

                    short_hash = image_hash[:16]
                    image_id = f"{doc_name}_p{page_num + 1}_img_{img_index:03d}_{short_hash}"

                    # 保存图片
                    image_filename = f"{image_id}.{image_ext}"
                    image_path = self.image_output_dir / image_filename

                    with open(image_path, "wb") as f:
                        f.write(image_data)

                    images.append(ImageReference(
                        image_id=image_id,
                        file_path=str(image_path),
                        page_num=page_num + 1,
                        bbox=bbox,
                        caption=f"图片 {len(images) + 1}",
                        original_url="",
                    ))
                    processed_regions.append((page_num + 1, bbox))

                except Exception as e:
                    continue

            # 2. 提取矢量图形（图表、流程图、线条图等）
            vector_images = self._extract_vector_graphics(
                page, page_num, doc_name, processed_hashes, processed_regions
            )
            images.extend(vector_images)

        doc.close()

        if self.verbose:
            print(f"    共提取 {len(images)} 张图片（含矢量图形）")

        return images

    def _extract_vector_graphics(
        self,
        page,
        page_num: int,
        doc_name: str,
        processed_hashes: set,
        processed_regions: List
    ) -> List[ImageReference]:
        """
        提取矢量图形并转换为图片

        使用 PyMuPDF 的 get_drawings() 方法提取页面中的矢量图形，
        包括图表、流程图、线条图、几何图形等。

        Args:
            page: PDF 页面对象
            page_num: 页码（从0开始）
            doc_name: 文档名称
            processed_hashes: 已处理的图片哈希集合
            processed_regions: 已处理的区域列表

        Returns:
            矢量图形图片引用列表
        """
        import fitz

        images = []

        try:
            # 获取页面中的所有绘图指令
            drawings = page.get_drawings()

            if not drawings:
                return images

            # 收集所有绘图区域的矩形
            all_rects = []
            for d in drawings:
                if "rect" in d:
                    rect = fitz.Rect(d["rect"])
                    # 过滤掉太小的绘图元素
                    if rect.width >= 30 and rect.height >= 30:
                        all_rects.append(rect)

            if not all_rects:
                return images

            # 合并相近的绘图区域
            merged_rects = self._merge_rects(all_rects, threshold=30.0)

            for idx, rect in enumerate(merged_rects):
                try:
                    width = int(rect.width)
                    height = int(rect.height)

                    # 过滤太小的区域（可能是图标、分隔线等）
                    if width < 80 or height < 80:
                        continue

                    # 过滤太大的区域（可能是整页背景）
                    page_width = page.rect.width
                    page_height = page.rect.height
                    if width > page_width * 0.9 and height > page_height * 0.9:
                        continue

                    # 添加边距
                    margin = 15
                    clip_rect = fitz.Rect(
                        max(0, rect.x0 - margin),
                        max(0, rect.y0 - margin),
                        min(page_width, rect.x1 + margin),
                        min(page_height, rect.y1 + margin)
                    )

                    # 检查区域是否与已处理的区域重叠
                    bbox_tuple = tuple(clip_rect)
                    if self._is_region_overlapping(page_num + 1, bbox_tuple, processed_regions, iou_threshold=0.3):
                        continue

                    # 渲染为图片（使用2倍缩放以提高清晰度）
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip_rect)
                    image_data = pix.tobytes("png")

                    # 生成图片哈希用于去重
                    image_hash = hashlib.md5(image_data).hexdigest()

                    # 检查是否已处理过相同的图片
                    if image_hash in processed_hashes:
                        continue
                    processed_hashes.add(image_hash)

                    short_hash = image_hash[:16]
                    image_id = f"{doc_name}_p{page_num + 1}_vec_{idx:03d}_{short_hash}"

                    # 保存图片
                    image_path = self.image_output_dir / f"{image_id}.png"
                    with open(image_path, "wb") as f:
                        f.write(image_data)

                    images.append(ImageReference(
                        image_id=image_id,
                        file_path=str(image_path),
                        page_num=page_num + 1,
                        bbox=bbox_tuple,
                        caption=f"图示 {len(images) + 1}",
                        original_url="",
                    ))
                    processed_regions.append((page_num + 1, bbox_tuple))

                except Exception as e:
                    continue

        except Exception as e:
            if self.verbose:
                print(f"    [警告] 提取矢量图形失败 (页 {page_num + 1}): {e}")

        return images

    def _is_region_overlapping(self, page_num: int, bbox: tuple,
                               processed_regions: List, iou_threshold: float = 0.5) -> bool:
        """检查区域是否与已处理的区域重叠"""
        x0, y0, x1, y1 = bbox
        bbox_area = (x1 - x0) * (y1 - y0)

        for processed_page, processed_bbox in processed_regions:
            if processed_page != page_num:
                continue

            px0, py0, px1, py1 = processed_bbox

            # 计算交集
            ix0 = max(x0, px0)
            iy0 = max(y0, py0)
            ix1 = min(x1, px1)
            iy1 = min(y1, py1)

            if ix0 < ix1 and iy0 < iy1:
                intersection = (ix1 - ix0) * (iy1 - iy0)
                processed_area = (px1 - px0) * (py1 - py0)
                union = bbox_area + processed_area - intersection
                iou = intersection / union if union > 0 else 0

                if iou > iou_threshold:
                    return True

        return False

    def _merge_rects(self, rects, threshold=50.0):
        """合并相近的矩形区域"""
        if not rects:
            return []

        sorted_rects = sorted(rects, key=lambda r: (r.y0, r.x0))
        merged = []

        for rect in sorted_rects:
            if not merged:
                merged.append(rect)
            else:
                last = merged[-1]
                if (abs(rect.y0 - last.y0) < threshold or
                    abs(rect.y1 - last.y1) < threshold or
                    rect.intersects(last)):
                    merged[-1] = last | rect
                else:
                    merged.append(rect)

        return merged

    def _insert_image_references(
        self, markdown_content: str, images: List[ImageReference]
    ) -> str:
        """
        在 Markdown 中插入图片引用

        Args:
            markdown_content: Markdown 内容
            images: 图片列表

        Returns:
            插入图片引用后的内容
        """
        lines = markdown_content.split("\n")
        result_lines = []
        current_page = 1

        for line in lines:
            # 检测页码
            if "<!-- Page" in line:
                import re
                match = re.search(r"<!-- Page (\d+) -->", line)
                if match:
                    current_page = int(match.group(1))

                # 在该页后插入图片
                result_lines.append(line)
                page_images = [img for img in images if img.page_num == current_page]
                if page_images:
                    result_lines.append("")
                    result_lines.append("**本页图片：**")
                    for img in page_images:
                        result_lines.append(f"![{img.caption}]({img.file_path})")
                    result_lines.append("")
            else:
                result_lines.append(line)

        return "\n".join(result_lines)

    def _process_documents(
        self,
        documents: List[Any],
        doc_name: str,
    ) -> tuple[str, List[ImageReference]]:
        """
        处理 LlamaParse 返回的文档，提取图片信息

        Args:
            documents: LlamaParse 返回的文档列表
            doc_name: 文档名称

        Returns:
            (markdown_content, images): Markdown 内容和图片列表
        """
        markdown_parts = []
        images = []

        for i, doc in enumerate(documents):
            page_num = doc.metadata.get("page", i + 1)

            # 添加页码标记
            markdown_parts.append(f"\n\n<!-- Page {page_num} -->\n\n")

            # 处理文本内容
            content = doc.text

            # 查找文档中的图片引用
            content, image_refs = self._process_images_in_content(
                content, page_num, doc_name
            )
            images.extend(image_refs)

            markdown_parts.append(content)

        markdown_content = "\n\n---\n\n".join(markdown_parts)
        return markdown_content, images

    def _process_images_in_content(
        self,
        content: str,
        page_num: int,
        doc_name: str,
    ) -> tuple[str, List[ImageReference]]:
        """
        处理 Markdown 内容中的图片

        Args:
            content: Markdown 内容
            page_num: 页码
            doc_name: 文档名称

        Returns:
            (processed_content, images): 处理后的内容和图片列表
        """
        images = []

        # 匹配 Markdown 图片语法: ![alt](url)
        pattern = r'!\[([^\]]*)\]\(([^)]+)\)'

        def replace_image(match):
            nonlocal images
            alt_text = match.group(1)
            image_url = match.group(2)

            self.image_counter += 1
            image_id = f"{doc_name}_p{page_num}_{self.image_counter:03d}"

            # 如果是 base64 编码的图片
            if image_url.startswith("data:image/"):
                try:
                    file_path = self._save_base64_image(image_url, image_id)
                    images.append(ImageReference(
                        image_id=image_id,
                        file_path=file_path,
                        page_num=page_num,
                        bbox=(0, 0, 0, 0),
                        caption=alt_text,
                        original_url=image_url[:50] + "..." if len(image_url) > 50 else image_url,
                    ))
                    return f"![{alt_text}]({file_path})"
                except Exception as e:
                    if self.verbose:
                        print(f"  [警告] 保存图片失败: {e}")
                    return match.group(0)  # 保持原样

            # 如果是文件路径（LlamaParse 可能返回临时文件路径）
            elif image_url.startswith("file://") or image_url.startswith("/"):
                # 复制到我们的目录
                try:
                    file_path = self._copy_image_file(image_url, image_id)
                    images.append(ImageReference(
                        image_id=image_id,
                        file_path=file_path,
                        page_num=page_num,
                        bbox=(0, 0, 0, 0),
                        caption=alt_text,
                        original_url=image_url,
                    ))
                    return f"![{alt_text}]({file_path})"
                except Exception as e:
                    if self.verbose:
                        print(f"  [警告] 复制图片失败: {e}")
                    return match.group(0)

            # 其他情况保持原样
            return match.group(0)

        processed_content = re.sub(pattern, replace_image, content)
        return processed_content, images

    def _save_base64_image(self, data_url: str, image_id: str) -> str:
        """
        保存 base64 编码的图片

        Args:
            data_url: data:image/... 格式的 URL
            image_id: 图片 ID

        Returns:
            保存的文件路径
        """
        # 解析 data URL
        if "," not in data_url:
            raise ValueError("Invalid data URL")

        header, encoded = data_url.split(",", 1)
        mime_match = re.search(r'data:image/(\w+)', header)

        if mime_match:
            extension = mime_match.group(1)
        else:
            extension = "png"  # 默认

        # 解码并保存
        image_data = base64.b64decode(encoded)
        file_path = self.image_output_dir / f"{image_id}.{extension}"

        with open(file_path, "wb") as f:
            f.write(image_data)

        return str(file_path)

    def _copy_image_file(self, source_path: str, image_id: str) -> str:
        """
        复制图片文件到输出目录

        Args:
            source_path: 源文件路径
            image_id: 图片 ID

        Returns:
            目标文件路径
        """
        # 处理 file:// 协议
        if source_path.startswith("file://"):
            source_path = source_path[7:]

        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"图片文件不存在: {source_path}")

        # 确定扩展名
        extension = source.suffix or ".png"
        target_path = self.image_output_dir / f"{image_id}{extension}"

        # 复制文件
        import shutil
        shutil.copy2(source, target_path)

        return str(target_path)


class MarkdownIndexBuilder:
    """
    基于 Markdown 的索引构建器
    支持图文关联的多模态检索
    """

    def __init__(
        self,
        qwen_api_key: str,
        persist_dir: str = "./markdown_index",
        embedding_model: str = "text-embedding-v4",
        llm_model: str = "qwen-flash",
    ):
        """
        初始化索引构建器

        Args:
            qwen_api_key: DashScope API Key
            persist_dir: 索引持久化目录
            embedding_model: 嵌入模型名称
            llm_model: LLM 模型名称
        """
        self.qwen_api_key = qwen_api_key
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        # 配置模型
        self._setup_models(embedding_model, llm_model)

        # 图片元数据
        self.image_metadata: Dict[str, ImageReference] = {}

    def _setup_models(self, embedding_model: str, llm_model: str):
        """配置嵌入模型和 LLM"""
        # 嵌入模型
        self.embed_model = DashScopeEmbedding(
            model_name=embedding_model,
            api_key=self.qwen_api_key,
            embed_batch_size=10,
        )

        # LLM
        self.llm = DashScope(
            model_name=llm_model,
            api_key=self.qwen_api_key,
        )

        # 全局设置
        Settings.embed_model = self.embed_model
        Settings.llm = self.llm
        Settings.embed_batch_size = 10

    def build_index_from_markdown(
        self,
        markdown_files: List[str],
        image_metadata: Optional[Dict[str, ImageReference]] = None,
    ) -> VectorStoreIndex:
        """
        从 Markdown 文件构建索引

        Args:
            markdown_files: Markdown 文件路径列表
            image_metadata: 图片元数据（可选）

        Returns:
            VectorStoreIndex 实例
        """
        documents = []

        for md_file in markdown_files:
            content = Path(md_file).read_text(encoding="utf-8")
            
            # 按页分割
            pages = content.split("\n\n---\n\n")
            
            for page_content in pages:
                if not page_content.strip():
                    continue
                    
                # 提取页码
                page_num = 1
                if "<!-- Page" in page_content:
                    import re
                    match = re.search(r"<!-- Page (\d+) -->", page_content)
                    if match:
                        page_num = int(match.group(1))
                        
                # 创建文档
                doc = Document(
                    text=page_content,
                    metadata={
                        "source_file": md_file,
                        "page_num": page_num,
                        "file_type": "markdown",
                    },
                )
                documents.append(doc)

        # 使用 Markdown 节点解析器
        node_parser = MarkdownNodeParser()

        # 构建索引
        index = VectorStoreIndex.from_documents(
            documents,
            node_parser=node_parser,
        )

        # 保存图片元数据
        if image_metadata:
            self.image_metadata = image_metadata

        return index

    def save_index(self, index: VectorStoreIndex):
        """保存索引"""
        index.storage_context.persist(persist_dir=str(self.persist_dir))
        
        # 保存图片元数据
        metadata_path = self.persist_dir / "image_metadata.json"
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(
                {k: asdict(v) for k, v in self.image_metadata.items()},
                f,
                ensure_ascii=False,
                indent=2,
            )

    def load_index(self) -> Optional[VectorStoreIndex]:
        """加载索引"""
        from llama_index.core import load_index_from_storage
        from llama_index.core.storage.storage_context import StorageContext

        if not self.persist_dir.exists():
            return None

        storage_context = StorageContext.from_defaults(
            persist_dir=str(self.persist_dir)
        )
        
        index = load_index_from_storage(storage_context)
        
        # 加载图片元数据
        metadata_path = self.persist_dir / "image_metadata.json"
        if metadata_path.exists():
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.image_metadata = {
                    k: ImageReference(**v) for k, v in data.items()
                }

        return index


# 便捷函数
def convert_pdf_to_markdown(
    pdf_path: str,
    output_dir: str = "./markdown_output",
    image_dir: str = "./images",
    llama_cloud_api_key: Optional[str] = None,
) -> str:
    """
    将 PDF 转换为 Markdown

    Args:
        pdf_path: PDF 文件路径
        output_dir: Markdown 输出目录
        image_dir: 图片输出目录
        llama_cloud_api_key: LlamaCloud API Key

    Returns:
        Markdown 文件路径
    """
    if llama_cloud_api_key is None:
        llama_cloud_api_key = os.environ.get("LLAMA_CLOUD_API_KEY")
        
    if not llama_cloud_api_key:
        raise ValueError("请提供 LlamaCloud API Key")

    converter = PDFToMarkdownConverter(
        llama_cloud_api_key=llama_cloud_api_key,
        image_output_dir=image_dir,
    )

    result = converter.convert(pdf_path, output_dir)
    
    return f"{output_dir}/{Path(pdf_path).stem}.md"


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python pdf_to_markdown.py <pdf路径>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    
    # 从环境变量获取 API Key
    api_key = os.environ.get("LLAMA_CLOUD_API_KEY")
    if not api_key:
        print("请设置 LLAMA_CLOUD_API_KEY 环境变量")
        sys.exit(1)

    # 转换
    md_path = convert_pdf_to_markdown(
        pdf_path,
        output_dir="./output/markdown",
        image_dir="./output/images",
    )
    
    print(f"转换完成: {md_path}")
