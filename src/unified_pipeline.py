"""
统一 PDF 处理管道

完整流程：
PDF -> Markdown(含目录) -> 章节解析 -> 图文关联 -> 图片描述 -> 向量索引 -> 查询
"""
import os
import json
import hashlib
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from collections import defaultdict

import fitz
from llama_parse import LlamaParse
from llama_index.core import VectorStoreIndex, Document, Settings, StorageContext
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.embeddings.dashscope import DashScopeEmbedding
from llama_index.llms.dashscope import DashScope

from .image_descriptor import ImageDescriptor, ImageDescription


@dataclass
class TOCItem:
    """目录项"""
    level: int
    title: str
    page_num: int
    anchor: str


@dataclass
class ImageInfo:
    """图片信息"""
    image_id: str
    file_path: str
    page_num: int
    bbox: Tuple[float, float, float, float]
    width: int
    height: int
    image_type: str = "bitmap"  # bitmap 或 vector
    caption: str = ""


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


@dataclass
class ProcessedDocument:
    """处理后的完整文档"""
    source_file: str
    title: str
    toc: List[TOCItem]
    sections: List[Section]
    images: List[ImageInfo]
    markdown_content: str
    metadata: Dict[str, Any]


class PDFToMarkdownConverter:
    """
    PDF 转 Markdown 转换器
    - 提取目录结构
    - 提取位图和矢量图形
    - 生成带页码标记的 Markdown
    """

    def __init__(
        self,
        llama_cloud_api_key: Optional[str] = None,
        image_output_dir: str = "./output/images",
        verbose: bool = True,
    ):
        self.api_key = llama_cloud_api_key
        self.image_output_dir = Path(image_output_dir)
        self.image_output_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose

        # 初始化 LlamaParse
        if self.api_key:
            self.llama_parser = LlamaParse(
                api_key=self.api_key,
                result_type="markdown",
                verbose=verbose,
                invalidate_cache=False,
                fast_mode=False,
                skip_diagonal_text=False,
                page_separator="\n\n---\n\n",
            )
        else:
            self.llama_parser = None
            if verbose:
                print("[警告] 未提供 LlamaCloud API Key，将使用 PyMuPDF 解析文本")

    def convert(self, pdf_path: str) -> Tuple[str, List[TOCItem], List[ImageInfo], Dict]:
        """
        转换 PDF 为 Markdown

        Returns:
            (markdown_content, toc, images, doc_info)
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"文件不存在: {pdf_path}")

        doc_name = pdf_path.stem

        if self.verbose:
            print(f"[转换] {pdf_path.name} -> Markdown...")

        # 1. 提取目录和文档信息
        toc, doc_info = self._extract_toc_and_info(str(pdf_path))

        # 2. 提取图片（位图 + 矢量）
        images = self._extract_all_images(str(pdf_path), doc_name)

        # 3. 转换文本为 Markdown
        if self.llama_parser:
            markdown_content = self._convert_with_llamaparse(str(pdf_path))
        else:
            markdown_content = self._convert_with_pymupdf(str(pdf_path))

        if self.verbose:
            print(f"  完成: {len(toc)} 个目录项, {len(images)} 张图片")

        return markdown_content, toc, images, doc_info

    def _extract_toc_and_info(self, pdf_path: str) -> Tuple[List[TOCItem], Dict]:
        """提取 PDF 目录结构和文档信息"""
        doc = fitz.open(pdf_path)
        toc = []
        doc_info = {
            "title": doc.metadata.get("title", ""),
            "author": doc.metadata.get("author", ""),
            "total_pages": len(doc),
        }

        # 提取内置目录
        try:
            pdf_toc = doc.get_toc()
            for item in pdf_toc:
                level, title, page = item[0], item[1], item[2]
                anchor = self._generate_anchor(title)
                toc.append(TOCItem(level=level, title=title, page_num=page, anchor=anchor))
        except Exception as e:
            if self.verbose:
                print(f"  [警告] 提取目录失败: {e}")

        # 如果没有目录，从文本中提取
        if not toc:
            toc = self._extract_toc_from_text(doc)

        doc.close()
        return toc, doc_info

    def _extract_toc_from_text(self, doc: fitz.Document) -> List[TOCItem]:
        """从文本中提取目录（备用方案）"""
        toc = []
        seen_titles = set()

        for page_num in range(min(10, len(doc))):
            page = doc[page_num]
            blocks = page.get_text("dict", flags=11)["blocks"]

            for block in blocks:
                if block["type"] != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span["text"].strip()
                        font_size = span["size"]
                        flags = span["flags"]

                        level = self._detect_heading_level(text, font_size, flags)
                        if level > 0 and 0 < len(text) < 100 and text not in seen_titles:
                            seen_titles.add(text)
                            anchor = self._generate_anchor(text)
                            toc.append(TOCItem(level=level, title=text, page_num=page_num + 1, anchor=anchor))

        toc.sort(key=lambda x: (x.page_num, x.level))
        return toc

    def _detect_heading_level(self, text: str, font_size: float, flags: int) -> int:
        """根据字体特征检测标题级别"""
        is_bold = flags & 2**4
        if font_size >= 20:
            return 1
        elif font_size >= 16:
            return 2
        elif font_size >= 14:
            return 3
        elif font_size >= 12 and is_bold and not text.endswith((".", ",", ";", "?", "!")):
            return 4
        return 0

    def _generate_anchor(self, title: str) -> str:
        """生成锚点链接"""
        anchor = re.sub(r'[^\w\s-]', '', title.lower())
        anchor = re.sub(r'[-\s]+', '-', anchor)
        return anchor.strip('-')

    def _extract_all_images(self, pdf_path: str, doc_name: str) -> List[ImageInfo]:
        """提取所有图片（位图 + 矢量）"""
        images = []
        processed_hashes = set()
        processed_regions = []

        doc = fitz.open(pdf_path)

        for page_num in range(len(doc)):
            page = doc[page_num]

            # 提取位图
            bitmap_images = self._extract_bitmap_images(
                page, page_num, doc_name, doc, processed_hashes, processed_regions
            )
            images.extend(bitmap_images)

            # 提取矢量图形
            vector_images = self._extract_vector_graphics(
                page, page_num, doc_name, processed_hashes, processed_regions
            )
            images.extend(vector_images)

        doc.close()
        return images

    def _extract_bitmap_images(
        self, page, page_num: int, doc_name: str, doc: fitz.Document,
        processed_hashes: set, processed_regions: list
    ) -> List[ImageInfo]:
        """提取位图图片"""
        images = []
        processed_xrefs = set()

        try:
            image_list = page.get_images(full=True)
            for img_index, img_info in enumerate(image_list):
                try:
                    xref = img_info[0]
                    if xref in processed_xrefs:
                        continue
                    processed_xrefs.add(xref)

                    base_image = doc.extract_image(xref)
                    if not base_image:
                        continue

                    image_data = base_image["image"]
                    image_ext = base_image.get("ext", "png")

                    width, height = img_info[2], img_info[3]
                    if width < 50 or height < 50:
                        continue

                    img_rects = page.get_image_rects(xref)
                    if not img_rects:
                        continue
                    bbox = tuple(img_rects[0])

                    if self._is_region_overlapping(page_num + 1, bbox, processed_regions):
                        continue

                    image_hash = hashlib.md5(image_data).hexdigest()
                    if image_hash in processed_hashes:
                        continue
                    processed_hashes.add(image_hash)

                    short_hash = image_hash[:16]
                    image_id = f"{doc_name}_p{page_num + 1}_img_{img_index:03d}_{short_hash}"
                    image_path = self.image_output_dir / f"{image_id}.{image_ext}"

                    with open(image_path, "wb") as f:
                        f.write(image_data)

                    images.append(ImageInfo(
                        image_id=image_id,
                        file_path=str(image_path),
                        page_num=page_num + 1,
                        bbox=bbox,
                        width=width,
                        height=height,
                        image_type="bitmap"
                    ))
                    processed_regions.append((page_num + 1, bbox))

                except Exception:
                    continue
        except Exception as e:
            if self.verbose:
                print(f"  [警告] 提取位图失败 (页 {page_num + 1}): {e}")

        return images

    def _extract_vector_graphics(
        self, page, page_num: int, doc_name: str,
        processed_hashes: set, processed_regions: list
    ) -> List[ImageInfo]:
        """提取矢量图形"""
        images = []

        try:
            drawings = page.get_drawings()
            if not drawings:
                return images

            all_rects = []
            for d in drawings:
                if "rect" in d:
                    rect = fitz.Rect(d["rect"])
                    if rect.width >= 30 and rect.height >= 30:
                        all_rects.append(rect)

            if not all_rects:
                return images

            merged_rects = self._merge_rects(all_rects, threshold=30.0)

            for idx, rect in enumerate(merged_rects):
                try:
                    width, height = int(rect.width), int(rect.height)
                    if width < 80 or height < 80:
                        continue

                    page_width, page_height = page.rect.width, page.rect.height
                    if width > page_width * 0.9 and height > page_height * 0.9:
                        continue

                    margin = 15
                    clip_rect = fitz.Rect(
                        max(0, rect.x0 - margin),
                        max(0, rect.y0 - margin),
                        min(page_width, rect.x1 + margin),
                        min(page_height, rect.y1 + margin)
                    )

                    bbox_tuple = tuple(clip_rect)
                    if self._is_region_overlapping(page_num + 1, bbox_tuple, processed_regions, iou_threshold=0.3):
                        continue

                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip_rect)
                    image_data = pix.tobytes("png")

                    image_hash = hashlib.md5(image_data).hexdigest()
                    if image_hash in processed_hashes:
                        continue
                    processed_hashes.add(image_hash)

                    short_hash = image_hash[:16]
                    image_id = f"{doc_name}_p{page_num + 1}_vec_{idx:03d}_{short_hash}"
                    image_path = self.image_output_dir / f"{image_id}.png"

                    with open(image_path, "wb") as f:
                        f.write(image_data)

                    images.append(ImageInfo(
                        image_id=image_id,
                        file_path=str(image_path),
                        page_num=page_num + 1,
                        bbox=bbox_tuple,
                        width=pix.width,
                        height=pix.height,
                        image_type="vector"
                    ))
                    processed_regions.append((page_num + 1, bbox_tuple))

                except Exception:
                    continue

        except Exception as e:
            if self.verbose:
                print(f"  [警告] 提取矢量图形失败 (页 {page_num + 1}): {e}")

        return images

    def _is_region_overlapping(
        self, page_num: int, bbox: tuple,
        processed_regions: list, iou_threshold: float = 0.5
    ) -> bool:
        """检查区域是否重叠"""
        x0, y0, x1, y1 = bbox
        bbox_area = (x1 - x0) * (y1 - y0)

        for processed_page, processed_bbox in processed_regions:
            if processed_page != page_num:
                continue

            px0, py0, px1, py1 = processed_bbox
            ix0, iy0 = max(x0, px0), max(y0, py0)
            ix1, iy1 = min(x1, px1), min(y1, py1)

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

    def _convert_with_llamaparse(self, pdf_path: str) -> str:
        """使用 LlamaParse 转换 PDF"""
        documents = self.llama_parser.load_data(pdf_path)
        markdown_parts = []

        for i, doc in enumerate(documents):
            page_num = doc.metadata.get("page", i + 1)
            markdown_parts.append(f"\n\n<!-- Page {page_num} -->\n\n")
            markdown_parts.append(doc.text)

        return "\n\n---\n\n".join(markdown_parts)

    def _convert_with_pymupdf(self, pdf_path: str) -> str:
        """使用 PyMuPDF 转换 PDF（降级方案）"""
        doc = fitz.open(pdf_path)
        markdown_parts = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            if text.strip():
                markdown_parts.append(f"\n\n<!-- Page {page_num + 1} -->\n\n")
                markdown_parts.append(text)

        doc.close()
        return "\n\n---\n\n".join(markdown_parts)


class MarkdownParser:
    """
    Markdown 解析器
    将 Markdown 内容解析为章节结构
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose

    def parse(self, markdown_content: str, toc: List[TOCItem]) -> List[Section]:
        """
        解析 Markdown 为章节

        Args:
            markdown_content: Markdown 内容
            toc: 目录项列表

        Returns:
            章节列表
        """
        if self.verbose:
            print("[解析] Markdown -> 章节...")

        if not toc:
            # 无目录时，将整个内容作为一个章节
            return [Section(
                section_id="sec_001",
                level=1,
                title="正文",
                content=markdown_content,
                page_num=1,
                anchor="",
                images=[]
            )]

        sections = []
        lines = markdown_content.split("\n")
        current_section = None
        current_content = []
        section_idx = 0

        # 创建标题匹配模式
        heading_patterns = []
        for item in toc:
            pattern = r"^" + "#" * item.level + r"\s+" + re.escape(item.title) + r"\s*$"
            heading_patterns.append((re.compile(pattern, re.IGNORECASE), item))

        current_page = 1

        for line in lines:
            # 检测页码
            page_match = re.search(r"<!-- Page (\d+) -->", line)
            if page_match:
                current_page = int(page_match.group(1))

            matched = False
            for pattern, toc_item in heading_patterns:
                if pattern.match(line.strip()):
                    # 保存当前章节
                    if current_section:
                        current_section.content = "\n".join(current_content)
                        sections.append(current_section)

                    # 开始新章节
                    section_idx += 1
                    current_section = Section(
                        section_id=f"sec_{section_idx:03d}",
                        level=toc_item.level,
                        title=toc_item.title,
                        content="",
                        page_num=current_page,
                        anchor=toc_item.anchor,
                        images=[]
                    )
                    current_content = [line]
                    matched = True
                    break

            if not matched:
                current_content.append(line)

        # 保存最后一个章节
        if current_section:
            current_section.content = "\n".join(current_content)
            sections.append(current_section)

        # 如果没有成功分割
        if not sections:
            sections = [Section(
                section_id="sec_001",
                level=1,
                title="正文",
                content=markdown_content,
                page_num=1,
                anchor="",
                images=[]
            )]

        if self.verbose:
            print(f"  完成: {len(sections)} 个章节")

        return sections


