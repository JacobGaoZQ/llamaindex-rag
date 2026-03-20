"""
文件转 Markdown 转换器
支持 PDF、TXT、Word(docx)、Excel(xlsx)、PowerPoint(pptx) 等主流格式
"""

import os
from pathlib import Path
from typing import Optional


class FileToMarkdownConverter:
    """文件转 Markdown 转换器"""

    def __init__(self):
        self.supported_extensions = {
            '.pdf': self._convert_pdf,
            '.txt': self._convert_txt,
            '.md': self._convert_txt,  # Markdown 文件直接返回
            '.docx': self._convert_docx,
            '.doc': self._convert_docx,  # 旧版 Word 需要额外处理
            '.xlsx': self._convert_xlsx,
            '.xls': self._convert_xlsx,
            '.pptx': self._convert_pptx,
            '.ppt': self._convert_pptx,
            '.csv': self._convert_csv,
            '.html': self._convert_html,
            '.htm': self._convert_html,
        }

    def convert(self, file_path: str, output_path: Optional[str] = None) -> str:
        """
        将文件转换为 Markdown 格式

        Args:
            file_path: 源文件路径
            output_path: 输出文件路径（可选，不指定则只返回内容）

        Returns:
            Markdown 格式的文本内容
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        ext = file_path.suffix.lower()
        if ext not in self.supported_extensions:
            raise ValueError(f"不支持的文件格式: {ext}")

        # 调用对应的转换方法
        markdown_content = self.supported_extensions[ext](file_path)

        # 添加文件头信息
        header = f"# {file_path.stem}\n\n> 来源: `{file_path.name}`\n\n---\n\n"
        markdown_content = header + markdown_content

        # 保存到文件
        if output_path:
            output_path = Path(output_path)
            output_path.write_text(markdown_content, encoding='utf-8')
            print(f"已保存到: {output_path}")

        return markdown_content

    def _convert_pdf(self, file_path: Path) -> str:
        """PDF 转 Markdown"""
        try:
            import pymupdf4llm
            # 使用 pymupdf4llm 转换，保留格式
            md_text = pymupdf4llm.to_markdown(str(file_path))
            return md_text
        except Exception:
            # 回退方案：使用 PyMuPDF 基础提取
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(str(file_path))
                text_parts = []
                for page_num, page in enumerate(doc, 1):
                    text_parts.append(f"## 第 {page_num} 页\n\n")
                    text_parts.append(page.get_text())
                    text_parts.append("\n\n")
                return "".join(text_parts)
            except Exception:
                # 最后回退：尝试作为文本读取
                try:
                    return file_path.read_text(encoding='utf-8')
                except Exception:
                    raise ImportError(
                        "请安装 PDF 处理库: pip install pymupdf4llm 或 pip install PyMuPDF"
                    )

    def _convert_txt(self, file_path: Path) -> str:
        """TXT 文件直接读取"""
        content = file_path.read_text(encoding='utf-8')
        return content

    def _convert_docx(self, file_path: Path) -> str:
        """Word 文档转 Markdown"""
        try:
            import mammoth
            with open(file_path, "rb") as docx_file:
                result = mammoth.convert_to_markdown(docx_file)
                return result.value
        except ImportError:
            # 回退方案：使用 python-docx
            try:
                from docx import Document
                doc = Document(str(file_path))
                text_parts = []
                for para in doc.paragraphs:
                    style = para.style.name.lower()
                    if 'heading 1' in style:
                        text_parts.append(f"# {para.text}\n\n")
                    elif 'heading 2' in style:
                        text_parts.append(f"## {para.text}\n\n")
                    elif 'heading 3' in style:
                        text_parts.append(f"### {para.text}\n\n")
                    else:
                        text_parts.append(f"{para.text}\n\n")

                # 处理表格
                for table in doc.tables:
                    text_parts.append(self._convert_docx_table(table))
                return "".join(text_parts)
            except ImportError:
                raise ImportError(
                    "请安装 Word 处理库: pip install mammoth 或 pip install python-docx"
                )

    def _convert_docx_table(self, table) -> str:
        """将 Word 表格转换为 Markdown 表格"""
        rows = []
        for row in table.rows:
            cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
            rows.append(cells)

        if not rows:
            return ""

        # 生成 Markdown 表格
        md_table = "| " + " | ".join(rows[0]) + " |\n"
        md_table += "| " + " | ".join(["---"] * len(rows[0])) + " |\n"
        for row in rows[1:]:
            md_table += "| " + " | ".join(row) + " |\n"
        return "\n" + md_table + "\n"

    def _convert_xlsx(self, file_path: Path) -> str:
        """Excel 文件转 Markdown"""
        try:
            import pandas as pd
            # 读取所有工作表
            xlsx = pd.ExcelFile(str(file_path))
            text_parts = []

            for sheet_name in xlsx.sheet_names:
                df = pd.read_excel(xlsx, sheet_name=sheet_name)
                text_parts.append(f"## 工作表: {sheet_name}\n\n")
                text_parts.append(df.fillna('').to_markdown(index=False))
                text_parts.append("\n\n")

            return "".join(text_parts)
        except ImportError:
            raise ImportError("请安装 Excel 处理库: pip install pandas openpyxl")

    def _convert_pptx(self, file_path: Path) -> str:
        """PowerPoint 转 Markdown"""
        try:
            from pptx import Presentation
            prs = Presentation(str(file_path))
            text_parts = []

            for slide_num, slide in enumerate(prs.slides, 1):
                text_parts.append(f"## 幻灯片 {slide_num}\n\n")

                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        # 检查是否是标题
                        if shape.is_title:
                            text_parts.append(f"### {shape.text}\n\n")
                        else:
                            text_parts.append(f"{shape.text}\n\n")

                # 处理表格
                for shape in slide.shapes:
                    if shape.has_table:
                        table = shape.table
                        rows = []
                        for row in table.rows:
                            cells = [cell.text.strip() for cell in row.cells]
                            rows.append(cells)
                        if rows:
                            md_table = "| " + " | ".join(rows[0]) + " |\n"
                            md_table += "| " + " | ".join(["---"] * len(rows[0])) + " |\n"
                            for row in rows[1:]:
                                md_table += "| " + " | ".join(row) + " |\n"
                            text_parts.append("\n" + md_table + "\n")

                text_parts.append("---\n\n")

            return "".join(text_parts)
        except ImportError:
            raise ImportError("请安装 PowerPoint 处理库: pip install python-pptx")

    def _convert_csv(self, file_path: Path) -> str:
        """CSV 文件转 Markdown 表格"""
        try:
            import pandas as pd
            df = pd.read_csv(str(file_path))
            return df.fillna('').to_markdown(index=False) + "\n"
        except ImportError:
            # 简单处理：直接读取
            import csv
            text_parts = []
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                rows = list(reader)
                if rows:
                    # 表头
                    text_parts.append("| " + " | ".join(rows[0]) + " |\n")
                    text_parts.append("| " + " | ".join(["---"] * len(rows[0])) + " |\n")
                    for row in rows[1:]:
                        text_parts.append("| " + " | ".join(row) + " |\n")
            return "".join(text_parts)

    def _convert_html(self, file_path: Path) -> str:
        """HTML 文件转 Markdown"""
        try:
            import html2text
            html_content = file_path.read_text(encoding='utf-8')
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = False
            return h.handle(html_content)
        except ImportError:
            raise ImportError("请安装 HTML 转换库: pip install html2text")


def convert_file_to_markdown(file_path: str, output_path: Optional[str] = None) -> str:
    """
    便捷函数：将文件转换为 Markdown

    Args:
        file_path: 源文件路径
        output_path: 输出文件路径（可选）

    Returns:
        Markdown 格式的文本内容
    """
    converter = FileToMarkdownConverter()
    return converter.convert(file_path, output_path)


def convert_directory(input_dir: str, output_dir: str) -> dict:
    """
    批量转换目录下的所有支持文件

    Args:
        input_dir: 输入目录
        output_dir: 输出目录

    Returns:
        转换结果字典 {文件名: 是否成功}
    """
    converter = FileToMarkdownConverter()
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    results = {}
    for file in input_path.iterdir():
        if file.is_file() and file.suffix.lower() in converter.supported_extensions:
            try:
                output_file = output_path / f"{file.stem}.md"
                converter.convert(str(file), str(output_file))
                results[file.name] = True
            except Exception as e:
                print(f"转换失败 {file.name}: {e}")
                results[file.name] = False

    return results


if __name__ == "__main__":
    # 示例用法
    import sys

    if len(sys.argv) < 2:
        print("用法:")
        print("  python file_converter.py <文件路径> [输出路径]")
        print("  python file_converter.py <输入目录> <输出目录> --batch")
        sys.exit(1)

    if len(sys.argv) >= 3 and sys.argv[2] == "--batch":
        # 批量转换模式
        results = convert_directory(sys.argv[1], sys.argv[3] if len(sys.argv) > 3 else "./markdown_output")
        print(f"\n转换完成: {sum(results.values())}/{len(results)} 成功")
    else:
        # 单文件转换模式
        file_path = sys.argv[1]
        output_path = sys.argv[2] if len(sys.argv) > 2 else None
        markdown = convert_file_to_markdown(file_path, output_path)
        if not output_path:
            print(markdown)
