"""
增强版 PDF 转 Markdown 处理器
支持目录结构提取、图片内联展示、层级格式化
"""
import os
import json
import base64
import hashlib
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime
from collections import defaultdict

import fitz  # PyMuPDF
from llama_parse import LlamaParse


@dataclass
class TOCItem:
    """目录项"""
    level: int  # 标题层级 (1, 2, 3...)
    title: str  # 标题文本
    page_num: int  # 所在页码
    anchor: str  # 锚点链接


@dataclass
class ImageReference:
    """图片引用信息"""
    image_id: str
    file_path: str
    page_num: int
    bbox: tuple  # (x0, y0, x1, y1)
    caption: str = ""
    image_type: str = "bitmap"  # bitmap 或 vector
    related_text: str = ""  # 关联的文本内容


@dataclass
class MarkdownSection:
    """Markdown 章节"""
    level: int
    title: str
    content: str
    page_num: int
    images: List[ImageReference] = field(default_factory=list)
    anchor: str = ""


@dataclass
class ProcessedDocument:
    """处理后的文档"""
    source_file: str
    title: str
    toc: List[TOCItem]  # 目录结构
    sections: List[MarkdownSection]  # 章节内容
    images: List[ImageReference]
    markdown_content: str  # 完整的 Markdown 内容
    metadata: Dict[str, Any]


