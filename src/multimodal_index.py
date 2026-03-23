"""
图文关联索引构建模块
构建文本向量索引，同时存储图片关联信息
"""
import os
import json
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Any
import pickle

from llama_index.core import VectorStoreIndex, Document, Settings
from llama_index.core.storage.storage_context import StorageContext
from llama_index.core.vector_stores.types import VectorStore
from llama_index.embeddings.dashscope import DashScopeEmbedding

from .pdf_parser import ParsedDocument, TextBlock, ImageInfo


@dataclass
class MultimodalNode:
    """多模态节点：文本 + 关联图片"""
    node_id: str
    text: str
    page_num: int
    source_file: str
    images: List[Dict[str, Any]]  # 关联图片信息列表
    bbox: tuple = (0, 0, 0, 0)


class MultimodalIndexBuilder:
    """多模态索引构建器"""

    def __init__(self, api_key: str, persist_dir: str = "./multimodal_index",
                 embedding_model: str = "text-embedding-v4"):
        """
        初始化索引构建器

        Args:
            api_key: DashScope API Key
            persist_dir: 索引持久化目录
            embedding_model: 嵌入模型名称
        """
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)

        # 配置嵌入模型（DashScope API 限制批量大小不超过 10）
        self.embed_model = DashScopeEmbedding(
            model_name=embedding_model,
            api_key=api_key,
            embed_batch_size=10  # 设置批量大小以符合 API 限制
        )
        Settings.embed_model = self.embed_model

        # 存储图片元数据
        self.image_metadata: Dict[str, ImageInfo] = {}
        # 存储节点与图片的映射
        self.node_image_map: Dict[str, List[str]] = {}

    def build_index(self, parsed_docs: List[ParsedDocument]) -> VectorStoreIndex:
        """
        从解析后的文档构建多模态索引

        Args:
            parsed_docs: 解析后的文档列表

        Returns:
            VectorStoreIndex: LlamaIndex 向量索引
        """
        documents = []
        multimodal_nodes = []

        for parsed_doc in parsed_docs:
            # 存储图片元数据
            for img in parsed_doc.images:
                self.image_metadata[img.image_id] = img

            # 为每个文本块创建文档
            for block in parsed_doc.text_blocks:
                if not block.content.strip():
                    continue

                # 获取关联图片的详细信息
                related_images = []
                for img_id in block.images:
                    if img_id in self.image_metadata:
                        img_info = self.image_metadata[img_id]
                        related_images.append({
                            "image_id": img_info.image_id,
                            "file_path": img_info.file_path,
                            "page_num": img_info.page_num,
                            "context_before": img_info.context_before,
                            "context_after": img_info.context_after
                        })

                # 创建多模态节点
                node = MultimodalNode(
                    node_id=block.block_id,
                    text=block.content,
                    page_num=block.page_num,
                    source_file=parsed_doc.source_file,
                    images=related_images,
                    bbox=block.bbox
                )
                multimodal_nodes.append(node)

                # 创建 LlamaIndex Document
                # 将图片信息存储在 metadata 中
                doc = Document(
                    text=block.content,
                    doc_id=block.block_id,
                    metadata={
                        "page_num": block.page_num,
                        "source_file": parsed_doc.source_file,
                        "image_ids": block.images,  # 只存储图片ID
                        "bbox": block.bbox,
                        "has_images": len(block.images) > 0
                    }
                )
                documents.append(doc)

                # 更新节点-图片映射
                self.node_image_map[block.block_id] = block.images

        print(f"创建 {len(documents)} 个文档节点")
        print(f"其中 {sum(1 for d in documents if d.metadata.get('has_images'))} 个节点包含图片")

        # 构建向量索引
        index = VectorStoreIndex.from_documents(
            documents,
            embed_model=self.embed_model,
            show_progress=True
        )

        # 保存多模态元数据
        self._save_metadata(multimodal_nodes)

        return index

    def _save_metadata(self, nodes: List[MultimodalNode]):
        """保存多模态元数据"""
        # 保存图片元数据
        image_meta_path = os.path.join(self.persist_dir, "image_metadata.pkl")
        with open(image_meta_path, "wb") as f:
            pickle.dump(self.image_metadata, f)

        # 保存节点-图片映射
        node_map_path = os.path.join(self.persist_dir, "node_image_map.pkl")
        with open(node_map_path, "wb") as f:
            pickle.dump(self.node_image_map, f)

        # 保存完整节点信息（JSON格式，便于查看）
        nodes_data = [asdict(node) for node in nodes]
        nodes_path = os.path.join(self.persist_dir, "multimodal_nodes.json")
        with open(nodes_path, "w", encoding="utf-8") as f:
            json.dump(nodes_data, f, ensure_ascii=False, indent=2)

        print(f"元数据已保存到: {self.persist_dir}")

    def load_metadata(self) -> bool:
        """加载已保存的元数据"""
        image_meta_path = os.path.join(self.persist_dir, "image_metadata.pkl")
        node_map_path = os.path.join(self.persist_dir, "node_image_map.pkl")

        if os.path.exists(image_meta_path) and os.path.exists(node_map_path):
            with open(image_meta_path, "rb") as f:
                self.image_metadata = pickle.load(f)
            with open(node_map_path, "rb") as f:
                self.node_image_map = pickle.load(f)
            return True
        return False

    def get_image_info(self, image_id: str) -> Optional[ImageInfo]:
        """获取图片信息"""
        return self.image_metadata.get(image_id)

    def get_node_images(self, node_id: str) -> List[ImageInfo]:
        """获取节点关联的所有图片"""
        image_ids = self.node_image_map.get(node_id, [])
        return [self.image_metadata[img_id] for img_id in image_ids
                if img_id in self.image_metadata]


