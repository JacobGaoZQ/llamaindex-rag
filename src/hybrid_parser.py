"""
混合 PDF 解析器
结合 LlamaParse（结构化文本）和 PyMuPDF（图片提取）
"""
import os
import fitz
import hashlib
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime

from llama_index.core import Document
from llama_parse import LlamaParse

from .pdf_parser import ImageInfo, PDFParser


@dataclass
class HybridParsedDocument:
    """混合解析后的文档"""
    source_file: str
    markdown_content: str  # LlamaParse 提取的 Markdown
    text_blocks: List[Dict[str, Any]]  # 结构化文本块
    images: List[ImageInfo]  # PyMuPDF 提取的图片
    image_mappings: List[Dict[str, Any]]  # 图片在 Markdown 中的位置映射
    total_pages: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class HybridPDFParser:
    """
    混合 PDF 解析器
    - 使用 LlamaParse 提取结构化 Markdown 文本
    - 使用 PyMuPDF 提取图片和矢量图形
    - 建立图文关联
    """

    def __init__(
        self,
        llama_cloud_api_key: Optional[str] = None,
        image_output_dir: str = "./extracted_images",
        markdown_output_dir: str = "./markdown_output",
        verbose: bool = True,
    ):
        """
        初始化混合解析器

        Args:
            llama_cloud_api_key: LlamaCloud API Key
            image_output_dir: 图片输出目录
            markdown_output_dir: Markdown 输出目录
            verbose: 是否显示详细日志
        """
        self.llama_cloud_api_key = llama_cloud_api_key or os.environ.get(
            "LLAMA_CLOUD_API_KEY"
        )
        self.image_output_dir = Path(image_output_dir)
        self.markdown_output_dir = Path(markdown_output_dir)
        self.verbose = verbose

        # 创建输出目录
        self.image_output_dir.mkdir(parents=True, exist_ok=True)
        self.markdown_output_dir.mkdir(parents=True, exist_ok=True)

        # 初始化解析器
        self._init_parsers()

    def _init_parsers(self):
        """初始化解析器"""
        # LlamaParse 用于提取结构化文本
        if self.llama_cloud_api_key:
            self.llama_parser = LlamaParse(
                api_key=self.llama_cloud_api_key,
                result_type="markdown",
                verbose=self.verbose,
                invalidate_cache=False,
                fast_mode=False,
                skip_diagonal_text=False,
                page_separator="\n\n---\n\n",
            )
        else:
            self.llama_parser = None
            if self.verbose:
                print("[警告] 未提供 LlamaCloud API Key，将使用纯 PyMuPDF 解析")

        # PyMuPDF 用于提取图片
        self.image_parser = PDFParser(output_dir=str(self.image_output_dir))

    def parse(
        self,
        pdf_path: str,
        force_reparse: bool = False,
    ) -> HybridParsedDocument:
        """
        解析 PDF 文件

        Args:
            pdf_path: PDF 文件路径
            force_reparse: 是否强制重新解析

        Returns:
            HybridParsedDocument: 混合解析结果
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"文件不存在: {pdf_path}")

        if self.verbose:
            print(f"[混合解析] {pdf_path.name}...")

        # 1. 使用 LlamaParse 提取 Markdown
        if self.llama_parser:
            markdown_content, text_blocks = self._parse_with_llama(str(pdf_path))
        else:
            # 降级方案：使用 PyMuPDF 提取文本
            markdown_content, text_blocks = self._parse_with_pymupdf_text(str(pdf_path))

        # 2. 使用 PyMuPDF 提取图片
        images = self._extract_images(str(pdf_path))

        # 3. 建立图文关联
        image_mappings = self._map_images_to_markdown(
            markdown_content, text_blocks, images
        )

        # 4. 获取总页数
        doc = fitz.open(str(pdf_path))
        total_pages = len(doc)
        doc.close()

        # 5. 保存 Markdown 文件
        md_output_path = self.markdown_output_dir / f"{pdf_path.stem}.md"
        md_output_path.write_text(markdown_content, encoding="utf-8")

        if self.verbose:
            print(f"  - Markdown 段落: {len(text_blocks)}")
            print(f"  - 提取图片: {len(images)}")
            print(f"  - 图文关联: {len(image_mappings)}")

        return HybridParsedDocument(
            source_file=str(pdf_path),
            markdown_content=markdown_content,
            text_blocks=text_blocks,
            images=images,
            image_mappings=image_mappings,
            total_pages=total_pages,
            metadata={
                "parse_time": datetime.now().isoformat(),
                "has_llama_parse": self.llama_parser is not None,
            },
        )

    def _parse_with_llama(self, pdf_path: str) -> Tuple[str, List[Dict[str, Any]]]:
        """使用 LlamaParse 解析 PDF"""
        if self.verbose:
            print("  [LlamaParse] 提取结构化文本...")

        # 解析文档
        documents = self.llama_parser.load_data(pdf_path)

        # 合并为 Markdown
        markdown_parts = []
        text_blocks = []

        for i, doc in enumerate(documents):
            # 添加页码标记
            page_num = doc.metadata.get("page", i + 1)
            markdown_parts.append(f"\n\n<!-- Page {page_num} -->\n\n")
            markdown_parts.append(doc.text)

            # 保存文本块信息
            text_blocks.append({
                "block_id": f"block_llama_{i}",
                "content": doc.text,
                "page_num": page_num,
                "metadata": doc.metadata,
            })

        markdown_content = "\n\n---\n\n".join(markdown_parts)

        return markdown_content, text_blocks

    def _parse_with_pymupdf_text(self, pdf_path: str) -> Tuple[str, List[Dict[str, Any]]]:
        """使用 PyMuPDF 提取文本（降级方案）"""
        if self.verbose:
            print("  [PyMuPDF] 提取文本...")

        doc = fitz.open(pdf_path)
        markdown_parts = []
        text_blocks = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")

            if text.strip():
                markdown_parts.append(f"\n\n<!-- Page {page_num + 1} -->\n\n")
                markdown_parts.append(text)

                text_blocks.append({
                    "block_id": f"block_pymupdf_{page_num}",
                    "content": text,
                    "page_num": page_num + 1,
                    "metadata": {},
                })

        doc.close()

        markdown_content = "\n\n---\n\n".join(markdown_parts)
        return markdown_content, text_blocks

    def _extract_images(self, pdf_path: str) -> List[ImageInfo]:
        """使用 PyMuPDF 提取图片"""
        if self.verbose:
            print("  [PyMuPDF] 提取图片...")

        # 使用现有的 PDFParser 提取图片
        # 注意：每次调用都会创建新的解析器实例，确保去重状态重置
        from .pdf_parser import PDFParser
        parser = PDFParser(output_dir=str(self.image_output_dir))
        parsed_doc = parser.parse(pdf_path)

        return parsed_doc.images

    def _map_images_to_markdown(
        self,
        markdown_content: str,
        text_blocks: List[Dict[str, Any]],
        images: List[ImageInfo],
    ) -> List[Dict[str, Any]]:
        """
        建立图片与 Markdown 的关联映射

        策略：
        1. 根据页码匹配
        2. 在 Markdown 中插入图片引用标记
        """
        image_mappings = []

        for img in images:
            # 找到对应页码的文本块
            page_blocks = [
                b for b in text_blocks
                if b.get("page_num") == img.page_num
            ]

            if page_blocks:
                # 使用第一个文本块作为关联
                primary_block = page_blocks[0]

                mapping = {
                    "image_id": img.image_id,
                    "page_num": img.page_num,
                    "block_id": primary_block.get("block_id"),
                    "file_path": img.file_path,
                    "markdown_ref": f"![{img.image_id}]({img.file_path})",
                }
                image_mappings.append(mapping)

        return image_mappings

    def save_result(
        self,
        result: HybridParsedDocument,
        output_path: Optional[str] = None,
    ) -> str:
        """
        保存解析结果

        Args:
            result: 解析结果
            output_path: 输出路径（可选）

        Returns:
            输出文件路径
        """
        if output_path is None:
            pdf_name = Path(result.source_file).stem
            output_path = self.markdown_output_dir / f"{pdf_name}_hybrid.json"

        output_path = Path(output_path)

        # 转换为可序列化的字典
        data = {
            "source_file": result.source_file,
            "markdown_content": result.markdown_content,
            "text_blocks": result.text_blocks,
            "images": [asdict(img) for img in result.images],
            "image_mappings": result.image_mappings,
            "total_pages": result.total_pages,
            "metadata": result.metadata,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        if self.verbose:
            print(f"[已保存] {output_path}")

        return str(output_path)

    def to_llama_documents(self, result: HybridParsedDocument) -> List[Document]:
        """
        将解析结果转换为 LlamaIndex Document 列表

        Args:
            result: 解析结果

        Returns:
            Document 列表
        """
        documents = []

        # 按页码分割 Markdown
        pages = result.markdown_content.split("\n\n---\n\n")

        for i, page_content in enumerate(pages):
            if not page_content.strip():
                continue

            # 提取页码
            page_num = i + 1

            # 查找该页的图片
            page_images = [
                img for img in result.images
                if img.page_num == page_num
            ]

            # 创建 Document
            doc = Document(
                text=page_content,
                metadata={
                    "source_file": result.source_file,
                    "page_num": page_num,
                    "image_ids": [img.image_id for img in page_images],
                    "image_paths": [img.file_path for img in page_images],
                },
            )
            documents.append(doc)

        return documents


def parse_pdf_directory(
    input_dir: str,
    llama_cloud_api_key: Optional[str] = None,
    output_dir: str = "./hybrid_output",
    image_dir: str = "./extracted_images",
) -> List[HybridParsedDocument]:
    """
    批量解析目录下的所有 PDF 文件

    Args:
        input_dir: 输入目录
        llama_cloud_api_key: LlamaCloud API Key
        output_dir: 输出目录
        image_dir: 图片输出目录

    Returns:
        解析结果列表
    """
    input_path = Path(input_dir)
    if not input_path.exists():
        raise FileNotFoundError(f"目录不存在: {input_dir}")

    # 创建解析器
    parser = HybridPDFParser(
        llama_cloud_api_key=llama_cloud_api_key,
        image_output_dir=image_dir,
        markdown_output_dir=output_dir,
    )

    results = []

    # 查找所有 PDF 文件
    pdf_files = list(input_path.glob("*.pdf"))

    print(f"[批量解析] 找到 {len(pdf_files)} 个 PDF 文件")

    for pdf_file in pdf_files:
        try:
            result = parser.parse(str(pdf_file))
            parser.save_result(result)
            results.append(result)
        except Exception as e:
            print(f"[错误] 解析 {pdf_file.name} 失败: {e}")
            continue

    print(f"[完成] 成功解析 {len(results)} 个文件")
    return results


if __name__ == "__main__":
    # 测试解析
    import sys

    if len(sys.argv) < 2:
        print("用法: python hybrid_parser.py <pdf路径>")
        sys.exit(1)

    pdf_path = sys.argv[1]

    parser = HybridPDFParser()
    result = parser.parse(pdf_path)
    parser.save_result(result)

    print(f"\n解析完成:")
    print(f"  源文件: {result.source_file}")
    print(f"  总页数: {result.total_pages}")
    print(f"  Markdown 长度: {len(result.markdown_content)} 字符")
    print(f"  提取图片: {len(result.images)}")