class ImageSectionAssociator:
    """
    图片与章节关联器
    将图片与对应的章节关联
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose

    def associate(self, sections: List[Section], images: List[ImageInfo]) -> List[Section]:
        """
        将图片关联到章节

        策略：
        1. 根据页码匹配
        2. 如果一章跨多页，关联所有相关页的图片
        """
        if self.verbose:
            print("[关联] 图片 -> 章节...")

        # 按页码分组图片
        images_by_page = defaultdict(list)
        for img in images:
            images_by_page[img.page_num].append(img)

        # 为每个章节分配图片
        for i, section in enumerate(sections):
            section_start_page = section.page_num

            # 确定章节结束页码
            if i < len(sections) - 1:
                section_end_page = sections[i + 1].page_num
            else:
                section_end_page = max(images_by_page.keys()) if images_by_page else section_start_page

            # 收集该章节范围内的所有图片
            for page_num in range(section_start_page, section_end_page + 1):
                section.images.extend(images_by_page.get(page_num, []))

        if self.verbose:
            total_associated = sum(len(s.images) for s in sections)
            print(f"  完成: {total_associated} 张图片已关联")

        return sections


class UnifiedRAGSystem:
    """
    统一 RAG 系统

    完整流程：
    PDF -> Markdown -> 章节 -> 图文关联 -> 图片描述 -> 索引 -> 查询
    """

    def __init__(
        self,
        qwen_api_key: str,
        llama_cloud_api_key: Optional[str] = None,
        persist_dir: str = "./unified_index",
        image_output_dir: str = "./output/images",
        llm_model: str = "qwen-flash",
        embedding_model: str = "text-embedding-v4",
        vl_model: str = "qwen-vl-max",
        verbose: bool = True,
    ):
        self.qwen_api_key = qwen_api_key
        self.llama_cloud_api_key = llama_cloud_api_key
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.image_output_dir = Path(image_output_dir)
        self.verbose = verbose

        # 模型配置
        self.llm_model = llm_model
        self.embedding_model = embedding_model
        self.vl_model = vl_model

        # 初始化组件
        self.pdf_converter = PDFToMarkdownConverter(
            llama_cloud_api_key=llama_cloud_api_key,
            image_output_dir=image_output_dir,
            verbose=verbose,
        )
        self.markdown_parser = MarkdownParser(verbose=verbose)
        self.image_associator = ImageSectionAssociator(verbose=verbose)
        self.image_descriptor = ImageDescriptor(
            api_key=qwen_api_key,
            model=vl_model
        )

        # 状态
        self.index = None
        self.sections = []
        self.images = []
        self.image_descriptions = {}

    def process_pdf(self, pdf_path: str, generate_descriptions: bool = True) -> ProcessedDocument:
        """
        处理 PDF 文件

        Args:
            pdf_path: PDF 文件路径
            generate_descriptions: 是否生成图片描述

        Returns:
            ProcessedDocument: 处理后的文档
        """
        pdf_path = Path(pdf_path)

        if self.verbose:
            print("=" * 60)
            print("开始处理 PDF")
            print("=" * 60)

        # Step 1: PDF -> Markdown
        markdown_content, toc, images, doc_info = self.pdf_converter.convert(str(pdf_path))
        self.images = images

        # Step 2: Markdown -> 章节
        sections = self.markdown_parser.parse(markdown_content, toc)

        # Step 3: 图文关联
        sections = self.image_associator.associate(sections, images)
        self.sections = sections

        # Step 4: 生成图片描述
        if generate_descriptions:
            sections = self._generate_image_descriptions(sections)

        # Step 5: 构建最终 Markdown（带图片引用）
        final_markdown = self._build_final_markdown(sections, toc, doc_info.get("title", pdf_path.stem))

        # 保存 Markdown 文件
        md_path = self.persist_dir / f"{pdf_path.stem}.md"
        md_path.write_text(final_markdown, encoding="utf-8")

        return ProcessedDocument(
            source_file=str(pdf_path),
            title=doc_info.get("title", pdf_path.stem),
            toc=toc,
            sections=sections,
            images=images,
            markdown_content=final_markdown,
            metadata={
                "process_time": datetime.now().isoformat(),
                "total_pages": doc_info.get("total_pages", 0),
                "total_images": len(images),
                "total_sections": len(sections),
            }
        )

    def _generate_image_descriptions(self, sections: List[Section]) -> List[Section]:
        """为章节中的图片生成描述"""
        if self.verbose:
            print("[生成] 图片描述...")

        # 收集所有需要描述的图片
        all_images = []
        for section in sections:
            for img in section.images:
                all_images.append({
                    "image_id": img.image_id,
                    "file_path": img.file_path,
                    "context": section.content[:500]  # 前500字符作为上下文
                })

        if not all_images:
            return sections

        # 批量生成描述
        descriptions = self.image_descriptor.batch_describe(
            all_images,
            max_workers=3,
            progress_callback=None
        )

        # 建立描述映射
        desc_map = {d.image_id: d for d in descriptions}
        self.image_descriptions = desc_map

        # 将描述关联到章节
        for section in sections:
            section.image_descriptions = [
                desc_map[img.image_id] for img in section.images
                if img.image_id in desc_map
            ]

        if self.verbose:
            success_count = sum(1 for d in descriptions if d.confidence > 0)
            print(f"  完成: {success_count}/{len(descriptions)} 个描述")

        return sections

    def _build_final_markdown(self, sections: List[Section], toc: List[TOCItem], title: str) -> str:
        """构建最终的 Markdown 内容"""
        parts = []

        # 文档标题
        parts.append(f"# {title}\n")

        # 目录
        if toc:
            parts.append("## 目录\n")
            for item in toc:
                indent = "  " * (item.level - 1)
                parts.append(f"{indent}- [{item.title}](#{item.anchor})\n")
            parts.append("\n---\n")

        # 各章节内容
        for section in sections:
            parts.append(section.content)

            # 添加图片引用
            if section.images:
                parts.append("\n\n**本节图片：**\n")
                for img in section.images:
                    rel_path = os.path.relpath(img.file_path, self.persist_dir)
                    parts.append(f"\n![{img.image_id}]({rel_path})\n")

                    # 添加图片描述（如果有）
                    if img.image_id in self.image_descriptions:
                        desc = self.image_descriptions[img.image_id]
                        if desc.description:
                            parts.append(f"*{desc.description[:100]}...*\n")

            parts.append("\n\n---\n\n")

        return "\n".join(parts)

    def build_index(self, processed_doc: ProcessedDocument) -> VectorStoreIndex:
        """
        构建向量索引

        Args:
            processed_doc: 处理后的文档

        Returns:
            VectorStoreIndex: 向量索引
        """
        if self.verbose:
            print("=" * 60)
            print("构建向量索引")
            print("=" * 60)

        # 配置模型
        embed_model = DashScopeEmbedding(
            model_name=self.embedding_model,
            api_key=self.qwen_api_key,
            embed_batch_size=10,
        )
        Settings.embed_model = embed_model
        Settings.chunk_size = 2048
        Settings.chunk_overlap = 128

        documents = []

        # 1. 创建文本节点（按章节）
        for section in processed_doc.sections:
            if not section.content.strip():
                continue

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

            doc = Document(
                text=enhanced_text,
                doc_id=section.section_id,
                metadata={
                    "page_num": section.page_num,
                    "section_title": section.title,
                    "section_level": section.level,
                    "image_ids": ",".join([img.image_id for img in section.images]),
                    "node_type": "text"
                },
                excluded_embed_metadata_keys=["image_ids", "section_level"],
                excluded_llm_metadata_keys=["image_ids", "section_level"],
            )
            documents.append(doc)

        # 2. 创建图片描述节点
        for section in processed_doc.sections:
            for desc in section.image_descriptions:
                if not desc.description:
                    continue

                # 找到对应的图片信息
                img_info = None
                for img in section.images:
                    if img.image_id == desc.image_id:
                        img_info = img
                        break

                if not img_info:
                    continue

                desc_text = f"""【图片信息】
