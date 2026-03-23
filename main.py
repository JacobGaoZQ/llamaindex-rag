from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain.agents.middleware import LLMToolSelectorMiddleware
from langchain.agents import create_agent

from llama_index.core import Settings, VectorStoreIndex, SimpleDirectoryReader
from llama_index.llms.dashscope import DashScope, DashScopeGenerationModels
from llama_index.embeddings.dashscope import DashScopeEmbedding, DashScopeTextEmbeddingModels
import os

# ==========================================
# 第一步：使用 LlamaIndex 构建本地知识库与查询引擎
# ==========================================
print("正在加载文档并构建 LlamaIndex 知识库...")
# 假设你的当前目录下有一个 "data" 文件夹存放了文本文档或 PDF
# 设置 API Key (从环境变量读取)
QWEN_API_KEY = "xxxxxx"  # 请替换为你的 API Key 或使用环境变量
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen-flash"

# 配置 LLM (使用 Qwen-Max 或 Qwen-Turbo)
# dashscope.api_key = QWEN_API_KEY  # 必须加这一行，解决底层 SDK 验证问题
Settings.llm = DashScope(model_name=MODEL, api_key=QWEN_API_KEY)

# 配置 Embedding (使用 Qwen 专属的向量模型)
Settings.embed_model = DashScopeEmbedding(model_name="text-embedding-v4",
                                          api_key=QWEN_API_KEY)

# Settings.embed_model = DashScopeEmbedding(model_name="tongyi-embedding-vision-flash",
#                                           api_key=QWEN_API_KEY)

# Settings.embed_model = DashScopeEmbedding(model_name="tongyi-embedding-vision-flash",
#                                           api_key=QWEN_API_KEY)

# 正常加载数据并创建索引
documents = SimpleDirectoryReader("./data").load_data()
index = VectorStoreIndex.from_documents(documents)
# 查询
query_engine = index.as_query_engine()


# ==========================================
# 第二步：使用 LangChain 的 @tool 装饰器封装 LlamaIndex
# ==========================================
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


# ==========================================
# 第三步：初始化 LangChain Agent 并挂载工具
# ==========================================
# 使用 LangChain 的 ChatOpenAI 连接 Qwen 模型
llm = ChatOpenAI(
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    model=MODEL,
    temperature=0.7
)

# 定义工具列表
system_prompt = "你是一个智能研发助手。在回答关于公司、文档内容或专业知识时，请优先使用 `retrieve_from_knowledge_base` 工具从知识库中获取上下文。"
tools = [retrieve_from_knowledge_base]

# 使用 LangGraph 创建 ReAct Agent
agent = create_agent(
    llm,
    system_prompt=system_prompt,
    tools=tools
)

# ==========================================
# 第四步：运行 Agent 进行提问
# ==========================================
user_question = "冰箱的安装步骤是什么？"
print(f"\n用户提问: {user_question}")

# LangChain 的自主循环：理解意图 -> 决定调用检索工具 -> 获取检索结果 -> 综合生成最终答案
response = agent.invoke({"messages": [("human", user_question)]})
print(f"\n回答: {response['messages'][-1].content}")