class MultimodalIndexManager:
    """多模态索引管理器：支持索引的持久化和加载"""

    def __init__(self, persist_dir: str = "./multimodal_index"):
        """
        初始化索引管理器

        Args:
            persist_dir: 索引持久化目录
        """
        self.persist_dir = persist_dir
        self.image_metadata: Dict[str, ImageInfo] = {}
        self.node_image_map: Dict[str, List[str]] = {}

    def save_index(self, index: VectorStoreIndex, builder: MultimodalIndexBuilder):
        """保存索引"""
        index.storage_context.persist(persist_dir=self.persist_dir)
        print(f"索引已保存到: {self.persist_dir}")

    def load_index(self, api_key: str, embedding_model: str = "text-embedding-v4") -> Optional[VectorStoreIndex]:
        """
        加载已保存的索引

        Args:
            api_key: DashScope API Key
            embedding_model: 嵌入模型名称

        Returns:
            VectorStoreIndex 或 None
        """
        from llama_index.core import load_index_from_storage

        storage_path = os.path.join(self.persist_dir, "docstore.json")
        if not os.path.exists(storage_path):
            print("未找到已保存的索引")
            return None

        try:
            # 配置嵌入模型（DashScope API 限制批量大小不超过 10）
            embed_model = DashScopeEmbedding(
                model_name=embedding_model,
                api_key=api_key,
                embed_batch_size=10
            )
            Settings.embed_model = embed_model

            # 加载存储上下文
            storage_context = StorageContext.from_defaults(persist_dir=self.persist_dir)

            # 加载索引
            index = load_index_from_storage(storage_context, embed_model=embed_model)

            # 加载元数据
            self._load_metadata()

            print(f"索引已加载，包含 {len(self.image_metadata)} 张图片元数据")
            return index

        except Exception as e:
            print(f"加载索引失败: {e}")
            return None

    def _load_metadata(self):
        """加载元数据"""
        image_meta_path = os.path.join(self.persist_dir, "image_metadata.pkl")
        node_map_path = os.path.join(self.persist_dir, "node_image_map.pkl")

        if os.path.exists(image_meta_path):
            with open(image_meta_path, "rb") as f:
                self.image_metadata = pickle.load(f)

        if os.path.exists(node_map_path):
            with open(node_map_path, "rb") as f:
                self.node_image_map = pickle.load(f)

    def get_image_info(self, image_id: str) -> Optional[ImageInfo]:
        """获取图片信息"""
        return self.image_metadata.get(image_id)

    def get_node_images(self, node_id: str) -> List[ImageInfo]:
        """获取节点关联的所有图片"""
        image_ids = self.node_image_map.get(node_id, [])
        return [self.image_metadata[img_id] for img_id in image_ids
                if img_id in self.image_metadata]


if __name__ == "__main__":
    # 测试索引构建
    import os

    api_key = os.environ.get("QWEN_API_KEY", "your-api-key")

    # 这里需要先解析 PDF
    # from pdf_parser import parse_pdf_directory
    # parsed_docs = parse_pdf_directory("./data")

    # builder = MultimodalIndexBuilder(api_key=api_key)
    # index = builder.build_index(parsed_docs)
