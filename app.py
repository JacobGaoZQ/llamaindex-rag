"""
增强的多模态 RAG 系统 Web UI
使用 Streamlit 构建
支持图片描述生成和智能问答
"""
import os
import sys
import base64
from pathlib import Path

import streamlit as st

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from src.unified_pipeline import UnifiedRAGSystem


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
    """构建知识库（使用 UnifiedRAGSystem，支持 Markdown 生成）"""
    with st.status("正在构建知识库...", expanded=True) as status:
        # 创建 Unified RAG 系统
        st.write("🔧 初始化 RAG 系统...")
        rag_system = UnifiedRAGSystem(
            qwen_api_key=api_key,
            persist_dir="./unified_index",
            image_output_dir="./output/images",
            llm_model="qwen-flash",
            embedding_model="text-embedding-v4",
            vl_model="qwen-vl-max"
        )

        # 处理 data 目录中的所有 PDF
        st.write("📁 处理 PDF 文档...")
        pdf_files = list(Path(data_dir).glob("*.pdf"))
        
        if not pdf_files:
            status.error(f"在 {data_dir} 中未找到 PDF 文件")
            return None
            
        st.write(f"找到 {len(pdf_files)} 个 PDF 文件")
        
        # 处理第一个 PDF（演示用）
        pdf_path = pdf_files[0]
        st.write(f"正在处理: {pdf_path.name}")
        
        try:
            # 处理 PDF 并生成 Markdown
            processed_doc = rag_system.process_pdf(str(pdf_path), generate_descriptions=True)
            
            # 构建向量索引
            st.write("🔍 构建向量索引...")
            rag_system.build_index(processed_doc)
            
            st.write(f"✅ 处理完成:")
            st.write(f"   - 标题: {processed_doc.title}")
            st.write(f"   - 章节数: {len(processed_doc.sections)}")
            st.write(f"   - 图片数: {len(processed_doc.images)}")
            st.write(f"   - Markdown 已保存到: ./unified_index/{pdf_path.stem}.md")
            
            status.update(label="✅ 知识库构建完成!", state="complete")
            return rag_system
            
        except Exception as e:
            status.error(f"处理失败: {e}")
            return None


def load_knowledge_base(api_key: str):
    """加载已保存的知识库"""
    rag_system = UnifiedRAGSystem(
        qwen_api_key=api_key,
        persist_dir="./unified_index",
        image_output_dir="./output/images",
        llm_model="qwen-flash",
        embedding_model="text-embedding-v4",
        vl_model="qwen-vl-max"
    )

    if rag_system.load_index():
        stats = rag_system.index.docstore.docs if rag_system.index else {}
        print(f"加载完成: {len(stats)} 个文档")
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

        if st.session_state.rag_system and st.session_state.rag_system.index:
            # 显示索引统计信息
            docstore = st.session_state.rag_system.index.docstore.docs
            total_docs = len(docstore)
            
            # 分类统计
            text_nodes = len([d for d in docstore.values() if d.metadata.get('node_type') != 'image_description'])
            image_nodes = len([d for d in docstore.values() if d.metadata.get('node_type') == 'image_description'])
            
            col_a, col_b = st.columns(2)
            with col_a:
                st.metric("总节点数", total_docs)
                st.metric("文本节点", text_nodes)
            with col_b:
                st.metric("图片描述节点", image_nodes)
                st.metric("图片总数", len(st.session_state.rag_system.images) if hasattr(st.session_state.rag_system, 'images') else "N/A")
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

                # 显示图片描述
                if message.get("image_descriptions"):
                    with st.expander("🖼️ 图片描述详情", expanded=False):
                        for i, desc in enumerate(message["image_descriptions"], 1):
                            st.markdown(f"**图片 {i}** ({desc.get('category', '未知类别')})")
                            st.write(desc.get("description", ""))
                            if desc.get("keywords"):
                                st.caption(f"关键词: {', '.join(desc['keywords'])}")
                            st.markdown("---")

                # 显示图片
                if message.get("images"):
                    st.markdown("### 🖼️ 相关图片")
                    cols = st.columns(min(len(message["images"]), 3))
                    for i, img in enumerate(message["images"]):
                        with cols[i % 3]:
                            # 构建图片路径
                            image_path = f"./output/images/{img['image_id']}.png"
                            if not os.path.exists(image_path):
                                for ext in ['.png', '.jpg', '.jpeg']:
                                    test_path = f"./output/images/{img['image_id']}{ext}"
                                    if os.path.exists(test_path):
                                        image_path = test_path
                                        break
                            
                            display_image(image_path, width=300)
                            if img.get("description"):
                                st.caption(f"📝 {img['description'][:50]}...")

                # 显示来源
                if message.get("sources"):
                    with st.expander("📚 查看来源"):
                        for source in message["sources"]:
                            node_type = source.get('node_type', 'text')
                            type_label = "🖼️ 图片描述" if node_type == "image_description" else "📄 文本"
                            st.write(f"- {type_label}: {source['text'][:100]}...")

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
                    st.write(result["text"])

                    # 显示图片描述
                    if result.get("image_descriptions"):
                        with st.expander("🖼️ 图片描述详情", expanded=False):
                            for i, desc in enumerate(result["image_descriptions"], 1):
                                st.markdown(f"**图片 {i}** ({desc.get('category', '未知类别')})")
                                st.write(desc.get("description", ""))
                                if desc.get("keywords"):
                                    st.caption(f"关键词: {', '.join(desc['keywords'])}")
                                st.markdown("---")

                    # 显示图片
                    if result.get("images"):
                        st.markdown("### 🖼️ 相关图片")
                        cols = st.columns(min(len(result["images"]), 3))
                        for i, img in enumerate(result["images"]):
                            with cols[i % 3]:
                                # 构建图片路径（相对于 persist_dir）
                                image_path = f"./output/images/{img['image_id']}.png"
                                if not os.path.exists(image_path):
                                    # 尝试其他可能的路径
                                    for ext in ['.png', '.jpg', '.jpeg']:
                                        test_path = f"./output/images/{img['image_id']}{ext}"
                                        if os.path.exists(test_path):
                                            image_path = test_path
                                            break
                                
                                display_image(image_path, width=300)
                                if img.get("description"):
                                    st.caption(f"📝 {img['description'][:50]}...")

                    # 显示来源
                    if result.get("source_nodes"):
                        with st.expander("📚 查看来源"):
                            for source in result["source_nodes"]:
                                node_type = source.get('node_type', 'text')
                                type_label = "🖼️ 图片描述" if node_type == "image_description" else "📄 文本"
                                st.write(f"- {type_label}: {source['text'][:100]}...")

                    # 显示置信度
                    st.progress(result["confidence"])
                    st.caption(f"置信度: {result['confidence']:.1%}")

                    # 保存到历史
                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "content": result["text"],
                        "images": result.get("images", []),
                        "image_descriptions": result.get("image_descriptions", []),
                        "sources": result.get("source_nodes", []),
                        "confidence": result["confidence"]
                    })

                except Exception as e:
                    st.error(f"查询出错: {e}")


if __name__ == "__main__":
    main()
