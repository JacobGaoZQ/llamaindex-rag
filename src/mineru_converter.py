"""
MinerU PDF 转 Markdown 转换器（稳定版本）
使用 MinerU 官方接口，支持完整的图文信息提取
"""
import os
import json
import shutil
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
import subprocess
import tempfile

from .image_descriptor import ImageDescription


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
    bbox: Tuple[float, float, float, float]  # (x0, y0, x1, y1)
    width: int
    height: int
    image_type: str = "bitmap"
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
    images: List[ImageInfo] = None
    image_descriptions: List[ImageDescription] = None
    
    def __post_init__(self):
        if self.images is None:
            self.images = []
        if self.image_descriptions is None:
            self.image_descriptions = []


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


class StableMinerUConverter:
    """
    稳定的 MinerU 转换器
    使用 MinerU CLI 命令行接口，避免依赖内部模块
    """

    def __init__(
        self,
        image_output_dir: str = "./output/images",
        verbose: bool = True,
        lang: str = "ch",
        parse_method: str = "auto",
    ):
        self.image_output_dir = Path(image_output_dir)
        self.image_output_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.lang = lang
        self.parse_method = parse_method

    def convert(self, pdf_path: str) -> Tuple[str, List[TOCItem], List[ImageInfo], Dict]:
        """
        使用 MinerU CLI 转换 PDF 为 Markdown
        
        Args:
            pdf_path: PDF 文件路径
            
        Returns:
            (markdown_content, toc, images, doc_info)
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"文件不存在: {pdf_path}")

        doc_name = pdf_path.stem

        if self.verbose:
            print(f"[MinerU] 转换 {pdf_path.name} -> Markdown...")

        # 使用临时目录
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # 1. 调用 MinerU CLI
            success = self._run_mineru_cli(str(pdf_path), str(temp_path))
            if not success:
                raise RuntimeError("MinerU 转换失败")

            # 2. 查找输出文件
            output_dirs = list(temp_path.glob(f"{doc_name}*"))
            if not output_dirs:
                raise RuntimeError("未找到 MinerU 输出目录")
            
            # 通常输出在 {doc_name}/{parse_method}/ 目录下
            md_dir = temp_path / doc_name / self.parse_method
            if not md_dir.exists():
                # 备选：直接在 temp_path 下查找
                md_dir = temp_path / doc_name
            
            md_file = md_dir / f"{doc_name}.md"
            images_dir = md_dir / "images"
            content_list_file = md_dir / f"{doc_name}_content_list.json"

            if not md_file.exists():
                raise RuntimeError(f"未找到 Markdown 文件: {md_file}")

            # 3. 读取内容
            markdown_content = md_file.read_text(encoding="utf-8")

            # 4. 提取图片信息
            images = self._collect_images(images_dir, doc_name, content_list_file)

            # 5. 更新 Markdown 中的图片路径
            markdown_content = self._rewrite_image_paths(markdown_content, images_dir, doc_name)

            # 6. 提取目录结构
            toc = self._extract_toc_from_markdown(markdown_content)

            # 7. 提取文档信息
            doc_info = self._extract_doc_info(content_list_file, doc_name, pdf_path)

        if self.verbose:
            print(f"  完成: {len(toc)} 个目录项, {len(images)} 张图片")

        return markdown_content, toc, images, doc_info

    def _run_mineru_cli(self, pdf_path: str, output_dir: str) -> bool:
        """调用 MinerU CLI 命令"""
        try:
            # MinerU CLI 命令路径
            mineru_path = "/home/codespace/.local/lib/python3.12/site-packages/bin/mineru"
            
            # 检查命令是否存在
            if not os.path.exists(mineru_path):
                # 尝试在 PATH 中查找
                import shutil
                mineru_path = shutil.which("mineru")
                if not mineru_path:
                    raise FileNotFoundError("未找到 mineru 命令")
            
            # MinerU CLI 命令（使用正确的参数）
            cmd = [
                mineru_path,
                "-p", pdf_path,           # --path
                "-o", output_dir,         # --output
                "-l", self.lang,          # --lang
                "-m", self.parse_method,  # --method
                "-b", "pipeline",         # --backend
            ]
            
            if self.verbose:
                print(f"  执行命令: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5分钟超时
            )
            
            if result.returncode != 0:
                if self.verbose:
                    print(f"  MinerU 错误输出: {result.stderr}")
                return False
                
            return True
            
        except subprocess.TimeoutExpired:
            if self.verbose:
                print("  [错误] MinerU 执行超时")
            return False
        except FileNotFoundError:
            raise RuntimeError("未找到 mineru 命令，请确保已安装 MinerU")
        except Exception as e:
            if self.verbose:
                print(f"  [错误] 执行 MinerU 失败: {e}")
            return False

    def _collect_images(
        self, 
        images_dir: Path, 
        doc_name: str, 
        content_list_file: Path
    ) -> List[ImageInfo]:
        """收集图片并补全页码和位置信息"""
        images = []
        
        if not images_dir.exists():
            return images

        # 先收集所有图片文件
        image_files = sorted(images_dir.glob("*"))
        image_file_map = {}  # 原始文件名 -> 图片信息
        
        for idx, img_file in enumerate(image_files):
            if img_file.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.svg'):
                continue

            # 生成统一的图片 ID
            image_id = f"{doc_name}_img_{idx:03d}"
            dest_path = self.image_output_dir / f"{image_id}{img_file.suffix}"

            # 移动图片到统一目录
            shutil.copy2(str(img_file), str(dest_path))

            # 获取图片尺寸
            width, height = 0, 0
            try:
                from PIL import Image
                with Image.open(str(dest_path)) as im:
                    width, height = im.size
            except Exception:
                pass

            img_info = ImageInfo(
                image_id=image_id,
                file_path=str(dest_path),
                page_num=0,  # 后续从 content_list 补充
                bbox=(0, 0, 0, 0),
                width=width,
                height=height,
                image_type="bitmap",
                caption="",
            )
            
            images.append(img_info)
            image_file_map[img_file.name] = img_info

        # 从 content_list 补充页码和位置信息
        if content_list_file.exists():
            self._supplement_image_info_from_content_list(
                content_list_file, image_file_map
            )

        return images

    def _supplement_image_info_from_content_list(
        self, 
        content_list_file: Path, 
        image_file_map: Dict[str, ImageInfo]
    ):
        """从 content_list.json 补充图片的页码和位置信息"""
        try:
            with open(content_list_file, "r", encoding="utf-8") as f:
                content_list = json.load(f)
            
            # 遍历 content_list，找到图片条目
            for item in content_list:
                if item.get("type") == "image":
                    # 获取图片信息
                    img_body = item.get("img_body", {})
                    page_idx = item.get("page_idx", 0)
                    bbox = img_body.get("bbox", [0, 0, 0, 0])
                    
                    # MinerU 的 bbox 是 [x0, y0, x1, y1] 格式
                    if len(bbox) == 4:
                        x0, y0, x1, y1 = bbox
                        normalized_bbox = (float(x0), float(y0), float(x1), float(y1))
                    else:
                        normalized_bbox = (0, 0, 0, 0)
                    
                    # 根据原始文件名匹配图片
                    raw_filename = item.get("raw_filename", "")
                    if raw_filename and raw_filename in image_file_map:
                        img_info = image_file_map[raw_filename]
                        img_info.page_num = page_idx + 1  # 转为 1-based
                        img_info.bbox = normalized_bbox
                        
        except Exception as e:
            if self.verbose:
                print(f"  [警告] 解析 content_list 失败: {e}")

    def _rewrite_image_paths(self, markdown_content: str, images_dir: Path, doc_name: str) -> str:
        """重写 Markdown 中的图片路径为统一路径"""
        if not images_dir.exists():
            return markdown_content

        # 建立原始文件名到新路径的映射
        name_map = {}
        for idx, img_file in enumerate(sorted(images_dir.glob("*"))):
            if img_file.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.svg'):
                continue
            image_id = f"{doc_name}_img_{idx:03d}"
            new_path = str(self.image_output_dir / f"{image_id}{img_file.suffix}")
            name_map[img_file.name] = new_path

        import re
        
        def replace_image_ref(match):
            alt_text = match.group(1)
            old_path = match.group(2)
            filename = Path(old_path).name
            if filename in name_map:
                return f"![{alt_text}]({name_map[filename]})"
            return match.group(0)

        # 替换 ![xxx](images/yyy.png) 格式
        markdown_content = re.sub(
            r'!\[([^\]]*)\]\(([^)]+)\)',
            replace_image_ref,
            markdown_content
        )

        return markdown_content

    def _extract_toc_from_markdown(self, markdown_content: str) -> List[TOCItem]:
        """从 Markdown 标题中提取目录结构"""
        import re
        toc = []
        lines = markdown_content.split("\n")

        for line in lines:
            match = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                anchor = re.sub(r'[^\w\s-]', '', title.lower())
                anchor = re.sub(r'[-\s]+', '-', anchor).strip('-')
                toc.append(TOCItem(
                    level=level,
                    title=title,
                    page_num=0,  # Markdown 中无页码信息
                    anchor=anchor,
                ))

        return toc

    def _extract_doc_info(self, content_list_file: Path, doc_name: str, pdf_path: Path) -> Dict:
        """从 content_list 提取文档信息"""
        doc_info = {
            "title": doc_name,
            "author": "",
            "total_pages": 0,
        }

        if content_list_file.exists():
            try:
                with open(content_list_file, "r", encoding="utf-8") as f:
                    content_list = json.load(f)
                
                if content_list:
                    # 获取最大页码
                    max_page = 0
                    for item in content_list:
                        page = item.get("page_idx", 0)
                        if page > max_page:
                            max_page = page
                    doc_info["total_pages"] = max_page + 1

                    # 获取第一个文本作为标题候选
                    for item in content_list:
                        if item.get("type") == "text" and item.get("text", "").strip():
                            doc_info["title"] = item["text"].strip()
                            break
            except Exception:
                pass

        return doc_info


class MarkdownParser:
    """Markdown 解析器"""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose

    def parse(self, markdown_content: str, toc: List[TOCItem]) -> List[Section]:
        """解析 Markdown 为章节"""
        if self.verbose:
            print("[解析] Markdown -> 章节...")

        if not toc:
            return [Section(
                section_id="sec_001",
                level=1,
                title="正文",
                content=markdown_content,
                page_num=1,
                anchor="",
            )]

        sections = []
        lines = markdown_content.split("\n")
        current_section = None
        current_content = []
        section_idx = 0

        # 创建标题匹配模式
        import re
        heading_patterns = []
        for item in toc:
            pattern = r"^" + "#" * item.level + r"\s+" + re.escape(item.title) + r"\s*$"
            heading_patterns.append((re.compile(pattern, re.IGNORECASE), item))

        current_page = 1

        for line in lines:
            # 检测页码标记
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

        if not sections:
            sections = [Section(
                section_id="sec_001",
                level=1,
                title="正文",
                content=markdown_content,
                page_num=1,
                anchor="",
            )]

        if self.verbose:
            print(f"  完成: {len(sections)} 个章节")

        return sections


# 便捷函数
def convert_pdf_with_mineru(
    pdf_path: str,
    output_dir: str = "./output",
    image_dir: str = "./output/images",
    generate_descriptions: bool = True,
    qwen_api_key: Optional[str] = None,
) -> ProcessedDocument:
    """
    使用 MinerU 转换 PDF 并处理为结构化文档
    
    Args:
        pdf_path: PDF 文件路径
        output_dir: 输出目录
        image_dir: 图片输出目录
        generate_descriptions: 是否生成图片描述
        qwen_api_key: 通义千问 API Key（用于图片描述）
        
    Returns:
        ProcessedDocument: 处理后的文档
    """
    from .image_descriptor import ImageDescriptor
    
    # 1. 转换 PDF
    converter = StableMinerUConverter(
        image_output_dir=image_dir,
        verbose=True
    )
    
    markdown_content, toc, images, doc_info = converter.convert(pdf_path)
    
    # 2. 解析为章节
    parser = MarkdownParser(verbose=True)
    sections = parser.parse(markdown_content, toc)
    
    # 3. 图文关联
    sections = _associate_images_to_sections(sections, images, markdown_content)
    
    # 4. 生成图片描述（可选）
    if generate_descriptions and qwen_api_key:
        image_descriptor = ImageDescriptor(api_key=qwen_api_key, model="qwen-vl-max")
        sections = _generate_image_descriptions(sections, image_descriptor)
    
    # 5. 保存 Markdown 文件
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    md_file = output_path / f"{Path(pdf_path).stem}.md"
    md_file.write_text(markdown_content, encoding="utf-8")
    
    return ProcessedDocument(
        source_file=str(pdf_path),
        title=doc_info.get("title", Path(pdf_path).stem),
        toc=toc,
        sections=sections,
        images=images,
        markdown_content=markdown_content,
        metadata={
            "total_pages": doc_info.get("total_pages", 0),
            "total_images": len(images),
            "total_sections": len(sections),
        }
    )


def _associate_images_to_sections(
    sections: List[Section], 
    images: List[ImageInfo], 
    markdown_content: str
) -> List[Section]:
    """根据 Markdown 内容中的图片引用关联图片到章节"""
    import re
    
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


def _generate_image_descriptions(
    sections: List[Section], 
    image_descriptor
) -> List[Section]:
    """为章节中的图片生成描述"""
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
    descriptions = image_descriptor.batch_describe(
        all_images,
        max_workers=3
    )

    # 建立描述映射
    desc_map = {d.image_id: d for d in descriptions}

    # 将描述关联到章节
    for section in sections:
        section.image_descriptions = [
            desc_map[img.image_id] for img in section.images
            if img.image_id in desc_map
        ]

    return sections