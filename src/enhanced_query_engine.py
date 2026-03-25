"""
增强的多模态查询引擎
将图片描述作为上下文增强查询，支持更精准的语义检索
"""
import os
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.llms.dashscope import DashScope

from .pdf_parser import ImageInfo
from .image_descriptor import ImageDescription


@dataclass
class EnhancedMultimodalResult:
    """增强的多模态查询结果"""
    text: str                                          # 生成的回答文本
    images: List[Dict[str, Any]]                       # 相关图片列表
    image_descriptions: List[Dict[str, Any]]           # 图片描述列表
    source_nodes: List[Dict[str, Any]]                 # 源节点信息
    confidence: float                                  # 置信度


class EnhancedMultimodalQueryEngine:
    """增强的多模态查询引擎"""

    def __init__(self, index: VectorStoreIndex,
                 image_metadata: Dict[str, ImageInfo],
                 image_descriptions: Dict[str, ImageDescription],
                 node_image_map: Dict[str, List[str]],
                 llm_model: str = "qwen-flash",
                 api_key: str = None):
        """
        初始化查询引擎

        Args:
            index: 向量索引
            image_metadata: 图片元数据
            image_descriptions: 图片描述字典
            node_image_map: 节点-图片映射
            llm_model: LLM 模型名称
            api_key: API Key
        """
        self.index = index
        self.image_metadata = image_metadata
        self.image_descriptions = image_descriptions
        self.node_image_map = node_image_map

        # 配置 LLM
        if api_key:
            self.llm = DashScope(model_name=llm_model, api_key=api_key)
        else:
            self.llm = None

        # 创建检索器 - 增加检索数量以包含图片描述节点
        self.retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=10
        )

    def query(self, query_text: str,
              similarity_threshold: float = 0.3) -> EnhancedMultimodalResult:
        """
        执行增强的多模态查询

        Args:
            query_text: 查询文本
            similarity_threshold: 相似度阈值

        Returns:
            EnhancedMultimodalResult: 查询结果
        """
        # Step 1: 检索相关节点
        retrieved_nodes = self.retriever.retrieve(query_text)

        # Step 2: 过滤并分类节点
        text_nodes = []
        image_desc_nodes = []

        for node in retrieved_nodes:
            if node.score and node.score >= similarity_threshold:
                metadata = self._get_node_metadata(node)
                node_type = metadata.get("node_type", "text")

                if node_type == "image_description":
                    image_desc_nodes.append((node, metadata))
                else:
                    text_nodes.append((node, metadata))

        if not text_nodes and not image_desc_nodes:
            return EnhancedMultimodalResult(
                text="抱歉，未找到相关信息。",
                images=[],
                image_descriptions=[],
                source_nodes=[],
                confidence=0.0
            )

        # Step 3: 收集图片信息
        all_images = []
        seen_image_ids = set()
        source_nodes_info = []

        # 处理文本节点
        for node, metadata in text_nodes:
            # 收集源节点信息
            source_nodes_info.append({
                "node_id": node.node_id,
                "text": node.text[:300] if node.text else "",
                "score": node.score,
                "page_num": metadata.get("page_num", 0),
                "source_file": metadata.get("source_file", ""),
                "node_type": "text"
            })

            # 收集关联图片
            image_ids = metadata.get("image_ids", [])
            for img_id in image_ids:
                if img_id not in seen_image_ids and img_id in self.image_metadata:
                    img_info = self.image_metadata[img_id]
                    img_data = {
                        "image_id": img_id,
                        "file_path": img_info.file_path,
                        "page_num": img_info.page_num,
                        "width": img_info.width,
                        "height": img_info.height,
                    }
                    # 添加图片描述
                    if img_id in self.image_descriptions:
                        desc = self.image_descriptions[img_id]
                        img_data["description"] = desc.description
                        img_data["category"] = desc.category
                        img_data["keywords"] = desc.keywords

                    all_images.append(img_data)
                    seen_image_ids.add(img_id)

        # 处理图片描述节点
        for node, metadata in image_desc_nodes:
            image_id = metadata.get("image_id")
            if image_id and image_id not in seen_image_ids:
                # 添加到源节点信息
                source_nodes_info.append({
                    "node_id": node.node_id,
                    "text": node.text[:300] if node.text else "",
                    "score": node.score,
                    "page_num": metadata.get("page_num", 0),
                    "source_file": metadata.get("source_file", ""),
                    "node_type": "image_description"
                })

                # 添加图片到结果
                if image_id in self.image_metadata:
                    img_info = self.image_metadata[image_id]
                    img_data = {
                        "image_id": image_id,
                        "file_path": img_info.file_path,
                        "page_num": img_info.page_num,
                        "width": img_info.width,
                        "height": img_info.height,
                    }
                    if image_id in self.image_descriptions:
                        desc = self.image_descriptions[image_id]
                        img_data["description"] = desc.description
                        img_data["category"] = desc.category
                        img_data["keywords"] = desc.keywords

                    all_images.append(img_data)
                    seen_image_ids.add(image_id)

        # Step 4: 构建增强上下文
        context_parts = []

        # 添加文本上下文
        for node, _ in text_nodes[:5]:
            if node.text:
                context_parts.append(node.text)

        # 添加图片描述上下文
        for node, metadata in image_desc_nodes[:3]:
            if node.text:
                context_parts.append(f"[图片信息]\n{node.text}")

        # Step 5: 生成回答
        if self.llm and context_parts:
            context_text = "\n\n---\n\n".join(context_parts)

            prompt = f"""你是一个智能助手，请基于以下参考信息回答用户问题。

参考信息中包含文档文本和图片描述，请综合分析后给出详细、准确的回答。
如果问题涉及图片内容，请在回答中说明相关图片的信息。

参考信息：
{context_text}

用户问题：{query_text}

请给出详细、准确的回答（使用中文）："""

            try:
                response = self.llm.complete(prompt)
                answer_text = response.text
            except Exception as e:
                print(f"LLM 生成回答失败: {e}")
                answer_text = "\n\n".join(context_parts[:3])
        else:
            answer_text = "\n\n".join(context_parts[:3]) if context_parts else "无法生成回答"

        # Step 6: 计算置信度
        all_scores = [node.score for node, _ in text_nodes + image_desc_nodes if node.score]
        confidence = sum(all_scores) / len(all_scores) if all_scores else 0.0

        # Step 7: 构建图片描述列表
        image_descriptions_list = []
        for img_id in seen_image_ids:
            if img_id in self.image_descriptions:
                desc = self.image_descriptions[img_id]
                image_descriptions_list.append({
                    "image_id": img_id,
                    "description": desc.description,
                    "keywords": desc.keywords,
                    "category": desc.category,
                    "ocr_text": desc.ocr_text
                })

        return EnhancedMultimodalResult(
            text=answer_text,
            images=all_images,
            image_descriptions=image_descriptions_list,
            source_nodes=source_nodes_info,
            confidence=confidence
        )

    def _get_node_metadata(self, node) -> dict:
        """获取节点元数据"""
        if hasattr(node, 'node') and hasattr(node.node, 'metadata'):
            return node.node.metadata
        elif hasattr(node, 'metadata'):
            return node.metadata
        return {}

    def query_with_context(self, query_text: str,
                           context_window: int = 200) -> EnhancedMultimodalResult:
        """
        执行查询并返回完整的图片上下文

        Args:
            query_text: 查询文本
            context_window: 上下文窗口大小

        Returns:
            EnhancedMultimodalResult: 查询结果
        """
        result = self.query(query_text)

        # 为每张图片添加完整上下文
        for img in result.images:
            img_id = img.get("image_id")
            if img_id and img_id in self.image_metadata:
                img_info = self.image_metadata[img_id]
                if img_info.context_before:
                    img["context_before"] = img_info.context_before[-context_window:]
                if img_info.context_after:
                    img["context_after"] = img_info.context_after[:context_window]

        return result

    def search_images(self, query_text: str,
                      top_k: int = 5) -> List[Dict[str, Any]]:
        """
        专门搜索图片（基于图片描述）

        Args:
            query_text: 查询文本
            top_k: 返回数量

        Returns:
            图片信息列表
        """
        # 检索节点
        retrieved_nodes = self.retriever.retrieve(query_text)

        # 筛选图片描述节点
        images = []
        seen_ids = set()

        for node in retrieved_nodes:
            metadata = self._get_node_metadata(node)
            if metadata.get("node_type") == "image_description":
                image_id = metadata.get("image_id")
                if image_id and image_id not in seen_ids and image_id in self.image_metadata:
                    img_info = self.image_metadata[image_id]
                    img_data = {
                        "image_id": image_id,
                        "file_path": img_info.file_path,
                        "page_num": img_info.page_num,
                        "score": node.score,
                    }
                    if image_id in self.image_descriptions:
                        desc = self.image_descriptions[image_id]
                        img_data["description"] = desc.description
                        img_data["category"] = desc.category

                    images.append(img_data)
                    seen_ids.add(image_id)

                    if len(images) >= top_k:
                        break

        return images


