"""
LlamaParse 文档处理器
使用 LlamaParse 解析文档并存储转换后的数据
支持 PDF、Word、PowerPoint 等多种格式
"""

import os
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Union

# LlamaIndex 核心组件
from llama_index.core import Document, VectorStoreIndex, StorageContext
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.storage.docstore import SimpleDocumentStore
from llama_index.core.storage.index_store import SimpleIndexStore
from llama_index.core.vector_stores.simple import SimpleVectorStore

# LlamaParse
from llama_parse import LlamaParse


class LlamaParseProcessor:
    """LlamaParse 文档处理器"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        result_type: str = "markdown",
        verbose: bool = True,
        storage_dir: str = "./storage",
        markdown_dir: str = "./markdown_output",
    ):
        """
        初始化 LlamaParse 处理器

        Args:
            api_key: LlamaCloud API Key (可从环境变量 LLAMA_CLOUD_API_KEY 获取)
            result_type: 输出格式 ("markdown" 或 "text")
            verbose: 是否显示详细日志
            storage_dir: 向量存储目录
            markdown_dir: Markdown 文件输出目录
        """
        self.api_key = api_key or os.environ.get("LLAMA_CLOUD_API_KEY")
        if not self.api_key:
            raise ValueError(
                "请提供 LlamaCloud API Key，或设置环境变量 LLAMA_CLOUD_API_KEY"
            )

        self.result_type = result_type
        self.verbose = verbose
        self.storage_dir = Path(storage_dir)
        self.markdown_dir = Path(markdown_dir)

        # 创建目录
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.markdown_dir.mkdir(parents=True, exist_ok=True)

        # 初始化 LlamaParse
        self.parser = LlamaParse(
            api_key=self.api_key,
            result_type=self.result_type,
            verbose=self.verbose,
            # 高级配置
            invalidate_cache=False,  # 启用缓存，避免重复解析
            do_not_cache_in_backend=False,
            fast_mode=False,  # 高质量模式
            skip_diagonal_text=False,  # 包含斜向文本
            page_separator="\n\n---\n\n",  # 页面分隔符
            gpt4o_mode=False,  # 可选：启用 GPT-4o 增强模式
            gpt4o_api_key=None,  # 可选：自定义 GPT-4o API Key
            bounding_box=None,  # 可选：指定解析区域
            target_pages=None,  # 可选：指定解析页面
            ignore_errors=False,
        )

        # 元数据存储
        self.metadata_file = self.storage_dir / "parse_metadata.json"
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> Dict:
        """加载解析元数据"""
        if self.metadata_file.exists():
            with open(self.metadata_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"files": {}}

    def _save_metadata(self):
        """保存解析元数据"""
        with open(self.metadata_file, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, ensure_ascii=False, indent=2)

    def _get_file_hash(self, file_path: Path) -> str:
        """计算文件哈希值"""
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def parse_file(
        self,
        file_path: str,
        force_reparse: bool = False,
        save_markdown: bool = True,
    ) -> List[Document]:
        """
        解析单个文件

        Args:
            file_path: 文件路径
            force_reparse: 是否强制重新解析（忽略缓存）
            save_markdown: 是否保存 Markdown 文件

        Returns:
            LlamaIndex Document 列表
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        file_hash = self._get_file_hash(file_path)
        file_key = str(file_path.absolute())

        # 检查是否已解析过（且文件未修改）
        if not force_reparse and file_key in self.metadata["files"]:
            cached_info = self.metadata["files"][file_key]
            if cached_info.get("hash") == file_hash:
                if self.verbose:
                    print(f"[缓存命中] {file_path.name} 已解析过，跳过")
                # 从已保存的 Markdown 文件加载
                cached_md = self.markdown_dir / f"{file_path.stem}.md"
                if cached_md.exists():
                    return self._markdown_to_documents(
                        cached_md.read_text(encoding="utf-8"),
                        source=str(file_path),
                    )

        if self.verbose:
            print(f"[解析中] {file_path.name}...")

        try:
            # 使用 LlamaParse 解析
            documents = self.parser.load_data(str(file_path))

            # 添加元数据
            for doc in documents:
                doc.metadata["source_file"] = file_path.name
                doc.metadata["source_path"] = str(file_path)
                doc.metadata["parse_time"] = datetime.now().isoformat()
                doc.metadata["file_hash"] = file_hash

            # 保存 Markdown 文件
            if save_markdown:
                markdown_content = self._documents_to_markdown(documents, file_path)
                output_path = self.markdown_dir / f"{file_path.stem}.md"
                output_path.write_text(markdown_content, encoding="utf-8")
                if self.verbose:
                    print(f"[已保存] {output_path}")

            # 更新元数据
            self.metadata["files"][file_key] = {
                "hash": file_hash,
                "parse_time": datetime.now().isoformat(),
                "output_file": f"{file_path.stem}.md",
                "num_documents": len(documents),
            }
            self._save_metadata()

            if self.verbose:
                print(f"[完成] {file_path.name} -> {len(documents)} 个文档片段")

            return documents

        except Exception as e:
            print(f"[错误] 解析 {file_path.name} 失败: {e}")
            raise

    def parse_directory(
        self,
        directory: str,
        extensions: Optional[List[str]] = None,
        recursive: bool = True,
        force_reparse: bool = False,
    ) -> List[Document]:
        """
        批量解析目录下的所有文件

        Args:
            directory: 目录路径
            extensions: 支持的文件扩展名列表
            recursive: 是否递归子目录
            force_reparse: 是否强制重新解析

        Returns:
            所有文档的列表
        """
        directory = Path(directory)
        if not directory.exists():
            raise FileNotFoundError(f"目录不存在: {directory}")

        # 默认支持的扩展名
        if extensions is None:
            extensions = [".pdf", ".docx", ".doc", ".pptx", ".ppt", ".txt", ".md"]

        # 收集文件
        files = []
        if recursive:
            for ext in extensions:
                files.extend(directory.rglob(f"*{ext}"))
        else:
            for ext in extensions:
                files.extend(directory.glob(f"*{ext}"))

        if not files:
            print(f"[警告] 目录 {directory} 中没有找到支持的文件")
            return []

        print(f"[批量解析] 找到 {len(files)} 个文件")

        all_documents = []
        for file_path in files:
            try:
                docs = self.parse_file(
                    str(file_path),
                    force_reparse=force_reparse,
                )
                all_documents.extend(docs)
            except Exception as e:
                print(f"[跳过] {file_path.name}: {e}")
                continue

        print(f"[批量完成] 共解析 {len(all_documents)} 个文档片段")
        return all_documents

    def _documents_to_markdown(self, documents: List[Document], source_path: Path) -> str:
        """将 Document 列表转换为 Markdown 格式"""
        parts = [
            f"# {source_path.stem}\n",
            f"> 来源: `{source_path.name}`\n",
            f"> 解析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            "\n---\n\n",
        ]

        for i, doc in enumerate(documents, 1):
            # 如果有页码信息，添加页码标题
            page_num = doc.metadata.get("page", None)
            if page_num is not None:
                parts.append(f"## 第 {page_num} 页\n\n")
            elif len(documents) > 1:
                parts.append(f"## 片段 {i}\n\n")

            parts.append(doc.text)
            parts.append("\n\n---\n\n")

        return "".join(parts)

    def _markdown_to_documents(self, markdown: str, source: str) -> List[Document]:
        """将 Markdown 转换为 Document 列表"""
        # 按分隔符分割
        sections = markdown.split("\n\n---\n\n")
        documents = []

        for section in sections:
            section = section.strip()
            if not section or section.startswith("# "):
                continue

            doc = Document(
                text=section,
                metadata={"source": source},
            )
            documents.append(doc)

        return documents

    def build_vector_index(
        self,
        documents: List[Document],
        embed_model=None,
        persist: bool = True,
        index_name: str = "default",
    ) -> VectorStoreIndex:
        """
        构建向量索引

        Args:
            documents: 文档列表
            embed_model: 嵌入模型 (None 则使用默认)
            persist: 是否持久化存储
            index_name: 索引名称

        Returns:
            VectorStoreIndex 实例
        """
        if not documents:
            raise ValueError("文档列表为空")

        print(f"[构建索引] 处理 {len(documents)} 个文档片段...")

        # 使用 Markdown 节点解析器
        node_parser = MarkdownNodeParser()

        # 创建存储上下文
        index_path = self.storage_dir / index_name
        index_path.mkdir(parents=True, exist_ok=True)

        storage_context = StorageContext.from_defaults(
            docstore=SimpleDocumentStore(),
            vector_store=SimpleVectorStore(),
            index_store=SimpleIndexStore(),
        )

        # 构建索引
        if embed_model:
            index = VectorStoreIndex.from_documents(
                documents,
                storage_context=storage_context,
                embed_model=embed_model,
                node_parser=node_parser,
            )
        else:
            index = VectorStoreIndex.from_documents(
                documents,
                storage_context=storage_context,
                node_parser=node_parser,
            )

        # 持久化
        if persist:
            index.storage_context.persist(persist_dir=str(index_path))
            print(f"[已存储] 索引保存到: {index_path}")

        return index

    def load_vector_index(
        self,
        embed_model=None,
        index_name: str = "default",
    ) -> Optional[VectorStoreIndex]:
        """
        加载已存储的向量索引

        Args:
            embed_model: 嵌入模型
            index_name: 索引名称

        Returns:
            VectorStoreIndex 实例，如果不存在则返回 None
        """
        index_path = self.storage_dir / index_name
        if not index_path.exists():
            return None

        print(f"[加载索引] 从 {index_path} 加载...")

        from llama_index.core import load_index_from_storage

        storage_context = StorageContext.from_defaults(persist_dir=str(index_path))
        index = load_index_from_storage(
            storage_context,
            embed_model=embed_model,
        )

        return index


