"""
统一多模态 RAG 系统主程序（新架构）

完整流程：
PDF -> Markdown(含目录) -> 章节解析 -> 图文关联 -> 图片描述 -> 向量索引 -> 查询

使用方法：
    # 构建知识库
    python main_unified.py --build --pdf ./data/document.pdf

    # 交互式查询
    python main_unified.py

    # 单次查询
    python main_unified.py --query "冰箱如何安装？"
"""
import os
import sys
from pathlib import Path

# 添加 src 目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.unified_pipeline import UnifiedRAGSystem, process_pdf_and_build_index

# ==========================================
# 配置
# ==========================================
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "sk-62c3f30ff4764eb9b3e1dc94bac59530")
LLAMA_CLOUD_API_KEY = os.environ.get("LLAMA_CLOUD_API_KEY", None)
DATA_DIR = "./data"  # PDF 文档目录
OUTPUT_DIR = "./output"  # 输出目录
INDEX_DIR = "./output/index"  # 索引存储目录
IMAGE_DIR = "./output/images"  # 提取的图片目录


def build_knowledge_base(pdf_path: str = None):
    """
    构建知识库

    Args:
        pdf_path: 单个 PDF 文件路径，如果为 None 则处理整个目录
    """
    print("=" * 60)
    print("构建统一多模态知识库")
    print("=" * 60)

    # 检查 API Key
    if not QWEN_API_KEY or QWEN_API_KEY == "xxxxxx":
        print("[错误] 请设置 QWEN_API_KEY 环境变量或在代码中配置")
        return None

    # 确定要处理的文件
    if pdf_path:
        pdf_files = [Path(pdf_path)]
    else:
        pdf_files = list(Path(DATA_DIR).glob("*.pdf"))

    if not pdf_files:
        print(f"[错误] 没有找到 PDF 文件")
        return None

    print(f"\n找到 {len(pdf_files)} 个 PDF 文件")

    # 创建统一的 RAG 系统
    rag_system = UnifiedRAGSystem(
        qwen_api_key=QWEN_API_KEY,
        llama_cloud_api_key=LLAMA_CLOUD_API_KEY,
        persist_dir=INDEX_DIR,
        image_output_dir=IMAGE_DIR,
        llm_model="qwen-flash",
        embedding_model="text-embedding-v4",
        vl_model="qwen-vl-max",
        verbose=True,
    )

    # 处理每个 PDF 文件
    all_processed_docs = []
    for i, pdf_file in enumerate(pdf_files, 1):
        print(f"\n[{i}/{len(pdf_files)}] 处理: {pdf_file.name}")
        print("-" * 60)

        try:
            processed_doc = rag_system.process_pdf(
                str(pdf_file),
                generate_descriptions=True
            )
            all_processed_docs.append(processed_doc)

            print(f"\n处理结果:")
            print(f"  标题: {processed_doc.title}")
            print(f"  章节数: {len(processed_doc.sections)}")
            print(f"  图片数: {len(processed_doc.images)}")
            print(f"  目录项: {len(processed_doc.toc)}")

        except Exception as e:
            print(f"[错误] 处理 {pdf_file.name} 失败: {e}")
            import traceback
            traceback.print_exc()
            continue

    if not all_processed_docs:
        print("[错误] 没有成功处理的文档")
        return None

    # 构建索引（支持多个文档）
    print("\n" + "=" * 60)
    print("构建向量索引")
    print("=" * 60)

    rag_system.build_index(all_processed_docs)

    # 打印统计信息
    print("\n" + "=" * 60)
    print("知识库构建完成！")
    print("=" * 60)

    return rag_system


def load_knowledge_base():
    """加载已构建的知识库"""
    print("=" * 60)
    print("加载知识库")
    print("=" * 60)

    rag_system = UnifiedRAGSystem(
        qwen_api_key=QWEN_API_KEY,
        llama_cloud_api_key=LLAMA_CLOUD_API_KEY,
        persist_dir=INDEX_DIR,
        image_output_dir=IMAGE_DIR,
        llm_model="qwen-flash",
        embedding_model="text-embedding-v4",
        vl_model="qwen-vl-max",
        verbose=True,
    )

    if rag_system.load_index():
        print("\n知识库加载成功！")
        return rag_system
    else:
        print("\n[错误] 未找到已保存的知识库")
        return None