class EnhancedPDFToMarkdownConverter:
    """
    增强版 PDF 转 Markdown 转换器

    功能：
    1. 提取 PDF 目录结构（TOC）
    2. 按章节组织 Markdown 内容
    3. 提取并内联展示图片
    4. 支持矢量图形提取
    """

    def __init__(
        self,
        llama_cloud_api_key: Optional[str] = None,
        image_output_dir: str = "./output/images",
        markdown_output_dir: str = "./output/markdown",
        verbose: bool = True,
    ):
        self.api_key = llama_cloud_api_key
        self.image_output_dir = Path(image_output_dir)
        self.markdown_output_dir = Path(markdown_output_dir)
        self.image_output_dir.mkdir(parents=True, exist_ok=True)
        self.markdown_output_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose

        # 初始化 LlamaParse（如果提供了 API key）
        if self.api_key:
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
        else:
            self.parser = None

    def convert(
        self,
        pdf_path: str,
        extract_images: bool = True,
        inline_images: bool = True,
        generate_toc: bool = True,
    ) -> ProcessedDocument:
        """
        转换 PDF 为带目录结构的 Markdown

        Args:
            pdf_path: PDF 文件路径
            extract_images: 是否提取图片
            inline_images: 是否在 Markdown 中内联图片
            generate_toc: 是否生成目录

        Returns:
            ProcessedDocument: 处理后的文档
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"文件不存在: {pdf_path}")

        doc_name = pdf_path.stem

        if self.verbose:
            print(f"[转换] {pdf_path.name} -> Markdown...")

        # 1. 使用 PyMuPDF 提取目录结构和基础信息
        toc, doc_info = self._extract_toc_and_info(str(pdf_path))

        # 2. 提取图片（位图 + 矢量图形）
        images = []
        if extract_images:
            images = self._extract_all_images(str(pdf_path), doc_name)

        # 3. 获取 Markdown 内容（优先使用 LlamaParse，否则用 PyMuPDF）
        if self.parser:
            markdown_content = self._convert_with_llamaparse(str(pdf_path))
        else:
            markdown_content = self._convert_with_pymupdf(str(pdf_path))

        # 4. 按章节组织内容
        sections = self._organize_by_sections(markdown_content, toc, images)

        # 5. 在章节中内联图片
        if inline_images:
            sections = self._inline_images_in_sections(sections)

        # 6. 生成最终 Markdown（带目录）
        final_markdown = self._generate_final_markdown(
            doc_info.get("title", doc_name),
            toc,
            sections,
            generate_toc
        )

        # 7. 保存文件
        md_path = self.markdown_output_dir / f"{doc_name}.md"
        md_path.write_text(final_markdown, encoding="utf-8")

        # 保存元数据
        metadata = {
            "conversion_time": datetime.now().isoformat(),
            "source_file": str(pdf_path),
            "doc_info": doc_info,
            "total_images": len(images),
            "total_sections": len(sections),
        }

        return ProcessedDocument(
            source_file=str(pdf_path),
            title=doc_info.get("title", doc_name),
            toc=toc,
            sections=sections,
            images=images,
            markdown_content=final_markdown,
            metadata=metadata,
        )

    def _extract_toc_and_info(self, pdf_path: str) -> Tuple[List[TOCItem], Dict]:
        """
        提取 PDF 目录结构和文档信息

        Args:
            pdf_path: PDF 文件路径

        Returns:
            (toc_list, doc_info): 目录列表和文档信息
        """
        doc = fitz.open(pdf_path)
        toc = []
        doc_info = {
            "title": doc.metadata.get("title", ""),
            "author": doc.metadata.get("author", ""),
            "subject": doc.metadata.get("subject", ""),
            "total_pages": len(doc),
        }

        # 提取目录
        try:
            pdf_toc = doc.get_toc()
            for item in pdf_toc:
                # item 格式: [level, title, page]
                level, title, page = item[0], item[1], item[2]
                anchor = self._generate_anchor(title)
                toc.append(TOCItem(
                    level=level,
                    title=title,
                    page_num=page,
                    anchor=anchor
                ))
        except Exception as e:
            if self.verbose:
                print(f"  [警告] 提取目录失败: {e}")

        # 如果没有目录，尝试从文本中提取标题
        if not toc:
            toc = self._extract_toc_from_text(doc)

        doc.close()
        return toc, doc_info

    def _extract_toc_from_text(self, doc: fitz.Document) -> List[TOCItem]:
        """
        从文本内容中提取目录结构（备用方案）

        通过分析字体大小和格式来识别标题
        """
        toc = []
        seen_titles = set()

        for page_num in range(min(10, len(doc))):  # 只分析前10页
            page = doc[page_num]
            blocks = page.get_text("dict", flags=11)["blocks"]

            for block in blocks:
                if block["type"] != 0:  # 只处理文本块
                    continue

                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span["text"].strip()
                        font_size = span["size"]
                        flags = span["flags"]

                        # 根据字体大小和样式判断标题级别
                        level = self._detect_heading_level(text, font_size, flags)

                        if level > 0 and len(text) > 0 and len(text) < 100:
                            # 去重
                            if text in seen_titles:
                                continue
                            seen_titles.add(text)

                            anchor = self._generate_anchor(text)
                            toc.append(TOCItem(
                                level=level,
                                title=text,
                                page_num=page_num + 1,
                                anchor=anchor
                            ))

        # 按页码和层级排序
        toc.sort(key=lambda x: (x.page_num, x.level))
        return toc

    def _detect_heading_level(self, text: str, font_size: float, flags: int) -> int:
        """
        根据字体特征检测标题级别

        Returns:
            0: 不是标题
            1-6: 标题级别
        """
        # 粗体标志
        is_bold = flags & 2**4

        # 根据字体大小判断（假设正文字体为 12pt）
        if font_size >= 20:
            return 1
        elif font_size >= 16:
            return 2
        elif font_size >= 14:
            return 3
        elif font_size >= 12 and is_bold:
            # 可能是小标题
            if text and not text.endswith((".", ",", ";", "?", "!")):
                return 4
        return 0

    def _generate_anchor(self, title: str) -> str:
        """生成锚点链接"""
        # 移除特殊字符，转换为小写
        anchor = re.sub(r'[^\w\s-]', '', title.lower())
        anchor = re.sub(r'[-\s]+', '-', anchor)
        return anchor.strip('-')

    def _extract_all_images(self, pdf_path: str, doc_name: str) -> List[ImageReference]:
        """
        提取所有图片（位图 + 矢量图形）
        """
        images = []
        processed_hashes = set()
        processed_regions = []

        doc = fitz.open(pdf_path)

        for page_num in range(len(doc)):
            page = doc[page_num]

            # 1. 提取位图
            bitmap_images = self._extract_bitmap_images(
                page, page_num, doc_name, doc, processed_hashes, processed_regions
            )
            images.extend(bitmap_images)

            # 2. 提取矢量图形
            vector_images = self._extract_vector_graphics(
                page, page_num, doc_name, processed_hashes, processed_regions
            )
            images.extend(vector_images)

        doc.close()

        if self.verbose:
            print(f"  共提取 {len(images)} 张图片（位图: {len([i for i in images if i.image_type == 'bitmap'])}, 矢量: {len([i for i in images if i.image_type == 'vector'])})")

        return images

    def _extract_bitmap_images(
        self, page, page_num: int, doc_name: str, doc: fitz.Document,
        processed_hashes: set, processed_regions: list
    ) -> List[ImageReference]:
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

                    images.append(ImageReference(
                        image_id=image_id,
                        file_path=str(image_path),
                        page_num=page_num + 1,
                        bbox=bbox,
                        caption=f"图片 {len(images) + 1}",
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
    ) -> List[ImageReference]:
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

                    images.append(ImageReference(
                        image_id=image_id,
                        file_path=str(image_path),
                        page_num=page_num + 1,
                        bbox=bbox_tuple,
                        caption=f"图示 {len(images) + 1}",
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
        documents = self.parser.load_data(pdf_path)
        markdown_parts = []

        for i, doc in enumerate(documents):
            page_num = doc.metadata.get("page", i + 1)
            markdown_parts.append(f"\n\n<!-- Page {page_num} -->\n\n")
            markdown_parts.append(doc.text)

        return "\n\n---\n\n".join(markdown_parts)

    def _convert_with_pymupdf(self, pdf_path: str) -> str:
        """使用 PyMuPDF 转换 PDF（备用方案）"""
        doc = fitz.open(pdf_path)
        markdown_parts = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("markdown")
            markdown_parts.append(f"\n\n<!-- Page {page_num + 1} -->\n\n")
            markdown_parts.append(text)

        doc.close()
        return "\n\n---\n\n".join(markdown_parts)

    def _organize_by_sections(
        self, markdown_content: str,
        toc: List[TOCItem],
        images: List[ImageReference]
    ) -> List[MarkdownSection]:
        """
        按章节组织 Markdown 内容
        """
        sections = []

        if not toc:
            # 如果没有目录，将整个内容作为一个章节
            sections.append(MarkdownSection(
                level=1,
                title="正文",
                content=markdown_content,
                page_num=1,
                images=images,
                anchor=""
            ))
            return sections

        # 按页码分组图片
        images_by_page = defaultdict(list)
        for img in images:
            images_by_page[img.page_num].append(img)

        # 根据目录分割内容
        lines = markdown_content.split("\n")
        current_section = None
        current_content = []

        # 创建标题匹配模式
        heading_patterns = []
        for item in toc:
            # 匹配不同级别的标题
            pattern = r"^" + "#" * item.level + r"\s+" + re.escape(item.title) + r"\s*$"
            heading_patterns.append((re.compile(pattern, re.IGNORECASE), item))

        for line in lines:
            matched = False

            for pattern, toc_item in heading_patterns:
                if pattern.match(line.strip()):
                    # 保存当前章节
                    if current_section:
                        current_section.content = "\n".join(current_content)
                        sections.append(current_section)

                    # 开始新章节
                    current_section = MarkdownSection(
                        level=toc_item.level,
                        title=toc_item.title,
                        content="",
                        page_num=toc_item.page_num,
                        images=images_by_page.get(toc_item.page_num, []),
                        anchor=toc_item.anchor
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

        # 如果没有成功分割，使用整个内容
        if not sections:
            sections.append(MarkdownSection(
                level=1,
                title="正文",
                content=markdown_content,
                page_num=1,
                images=images,
                anchor=""
            ))

        return sections

    def _inline_images_in_sections(self, sections: List[MarkdownSection]) -> List[MarkdownSection]:
        """
        在章节内容中内联图片

        在章节末尾添加相关图片的 Markdown 引用
        """
        for section in sections:
            if section.images:
                image_markdown = ["\n\n**本节图片：**\n"]
                for img in section.images:
                    rel_path = os.path.relpath(img.file_path, self.markdown_output_dir)
                    image_markdown.append(f"\n![{img.caption}]({rel_path})\n")
                    if img.caption:
                        image_markdown.append(f"*{img.caption}*\n")

                section.content += "\n".join(image_markdown)

        return sections

    def _generate_final_markdown(
        self,
        title: str,
        toc: List[TOCItem],
        sections: List[MarkdownSection],
        generate_toc: bool
    ) -> str:
        """
        生成最终的 Markdown 内容
        """
        parts = []

        # 文档标题
        parts.append(f"# {title}\n")

        # 生成目录
        if generate_toc and toc:
            parts.append("## 目录\n")
            for item in toc:
                indent = "  " * (item.level - 1)
                parts.append(f"{indent}- [{item.title}](#{item.anchor})\n")
            parts.append("\n---\n")

        # 添加各章节内容
        for section in sections:
            parts.append(section.content)
            parts.append("\n\n")

        return "\n".join(parts)

    def save_result(self, result: ProcessedDocument, output_dir: Optional[str] = None):
        """
        保存处理结果

        Args:
            result: 处理后的文档
            output_dir: 输出目录
        """
        if output_dir:
            output_path = Path(output_dir)
        else:
            output_path = self.markdown_output_dir

        output_path.mkdir(parents=True, exist_ok=True)

        # 保存 Markdown 文件
        md_path = output_path / f"{Path(result.source_file).stem}.md"
        md_path.write_text(result.markdown_content, encoding="utf-8")

        # 保存元数据 JSON
        metadata = {
            "source_file": result.source_file,
            "title": result.title,
            "toc": [asdict(item) for item in result.toc],
            "images": [asdict(img) for img in result.images],
            "metadata": result.metadata,
        }

        json_path = output_path / f"{Path(result.source_file).stem}_metadata.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        if self.verbose:
            print(f"[保存] Markdown: {md_path}")
            print(f"[保存] 元数据: {json_path}")


# 便捷函数
def convert_pdf_to_structured_markdown(
    pdf_path: str,
    llama_cloud_api_key: Optional[str] = None,
    output_dir: str = "./output",
    extract_images: bool = True,
) -> ProcessedDocument:
    """
    将 PDF 转换为带目录结构的 Markdown

    Args:
        pdf_path: PDF 文件路径
        llama_cloud_api_key: LlamaCloud API Key（可选）
        output_dir: 输出目录
        extract_images: 是否提取图片

    Returns:
        ProcessedDocument: 处理后的文档
    """
    converter = EnhancedPDFToMarkdownConverter(
        llama_cloud_api_key=llama_cloud_api_key,
        image_output_dir=f"{output_dir}/images",
        markdown_output_dir=f"{output_dir}/markdown",
        verbose=True,
    )

    result = converter.convert(
        pdf_path,
        extract_images=extract_images,
        inline_images=True,
        generate_toc=True,
    )

    converter.save_result(result)

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python enhanced_pdf_to_markdown.py <pdf路径> [output_dir]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "./output"

    # 从环境变量获取 API Key（可选）
    api_key = os.environ.get("LLAMA_CLOUD_API_KEY")

    # 转换
    result = convert_pdf_to_structured_markdown(
        pdf_path,
        llama_cloud_api_key=api_key,
        output_dir=output_dir,
    )

    print(f"\n转换完成！")
    print(f"  标题: {result.title}")
    print(f"  章节数: {len(result.sections)}")
    print(f"  图片数: {len(result.images)}")
    print(f"  目录项: {len(result.toc)}")
