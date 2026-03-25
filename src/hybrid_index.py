"""
基于混合解析结果的索引构建器
支持 Markdown 结构化和多模态图片
"""
import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from llama_index.core import (
    VectorStoreIndex,
    Document,
    Settings,
    StorageContext,
)
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.storage.docstore import SimpleDocumentStore
from llama_index.core.storage.index_store import SimpleIndexStore
from llama_index.core.vector_stores.simple import SimpleVectorStore

from llama_index.embeddings.dashscope import DashScopeEmbedding
from llama_index.llms.dashscope import DashScope

from .hybrid_parser import HybridParsedDocument, HybridPDFParser
from .pdf_parser import ImageInfo


class HybridIndexBuilder:
    """
    混合索引构建器
    基于 Markdown 结构化和图片信息构建多模态索引
    """

    def __init__(
        self,
        api_key: str,
        persist_dir: str = "./hybrid_index",
        embedding_model: str = "text-embedding-v4",
        llm_model: str = "qwen-flash",
    ):
        """
        初始化索引构建器

        Args:
            api_key: DashScope API Key
            persist_dir: 索引持久化目录
            embedding_model: 嵌入模型名称
            llm_model: LLM 模型名称
        """
        self.api_key = api_key
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        # 配置模型
        self._setup_models(embedding_model, llm_model)

        # 元数据存储
        self.image_metadata: Dict[str, ImageInfo] = {}
        self.node_image_map: Dict[str, List[str]] = {}

    def _setup_models(self, embedding_model: str, llm_model: str):
        """配置嵌入模型和 LLM"""
        # 嵌入模型
        self.embed_model = DashScopeEmbedding(
            model_name=embedding_model,
            api_key=self.api_key,
            embed_batch_size=10,
        )

        # LLM
        self.llm = DashScope(
            model_name=llm_model,
            api_key=self.api_key,
        )

        # 全局设置
        Settings.embed_model = self.embed_model
        Settings.llm = self.llm
        Settings.embed_batch_size = 10

    def build_index(
        self,
        parsed_docs: List[HybridParsedDocument],
    ) -> VectorStoreIndex:
        """
        从解析后的文档构建索引

        Args:
            parsed_docs: 混合解析后的文档列表

        Returns:
            VectorStoreIndex 实例
        """
        print(f"[构建索引] 处理 {len(parsed_docs)} 个文档...")

        # 收集所有 Document
        all_documents = []

        for doc in parsed_docs:
            documents = self._convert_to_documents(doc)
            all_documents.extend(documents)

            # 收集图片元数据
            for img in doc.images:
                self.image_metadata[img.image_id] = img

        print(f"  - 总文档片段: {len(all_documents)}")
        print(f"  - 总图片数: {len(self.image_metadata)}")

        # 使用 Markdown 节点解析器
        node_parser = MarkdownNodeParser()

        # 创建存储上下文
        storage_context = StorageContext.from_defaults(
            docstore=SimpleDocumentStore(),
            vector_store=SimpleVectorStore(),
            index_store=SimpleIndexStore(),
        )

        # 构建索引
        index = VectorStoreIndex.from_documents(
            all_documents,
            storage_context=storage_context,
            node_parser=node_parser,
        )

        # 建立节点-图片映射
        self._build_node_image_map(index, all_documents)

        print(f"  - 索引节点数: {len(index.docstore.docs)}")
        print(f"  - 带图片的节点: {sum(1 for imgs in self.node_image_map.values() if imgs)}")

        return index

    def _convert_to_documents(
        self,
        parsed_doc: HybridParsedDocument,
    ) -> List[Document]:
        """将解析结果转换为 LlamaIndex Document"""
        documents = []

        # 按页码组织内容
        page_contents: Dict[int, List[str]] = {}
        page_images: Dict[int, List[str]] = {}

        # 解析 Markdown 中的页码标记
        current_page = 1
        lines = parsed_doc.markdown_content.split("\n")
        current_content = []

        for line in lines:
            # 检测页码标记 <!-- Page X -->
            if line.strip().startswith("<!-- Page") and "-->" in line:
                # 保存前一页的内容
                if current_content:
                    page_contents[current_page] = ["\n".join(current_content)]
                    current_content = []

                # 提取页码
                try:
                    page_str = line.split("Page")[1].split("-->")[0].strip()
                    current_page = int(page_str)
                except (IndexError, ValueError):
                    current_page += 1
            else:
                current_content.append(line)

        # 保存最后一页
        if current_content:
            page_contents[current_page] = ["\n".join(current_content)]

        # 收集每页的图片
        for img in parsed_doc.images:
            if img.page_num not in page_images:
                page_images[img.page_num] = []
            page_images[img.page_num].append(img.image_id)

        # 创建 Document（按页）
        all_pages = sorted(set(list(page_contents.keys()) + list(page_images.keys())))

        for page_num in all_pages:
            content_parts = page_contents.get(page_num, [])
            image_ids = page_images.get(page_num, [])

            # 合并内容
            content = "\n\n".join(content_parts) if content_parts else ""

            # 添加图片引用到内容中
            if image_ids:
                img_refs = []
                for img_id in image_ids:
                    img_info = self.image_metadata.get(img_id)
                    if img_info:
                        img_refs.append(f"![{img_id}]({img_info.file_path})")

                if img_refs:
                    content += "\n\n**相关图片：**\n" + "\n".join(img_refs)

            if content.strip():
                doc = Document(
                    text=content,
                    metadata={
                        "source_file": parsed_doc.source_file,
                        "page_num": page_num,
                        "image_ids": image_ids,
                        "total_pages": parsed_doc.total_pages,
                    },
                )
                documents.append(doc)

        return documents

    def _build_node_image_map(
        self,
        index: VectorStoreIndex,
        documents: List[Document],
    ):
        """建立节点到图片的映射"""
        # 获取所有节点
        for doc_id, doc in index.docstore.docs.items():
            # 从文档元数据中获取图片信息
            source_metadata = None
            for source_doc in documents:
                if doc.text.startswith(source_doc.text[:100]):
                    source_metadata = source_doc.metadata
                    break

            if source_metadata:
                image_ids = source_metadata.get("image_ids", [])
                if image_ids:
                    self.node_image_map[doc_id] = image_ids

    def save(self, index: VectorStoreIndex, metadata_path: Optional[str] = None):
        """
        保存索引和元数据

        Args:
            index: 向量索引
            metadata_path: 元数据保存路径
        """
        # 保存索引
        index.storage_context.persist(persist_dir=str(self.persist_dir))
        print(f"[已保存] 索引保存到: {self.persist_dir}")

        # 保存元数据
        if metadata_path is None:
            metadata_path = self.persist_dir / "hybrid_metadata.json"

        metadata = {
            "image_metadata": {
                img_id: {
                    "image_id": img.image_id,
                    "file_path": img.file_path,
                    "page_num": img.page_num,
                    "width": img.width,
                    "height": img.height,
                }
                for img_id, img in self.image_metadata.items()
            },
            "node_image_map": self.node_image_map,
        }

        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        print(f"[已保存] 元数据保存到: {metadata_path}")

    def load(self) -> Optional[VectorStoreIndex]:
        """
        加载索引和元数据

        Returns:
            VectorStoreIndex 实例，如果不存在则返回 None
        """
        from llama_index.core import load_index_from_storage

        if not self.persist_dir.exists():
            return None

        print(f"[加载索引] 从 {self.persist_dir} 加载...")

        # 加载索引
        storage_context = StorageContext.from_defaults(
            persist_dir=str(self.persist_dir)
        )
        index = load_index_from_storage(storage_context)

        # 加载元数据
        metadata_path = self.persist_dir / "hybrid_metadata.json"
        if metadata_path.exists():
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            # 恢复图片元数据
            for img_id, img_data in metadata.get("image_metadata", {}).items():
                self.image_metadata[img_id] = ImageInfo(**img_data)

            # 恢复节点映射
            self.node_image_map = metadata.get("node_image_map", {})

        print(f"  - 加载图片元数据: {len(self.image_metadata)}")
        print(f"  - 加载节点映射: {len(self.node_image_map)}")

        return index


