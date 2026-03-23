"""
多模态 RAG 系统 Web UI
使用 Streamlit 构建
"""
import os
import sys
import base64
from pathlib import Path

import streamlit as st

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from src.pdf_parser import parse_pdf_directory
from src.multimodal_query import MultimodalRAGSystem, format_result


# 页面配置
st.set_page_config(
    page_title="智能家居问答系统",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded"
)


def get_image_base64(image_path: str) -> str:
    """将图片转换为 base64 编码"""
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception:
        return None


def display_image(image_path: str, width: int = 400):
    """显示图片"""
    if os.path.exists(image_path):
        st.image(image_path, width=width)
    else:
        st.warning(f"图片文件不存在: {image_path}")


def init_session_state():
    """初始化会话状态"""
    if "rag_system" not in st.session_state:
        st.session_state.rag_system = None
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "index_built" not in st.session_state:
        st.session_state.index_built = False


def build_knowledge_base(api_key: str, data_dir: str):
    """构建知识库"""
    with st.status("正在构建知识库...", expanded=True) as status:
        st.write("📁 解析 PDF 文档...")

        # 解析 PDF
        parsed_docs = parse_pdf_directory(
            input_dir=data_dir,
            output_dir="./extracted_images",
            metadata_dir="./parsed_metadata"
        )

        if not parsed_docs:
            status.error("未找到可解析的 PDF 文档")
            return None

        total_text_blocks = sum(len(doc.text_blocks) for doc in parsed_docs)
        total_images = sum(len(doc.images) for doc in parsed_docs)
        st.write(f"✅ 解析完成: {total_text_blocks} 个文本块, {total_images} 张图片")

        st.write("🔍 构建向量索引...")
        rag_system = MultimodalRAGSystem(
            api_key=api_key,
            persist_dir="./multimodal_index",
            llm_model="qwen-flash",
            embedding_model="text-embedding-v4"
        )

        rag_system.build_from_documents(parsed_docs)

        status.update(label="✅ 知识库构建完成!", state="complete")

        return rag_system


def load_knowledge_base(api_key: str):
    """加载已保存的知识库"""
    rag_system = MultimodalRAGSystem(
        api_key=api_key,
        persist_dir="./multimodal_index",
        llm_model="qwen-flash",
        embedding_model="text-embedding-v4"
    )

    if rag_system.load_index():
        return rag_system
    return None


def main():
    init_session_state()

    # 侧边栏
    with st.sidebar:
        st.title("🏠 智能家居问答系统")
        st.markdown("---")

        # API Key 输入
        api_key = st.text_input(
            "QWEN API Key",
            type="password",
            value=os.environ.get("QWEN_API_KEY", ""),
            help="请输入 DashScope API Key"
        )

        # 数据目录
        data_dir = st.text_input(
            "文档目录",
            value="./data",
            help="PDF 文档所在目录"
        )

        st.markdown("---")

        # 知识库操作
        col1, col2 = st.columns(2)

        with col1:
            if st.button("🔄 重建索引", use_container_width=True):
                if not api_key:
                    st.error("请先输入 API Key")
                else:
                    st.session_state.rag_system = build_knowledge_base(api_key, data_dir)
                    if st.session_state.rag_system:
                        st.session_state.index_built = True

        with col2:
            if st.button("📂 加载索引", use_container_width=True):
                if not api_key:
                    st.error("请先输入 API Key")
                else:
                    with st.spinner("正在加载..."):
                        st.session_state.rag_system = load_knowledge_base(api_key)
                        if st.session_state.rag_system:
                            st.session_state.index_built = True
                            st.success("加载成功!")
                        else:
                            st.warning("未找到已保存的索引")

        # 状态显示
        st.markdown("---")
        st.subheader("📊 系统状态")

        if st.session_state.rag_system:
            stats = st.session_state.rag_system.get_stats()
            st.metric("总图片数", stats["total_images"])
            st.metric("总节点数", stats["total_nodes"])
            st.metric("含图片节点", stats["nodes_with_images"])
        else:
            st.info("请先构建或加载知识库")

        # 清除历史
        st.markdown("---")
        if st.button("🗑️ 清除对话历史", use_container_width=True):
            st.session_state.chat_history = []
            st.rerun()

    # 主内容区
    st.title("💬 智能问答")

    # 检查系统状态
    if not st.session_state.rag_system:
        st.info("👈 请在侧边栏构建或加载知识库后开始使用")
        return

    # 显示对话历史
    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.write(message["content"])
            else:
                # 显示回答
                st.markdown("### 📝 回答")
                st.write(message["content"])

                # 显示图片
                if message.get("images"):
                    st.markdown("### 🖼️ 相关图片")
                    cols = st.columns(min(len(message["images"]), 3))
                    for i, img in enumerate(message["images"]):
                        with cols[i % 3]:
                            display_image(img["file_path"], width=300)
                            if img.get("page_num"):
                                st.caption(f"📄 第 {img['page_num']} 页")

                # 显示来源
                if message.get("sources"):
                    with st.expander("📚 查看来源"):
                        for source in message["sources"]:
                            st.write(f"- 页码 {source['page_num']}, 相似度: {source['score']:.3f}")

                # 显示置信度
                if message.get("confidence"):
                    st.progress(message["confidence"])
                    st.caption(f"置信度: {message['confidence']:.1%}")

    # 用户输入
    if prompt := st.chat_input("请输入您的问题..."):
        # 显示用户消息
        with st.chat_message("user"):
            st.write(prompt)

        st.session_state.chat_history.append({
            "role": "user",
            "content": prompt
        })

        # 生成回答
        with st.chat_message("assistant"):
            with st.spinner("正在思考..."):
                try:
                    result = st.session_state.rag_system.query(prompt)

                    # 显示回答
                    st.markdown("### 📝 回答")
                    st.write(result.text)

                    # 显示图片
                    if result.images:
                        st.markdown("### 🖼️ 相关图片")
                        cols = st.columns(min(len(result.images), 3))
                        for i, img in enumerate(result.images):
                            with cols[i % 3]:
                                display_image(img["file_path"], width=300)
                                if img.get("page_num"):
                                    st.caption(f"📄 第 {img['page_num']} 页")

                    # 显示来源
                    if result.source_nodes:
                        with st.expander("📚 查看来源"):
                            for source in result.source_nodes:
                                st.write(f"- 页码 {source['page_num']}, 相似度: {source['score']:.3f}")

                    # 显示置信度
                    st.progress(result.confidence)
                    st.caption(f"置信度: {result.confidence:.1%}")

                    # 保存到历史
                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "content": result.text,
                        "images": result.images,
                        "sources": result.source_nodes,
                        "confidence": result.confidence
                    })

                except Exception as e:
                    st.error(f"查询出错: {e}")


if __name__ == "__main__":
    main()
