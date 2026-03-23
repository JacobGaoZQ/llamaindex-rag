"""
PDF 解析模块：提取文字、图片及其关联关系
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
    """PDF 解析器，提取文字和图片及其关联关系"""

    def __init__(self, output_dir: str = "./extracted_images"):
        """
        初始化解析器

        Args:
            output_dir: 图片输出目录
        """
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def parse(self, pdf_path: str) -> ParsedDocument:
        """
        解析 PDF 文件

        Args:
            pdf_path: PDF 文件路径

        Returns:
            ParsedDocument: 解析后的文档对象
        """
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
        return parsed_doc

    def _parse_page(self, page, page_num: int, parsed_doc: ParsedDocument, image_dir: str):
        """解析单页 PDF"""
        # 使用更可靠的方法提取图片
        page_images = self._extract_page_images(page, page_num, image_dir, parsed_doc)

        # 提取文本并关联图片
        text_content = page.get_text("text")
        if text_content.strip():
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

    def _extract_page_images(self, page, page_num: int, image_dir: str,
                             parsed_doc: ParsedDocument) -> List[ImageInfo]:
        """使用更可靠的方法提取页面图片"""
        page_images = []

        try:
            # 获取页面中的所有图片引用
            image_list = page.get_images(full=True)

            for img_index, img_info in enumerate(image_list):
                try:
                    # img_info 是一个元组 (xref, smask, width, height, bpc, colorspace, ...)
                    xref = img_info[0]

                    # 提取图片数据
                    base_image = page.parent.extract_image(xref)
                    if not base_image:
                        continue

                    image_data = base_image["image"]
                    image_ext = base_image.get("ext", "png")

                    # 过滤太小的图片（可能是图标、装饰等）
                    width = img_info[2]
                    height = img_info[3]
                    if width < 50 or height < 50:
                        continue

                    # 生成图片唯一ID
                    image_hash = hashlib.md5(image_data).hexdigest()[:12]
                    image_id = f"img_p{page_num + 1}_{img_index}_{image_hash}"

                    # 保存图片
                    image_filename = f"{image_id}.{image_ext}"
                    image_path = os.path.join(image_dir, image_filename)

                    with open(image_path, "wb") as f:
                        f.write(image_data)

                    # 获取图片在页面上的位置
                    # 注意：同一图片可能在页面出现多次，这里取第一个位置
                    img_rects = page.get_image_rects(xref)
                    bbox = tuple(img_rects[0]) if img_rects else (0, 0, width, height)

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

                except Exception as e:
                    # 单个图片提取失败不影响整体
                    continue

        except Exception as e:
            print(f"提取页面图片时出错 (页 {page_num + 1}): {e}")

        return page_images

    def _extract_image(self, block: dict, page_num: int, image_dir: str) -> Optional[ImageInfo]:
        """提取图片并保存"""
        try:
            image_index = block.get("image", 0)
            if not image_index:
                return None

            # 生成图片唯一ID
            image_data = block.get("image")
            if not image_data:
                # 尝试从 xref 获取图片
                return None

            # 生成图片文件名
            image_hash = hashlib.md5(image_data).hexdigest()[:12]
            image_id = f"img_p{page_num + 1}_{image_hash}"

            # 确定图片格式并保存
            ext = self._detect_image_format(image_data)
            image_filename = f"{image_id}.{ext}"
            image_path = os.path.join(image_dir, image_filename)

            # 保存图片
            with open(image_path, "wb") as f:
                f.write(image_data)

            # 获取图片尺寸
            width = block.get("width", 0)
            height = block.get("height", 0)
            bbox = tuple(block.get("bbox", (0, 0, 0, 0)))

            return ImageInfo(
                image_id=image_id,
                file_path=image_path,
                page_num=page_num + 1,
                bbox=bbox,
                width=width,
                height=height
            )

        except Exception as e:
            print(f"提取图片失败: {e}")
            return None

    def _detect_image_format(self, image_data: bytes) -> str:
        """检测图片格式"""
        if image_data[:8] == b'\x89PNG\r\n\x1a\n':
            return "png"
        elif image_data[:2] == b'\xff\xd8':
            return "jpg"
        elif image_data[:6] in (b'GIF87a', b'GIF89a'):
            return "gif"
        else:
            return "png"  # 默认使用 PNG

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

            # 图片在文本块上下 200 像素范围内认为关联
            if abs(img_y_center - text_y_center) < 200:
                related_images.append(img.image_id)
                # 更新图片的上下文
                img.context_before = content[:200] if img_y_center > text_y_center else ""
                img.context_after = content[:200] if img_y_center <= text_y_center else ""

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