class HybridRAGSystem:
    """
    混合 RAG 系统完整封装
    整合解析、索引、查询全流程
    """

    def __init__(
        self,
        qwen_api_key: str,
        llama_cloud_api_key: Optional[str] = None,
        persist_dir: str = "./hybrid_index",
        llm_model: str = "qwen-flash",
        embedding_model: str = "text-embedding-v4",
    ):
        """
        初始化混合 RAG 系统

        Args:
            qwen_api_key: DashScope API Key
            llama_cloud_api_key: LlamaCloud API Key（可选）
            persist_dir: 索引持久化目录
            llm_model: LLM 模型名称
            embedding_model: 嵌入模型名称
        """
        self.qwen_api_key = qwen_api_key
        self.llama_cloud_api_key = llama_cloud_api_key
        self.persist_dir = persist_dir

        # 初始化组件
        self.parser = HybridPDFParser(
            llama_cloud_api_key=llama_cloud_api_key,
        )

        self.index_builder = HybridIndexBuilder(
            api_key=qwen_api_key,
            persist_dir=persist_dir,
            llm_model=llm_model,
            embedding_model=embedding_model,
        )

        self.index: Optional[VectorStoreIndex] = None
        self.query_engine = None

    def build_from_pdf(
        self,
        pdf_path: str,
        force_rebuild: bool = False,
    ) -> bool:
        """
        从 PDF 文件构建 RAG 系统

        Args:
            pdf_path: PDF 文件路径
            force_rebuild: 是否强制重建

        Returns:
            是否成功
        """
        # 检查是否已有索引
        if not force_rebuild:
            self.index = self.index_builder.load()
            if self.index:
                print("[加载] 使用已存在的索引")
                self._create_query_engine()
                return True

        # 解析 PDF
        print("[构建] 解析 PDF...")
        parsed_doc = self.parser.parse(pdf_path)

        # 构建索引
        print("[构建] 构建向量索引...")
        self.index = self.index_builder.build_index([parsed_doc])

        # 保存索引
        self.index_builder.save(self.index)

        # 创建查询引擎
        self._create_query_engine()

        return True

    def build_from_directory(
        self,
        directory: str,
        force_rebuild: bool = False,
    ) -> bool:
        """
        从目录批量构建 RAG 系统

        Args:
            directory: PDF 文件目录
            force_rebuild: 是否强制重建

        Returns:
            是否成功
        """
        # 检查是否已有索引
        if not force_rebuild:
            self.index = self.index_builder.load()
            if self.index:
                print("[加载] 使用已存在的索引")
                self._create_query_engine()
                return True

        # 解析所有 PDF
        print(f"[构建] 解析目录: {directory}")
        pdf_files = list(Path(directory).glob("*.pdf"))

        if not pdf_files:
            print(f"[错误] 目录中没有 PDF 文件: {directory}")
            return False

        parsed_docs = []
        for pdf_file in pdf_files:
            try:
                parsed_doc = self.parser.parse(str(pdf_file))
                parsed_docs.append(parsed_doc)
            except Exception as e:
                print(f"[错误] 解析 {pdf_file.name} 失败: {e}")
                continue

        if not parsed_docs:
            print("[错误] 没有成功解析的文档")
            return False

        # 构建索引
        print("[构建] 构建向量索引...")
        self.index = self.index_builder.build_index(parsed_docs)

        # 保存索引
        self.index_builder.save(self.index)

        # 创建查询引擎
        self._create_query_engine()

        return True

    def _create_query_engine(self):
        """创建查询引擎"""
        from .multimodal_query import MultimodalQueryEngine

        self.query_engine = MultimodalQueryEngine(
            index=self.index,
            image_metadata=self.index_builder.image_metadata,
            node_image_map=self.index_builder.node_image_map,
            llm_model="qwen-flash",
            api_key=self.qwen_api_key,
        )

    def query(self, query_text: str) -> Any:
        """
        执行查询

        Args:
            query_text: 查询文本

        Returns:
            查询结果
        """
        if not self.query_engine:
            raise RuntimeError("RAG 系统未初始化，请先构建或加载索引")

        return self.query_engine.query_with_context(query_text)

    def get_stats(self) -> Dict[str, Any]:
        """获取系统统计信息"""
        return {
            "index_loaded": self.index is not None,
            "total_images": len(self.index_builder.image_metadata),
            "total_nodes": len(self.index_builder.node_image_map),
            "nodes_with_images": sum(
                1 for imgs in self.index_builder.node_image_map.values() if imgs
            ),
        }


if __name__ == "__main__":
    # 测试
    import sys

    if len(sys.argv) < 2:
        print("用法: python hybrid_index.py <pdf路径或目录>")
        sys.exit(1)

    source = sys.argv[1]

    # 从环境变量获取 API Key
    qwen_api_key = os.environ.get("QWEN_API_KEY")
    llama_cloud_api_key = os.environ.get("LLAMA_CLOUD_API_KEY")

    if not qwen_api_key:
        print("[错误] 请设置 QWEN_API_KEY 环境变量")
        sys.exit(1)

    # 创建 RAG 系统
    rag = HybridRAGSystem(
        qwen_api_key=qwen_api_key,
        llama_cloud_api_key=llama_cloud_api_key,
    )

    # 构建索引
    if Path(source).is_file():
        rag.build_from_pdf(source)
    else:
        rag.build_from_directory(source)

    print(f"\n统计信息: {rag.get_stats()}")
