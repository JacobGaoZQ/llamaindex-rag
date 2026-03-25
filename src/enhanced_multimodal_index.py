"""
增强的多模态索引构建模块
将图片描述向量化并入库，支持语义检索图片内容
"""
import os
import json
import pickle
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Any

from llama_index.core import VectorStoreIndex, Document, Settings
from llama_index.core.storage.storage_context import StorageContext
from llama_index.embeddings.dashscope import DashScopeEmbedding

from .pdf_parser import ParsedDocument, TextBlock, ImageInfo
from .image_descriptor import ImageDescriptor, ImageDescription


@dataclass
class EnhancedMultimodalNode:
    """增强的多模态节点"""
    node_id: str
    text: str
    page_num: int
    source_file: str
    node_type: str  # "text" | "image_description"
    images: List[Dict[str, Any]]
    image_description: Optional[str] = None
    bbox: tuple = (0, 0, 0, 0)


class EnhancedMultimodalIndexBuilder:
    """增强的多模态索引构建器"""

    def __init__(self, api_key: str, persist_dir: str = "./multimodal_index",
                 embedding_model: str = "text-embedding-v4",
                 vl_model: str = "qwen-vl-max"):
        """
        初始化索引构建器

        Args:
            api_key: DashScope API Key
            persist_dir: 索引持久化目录
            embedding_model: 嵌入模型名称
            vl_model: 视觉语言模型名称
        """
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)

        # 配置嵌入模型
        self.embed_model = DashScopeEmbedding(
            model_name=embedding_model,
            api_key=api_key,
            embed_batch_size=10
        )
        Settings.embed_model = self.embed_model

        # 图片描述生成器
        self.image_descriptor = ImageDescriptor(api_key=api_key, model=vl_model)

        # 存储数据
        self.image_metadata: Dict[str, ImageInfo] = {}
        self.image_descriptions: Dict[str, ImageDescription] = {}
        self.node_image_map: Dict[str, List[str]] = {}

    def build_index(self, parsed_docs: List[ParsedDocument],
                    generate_descriptions: bool = True,
                    progress_callback=None) -> VectorStoreIndex:
        """
        构建增强的多模态索引

        Args:
            parsed_docs: 解析后的文档列表
            generate_descriptions: 是否生成图片描述
            progress_callback: 进度回调函数

        Returns:
            VectorStoreIndex: 向量索引
        """
        documents = []
        enhanced_nodes = []

        # Step 1: 收集所有图片信息
        print("Step 1: 收集图片信息...")
        all_images = []
        for parsed_doc in parsed_docs:
            for img in parsed_doc.images:
                self.image_metadata[img.image_id] = img
                all_images.append({
                    "image_id": img.image_id,
                    "file_path": img.file_path,
                    "context": f"{img.context_before}\n{img.context_after}"
                })

        print(f"  共发现 {len(all_images)} 张图片")

        # Step 2: 生成图片描述
        if generate_descriptions and all_images:
            print("\nStep 2: 生成图片描述...")

            def desc_progress(current, total, result):
                if progress_callback:
                    progress_callback("descriptions", current, total)

            descriptions = self.image_descriptor.batch_describe(
                all_images,
                max_workers=3,
                progress_callback=desc_progress
            )

            for desc in descriptions:
                self.image_descriptions[desc.image_id] = desc

            success_count = sum(1 for d in descriptions if d.confidence > 0)
            print(f"  成功生成 {success_count}/{len(descriptions)} 个图片描述")
        else:
            print("\nStep 2: 跳过图片描述生成")

        # Step 3: 创建文本节点
        print("\nStep 3: 创建文本节点...")
        for parsed_doc in parsed_docs:
            for block in parsed_doc.text_blocks:
                if not block.content.strip():
                    continue

                # 获取关联图片的描述信息
                related_images = []
                image_descriptions_text = []

                for img_id in block.images:
                    if img_id in self.image_metadata:
                        img_info = self.image_metadata[img_id]
                        related_images.append({
                            "image_id": img_info.image_id,
                            "file_path": img_info.file_path,
                            "page_num": img_info.page_num,
                        })

                        # 添加图片描述
                        if img_id in self.image_descriptions:
                            desc = self.image_descriptions[img_id]
                            if desc.description:
                                image_descriptions_text.append(
                                    f"【关联图片描述】{desc.description}"
                                )

                # 创建增强文本：原始文本 + 图片描述
                enhanced_text = block.content
                if image_descriptions_text:
                    enhanced_text += "\n\n" + "\n".join(image_descriptions_text)

                # 创建文档节点
                doc = Document(
                    text=enhanced_text,
                    doc_id=block.block_id,
                    metadata={
                        "page_num": block.page_num,
                        "source_file": parsed_doc.source_file,
                        "image_ids": block.images,
                        "bbox": block.bbox,
                        "has_images": len(block.images) > 0,
                        "node_type": "text"
                    }
                )
                documents.append(doc)

                # 更新映射
                self.node_image_map[block.block_id] = block.images

        print(f"  创建 {len(documents)} 个文本节点")

        # Step 4: 创建图片描述节点（独立索引）
        print("\nStep 4: 创建图片描述节点...")
        image_doc_count = 0
        for img_id, desc in self.image_descriptions.items():
            if not desc.description:
                continue

            img_info = self.image_metadata.get(img_id)
            if not img_info:
                continue

            # 构建图片描述文本
            desc_text = f"""【图片信息】
图片ID: {img_id}
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
                doc_id=f"img_desc_{img_id}",
                metadata={
                    "page_num": img_info.page_num,
                    "source_file": getattr(img_info, 'source_file', ''),
                    "image_id": img_id,
                    "image_path": img_info.file_path,
                    "category": desc.category,
                    "keywords": desc.keywords,
                    "node_type": "image_description"
                }
            )
            documents.append(doc)
            image_doc_count += 1

        print(f"  创建 {image_doc_count} 个图片描述节点")
        print(f"\n总计: {len(documents)} 个文档节点")

        # Step 5: 构建向量索引
        print("\nStep 5: 构建向量索引...")

        if progress_callback:
            progress_callback("indexing", 0, len(documents))

        index = VectorStoreIndex.from_documents(
            documents,
            embed_model=self.embed_model,
            show_progress=True
        )

        if progress_callback:
            progress_callback("indexing", len(documents), len(documents))

        # Step 6: 保存元数据
        print("\nStep 6: 保存元数据...")
        self._save_metadata()

        print("\n索引构建完成!")
        return index

    def _save_metadata(self):
        """保存元数据"""
        # 保存图片元数据
        image_meta_path = os.path.join(self.persist_dir, "image_metadata.pkl")
        with open(image_meta_path, "wb") as f:
            pickle.dump(self.image_metadata, f)

        # 保存图片描述
        descriptions_data = {
            img_id: asdict(desc) for img_id, desc in self.image_descriptions.items()
        }
        desc_path = os.path.join(self.persist_dir, "image_descriptions.json")
        with open(desc_path, "w", encoding="utf-8") as f:
            json.dump(descriptions_data, f, ensure_ascii=False, indent=2)

        # 保存节点映射
        node_map_path = os.path.join(self.persist_dir, "node_image_map.pkl")
        with open(node_map_path, "wb") as f:
            pickle.dump(self.node_image_map, f)

        print(f"  元数据已保存到: {self.persist_dir}")


class EnhancedMultimodalIndexManager:
    """增强的多模态索引管理器：支持索引的持久化和加载"""

    def __init__(self, persist_dir: str = "./multimodal_index"):
        """
        初始化索引管理器

        Args:
            persist_dir: 索引持久化目录
        """
        self.persist_dir = persist_dir
        self.image_metadata: Dict[str, ImageInfo] = {}
        self.image_descriptions: Dict[str, ImageDescription] = {}
        self.node_image_map: Dict[str, List[str]] = {}

    def save_index(self, index: VectorStoreIndex, builder: EnhancedMultimodalIndexBuilder):
        """保存索引"""
        index.storage_context.persist(persist_dir=self.persist_dir)
        print(f"索引已保存到: {self.persist_dir}")

    def load_index(self, api_key: str,
                   embedding_model: str = "text-embedding-v4") -> Optional[VectorStoreIndex]:
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
            # 配置嵌入模型
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

            print(f"索引已加载")
            print(f"  图片元数据: {len(self.image_metadata)} 条")
            print(f"  图片描述: {len(self.image_descriptions)} 条")
            print(f"  节点映射: {len(self.node_image_map)} 条")

            return index

        except Exception as e:
            print(f"加载索引失败: {e}")
            return None

    def _load_metadata(self):
        """加载元数据"""
        # 加载图片元数据
        image_meta_path = os.path.join(self.persist_dir, "image_metadata.pkl")
        if os.path.exists(image_meta_path):
            with open(image_meta_path, "rb") as f:
                self.image_metadata = pickle.load(f)

        # 加载图片描述
        desc_path = os.path.join(self.persist_dir, "image_descriptions.json")
        if os.path.exists(desc_path):
            with open(desc_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.image_descriptions = {
                    img_id: ImageDescription(**desc_data)
                    for img_id, desc_data in data.items()
                }

        # 加载节点映射
        node_map_path = os.path.join(self.persist_dir, "node_image_map.pkl")
        if os.path.exists(node_map_path):
            with open(node_map_path, "rb") as f:
                self.node_image_map = pickle.load(f)

    def get_image_info(self, image_id: str) -> Optional[ImageInfo]:
        """获取图片信息"""
        return self.image_metadata.get(image_id)

    def get_image_description(self, image_id: str) -> Optional[ImageDescription]:
        """获取图片描述"""
        return self.image_descriptions.get(image_id)

    def get_node_images(self, node_id: str) -> List[ImageInfo]:
        """获取节点关联的所有图片"""
        image_ids = self.node_image_map.get(node_id, [])
        return [self.image_metadata[img_id] for img_id in image_ids
                if img_id in self.image_metadata]


if __name__ == "__main__":
    # 测试代码
    import os

    api_key = os.environ.get("QWEN_API_KEY", "your-api-key")
    print("增强的多模态索引构建模块")
