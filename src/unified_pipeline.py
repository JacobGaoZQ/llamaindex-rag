"""
统一 PDF 处理管道

完整流程：
PDF -> MinerU转Markdown(含目录+图片) -> 章节解析 -> 图片描述 -> 向量索引 -> 查询
"""
import os
import copy
import json
import hashlib
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
from .mineru_converter import StableMinerUConverter, MarkdownParser, ProcessedDocument


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
        cache_dir: str = "./output/convert_cache",
        **kwargs,
    ):
        self.qwen_api_key = qwen_api_key
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.image_output_dir = Path(image_output_dir)
        self.verbose = verbose
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 模型配置
        self.llm_model = llm_model
        self.embedding_model = embedding_model
        self.vl_model = vl_model

        # 初始化组件（使用新的稳定转换器）
        self.pdf_converter = StableMinerUConverter(
            image_output_dir=image_output_dir,
            verbose=verbose,
            lang=lang,
            cache_dir=str(self.cache_dir),
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

    def _compute_pdf_hash(self, pdf_path: str) -> str:
        """计算 PDF 文件的 SHA256 哈希值"""
        sha256 = hashlib.sha256()
        with open(pdf_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _get_doc_cache_path(self, pdf_hash: str) -> Path:
        """获取文档级缓存路径"""
        return self.cache_dir / f"{pdf_hash}_processed.json"

    def _load_processed_doc_cache(self, pdf_path: str, generate_descriptions: bool) -> Optional[ProcessedDocument]:
        """
        尝试从缓存加载完整的处理后文档

        Args:
            pdf_path: PDF 文件路径
            generate_descriptions: 是否需要图片描述

        Returns:
            缓存命中返回 ProcessedDocument，否则返回 None
        """
        pdf_hash = self._compute_pdf_hash(pdf_path)
        cache_path = self._get_doc_cache_path(pdf_hash)

        if not cache_path.exists():
            return None

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache_data = json.load(f)

            if cache_data.get("cache_version") != 1:
                return None

            # 如果要求图片描述但缓存中没有，则缓存失效
            if generate_descriptions and not cache_data.get("has_descriptions", False):
                return None

            # 恢复 TOC
            toc = [TOCItem(**item) for item in cache_data.get("toc", [])]

            # 恢复图片信息
            images = []
            for img_data in cache_data.get("images", []):
                img_data = dict(img_data)
                img_data["bbox"] = tuple(img_data["bbox"])
                img_info = ImageInfo(**img_data)
                if not Path(img_info.file_path).exists():
                    if self.verbose:
                        print(f"  [文档缓存] 图片文件缺失: {img_info.file_path}，缓存失效")
                    return None
                images.append(img_info)

            # 恢复章节
            sections = []
            for sec_data in cache_data.get("sections", []):
                sec_images = []
                for sid in sec_data.get("images", []):
                    sid = dict(sid)
                    sid["bbox"] = tuple(sid["bbox"])
                    sec_images.append(ImageInfo(**sid))

                sec_descs = []
                for desc_data in sec_data.get("image_descriptions", []):
                    sec_descs.append(ImageDescription(**desc_data))

                sections.append(Section(
                    section_id=sec_data["section_id"],
                    level=sec_data["level"],
                    title=sec_data["title"],
                    content=sec_data["content"],
                    page_num=sec_data["page_num"],
                    anchor=sec_data["anchor"],
                    images=sec_images,
                    image_descriptions=sec_descs,
                ))

            # 恢复图片描述映射
            self.image_descriptions = {
                k: ImageDescription(**v)
                for k, v in cache_data.get("image_descriptions_map", {}).items()
            }
            self.images = images
            self.sections = sections

            # 读取 Markdown 内容（优先从缓存文件，回退到 persist_dir）
            markdown_content = ""
            cached_md_file = cache_data.get("markdown_file")
            if cached_md_file:
                cached_md_path = self.cache_dir / cached_md_file
                if cached_md_path.exists():
                    markdown_content = cached_md_path.read_text(encoding="utf-8")

            if not markdown_content:
                md_path = self.persist_dir / f"{Path(pdf_path).stem}.md"
                if md_path.exists():
                    markdown_content = md_path.read_text(encoding="utf-8")

            if not markdown_content:
                if self.verbose:
                    print(f"  [文档缓存] Markdown 内容缺失，缓存失效")
                return None

            if self.verbose:
                print(f"  [文档缓存] 命中缓存，跳过全部处理流程")

            return ProcessedDocument(
                source_file=str(pdf_path),
                title=cache_data.get("title", Path(pdf_path).stem),
                toc=toc,
                sections=sections,
                images=images,
                markdown_content=markdown_content,
                metadata=cache_data.get("metadata", {}),
            )

        except Exception as e:
            if self.verbose:
                print(f"  [文档缓存] 加载失败: {e}，将重新处理")
            return None

    def _save_processed_doc_cache(
        self, pdf_path: str, doc: ProcessedDocument, has_descriptions: bool
    ):
        """将处理后的文档保存到缓存"""
        try:
            pdf_hash = self._compute_pdf_hash(pdf_path)
            doc_name = Path(pdf_path).stem

            # 单独保存 Markdown 内容（避免 JSON 过大）
            md_filename = f"{pdf_hash}_{doc_name}_processed.md"
            md_cache_path = self.cache_dir / md_filename
            md_cache_path.write_text(doc.markdown_content, encoding="utf-8")

            cache_data = {
                "cache_version": 1,
                "pdf_hash": pdf_hash,
                "source_file": str(pdf_path),
                "title": doc.title,
                "has_descriptions": has_descriptions,
                "markdown_file": md_filename,
                "toc": [asdict(item) for item in doc.toc],
                "images": [asdict(img) for img in doc.images],
                "sections": [asdict(sec) for sec in doc.sections],
                "image_descriptions_map": {
                    k: asdict(v) for k, v in self.image_descriptions.items()
                },
                "metadata": doc.metadata,
            }

            cache_path = self._get_doc_cache_path(pdf_hash)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)

            if self.verbose:
                print(f"  [文档缓存] 已保存: {cache_path.name}")

        except Exception as e:
            if self.verbose:
                print(f"  [文档缓存] 保存失败: {e}")

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

        # 尝试从文档级缓存加载
        cached_doc = self._load_processed_doc_cache(str(pdf_path), generate_descriptions)
        if cached_doc is not None:
            # 确保 Markdown 文件存在
            md_path = self.persist_dir / f"{pdf_path.stem}.md"
            if not md_path.exists() and cached_doc.markdown_content:
                md_path.write_text(cached_doc.markdown_content, encoding="utf-8")
            return cached_doc

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

        # Step 5: 保存 Markdown 文件
        md_path = self.persist_dir / f"{pdf_path.stem}.md"
        md_path.write_text(markdown_content, encoding="utf-8")

        processed_doc = ProcessedDocument(
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

        # 保存到文档级缓存
        self._save_processed_doc_cache(str(pdf_path), processed_doc, generate_descriptions)

        return processed_doc

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

    def _is_index_valid(self, processed_docs: list) -> bool:
        """
        检查已有索引是否与当前文档集一致（通过比对 PDF 文件哈希）

        Returns:
            True 表示索引有效可复用，False 表示需要重建
        """
        index_hash_path = self.persist_dir / "index_source_hashes.json"
        if not index_hash_path.exists():
            return False

        # 检查向量索引文件是否存在
        if not (self.persist_dir / "default__vector_store.json").exists():
            return False

        try:
            with open(index_hash_path, "r", encoding="utf-8") as f:
                saved = json.load(f)

            saved_hashes = saved.get("source_hashes", {})
            if not saved_hashes:
                return False

            # 对比每个源文件的哈希
            current_files = {doc.source_file for doc in processed_docs}
            if current_files != set(saved_hashes.keys()):
                return False

            for doc in processed_docs:
                current_hash = self._compute_pdf_hash(doc.source_file)
                if saved_hashes.get(doc.source_file) != current_hash:
                    return False

            return True

        except Exception as e:
            if self.verbose:
                print(f"  [索引校验] 读取失败: {e}")
            return False

    def _save_index_hashes(self, processed_docs: list):
        """保存构建索引所用 PDF 文件的哈希值"""
        try:
            hashes = {
                doc.source_file: self._compute_pdf_hash(doc.source_file)
                for doc in processed_docs
            }
            index_hash_path = self.persist_dir / "index_source_hashes.json"
            with open(index_hash_path, "w", encoding="utf-8") as f:
                json.dump({"source_hashes": hashes}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            if self.verbose:
                print(f"  [索引校验] 保存哈希失败: {e}")

    def build_index(self, processed_docs, force: bool = False) -> VectorStoreIndex:
        """
        构建向量索引（支持单个或多个文档）

        Args:
            processed_docs: 处理后的文档（单个 ProcessedDocument 或列表）

        Returns:
            VectorStoreIndex: 向量索引
        """
        # 支持单个文档输入
        if isinstance(processed_docs, ProcessedDocument):
            processed_docs = [processed_docs]

        if self.verbose:
            print("=" * 60)
            print(f"构建向量索引（{len(processed_docs)} 个文档）")
            print("=" * 60)

        # 校验已有索引是否有效，有效则直接加载，跳过 Embedding
        if not force and self._is_index_valid(processed_docs):
            if self.verbose:
                print("  [索引缓存] PDF 未变化，加载已有索引，跳过 Embedding...")
            if self.load_index():
                if self.verbose:
                    print("  [索引缓存] 已有索引加载成功")
                return self.index
            if self.verbose:
                print("  [索引缓存] 加载失败，重新构建...")

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

        # 处理每个文档
        for doc_idx, processed_doc in enumerate(processed_docs):
            doc_title = processed_doc.title
            doc_name = Path(processed_doc.source_file).stem
            if self.verbose:
                print(f"[{doc_idx+1}/{len(processed_docs)}] 处理文档: {doc_title}")

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
                    doc_id=f"{doc_name}_{section.section_id}",
                    metadata={
                        "source_file": processed_doc.source_file,
                        "doc_title": doc_title,
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
文档标题: {doc_title}
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
                        "source_file": processed_doc.source_file,
                        "doc_title": doc_title,
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

        # 5. 保存索引来源哈希（用于下次校验）
        self._save_index_hashes(processed_docs)

        # 6. 保存元数据（确保包含完整的图片描述信息）
        metadata = {
            "documents": [
                {
                    "source_file": doc.source_file,
                    "title": doc.title,
                    "sections": [asdict(s) for s in doc.sections],
                    "images": [asdict(img) for img in doc.images],
                }
                for doc in processed_docs
            ],
            "image_descriptions": {k: asdict(v) for k, v in self.image_descriptions.items()},
            "total_images": len(self.images),
            "total_descriptions": len(self.image_descriptions),
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

                # 恢复图片列表
                self.images = []
                for doc_data in metadata.get("documents", []):
                    for img_data in doc_data.get("images", []):
                        img_data = dict(img_data)
                        img_data["bbox"] = tuple(img_data["bbox"])
                        self.images.append(ImageInfo(**img_data))

                # 恢复图片描述（加强错误处理和兼容性）
                try:
                    image_desc_data = metadata.get("image_descriptions", {})
                    self.image_descriptions = {}
                    for k, v in image_desc_data.items():
                        try:
                            if isinstance(v, dict):
                                # 处理可能缺失的字段，提供默认值
                                desc_data = {
                                    "image_id": v.get("image_id", k),
                                    "description": v.get("description", ""),
                                    "category": v.get("category", "unknown"),
                                    "keywords": v.get("keywords", []),
                                    "confidence": float(v.get("confidence", 0.0)),
                                    "ocr_text": v.get("ocr_text", ""),
                                }
                                self.image_descriptions[k] = ImageDescription(**desc_data)
                        except Exception as inner_e:
                            if self.verbose:
                                print(f"  [警告] 恢复单个图片描述失败 {k}: {inner_e}")
                    
                    if self.verbose:
                        print(f"  [元数据] 成功恢复 {len(self.image_descriptions)} 个图片描述")
                except Exception as e:
                    if self.verbose:
                        print(f"  [错误] 恢复图片描述时出错: {e}")
                    self.image_descriptions = {}

            if self.verbose:
                print(f"索引已加载: {self.persist_dir}")

            return True

        except Exception as e:
            if self.verbose:
                print(f"加载索引失败: {e}")
            return False

    def _extract_markdown_section(self, md_path: Path, section_keywords: List[str]) -> str:
        """
        从 Markdown 文件中提取包含指定关键词的完整章节内容。
        用于补充向量检索中被拆散的章节，确保 LLM 获得完整上下文。
        """
        if not md_path.exists():
            return ""

        content = md_path.read_text(encoding="utf-8")
        lines = content.split("\n")

        # 查找匹配的行
        best_start = -1
        best_title = ""
        for i, line in enumerate(lines):
            # 匹配一级或二级标题
            if line.startswith("# ") or line.startswith("## "):
                title = line.lstrip("# ").strip()
                for kw in section_keywords:
                    if kw in title:
                        if best_start == -1 or len(title) < len(best_title):
                            best_start = i
                            best_title = title

        if best_start == -1:
            return ""

        # 确定提取范围：从匹配标题到下一个不相关的同级或更高级标题
        continuation_keywords = set(section_keywords + [
            "步骤", "注意", "警示", "警告", "调整", "拆卸", "安装",
            "位置", "地面", "门端差", "初始设置", "最终检查", "NOTE",
            "需要", "工具", "分离", "固定", "连接", "卡环",
        ])

        end = len(lines)
        initial_level = lines[best_start].find(" ")  # # 后面第一个空格的位置即标题级别

        for i in range(best_start + 1, len(lines)):
            line = lines[i]
            if line.startswith("# "):
                title = line.lstrip("# ").strip()
                # 如果标题不包含任何延续关键词，则视为章节结束
                if not any(kw in title for kw in continuation_keywords):
                    end = i
                    break

        section_text = "\n".join(lines[best_start:end]).strip()
        return section_text

    def _get_complete_section_context(self, query_text: str, text_nodes: list) -> List[str]:
        """
        根据查询意图，从原始 Markdown 文件中提取完整章节作为补充上下文。
        """
        import re

        query_lower = query_text.lower()
        extra_contexts = []
        processed_files = set()

        # 定义查询意图到章节关键词的映射
        intent_map = [
            (["安装步骤", "怎么安装", "如何安装", "安装方法", "装冰箱"], ["安装步骤"]),
            (["拆卸冰箱门", "拆门", "门太大"], ["拆卸冰箱门方便进出", "拆卸冰箱门"]),
            (["安全信息", "安全注意", "警告", "警示"], ["安全信息", "安全说明须知", "重要安全注意事项"]),
            (["操作", "功能面板", "分配器", "SmartThings"], ["操作", "功能面板"]),
            (["维护", "清洁", "附件"], ["维护", "清洁", "移动和维护附件"]),
            (["故障排除", "异常声音", "不制冷"], ["故障排除", "异常声音"]),
        ]

        matched_keywords = None
        for intent_keywords, section_keywords in intent_map:
            if any(kw in query_lower for kw in intent_keywords):
                matched_keywords = section_keywords
                break

        if not matched_keywords:
            return extra_contexts

        # 从检索到的节点中找出对应的源文件
        for node in text_nodes:
            metadata = node.node.metadata if hasattr(node.node, 'metadata') else {}
            source_file = metadata.get("source_file", "")
            if not source_file:
                continue
            doc_name = Path(source_file).stem
            md_path = self.persist_dir / f"{doc_name}.md"
            if md_path in processed_files:
                continue
            processed_files.add(md_path)

            section_text = self._extract_markdown_section(md_path, matched_keywords)
            if section_text:
                extra_contexts.append(section_text)

        return extra_contexts

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

        # 配置 LLM（增大 max_tokens 以确保回答完整）
        llm = DashScope(model_name=self.llm_model, api_key=self.qwen_api_key, max_tokens=4096)

        # 创建查询引擎
        query_engine = self.index.as_query_engine(
            llm=llm,
            similarity_top_k=20,
        )

        # 执行检索
        retriever = self.index.as_retriever(similarity_top_k=20)
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

        # 收集图片（建立 image_id -> ImageInfo 的查找表）
        img_info_map = {img.image_id: img for img in self.images}
        all_images = []
        seen_image_ids = set()

        for node in text_nodes:
            metadata = node.node.metadata if hasattr(node.node, 'metadata') else {}
            image_ids_str = metadata.get("image_ids", "")
            image_ids = [i for i in image_ids_str.split(",") if i] if image_ids_str else []
            for img_id in image_ids:
                if img_id not in seen_image_ids:
                    img_info = img_info_map.get(img_id)
                    img_data = {
                        "image_id": img_id,
                        "file_path": img_info.file_path if img_info else None,
                    }
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
                img_info = img_info_map.get(image_id)
                img_data = {
                    "image_id": image_id,
                    "file_path": img_info.file_path if img_info else metadata.get("image_path"),
                }
                if image_id in self.image_descriptions:
                    desc = self.image_descriptions[image_id]
                    img_data["description"] = desc.description
                    img_data["category"] = desc.category
                all_images.append(img_data)
                seen_image_ids.add(image_id)

        # 构建上下文：增加节点数量以覆盖更完整的信息
        context_parts = []
        for node in text_nodes[:15]:
            if node.node.text:
                context_parts.append(node.node.text)

        for node in image_desc_nodes[:5]:
            if node.node.text:
                context_parts.append(f"[图片信息]\n{node.node.text}")

        # 补充：从原始 Markdown 中提取完整章节，避免节点碎片化导致信息缺失
        complete_sections = self._get_complete_section_context(query_text, text_nodes)
        if complete_sections:
            # 将完整章节放在最前面，确保 LLM 能看到连贯的内容
            context_parts = ["[完整章节]\n\n" + "\n\n".join(complete_sections)] + context_parts

        # 生成回答
        if context_parts:
            context_text = "\n\n---\n\n".join(context_parts)

            # 构建图片信息用于提示
            img_info_parts = []
            for i, img_data in enumerate(all_images[:10], 1):
                img_info = f"图片 {i}: ID={img_data['image_id']}"
                if img_data.get("description"):
                    img_info += f", 描述={img_data['description'][:150]}"
                if img_data.get("keywords"):
                    img_info += f", 关键词={', '.join(img_data['keywords'][:5])}"
                img_info_parts.append(img_info)

            img_context = "\n".join(img_info_parts) if img_info_parts else "无相关图片"

            prompt = f"""你是一个专业的智能家居产品说明书助手。基于以下参考信息回答用户问题。

【重要要求】
1. 回答必须完整、详细，列出所有相关步骤和注意事项，不要遗漏任何内容。
2. 如果内容包含多个步骤，请按顺序编号（1. 2. 3. ...）。
3. 当某个步骤有相关图片时，请**立即在该步骤的文本后面**插入图片引用标记 `[图片: image_id]`，不要等全部说完再统一放图片。
4. 如果一张图片对应多个步骤，请在最相关的那个步骤后面引用。
5. 确保图片引用标记的 image_id 与下方【相关图片信息】中的 ID 完全一致。

参考文本信息：
{context_text}

相关图片信息：
{img_context}

问题：{query_text}

请给出完整、详细、准确的回答，并在适当位置插入图片引用标记："""

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
