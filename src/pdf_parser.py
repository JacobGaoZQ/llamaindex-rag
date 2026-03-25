"""
PDF 解析模块：提取文字、图片及其关联关系
优化版本：简化图片提取逻辑，提高准确性和完整性
"""
import fitz  # PyMuPDF
import os
import hashlib
from dataclasses import dataclass, field, asdict
from typing import List, Optional
import json


@dataclass
class ImageInfo:
    """图片信息"""
    image_id: str  # 图片唯一标识
    file_path: str  # 图片保存路径
    page_num: int  # 所在页码
    bbox: tuple  # 图片边界框 (x0, y0, x1, y1)
    width: int  # 图片宽度
    height: int  # 图片高度
    context_before: str = ""  # 图片前文上下文
    context_after: str = ""  # 图片后文上下文
    caption: str = ""  # 图片标题/说明


@dataclass
class TextBlock:
    """文本块"""
    block_id: str  # 块唯一标识
    content: str  # 文本内容
    page_num: int  # 所在页码
    bbox: tuple  # 文本边界框
    images: List[str] = field(default_factory=list)  # 关联的图片ID列表


@dataclass
class ParsedDocument:
    """解析后的文档"""
    source_file: str  # 源文件路径
    text_blocks: List[TextBlock] = field(default_factory=list)  # 文本块列表
    images: List[ImageInfo] = field(default_factory=list)  # 图片列表
    total_pages: int = 0  # 总页数