class LlamaParseRAGPipeline:
    """完整的 RAG 管道：解析 -> 存储 -> 检索"""

    def __init__(
        self,
        llama_cloud_api_key: Optional[str] = None,
        qwen_api_key: Optional[str] = None,
        storage_dir: str = "./rag_storage",
        markdown_dir: str = "./markdown_output",
    ):
        """
        初始化 RAG 管道

        Args:
            llama_cloud_api_key: LlamaCloud API Key
            qwen_api_key: 通义千问 API Key (用于 LLM 和 Embedding)
            storage_dir: 存储目录
            markdown_dir: Markdown 输出目录
        """
        self.llama_cloud_api_key = llama_cloud_api_key or os.environ.get(
            "LLAMA_CLOUD_API_KEY"
        )
        self.qwen_api_key = qwen_api_key or os.environ.get("QWEN_API_KEY")

        self.storage_dir = Path(storage_dir)
        self.markdown_dir = Path(markdown_dir)

        # 初始化处理器
        self.processor = LlamaParseProcessor(
            api_key=self.llama_cloud_api_key,
            storage_dir=str(self.storage_dir / "parse"),
            markdown_dir=str(self.markdown_dir),
        )

        # 初始化嵌入模型
        self._setup_embed_model()

        self.index: Optional[VectorStoreIndex] = None
        self.query_engine = None

    def _setup_embed_model(self):
        """设置嵌入模型和 LLM"""
        if self.qwen_api_key:
            from llama_index.embeddings.dashscope import DashScopeEmbedding
            from llama_index.llms.dashscope import DashScope
            from llama_index.core import Settings

            self.embed_model = DashScopeEmbedding(
                model_name="text-embedding-v4",
                api_key=self.qwen_api_key,
                embed_batch_size=10,  # DashScope API 限制批量大小不超过 10
            )
            # 设置 LLM
            self.llm = DashScope(
                model_name="qwen-flash",
                api_key=self.qwen_api_key,
            )
            # 全局设置
            Settings.embed_batch_size = 10
            Settings.llm = self.llm
            Settings.embed_model = self.embed_model
        else:
            # 使用 OpenAI 嵌入模型
            from llama_index.embeddings.openai import OpenAIEmbedding

            self.embed_model = OpenAIEmbedding()
            self.llm = None

    def ingest_documents(
        self,
        source: str,
        force_reparse: bool = False,
    ) -> int:
        """
        导入文档并构建索引

        Args:
            source: 文件或目录路径
            force_reparse: 是否强制重新解析

        Returns:
            导入的文档数量
        """
        source_path = Path(source)

        # 解析文档
        if source_path.is_file():
            documents = self.processor.parse_file(
                str(source_path),
                force_reparse=force_reparse,
            )
        elif source_path.is_dir():
            documents = self.processor.parse_directory(
                str(source_path),
                force_reparse=force_reparse,
            )
        else:
            raise FileNotFoundError(f"路径不存在: {source}")

        if not documents:
            print("[警告] 没有文档被解析")
            return 0

        # 构建索引
        self.index = self.processor.build_vector_index(
            documents,
            embed_model=self.embed_model,
            persist=True,
        )

        # 创建查询引擎
        if self.llm:
            self.query_engine = self.index.as_query_engine(llm=self.llm)
        else:
            self.query_engine = self.index.as_query_engine()

        return len(documents)

    def load_existing_index(self) -> bool:
        """
        加载已存在的索引

        Returns:
            是否成功加载
        """
        self.index = self.processor.load_vector_index(
            embed_model=self.embed_model,
        )

        if self.index:
            if self.llm:
                self.query_engine = self.index.as_query_engine(llm=self.llm)
            else:
                self.query_engine = self.index.as_query_engine()
            return True
        return False

    def query(self, question: str) -> str:
        """
        查询知识库

        Args:
            question: 问题

        Returns:
            回答
        """
        if not self.query_engine:
            raise RuntimeError("请先导入文档或加载索引")

        response = self.query_engine.query(question)
        return str(response)

    def get_retriever(self):
        """获取检索器，用于 LangChain 集成"""
        if not self.index:
            raise RuntimeError("请先导入文档或加载索引")
        return self.index.as_retriever()


