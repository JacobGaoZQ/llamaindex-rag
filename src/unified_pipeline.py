"""
统一 PDF 处理管道

完整流程：
PDF -> MinerU转Markdown(含目录+图片) -> 章节解析 -> 图片描述 -> 向量索引 -> 查询
"""
import os
import copy
import json
import re
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime

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
    使用 MinerU 将 PDF 转换为 Markdown
    - 自动提取目录结构
    - 自动提取图片（位图+矢量）
    - 生成带图片引用的 Markdown
    """

    def __init__(
        self,
        image_output_dir: str = "./output/images",
        verbose: bool = True,
        lang: str = "ch",
        backend: str = "pipeline",
        parse_method: str = "auto",
    ):
        self.image_output_dir = Path(image_output_dir)
        self.image_output_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.lang = lang
        self.backend = backend
        self.parse_method = parse_method

    def convert(self, pdf_path: str) -> Tuple[str, List[TOCItem], List[ImageInfo], Dict]:
        """
        使用 MinerU 转换 PDF 为 Markdown

        Returns:
            (markdown_content, toc, images, doc_info)
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"文件不存在: {pdf_path}")

        doc_name = pdf_path.stem

        if self.verbose:
            print(f"[MinerU] 转换 {pdf_path.name} -> Markdown...")

        # 1. 使用 MinerU 转换
        mineru_output_dir = self.image_output_dir.parent / "mineru_temp"
        mineru_output_dir.mkdir(parents=True, exist_ok=True)

        self._run_mineru(str(pdf_path), str(mineru_output_dir))

        # 2. 读取 MinerU 输出
        md_dir = mineru_output_dir / doc_name / self.parse_method
        md_file = md_dir / f"{doc_name}.md"
        images_dir = md_dir / "images"

        if not md_file.exists():
            raise RuntimeError(f"MinerU 转换失败，未找到输出: {md_file}")

        markdown_content = md_file.read_text(encoding="utf-8")

        # 3. 提取图片信息并移动到统一目录
        images = self._collect_images(images_dir, doc_name)

        # 4. 更新 Markdown 中的图片路径为统一路径
        markdown_content = self._rewrite_image_paths(markdown_content, images_dir, doc_name)

        # 5. 从 Markdown 中提取目录结构
        toc = self._extract_toc_from_markdown(markdown_content)

        # 6. 读取 content_list 获取文档信息
        doc_info = self._extract_doc_info(md_dir, doc_name, pdf_path)

        # 7. 清理 MinerU 临时目录
        shutil.rmtree(mineru_output_dir, ignore_errors=True)

        if self.verbose:
            print(f"  完成: {len(toc)} 个目录项, {len(images)} 张图片")

        return markdown_content, toc, images, doc_info

    def _run_mineru(self, pdf_path: str, output_dir: str):
        """调用 MinerU 进行 PDF 转换"""
        from mineru.cli.common import (
            convert_pdf_bytes_to_bytes_by_pypdfium2,
            prepare_env,
            read_fn,
        )
        from mineru.data.data_reader_writer import FileBasedDataWriter
        from mineru.utils.enum_class import MakeMode
        from mineru.backend.pipeline.pipeline_analyze import doc_analyze as pipeline_doc_analyze
        from mineru.backend.pipeline.pipeline_middle_json_mkcontent import union_make as pipeline_union_make
        from mineru.backend.pipeline.model_json_to_middle_json import result_to_middle_json as pipeline_result_to_middle_json

        pdf_name = Path(pdf_path).stem
        pdf_bytes = read_fn(pdf_path)
        pdf_bytes = convert_pdf_bytes_to_bytes_by_pypdfium2(pdf_bytes, 0, None)

        # 使用 pipeline 后端进行分析
        if self.verbose:
            print(f"  [MinerU] 正在分析文档布局...")

        infer_results, all_image_lists, all_pdf_docs, lang_list, ocr_enabled_list = (
            pipeline_doc_analyze(
                [pdf_bytes],
                [self.lang],
                parse_method=self.parse_method,
                formula_enable=True,
                table_enable=True,
            )
        )

        model_list = infer_results[0]
        images_list = all_image_lists[0]
        pdf_doc = all_pdf_docs[0]
        _lang = lang_list[0]
        _ocr_enable = ocr_enabled_list[0]

        # 准备输出目录
        local_image_dir, local_md_dir = prepare_env(output_dir, pdf_name, self.parse_method)
        image_writer = FileBasedDataWriter(local_image_dir)
        md_writer = FileBasedDataWriter(local_md_dir)

        if self.verbose:
            print(f"  [MinerU] 正在生成 Markdown...")

        # 生成中间 JSON
        middle_json = pipeline_result_to_middle_json(
            model_list, images_list, pdf_doc, image_writer,
            _lang, _ocr_enable, True
        )

        pdf_info = middle_json["pdf_info"]
        image_dir = str(os.path.basename(local_image_dir))

        # 生成 Markdown
        md_content = pipeline_union_make(pdf_info, MakeMode.MM_MD, image_dir)
        md_writer.write_string(f"{pdf_name}.md", md_content)

        # 生成 content_list（用于辅助提取信息）
        content_list = pipeline_union_make(pdf_info, MakeMode.CONTENT_LIST, image_dir)
        md_writer.write_string(
            f"{pdf_name}_content_list.json",
            json.dumps(content_list, ensure_ascii=False, indent=2),
        )

        if self.verbose:
            print(f"  [MinerU] 输出目录: {local_md_dir}")

    def _collect_images(self, images_dir: Path, doc_name: str) -> List[ImageInfo]:
        """收集 MinerU 提取的图片并移动到统一目录"""
        images = []
        if not images_dir.exists():
            return images

        for idx, img_file in enumerate(sorted(images_dir.glob("*"))):
            if img_file.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.svg'):
                continue

            # 生成统一的图片 ID
            image_id = f"{doc_name}_img_{idx:03d}"
            dest_path = self.image_output_dir / f"{image_id}{img_file.suffix}"

            # 移动图片到统一目录
            shutil.copy2(str(img_file), str(dest_path))

            # 尝试获取图片尺寸
            width, height = 0, 0
            try:
                from PIL import Image
                with Image.open(str(dest_path)) as im:
                    width, height = im.size
            except Exception:
                pass

            images.append(ImageInfo(
                image_id=image_id,
                file_path=str(dest_path),
                page_num=0,  # MinerU 不直接暴露页码，后续从 content_list 补充
                bbox=(0, 0, 0, 0),
                width=width,
                height=height,
                image_type="bitmap",
                caption="",
            ))

        return images

    def _rewrite_image_paths(self, markdown_content: str, images_dir: Path, doc_name: str) -> str:
        """将 Markdown 中 MinerU 生成的图片路径替换为统一路径"""
        if not images_dir.exists():
            return markdown_content

        # MinerU 输出格式: ![](images/xxx.jpg) 或 ![caption](images/xxx.png)
        # 建立原始文件名 → 新路径的映射
        name_map = {}
        for idx, img_file in enumerate(sorted(images_dir.glob("*"))):
            if img_file.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.svg'):
                continue
            image_id = f"{doc_name}_img_{idx:03d}"
            new_path = str(self.image_output_dir / f"{image_id}{img_file.suffix}")
            # 匹配 MinerU 输出的相对路径
            name_map[img_file.name] = new_path

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
                    page_num=0,
                    anchor=anchor,
                ))

        return toc

    def _extract_doc_info(self, md_dir: Path, doc_name: str, pdf_path: Path) -> Dict:
        """从 MinerU 输出中提取文档信息"""
        doc_info = {
            "title": doc_name,
            "author": "",
            "total_pages": 0,
        }

        # 读取 content_list 获取更多信息
        content_list_file = md_dir / f"{doc_name}_content_list.json"
        if content_list_file.exists():
            try:
                with open(content_list_file, "r", encoding="utf-8") as f:
                    content_list = json.load(f)
                if content_list:
                    # 从 content_list 获取最大页码
                    max_page = 0
                    for item in content_list:
                        page = item.get("page_idx", 0)
                        if page > max_page:
                            max_page = page
                    doc_info["total_pages"] = max_page + 1

                    # 获取第一个标题作为文档标题
                    for item in content_list:
                        if item.get("type") == "text" and item.get("text", "").strip():
                            doc_info["title"] = item["text"].strip()
                            break
            except Exception:
                pass

        return doc_info


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


