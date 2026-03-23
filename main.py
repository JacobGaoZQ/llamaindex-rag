"""
多模态 RAG 系统主程序
支持智能家居产品文档的图文智能问答
"""
import os
import sys

# 添加 src 目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.pdf_parser import parse_pdf_directory
from src.multimodal_query import MultimodalRAGSystem, format_result

# ==========================================
# 配置
# ==========================================
QWEN_API_KEY = "sk-62c3f30ff4764eb9b3e1dc94bac59530"  # 请替换为你的 API Key
DATA_DIR = "./data"  # PDF 文档目录
INDEX_DIR = "./multimodal_index"  # 索引存储目录
IMAGE_DIR = "./extracted_images"  # 提取的图片目录
METADATA_DIR = "./parsed_metadata"  # 解析元数据目录


def build_knowledge_base():
    """构建知识库（首次运行或更新文档时执行）"""
    print("=" * 60)
    print("开始构建多模态知识库...")
    print("=" * 60)

    # 第一步：解析 PDF 文档，提取文字和图片
    print("\n[1/2] 解析 PDF 文档...")
    parsed_docs = parse_pdf_directory(
        input_dir=DATA_DIR,
        output_dir=IMAGE_DIR,
        metadata_dir=METADATA_DIR
    )

    if not parsed_docs:
        print("错误：未找到可解析的 PDF 文档")
        return None

    # 第二步：构建多模态索引
    print("\n[2/2] 构建向量索引...")
    rag_system = MultimodalRAGSystem(
        api_key=QWEN_API_KEY,
        persist_dir=INDEX_DIR,
        llm_model="qwen-flash",
        embedding_model="text-embedding-v4"
    )

    rag_system.build_from_documents(parsed_docs)

    # 打印统计信息
    stats = rag_system.get_stats()
    print("\n" + "=" * 60)
    print("知识库构建完成！")
    print(f"  - 总图片数: {stats['total_images']}")
    print(f"  - 总节点数: {stats['total_nodes']}")
    print(f"  - 包含图片的节点: {stats['nodes_with_images']}")
    print("=" * 60)

    return rag_system


def load_knowledge_base():
    """加载已构建的知识库"""
    rag_system = MultimodalRAGSystem(
        api_key=QWEN_API_KEY,
        persist_dir=INDEX_DIR,
        llm_model="qwen-flash",
        embedding_model="text-embedding-v4"
    )

    if rag_system.load_index():
        stats = rag_system.get_stats()
        print(f"知识库加载成功！")
        print(f"  - 总图片数: {stats['total_images']}")
        print(f"  - 总节点数: {stats['total_nodes']}")
        return rag_system
    else:
        print("未找到已保存的知识库，需要重新构建")
        return None


def interactive_query(rag_system: MultimodalRAGSystem):
    """交互式问答"""
    print("\n" + "=" * 60)
    print("多模态智能问答系统（输入 'quit' 退出）")
    print("=" * 60)

    while True:
        try:
            query = input("\n请输入问题: ").strip()

            if query.lower() in ['quit', 'exit', 'q']:
                print("感谢使用，再见！")
                break

            if not query:
                continue

            # 执行查询
            result = rag_system.query(query)

            # 显示结果
            print(format_result(result))

        except KeyboardInterrupt:
            print("\n\n感谢使用，再见！")
            break
        except Exception as e:
            print(f"查询出错: {e}")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="多模态 RAG 智能问答系统")
    parser.add_argument("--build", action="store_true", help="重新构建知识库")
    parser.add_argument("--query", type=str, help="直接查询（非交互模式）")
    args = parser.parse_args()

    # 检查 API Key
    if QWEN_API_KEY == "xxxxxx":
        print("请设置 QWEN_API_KEY 环境变量或在代码中配置")
        return

    # 构建或加载知识库
    if args.build:
        rag_system = build_knowledge_base()
    else:
        rag_system = load_knowledge_base()
        if not rag_system:
            print("\n正在构建知识库...")
            rag_system = build_knowledge_base()

    if not rag_system:
        print("知识库初始化失败")
        return

    # 查询模式
    if args.query:
        # 单次查询模式
        result = rag_system.query(args.query)
        print(format_result(result))
    else:
        # 交互模式
        interactive_query(rag_system)


if __name__ == "__main__":
    main()