def format_result(result: dict) -> str:
    """格式化查询结果"""
    output = []

    # 回答
    output.append("=" * 60)
    output.append("【回答】")
    output.append(result.get("text", ""))
    output.append("")

    # 图片信息
    images = result.get("images", [])
    if images:
        output.append("=" * 60)
        output.append(f"【相关图片】共 {len(images)} 张")
        output.append("")

        for i, img in enumerate(images[:5], 1):  # 最多显示5张
            output.append(f"图片 {i}:")
            output.append(f"  ID: {img.get('image_id', 'N/A')}")
            if img.get('description'):
                output.append(f"  描述: {img['description'][:100]}...")
            if img.get('category'):
                output.append(f"  类别: {img['category']}")
            if img.get('keywords'):
                output.append(f"  关键词: {', '.join(img['keywords'][:5])}")
            output.append("")

    # 来源
    source_nodes = result.get("source_nodes", [])
    if source_nodes:
        output.append("=" * 60)
        output.append("【参考来源】")
        for node in source_nodes[:5]:
            node_type = node.get('node_type', 'text')
            type_label = "图片描述" if node_type == "image_description" else "文本"
            output.append(f"  - [{type_label}] 相似度: {node.get('score', 0):.3f}")
        output.append("")

    # 置信度
    output.append("=" * 60)
    output.append(f"置信度: {result.get('confidence', 0):.1%}")

    return "\n".join(output)


def interactive_query(rag_system: UnifiedRAGSystem):
    """交互式问答"""
    print("\n" + "=" * 60)
    print("统一多模态智能问答系统")
    print("输入 'quit' 或 'q' 退出")
    print("=" * 60)

    while True:
        try:
            query = input("\n请输入问题: ").strip()

            if query.lower() in ['quit', 'exit', 'q']:
                print("\n感谢使用，再见！")
                break

            if not query:
                continue

            # 执行查询
            print("\n正在查询...")
            result = rag_system.query(query)

            # 显示结果
            print(format_result(result))

        except KeyboardInterrupt:
            print("\n\n感谢使用，再见！")
            break
        except Exception as e:
            print(f"\n查询出错: {e}")
            import traceback
            traceback.print_exc()


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="统一多模态 RAG 智能问答系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 构建知识库
  python main_unified.py --build
  python main_unified.py --build --pdf ./data/doc.pdf

  # 交互式查询
  python main_unified.py

  # 单次查询
  python main_unified.py --query "冰箱如何安装？"
        """
    )

    parser.add_argument(
        "--build",
        action="store_true",
        help="构建知识库"
    )
    parser.add_argument(
        "--pdf",
        type=str,
        help="指定单个 PDF 文件路径"
    )
    parser.add_argument(
        "--query",
        type=str,
        help="直接查询（非交互模式）"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./output",
        help="输出目录（默认: ./output）"
    )

    args = parser.parse_args()

    # 更新输出目录
    global OUTPUT_DIR, INDEX_DIR, IMAGE_DIR
    OUTPUT_DIR = args.output_dir
    INDEX_DIR = f"{OUTPUT_DIR}/index"
    IMAGE_DIR = f"{OUTPUT_DIR}/images"

    # 创建输出目录
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(INDEX_DIR).mkdir(parents=True, exist_ok=True)
    Path(IMAGE_DIR).mkdir(parents=True, exist_ok=True)

    # 构建或加载知识库
    if args.build:
        rag_system = build_knowledge_base(args.pdf)
    else:
        rag_system = load_knowledge_base()
        if not rag_system:
            print("\n未找到知识库，正在构建...")
            rag_system = build_knowledge_base(args.pdf)

    if not rag_system:
        print("[错误] 知识库初始化失败")
        return

    # 查询模式
    if args.query:
        # 单次查询模式
        print("\n" + "=" * 60)
        result = rag_system.query(args.query)
        print(format_result(result))
    else:
        # 交互模式
        interactive_query(rag_system)


if __name__ == "__main__":
    main()