class UnifiedRAGSystem:
    """
    统一 RAG 系统

    完整流程：
    PDF -> Markdown -> 章节 -> 图文关联 -> 图片描述 -> 索引 -> 查询
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
    ):
        self.qwen_api_key = qwen_api_key
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
            image_output_dir=image_output_dir,
            verbose=verbose,
            lang=lang,
        )
        self.markdown_parser = MarkdownParser(verbose=verbose)
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

        # Step 1: PDF -> Markdown (via MinerU)
        markdown_content, toc, images, doc_info = self.pdf_converter.convert(str(pdf_path))
        self.images = images

        # Step 2: Markdown -> 章节
        sections = self.markdown_parser.parse(markdown_content, toc)

        # Step 3: 图文关联（将图片分配到对应章节）
        sections = self._associate_images_to_sections(sections, images, markdown_content)
        self.sections = sections

        # Step 4: 生成图片描述
        if generate_descriptions:
            sections = self._generate_image_descriptions(sections)

        # Step 5: 保存 Markdown 文件（MinerU 生成的已含图片引用）
        md_path = self.persist_dir / f"{pdf_path.stem}.md"
        md_path.write_text(markdown_content, encoding="utf-8")

        return ProcessedDocument(
            source_file=str(pdf_path),
            title=doc_info.get("title", pdf_path.stem),
            toc=toc,
            sections=sections,
            images=images,
            markdown_content=markdown_content,
            metadata={
                "process_time": datetime.now().isoformat(),
                "total_pages": doc_info.get("total_pages", 0),
                "total_images": len(images),
                "total_sections": len(sections),
            }
        )

    def _associate_images_to_sections(
        self, sections: List[Section], images: List[ImageInfo], markdown_content: str
    ) -> List[Section]:
        """根据 Markdown 内容中的图片引用，将图片关联到对应章节"""
        if self.verbose:
            print("[关联] 图片 -> 章节...")

        # 建立图片文件路径到 ImageInfo 的映射
        path_to_image = {}
        for img in images:
            path_to_image[img.file_path] = img
            # 也用文件名做映射
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

        if self.verbose:
            total = sum(len(s.images) for s in sections)
            print(f"  完成: {total} 张图片已关联")

        return sections

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
    output_dir: str = "./output",
    generate_descriptions: bool = True,
) -> UnifiedRAGSystem:
    """
    处理 PDF 并构建索引的便捷函数

    Args:
        pdf_path: PDF 文件路径
        qwen_api_key: DashScope API Key
        output_dir: 输出目录
        generate_descriptions: 是否生成图片描述

    Returns:
        UnifiedRAGSystem: 配置好的 RAG 系统
    """
    rag_system = UnifiedRAGSystem(
        qwen_api_key=qwen_api_key,
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

    if not qwen_api_key:
        print("[错误] 请设置 QWEN_API_KEY 环境变量")
        sys.exit(1)

    # 处理并构建索引
    rag = process_pdf_and_build_index(
        pdf_path,
        qwen_api_key=qwen_api_key,
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