图片ID: {desc.image_id}
所在页码: 第 {img_info.page_num} 页
图片类别: {desc.category}

【图片内容描述】
{desc.description}"""

                if desc.keywords:
                    desc_text += f"\n\n【关键词】\n{', '.join(desc.keywords)}"

                if desc.ocr_text:
                    desc_text += f"\n\n【图片中的文字】\n{desc.ocr_text}"

                doc = Document(
                    text=desc_text,
                    doc_id=f"img_desc_{desc.image_id}",
                    metadata={
                        "page_num": img_info.page_num,
                        "image_id": desc.image_id,
                        "image_path": img_info.file_path,
                        "category": desc.category,
                        "keywords": ", ".join(desc.keywords) if desc.keywords else "",
                        "node_type": "image_description"
                    },
                    excluded_embed_metadata_keys=["image_path", "keywords"],
                    excluded_llm_metadata_keys=["image_path"],
                )
                documents.append(doc)

        if self.verbose:
            print(f"  文本节点: {len([d for d in documents if d.metadata.get('node_type') == 'text'])}")
            print(f"  图片描述节点: {len([d for d in documents if d.metadata.get('node_type') == 'image_description'])}")

        # 3. 构建索引
        node_parser = MarkdownNodeParser()
        index = VectorStoreIndex.from_documents(
            documents,
            node_parser=node_parser,
        )

        self.index = index

        # 4. 保存索引
        index.storage_context.persist(persist_dir=str(self.persist_dir))

        # 保存元数据
        metadata = {
            "source_file": processed_doc.source_file,
            "title": processed_doc.title,
            "sections": [asdict(s) for s in processed_doc.sections],
            "images": [asdict(img) for img in processed_doc.images],
            "image_descriptions": {k: asdict(v) for k, v in self.image_descriptions.items()},
        }

        metadata_path = self.persist_dir / "document_metadata.json"
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        if self.verbose:
            print(f"  索引已保存到: {self.persist_dir}")

        return index

    def load_index(self) -> bool:
        """加载已保存的索引"""
        from llama_index.core import load_index_from_storage

        if not self.persist_dir.exists():
            return False

        try:
            # 配置嵌入模型
            embed_model = DashScopeEmbedding(
                model_name=self.embedding_model,
                api_key=self.qwen_api_key,
                embed_batch_size=10,
            )
            Settings.embed_model = embed_model

            # 加载索引
            storage_context = StorageContext.from_defaults(
                persist_dir=str(self.persist_dir)
            )
            self.index = load_index_from_storage(storage_context)

            # 加载元数据
            metadata_path = self.persist_dir / "document_metadata.json"
            if metadata_path.exists():
                with open(metadata_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)

                # 恢复图片描述
                self.image_descriptions = {
                    k: ImageDescription(**v)
                    for k, v in metadata.get("image_descriptions", {}).items()
                }

            if self.verbose:
                print(f"索引已加载: {self.persist_dir}")

            return True

        except Exception as e:
            if self.verbose:
                print(f"加载索引失败: {e}")
            return False

    def query(self, query_text: str, similarity_threshold: float = 0.3) -> Dict[str, Any]:
        """
        执行查询

        Args:
            query_text: 查询文本
            similarity_threshold: 相似度阈值

        Returns:
            查询结果字典
        """
        if not self.index:
            raise RuntimeError("索引未初始化，请先构建或加载索引")

        # 配置 LLM
        llm = DashScope(model_name=self.llm_model, api_key=self.qwen_api_key)

        # 创建查询引擎
        query_engine = self.index.as_query_engine(
            llm=llm,
            similarity_top_k=10,
        )

        # 执行检索
        retriever = self.index.as_retriever(similarity_top_k=10)
        retrieved_nodes = retriever.retrieve(query_text)

        # 分类节点
        text_nodes = []
        image_desc_nodes = []

        for node in retrieved_nodes:
            if node.score and node.score >= similarity_threshold:
                metadata = node.node.metadata if hasattr(node.node, 'metadata') else {}
                node_type = metadata.get("node_type", "text")

                if node_type == "image_description":
                    image_desc_nodes.append(node)
                else:
                    text_nodes.append(node)

        # 收集图片
        all_images = []
        seen_image_ids = set()

        for node in text_nodes:
            metadata = node.node.metadata if hasattr(node.node, 'metadata') else {}
            image_ids_str = metadata.get("image_ids", "")
            image_ids = [i for i in image_ids_str.split(",") if i] if image_ids_str else []
            for img_id in image_ids:
                if img_id not in seen_image_ids:
                    img_data = {"image_id": img_id}
                    if img_id in self.image_descriptions:
                        desc = self.image_descriptions[img_id]
                        img_data["description"] = desc.description
                        img_data["category"] = desc.category
                        img_data["keywords"] = desc.keywords
                    all_images.append(img_data)
                    seen_image_ids.add(img_id)

        for node in image_desc_nodes:
            metadata = node.node.metadata if hasattr(node.node, 'metadata') else {}
            image_id = metadata.get("image_id")
            if image_id and image_id not in seen_image_ids:
                img_data = {"image_id": image_id}
                if image_id in self.image_descriptions:
                    desc = self.image_descriptions[image_id]
                    img_data["description"] = desc.description
                    img_data["category"] = desc.category
                all_images.append(img_data)
                seen_image_ids.add(image_id)

        # 构建上下文
        context_parts = []
        for node in text_nodes[:5]:
            if node.node.text:
                context_parts.append(node.node.text)

        for node in image_desc_nodes[:3]:
            if node.node.text:
                context_parts.append(f"[图片信息]\n{node.node.text}")

        # 生成回答
        if context_parts:
            context_text = "\n\n---\n\n".join(context_parts)
            prompt = f"""基于以下参考信息回答问题：

