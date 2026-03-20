"""
LlamaParse + LlamaIndex + LangChain 完整示例
使用 LlamaParse 解析文档，构建 RAG 系统
"""

import os
from pathlib import Path

# ==========================================
# 配置
# ==========================================
LLAMA_CLOUD_API_KEY = "llx-RE39rob6rVmnYMnb3noMjBZvN8UzHIU1QoPYMMnfvqoI3AsI"
QWEN_API_KEY = "sk-62c3f30ff4764eb9b3e1dc94bac59530"
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen-flash"

# ==========================================
# 方式一：使用 LlamaParseRAGPipeline（推荐）
# ==========================================
def demo_pipeline():
    """使用 LlamaParseRAGPipeline 快速构建 RAG"""
    from llamaparse_processor import LlamaParseRAGPipeline

    # 初始化管道
    pipeline = LlamaParseRAGPipeline(
        llama_cloud_api_key=LLAMA_CLOUD_API_KEY,
        qwen_api_key=QWEN_API_KEY,
        storage_dir="./rag_storage",
        markdown_dir="./markdown_output",
    )

    # 检查是否有已存在的索引
    if not pipeline.load_existing_index():
        # 没有索引，导入文档
        print("首次运行，正在解析文档并构建索引...")
        doc_count = pipeline.ingest_documents("./data")
        print(f"已导入 {doc_count} 个文档片段")
    else:
        print("已加载现有索引")

    # 查询
    questions = [
        "冰箱的安装步骤是什么？",
        "如何降低冰箱能耗？",
        "冰箱使用时有哪些安全注意事项？",
    ]

    for q in questions:
        print(f"\n问题: {q}")
        answer = pipeline.query(q)
        print(f"回答: {answer}")


# ==========================================
# 方式二：手动集成到现有 main.py
# ==========================================
def demo_integrated():
    """集成到现有 LangChain Agent"""
    from langchain_openai import ChatOpenAI
    from langchain.agents import create_agent

    from llama_index.core import Settings
    from llama_index.llms.dashscope import DashScope
    from llama_index.embeddings.dashscope import DashScopeEmbedding

    from llamaparse_processor import LlamaParseProcessor

    # 1. 配置 LLM 和 Embedding
    Settings.llm = DashScope(model_name=MODEL, api_key=QWEN_API_KEY)
    Settings.embed_model = DashScopeEmbedding(
        model_name="text-embedding-v4",
        api_key=QWEN_API_KEY,
        embed_batch_size=10,  # DashScope API 限制批量大小不超过 10
    )
    Settings.embed_batch_size = 10  # 全局批量大小设置

    # 2. 使用 LlamaParse 解析文档
    processor = LlamaParseProcessor(
        api_key=LLAMA_CLOUD_API_KEY,
        storage_dir="./rag_storage/parse",
        markdown_dir="./markdown_output",
    )

    # 尝试加载已有索引
    index = processor.load_vector_index(embed_model=Settings.embed_model)

    if not index:
        # 解析文档
        print("正在解析文档...")
        documents = processor.parse_directory("./data")

        # 构建索引
        print("正在构建向量索引...")
        index = processor.build_vector_index(
            documents,
            embed_model=Settings.embed_model,
            persist=True,
        )

    # 3. 创建查询引擎
    query_engine = index.as_query_engine()

    # 4. 封装为 LangChain 工具
    from langchain_core.tools import tool

    @tool
    def retrieve_from_knowledge_base(query: str) -> str:
        """
        当用户询问特定领域、公司内部信息或需要查阅文档时，调用此工具检索信息。

        Args:
            query: 用户的具体问题或用于检索的关键词。
        """
        print(f"-> [Agent 正在调用工具] 检索关键词: {query}")
        response = query_engine.query(query)
        return str(response)

    # 5. 创建 LangChain Agent
    llm = ChatOpenAI(
        api_key=QWEN_API_KEY,
        base_url=QWEN_BASE_URL,
        model=MODEL,
        temperature=0.7,
    )

    system_prompt = """你是一个智能研发助手。在回答关于公司、文档内容或专业知识时，
请优先使用 `retrieve_from_knowledge_base` 工具从知识库中获取上下文。"""

    agent = create_agent(
        llm,
        system_prompt=system_prompt,
        tools=[retrieve_from_knowledge_base],
    )

    # 6. 运行查询
    user_question = "如何降低冰箱能耗？"
    print(f"\n用户提问: {user_question}")

    response = agent.invoke({"messages": [("human", user_question)]})
    print(f"\n回答: {response['messages'][-1].content}")


# ==========================================
# 方式三：仅解析文档保存 Markdown
# ==========================================
def demo_parse_only():
    """仅解析文档，保存为 Markdown"""
    from llamaparse_processor import LlamaParseProcessor

    processor = LlamaParseProcessor(
        api_key=LLAMA_CLOUD_API_KEY,
        markdown_dir="./markdown_output",
    )

    # 解析单个文件
    pdf_file = "./data/Web_DA68-04852M-03_SBS_FSR_RS5000FC_UX_CN.pdf"
    if Path(pdf_file).exists():
        documents = processor.parse_file(pdf_file)
        print(f"解析完成: {len(documents)} 个文档片段")

    # 批量解析目录
    # documents = processor.parse_directory("./data")


# ==========================================
# 方式四：增量更新文档
# ==========================================
def demo_incremental_update():
    """增量更新：只解析新增或修改的文件"""
    from llamaparse_processor import LlamaParseProcessor

    processor = LlamaParseProcessor(
        api_key=LLAMA_CLOUD_API_KEY,
        storage_dir="./rag_storage/parse",
    )

    # 默认会跳过已解析且未修改的文件
    documents = processor.parse_directory("./data")

    # 强制重新解析所有文件
    # documents = processor.parse_directory("./data", force_reparse=True)


# ==========================================
# 主程序
# ==========================================
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法:")
        print("  python llamaparse_demo.py pipeline    # 完整 RAG 管道")
        print("  python llamaparse_demo.py integrated  # 集成 LangChain")
        print("  python llamaparse_demo.py parse       # 仅解析文档")
        print("  python llamaparse_demo.py update      # 增量更新")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "pipeline":
        demo_pipeline()
    elif mode == "integrated":
        demo_integrated()
    elif mode == "parse":
        demo_parse_only()
    elif mode == "update":
        demo_incremental_update()
    else:
        print(f"未知模式: {mode}")
        sys.exit(1)
