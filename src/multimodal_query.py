"""
多模态查询引擎
支持返回文字 + 关联图片的查询结果
"""
import os
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.postprocessor import SimilarityPostprocessor
from llama_index.core.response_synthesizers import get_response_synthesizer
from llama_index.llms.dashscope import DashScope

from .pdf_parser import ImageInfo


@dataclass
class MultimodalResult:
    """多模态查询结果"""
    text: str  # 文字回答
    images: List[Dict[str, Any]]  # 关联图片列表
    source_nodes: List[Dict[str, Any]]  # 源节点信息
    confidence: float  # 置信度


class MultimodalQueryEngine:
    """多模态查询引擎"""

    def __init__(self, index: VectorStoreIndex, image_metadata: Dict[str, ImageInfo],
                 node_image_map: Dict[str, List[str]], llm_model: str = "qwen-flash",
                 api_key: str = None):
        """
        初始化查询引擎

        Args:
            index: LlamaIndex 向量索引
            image_metadata: 图片元数据字典
            node_image_map: 节点-图片映射
            llm_model: LLM 模型名称
            api_key: API Key
        """
        self.index = index
        self.image_metadata = image_metadata
        self.node_image_map = node_image_map

        # 配置 LLM
        if api_key:
            self.llm = DashScope(model_name=llm_model, api_key=api_key)
            Settings.llm = self.llm
        else:
            self.llm = None

        # 创建检索器
        self.retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=5  # 检索 top 5 相关节点
        )

    def query(self, query_text: str, include_images: bool = True,
              similarity_threshold: float = 0.5) -> MultimodalResult:
        """
        执行多模态查询

        Args:
            query_text: 查询文本
            include_images: 是否包含图片
            similarity_threshold: 相似度阈值

        Returns:
            MultimodalResult: 查询结果
        """
        # 检索相关节点
        retrieved_nodes = self.retriever.retrieve(query_text)

        # 过滤低相似度节点
        filtered_nodes = [
            node for node in retrieved_nodes
            if node.score and node.score >= similarity_threshold
        ]

        if not filtered_nodes:
            return MultimodalResult(
                text="抱歉，未找到相关信息。",
                images=[],
                source_nodes=[],
                confidence=0.0
            )

        # 收集相关图片
        all_images = []
        seen_image_ids = set()
        source_nodes_info = []

        for node in filtered_nodes:
            node_id = node.node_id
            # 获取节点的 metadata
            node_metadata = {}
            if hasattr(node, 'node') and hasattr(node.node, 'metadata'):
                node_metadata = node.node.metadata
            elif hasattr(node, 'metadata'):
                node_metadata = node.metadata

            # 收集源节点信息
            source_info = {
                "node_id": node_id,
                "text": node.text[:500] if node.text else "",
                "score": node.score,
                "page_num": node_metadata.get("page_num", 0),
                "source_file": node_metadata.get("source_file", "")
            }
            source_nodes_info.append(source_info)

            # 收集关联图片 - 优先从 metadata 获取，否则从 node_image_map 获取
            if include_images:
                image_ids = node_metadata.get("image_ids", [])
                if not image_ids:
                    image_ids = self.node_image_map.get(node_id, [])

                for img_id in image_ids:
                    if img_id not in seen_image_ids and img_id in self.image_metadata:
                        img_info = self.image_metadata[img_id]
                        all_images.append({
                            "image_id": img_info.image_id,
                            "file_path": img_info.file_path,
                            "page_num": img_info.page_num,
                            "width": img_info.width,
                            "height": img_info.height,
                            "context_before": img_info.context_before,
                            "context_after": img_info.context_after
                        })
                        seen_image_ids.add(img_id)

        # 生成回答
        if self.llm:
            # 使用 LLM 生成综合回答
            context_text = "\n\n".join([node.text for node in filtered_nodes])

            prompt = f"""基于以下参考信息回答用户问题。如果参考信息中包含图片，请在回答中说明相关图片的内容。

参考信息：
{context_text}

用户问题：{query_text}

请给出详细、准确的回答："""

            response = self.llm.complete(prompt)
            answer_text = response.text
        else:
            # 不使用 LLM，直接返回检索到的文本
            answer_text = "\n\n".join([node.text for node in filtered_nodes[:3]])

        # 计算置信度
        confidence = sum(node.score or 0 for node in filtered_nodes) / len(filtered_nodes)

        return MultimodalResult(
            text=answer_text,
            images=all_images,
            source_nodes=source_nodes_info,
            confidence=confidence
        )

    def query_with_context(self, query_text: str, context_window: int = 200) -> MultimodalResult:
        """
        执行查询并返回图片上下文

        Args:
            query_text: 查询文本
            context_window: 上下文窗口大小（字符数）

        Returns:
            MultimodalResult: 查询结果
        """
        result = self.query(query_text, include_images=True)

        # 为每张图片添加上下文描述
        for img in result.images:
            context_parts = []
            if img.get("context_before"):
                context_parts.append(f"前文: ...{img['context_before'][-context_window:]}")
            if img.get("context_after"):
                context_parts.append(f"后文: {img['context_after'][:context_window]}...")

            img["context_description"] = "\n".join(context_parts)

        return result