# ==========================================
# 便捷函数
# ==========================================

def parse_and_save(
    file_path: str,
    output_dir: str = "./markdown_output",
    api_key: Optional[str] = None,
) -> str:
    """
    解析文件并保存为 Markdown

    Args:
        file_path: 文件路径
        output_dir: 输出目录
        api_key: LlamaCloud API Key

    Returns:
        输出文件路径
    """
    processor = LlamaParseProcessor(
        api_key=api_key,
        markdown_dir=output_dir,
    )
    documents = processor.parse_file(file_path)

    output_path = Path(output_dir) / f"{Path(file_path).stem}.md"
    return str(output_path)


def build_rag_from_documents(
    source: str,
    llama_cloud_api_key: Optional[str] = None,
    qwen_api_key: Optional[str] = None,
) -> LlamaParseRAGPipeline:
    """
    从文档构建 RAG 系统

    Args:
        source: 文件或目录路径
        llama_cloud_api_key: LlamaCloud API Key
        qwen_api_key: 通义千问 API Key

    Returns:
        RAG 管道实例
    """
    pipeline = LlamaParseRAGPipeline(
        llama_cloud_api_key=llama_cloud_api_key,
        qwen_api_key=qwen_api_key,
    )
    pipeline.ingest_documents(source)
    return pipeline