class PDFParser:
    """
    PDF 解析器，提取文字和图片及其关联关系

    优化策略：
    1. 主要使用 page.get_images() 提取PDF原生嵌入图片（最可靠）
    2. 使用整页渲染作为矢量图形的备选方案
    3. 使用内容哈希 + 区域重叠检测进行去重
    """

    def __init__(self, output_dir: str = "./extracted_images"):
        """
        初始化解析器

        Args:
            output_dir: 图片输出目录
        """
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        # 用于去重：已处理的图片内容哈希集合
        self._processed_hashes: set = set()
        # 用于去重：已处理的图片区域 (page_num, bbox)
        self._processed_regions: List[tuple] = []

    def parse(self, pdf_path: str) -> ParsedDocument:
        """
        解析 PDF 文件

        Args:
            pdf_path: PDF 文件路径

        Returns:
            ParsedDocument: 解析后的文档对象
        """
        # 重置去重状态
        self._processed_hashes.clear()
        self._processed_regions.clear()

        doc = fitz.open(pdf_path)
        parsed_doc = ParsedDocument(
            source_file=pdf_path,
            total_pages=len(doc)
        )

        # 为文档创建图片存储子目录
        pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
        image_dir = os.path.join(self.output_dir, pdf_name)
        os.makedirs(image_dir, exist_ok=True)

        # 逐页解析
        for page_num in range(len(doc)):
            page = doc[page_num]
            self._parse_page(page, page_num, parsed_doc, image_dir)

        doc.close()

        if parsed_doc.images:
            print(f"  共提取 {len(parsed_doc.images)} 张唯一图片")

        return parsed_doc

    def _parse_page(self, page, page_num: int, parsed_doc: ParsedDocument, image_dir: str):
        """解析单页 PDF"""
        # 提取页面中的位图图片（主要方法）
        page_images = self._extract_page_images(page, page_num, image_dir, parsed_doc)

        # 提取矢量图形（图表、流程图等）
        vector_images = self._extract_vector_graphics(page, page_num, image_dir, parsed_doc)
        page_images.extend(vector_images)

        # 提取文本并关联图片
        self._extract_text_with_image_context(page, page_num, parsed_doc, page_images)

    def _extract_page_images(self, page, page_num: int, image_dir: str,
                             parsed_doc: ParsedDocument) -> List[ImageInfo]:
        """
        提取页面中的图片（主要方法）

        使用 page.get_images() 获取PDF原生嵌入图片，这是最可靠的方法。
        同时通过 xref 追踪避免同一图片在同一页面的重复提取。
        """
        page_images = []
        processed_xrefs = set()  # 追踪本页已处理的 xref

        try:
            # 获取页面中的所有图片引用
            image_list = page.get_images(full=True)

            for img_index, img_info in enumerate(image_list):
                try:
                    # img_info 是一个元组 (xref, smask, width, height, bpc, colorspace, ...)
                    xref = img_info[0]

                    # 跳过同一页面中重复出现的相同图片（如页眉页脚logo）
                    if xref in processed_xrefs:
                        continue
                    processed_xrefs.add(xref)

                    # 提取图片数据
                    base_image = page.parent.extract_image(xref)
                    if not base_image:
                        continue

                    image_data = base_image["image"]
                    image_ext = base_image.get("ext", "png")

                    # 过滤太小的图片（可能是图标、装饰等）
                    width = img_info[2]
                    height = img_info[3]
                    if width < 50 or height < 50:  # 提高阈值，过滤小图标
                        continue

                    # 获取图片在页面上的位置
                    img_rects = page.get_image_rects(xref)
                    if not img_rects:
                        continue

                    # 使用第一个位置（主要显示位置）
                    bbox = tuple(img_rects[0])

                    # 检查该区域是否与已处理的图片区域重叠
                    if self._is_region_overlapping(page_num + 1, bbox):
                        continue

                    # 生成图片唯一ID（使用完整哈希确保唯一性）
                    image_hash = hashlib.md5(image_data).hexdigest()

                    # 全局去重：检查是否已处理过完全相同的图片内容
                    if image_hash in self._processed_hashes:
                        continue
                    self._processed_hashes.add(image_hash)

                    short_hash = image_hash[:16]
                    image_id = f"img_p{page_num + 1}_{img_index}_{short_hash}"

                    # 保存图片
                    image_filename = f"{image_id}.{image_ext}"
                    image_path = os.path.join(image_dir, image_filename)

                    with open(image_path, "wb") as f:
                        f.write(image_data)

                    image_info = ImageInfo(
                        image_id=image_id,
                        file_path=image_path,
                        page_num=page_num + 1,
                        bbox=bbox,
                        width=width,
                        height=height
                    )

                    page_images.append(image_info)
                    parsed_doc.images.append(image_info)
                    self._processed_regions.append((page_num + 1, bbox))

                except Exception as e:
                    # 单个图片提取失败不影响整体
                    continue

        except Exception as e:
            print(f"提取页面图片时出错 (页 {page_num + 1}): {e}")

        return page_images

    def _extract_vector_graphics(self, page, page_num: int, image_dir: str,
                                  parsed_doc: ParsedDocument) -> List[ImageInfo]:
        """
        提取矢量图形并转换为图片

        使用 PyMuPDF 的 get_drawings() 方法提取页面中的矢量图形，
        包括图表、流程图、线条图、几何图形等。这些图形在 PDF 中通常
        以绘图指令的形式存在，而不是位图。

        Args:
            page: PDF 页面对象
            page_num: 页码（从0开始）
            image_dir: 图片保存目录
            parsed_doc: 解析后的文档对象

        Returns:
            矢量图形图片信息列表
        """
        page_images = []

        try:
            # 获取页面中的所有绘图指令
            drawings = page.get_drawings()

            if not drawings:
                return page_images

            # 收集所有绘图区域的矩形
            all_rects = []
            for d in drawings:
                if "rect" in d:
                    rect = fitz.Rect(d["rect"])
                    # 过滤掉太小的绘图元素（可能是线条、点等）
                    if rect.width >= 30 and rect.height >= 30:
                        all_rects.append(rect)

            if not all_rects:
                return page_images

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

                    # 检查该区域是否与已处理的图片区域重叠
                    bbox_tuple = tuple(clip_rect)
                    if self._is_region_overlapping(page_num + 1, bbox_tuple, iou_threshold=0.3):
                        continue

                    # 渲染为图片（使用2倍缩放以提高清晰度）
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip_rect)
                    image_data = pix.tobytes("png")

                    # 生成图片哈希用于去重
                    image_hash = hashlib.md5(image_data).hexdigest()

                    # 检查是否已处理过相同的图片
                    if image_hash in self._processed_hashes:
                        continue
                    self._processed_hashes.add(image_hash)

                    short_hash = image_hash[:16]
                    image_id = f"img_p{page_num + 1}_vec_{idx}_{short_hash}"

                    # 保存图片
                    image_filename = f"{image_id}.png"
                    image_path = os.path.join(image_dir, image_filename)

                    with open(image_path, "wb") as f:
                        f.write(image_data)

                    image_info = ImageInfo(
                        image_id=image_id,
                        file_path=image_path,
                        page_num=page_num + 1,
                        bbox=bbox_tuple,
                        width=pix.width,
                        height=pix.height
                    )

                    page_images.append(image_info)
                    parsed_doc.images.append(image_info)
                    self._processed_regions.append((page_num + 1, bbox_tuple))

                except Exception as e:
                    # 单个图形提取失败不影响整体
                    continue

        except Exception as e:
            print(f"提取矢量图形时出错 (页 {page_num + 1}): {e}")

        return page_images

    def _merge_rects(self, rects, threshold=50.0):
        """
        合并相近的矩形区域

        Args:
            rects: 矩形列表
            threshold: 合并阈值（像素）

        Returns:
            合并后的矩形列表
        """
        if not rects:
            return []

        # 按 y 坐标排序，然后按 x 坐标排序
        sorted_rects = sorted(rects, key=lambda r: (r.y0, r.x0))
        merged = []

        for rect in sorted_rects:
            if not merged:
                merged.append(rect)
            else:
                last = merged[-1]
                # 如果当前矩形与最后一个合并的矩形相近或有交集，则合并
                if (abs(rect.y0 - last.y0) < threshold or
                    abs(rect.y1 - last.y1) < threshold or
                    rect.intersects(last)):
                    merged[-1] = last | rect  # 合并矩形
                else:
                    merged.append(rect)

        return merged

    def _is_region_overlapping(self, page_num: int, bbox: tuple,
                               iou_threshold: float = 0.5) -> bool:
        """
        检查区域是否与已处理的区域重叠

        Args:
            page_num: 页码
            bbox: 边界框 (x0, y0, x1, y1)
            iou_threshold: IoU 阈值，超过则认为重叠

        Returns:
            是否重叠
        """
        x0, y0, x1, y1 = bbox
        bbox_area = (x1 - x0) * (y1 - y0)

        for processed_page, processed_bbox in self._processed_regions:
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

    def _extract_text_with_image_context(self, page, page_num: int,
                                         parsed_doc: ParsedDocument,
                                         page_images: List[ImageInfo]):
        """提取文本并建立与图片的上下文关联"""
        try:
            text_content = page.get_text("text")
            if not text_content.strip():
                return

            # 获取文本块的详细位置信息
            text_blocks = page.get_text("dict", flags=11)["blocks"]

            current_text_parts = []
            last_y = None

            for block in text_blocks:
                if block["type"] == 0:  # 文本块
                    for line in block.get("lines", []):
                        line_text = "".join([span["text"] for span in line.get("spans", [])])
                        line_y = line["bbox"][1]

                        # 检查是否需要分割（基于垂直间距）
                        if last_y is not None and abs(line_y - last_y) > 50:
                            if current_text_parts:
                                self._create_text_block(
                                    current_text_parts, page_num, parsed_doc, page_images
                                )
                                current_text_parts = []

                        current_text_parts.append({
                            "text": line_text,
                            "bbox": line["bbox"]
                        })
                        last_y = line_y

            # 处理剩余的文本
            if current_text_parts:
                self._create_text_block(current_text_parts, page_num, parsed_doc, page_images)

        except Exception as e:
            print(f"提取文本时出错 (页 {page_num + 1}): {e}")

    def _create_text_block(self, text_parts: List[dict], page_num: int,
                           parsed_doc: ParsedDocument, page_images: List[ImageInfo]):
        """创建文本块并关联图片"""
        if not text_parts:
            return

        # 合并文本
        content = "\n".join([part["text"] for part in text_parts if part["text"].strip()])
        if not content.strip():
            return

        # 计算文本块边界框
        bboxes = [part["bbox"] for part in text_parts]
        x0 = min(b[0] for b in bboxes)
        y0 = min(b[1] for b in bboxes)
        x1 = max(b[2] for b in bboxes)
        y1 = max(b[3] for b in bboxes)

        # 生成块ID
        block_hash = hashlib.md5(content.encode()).hexdigest()[:8]
        block_id = f"block_p{page_num + 1}_{block_hash}"

        # 查找关联的图片（在同一页且位置相近）
        related_images = []
        for img in page_images:
            # 判断图片是否在文本块附近（垂直距离小于阈值）
            img_y_center = (img.bbox[1] + img.bbox[3]) / 2
            text_y_center = (y0 + y1) / 2

            # 图片在文本块上下 300 像素范围内认为关联
            if abs(img_y_center - text_y_center) < 300:
                related_images.append(img.image_id)
                # 更新图片的上下文
                if img_y_center > text_y_center:
                    img.context_before = content[:200]
                else:
                    img.context_after = content[:200]

        text_block = TextBlock(
            block_id=block_id,
            content=content,
            page_num=page_num + 1,
            bbox=(x0, y0, x1, y1),
            images=related_images
        )
        parsed_doc.text_blocks.append(text_block)

    def save_parsed_document(self, parsed_doc: ParsedDocument, output_path: str):
        """保存解析结果到 JSON 文件"""
        data = {
            "source_file": parsed_doc.source_file,
            "total_pages": parsed_doc.total_pages,
            "text_blocks": [asdict(block) for block in parsed_doc.text_blocks],
            "images": [asdict(img) for img in parsed_doc.images]
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"解析结果已保存到: {output_path}")