{context_text}

问题：{query_text}

请给出详细、准确的回答："""

            response = llm.complete(prompt)
            answer_text = response.text
        else:
            answer_text = "抱歉，未找到相关信息。"

        # 计算置信度
        all_scores = [node.score for node in text_nodes + image_desc_nodes if node.score]
        confidence = sum(all_scores) / len(all_scores) if all_scores else 0.0

        return {
            "text": answer_text,
            "images": all_images,
            "image_descriptions": [
                {
                    "image_id": img_id,
                    "description": desc.description,
                    "keywords": desc.keywords,
                    "category": desc.category,
                }
                for img_id, desc in self.image_descriptions.items()
                if img_id in seen_image_ids
            ],
            "source_nodes": [
                {
                    "node_id": node.node_id,
                    "text": node.node.text[:200] if node.node.text else "",
                    "score": node.score,
                    "node_type": node.node.metadata.get("node_type", "text") if hasattr(node.node, 'metadata') else "text",
                }
                for node in text_nodes + image_desc_nodes
            ],
            "confidence": confidence,
        }


# 便捷函数
def process_pdf_and_build_index(
    pdf_path: str,
    qwen_api_key: str,
    llama_cloud_api_key: Optional[str] = None,
    output_dir: str = "./output",
    generate_descriptions: bool = True,
) -> UnifiedRAGSystem:
    """
    处理 PDF 并构建索引的便捷函数

    Args:
        pdf_path: PDF 文件路径
        qwen_api_key: DashScope API Key
        llama_cloud_api_key: LlamaCloud API Key（可选）
        output_dir: 输出目录
        generate_descriptions: 是否生成图片描述

    Returns:
        UnifiedRAGSystem: 配置好的 RAG 系统
    """
    rag_system = UnifiedRAGSystem(
        qwen_api_key=qwen_api_key,
        llama_cloud_api_key=llama_cloud_api_key,
        persist_dir=f"{output_dir}/index",
        image_output_dir=f"{output_dir}/images",
        verbose=True,
    )

    # 处理 PDF
    processed_doc = rag_system.process_pdf(pdf_path, generate_descriptions=generate_descriptions)

    # 构建索引
    rag_system.build_index(processed_doc)

    return rag_system


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python unified_pipeline.py <pdf路径>")
        sys.exit(1)

    pdf_path = sys.argv[1]

    # 从环境变量获取 API Key
    qwen_api_key = os.environ.get("QWEN_API_KEY")
    llama_cloud_api_key = os.environ.get("LLAMA_CLOUD_API_KEY")

    if not qwen_api_key:
        print("[错误] 请设置 QWEN_API_KEY 环境变量")
        sys.exit(1)

    # 处理并构建索引
    rag = process_pdf_and_build_index(
        pdf_path,
        qwen_api_key=qwen_api_key,
        llama_cloud_api_key=llama_cloud_api_key,
    )

    print("\n" + "=" * 60)
    print("处理完成！")
    print("=" * 60)

    # 交互式查询
    print("\n输入问题进行查询（输入 'quit' 退出）：")
    while True:
        try:
            query = input("\n问题: ").strip()
            if query.lower() in ['quit', 'exit', 'q']:
                break
            if not query:
                continue

            result = rag.query(query)
            print(f"\n回答: {result['text']}")
            print(f"置信度: {result['confidence']:.2%}")

            if result['images']:
                print(f"\n相关图片: {len(result['images'])} 张")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"查询出错: {e}")

    print("\n再见！")