# ==========================================
# 示例用法
# ==========================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法:")
        print("  1. 解析单个文件:")
        print("     python llamaparse_processor.py parse <文件路径>")
        print()
        print("  2. 批量解析目录:")
        print("     python llamaparse_processor.py parse-dir <目录路径>")
        print()
        print("  3. 构建 RAG 系统并查询:")
        print("     python llamaparse_processor.py rag <文件/目录路径> \"问题\"")
        print()
        print("环境变量:")
        print("  LLAMA_CLOUD_API_KEY: LlamaCloud API Key (必需)")
        print("  QWEN_API_KEY: 通义千问 API Key (可选，用于嵌入模型)")
        sys.exit(1)

    command = sys.argv[1]

    if command == "parse":
        # 解析单个文件
        file_path = sys.argv[2]
        output_path = parse_and_save(file_path)
        print(f"\n输出文件: {output_path}")

    elif command == "parse-dir":
        # 批量解析目录
        directory = sys.argv[2]
        processor = LlamaParseProcessor()
        documents = processor.parse_directory(directory)
        print(f"\n共解析 {len(documents)} 个文档片段")

    elif command == "rag":
        # 构建 RAG 并查询
        source = sys.argv[2]
        question = sys.argv[3] if len(sys.argv) > 3 else "文档的主要内容是什么？"

        pipeline = LlamaParseRAGPipeline()
        pipeline.ingest_documents(source)

        print(f"\n问题: {question}")
        answer = pipeline.query(question)
        print(f"\n回答: {answer}")

    else:
        print(f"未知命令: {command}")
        sys.exit(1)