def parse_pdf_directory(input_dir: str, output_dir: str = "./extracted_images",
                        metadata_dir: str = "./parsed_metadata") -> List[ParsedDocument]:
    """
    批量解析目录下的所有 PDF 文件

    Args:
        input_dir: PDF 文件目录
        output_dir: 图片输出目录
        metadata_dir: 元数据输出目录

    Returns:
        解析后的文档列表
    """
    os.makedirs(metadata_dir, exist_ok=True)

    parser = PDFParser(output_dir=output_dir)
    parsed_docs = []

    for filename in os.listdir(input_dir):
        if filename.lower().endswith(".pdf"):
            pdf_path = os.path.join(input_dir, filename)
            print(f"正在解析: {filename}")

            try:
                parsed_doc = parser.parse(pdf_path)
                parsed_docs.append(parsed_doc)

                # 保存元数据
                metadata_path = os.path.join(
                    metadata_dir,
                    f"{os.path.splitext(filename)[0]}_parsed.json"
                )
                parser.save_parsed_document(parsed_doc, metadata_path)

                print(f"  - 提取文本块: {len(parsed_doc.text_blocks)}")
                print(f"  - 提取图片: {len(parsed_doc.images)}")

            except Exception as e:
                print(f"解析失败 {filename}: {e}")

    return parsed_docs


if __name__ == "__main__":
    # 测试解析
    parsed_docs = parse_pdf_directory("./data")
    print(f"\n共解析 {len(parsed_docs)} 个文档")