class EnhancedMultimodalRAGSystem:
    """增强的多模态 RAG 系统完整封装"""

    def __init__(self, api_key: str, persist_dir: str = "./multimodal_index",
                 llm_model: str = "qwen-flash",
                 embedding_model: str = "text-embedding-v4",
                 vl_model: str = "qwen-vl-max"):
        """
        初始化多模态 RAG 系统

        Args:
            api_key: DashScope API Key
            persist_dir: 索引持久化目录
            llm_model: LLM 模型名称
            embedding_model: 嵌入模型名称
            vl_model: 视觉语言模型名称
        """
        self.api_key = api_key
        self.persist_dir = persist_dir
        self.llm_model = llm_model
        self.embedding_model = embedding_model
        self.vl_model = vl_model

        self.index = None
        self.image_metadata = {}
        self.image_descriptions = {}
        self.node_image_map = {}
        self.query_engine = None

    def build_from_documents(self, parsed_docs: List,
                             generate_descriptions: bool = True,
                             progress_callback=None) -> bool:
        """
        从解析后的文档构建索引

        Args:
            parsed_docs: 解析后的文档列表
            generate_descriptions: 是否生成图片描述
            progress_callback: 进度回调函数

        Returns:
            是否成功
        """
        from .enhanced_multimodal_index import EnhancedMultimodalIndexBuilder, EnhancedMultimodalIndexManager

        builder = EnhancedMultimodalIndexBuilder(
            api_key=self.api_key,
            persist_dir=self.persist_dir,
            embedding_model=self.embedding_model,
            vl_model=self.vl_model
        )

        self.index = builder.build_index(
            parsed_docs,
            generate_descriptions=generate_descriptions,
            progress_callback=progress_callback
        )
        self.image_metadata = builder.image_metadata
        self.image_descriptions = builder.image_descriptions
        self.node_image_map = builder.node_image_map

        # 保存索引
        manager = EnhancedMultimodalIndexManager(persist_dir=self.persist_dir)
        manager.save_index(self.index, builder)

        # 创建查询引擎
        self._create_query_engine()

        return True

    def load_index(self) -> bool:
        """
        加载已保存的索引

        Returns:
            是否成功
        """
        from .enhanced_multimodal_index import EnhancedMultimodalIndexManager

        manager = EnhancedMultimodalIndexManager(persist_dir=self.persist_dir)
        self.index = manager.load_index(
            api_key=self.api_key,
            embedding_model=self.embedding_model
        )

        if self.index:
            self.image_metadata = manager.image_metadata
            self.image_descriptions = manager.image_descriptions
            self.node_image_map = manager.node_image_map
            self._create_query_engine()
            return True

        return False

    def _create_query_engine(self):
        """创建查询引擎"""
        self.query_engine = EnhancedMultimodalQueryEngine(
            index=self.index,
            image_metadata=self.image_metadata,
            image_descriptions=self.image_descriptions,
            node_image_map=self.node_image_map,
            llm_model=self.llm_model,
            api_key=self.api_key
        )

    def query(self, query_text: str) -> EnhancedMultimodalResult:
        """
        执行查询

        Args:
            query_text: 查询文本

        Returns:
            EnhancedMultimodalResult: 查询结果
        """
        if not self.query_engine:
            raise RuntimeError("查询引擎未初始化，请先构建或加载索引")

        return self.query_engine.query_with_context(query_text)

    def search_images(self, query_text: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        搜索图片

        Args:
            query_text: 查询文本
            top_k: 返回数量

        Returns:
            图片信息列表
        """
        if not self.query_engine:
            raise RuntimeError("查询引擎未初始化")

        return self.query_engine.search_images(query_text, top_k)

    def get_stats(self) -> Dict[str, Any]:
        """获取系统统计信息"""
        return {
            "total_images": len(self.image_metadata),
            "image_descriptions": len(self.image_descriptions),
            "total_nodes": len(self.node_image_map),
            "nodes_with_images": sum(1 for imgs in self.node_image_map.values() if imgs),
            "index_loaded": self.index is not None
        }


def format_enhanced_result(result: EnhancedMultimodalResult,
                           show_images: bool = True) -> str:
    """
    格式化查询结果用于显示

    Args:
        result: 查询结果
        show_images: 是否显示图片信息

    Returns:
        格式化后的字符串
    """
    output = []

    # 添加回答文本
    output.append("=" * 60)
    output.append("【回答】")
    output.append(result.text)
    output.append("")

    # 添加图片信息
    if show_images and result.images:
        output.append("=" * 60)
        output.append(f"【相关图片】共 {len(result.images)} 张")
        output.append("")

        for i, img in enumerate(result.images, 1):
            output.append(f"图片 {i}:")
            output.append(f"  路径: {img.get('file_path', 'N/A')}")
            output.append(f"  页码: {img.get('page_num', 'N/A')}")

            if img.get("description"):
                output.append(f"  描述: {img['description'][:100]}...")

            if img.get("keywords"):
                output.append(f"  关键词: {', '.join(img['keywords'])}")

            output.append("")

    # 添加源节点信息
    if result.source_nodes:
        output.append("=" * 60)
        output.append("【参考来源】")
        for node in result.source_nodes[:5]:
            node_type = node.get('node_type', 'text')
            type_label = "图片描述" if node_type == "image_description" else "文本"
            output.append(f"  - [{type_label}] 页码 {node['page_num']}, 相似度: {node['score']:.3f}")

    output.append("=" * 60)
    output.append(f"置信度: {result.confidence:.3f}")

    return "\n".join(output)


if __name__ == "__main__":
    print("增强的多模态查询引擎模块")