class MultimodalRAGSystem:
    """多模态 RAG 系统完整封装"""

    def __init__(self, api_key: str, persist_dir: str = "./multimodal_index",
                 llm_model: str = "qwen-flash", embedding_model: str = "text-embedding-v4"):
        """
        初始化多模态 RAG 系统

        Args:
            api_key: DashScope API Key
            persist_dir: 索引持久化目录
            llm_model: LLM 模型名称
            embedding_model: 嵌入模型名称
        """
        self.api_key = api_key
        self.persist_dir = persist_dir
        self.llm_model = llm_model
        self.embedding_model = embedding_model

        self.index = None
        self.image_metadata = {}
        self.node_image_map = {}
        self.query_engine = None

    def build_from_documents(self, parsed_docs: List) -> bool:
        """
        从解析后的文档构建索引

        Args:
            parsed_docs: 解析后的文档列表

        Returns:
            是否成功
        """
        from .multimodal_index import MultimodalIndexBuilder, MultimodalIndexManager

        builder = MultimodalIndexBuilder(
            api_key=self.api_key,
            persist_dir=self.persist_dir,
            embedding_model=self.embedding_model
        )

        self.index = builder.build_index(parsed_docs)
        self.image_metadata = builder.image_metadata
        self.node_image_map = builder.node_image_map

        # 保存索引
        manager = MultimodalIndexManager(persist_dir=self.persist_dir)
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
        from .multimodal_index import MultimodalIndexManager

        manager = MultimodalIndexManager(persist_dir=self.persist_dir)
        self.index = manager.load_index(
            api_key=self.api_key,
            embedding_model=self.embedding_model
        )

        if self.index:
            self.image_metadata = manager.image_metadata
            self.node_image_map = manager.node_image_map
            self._create_query_engine()
            return True

        return False

    def _create_query_engine(self):
        """创建查询引擎"""
        self.query_engine = MultimodalQueryEngine(
            index=self.index,
            image_metadata=self.image_metadata,
            node_image_map=self.node_image_map,
            llm_model=self.llm_model,
            api_key=self.api_key
        )

    def query(self, query_text: str) -> MultimodalResult:
        """
        执行查询

        Args:
            query_text: 查询文本

        Returns:
            MultimodalResult: 查询结果
        """
        if not self.query_engine:
            raise RuntimeError("查询引擎未初始化，请先构建或加载索引")

        return self.query_engine.query_with_context(query_text)

    def get_stats(self) -> Dict[str, Any]:
        """获取系统统计信息"""
        return {
            "total_images": len(self.image_metadata),
            "total_nodes": len(self.node_image_map),
            "nodes_with_images": sum(1 for imgs in self.node_image_map.values() if imgs),
            "index_loaded": self.index is not None
        }


def format_result(result: MultimodalResult, show_images: bool = True) -> str:
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
            output.append(f"  路径: {img['file_path']}")
            output.append(f"  页码: {img['page_num']}")
            output.append(f"  尺寸: {img['width']} x {img['height']}")

            if img.get("context_description"):
                output.append(f"  上下文: {img['context_description']}")
            output.append("")

    # 添加源节点信息
    if result.source_nodes:
        output.append("=" * 60)
        output.append("【参考来源】")
        for node in result.source_nodes[:3]:
            output.append(f"  - 页码 {node['page_num']}, 相似度: {node['score']:.3f}")

    output.append("=" * 60)
    output.append(f"置信度: {result.confidence:.3f}")

    return "\n".join(output)


if __name__ == "__main__":
    # 测试查询
    print("多模态查询引擎模块")
