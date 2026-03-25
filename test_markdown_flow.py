#!/usr/bin/env python3
"""
测试 PDF 转 Markdown 流程
"""
import os
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent))

from src.pdf_to_markdown import PDFToMarkdownConverter, MarkdownIndexBuilder


def test_conversion():
    """测试 PDF 转 Markdown"""
    print("=" * 60)
    print("步骤 1: PDF 转 Markdown")
    print("=" * 60)

    # 获取 API Key
    llama_cloud_api_key = os.environ.get("LLAMA_CLOUD_API_KEY")
    if not llama_cloud_api_key:
        print("[错误] 请设置 LLAMA_CLOUD_API_KEY 环境变量")
        return None

    # 查找 PDF 文件
    pdf_files = [f for f in Path("./data").glob("*.pdf") if f.stat().st_size > 10000]
    if not pdf_files:
        print("[错误] 没有找到有效的 PDF 文件")
        return None

    pdf_path = str(pdf_files[0])
    print(f"处理文件: {pdf_path}")

    # 转换
    converter = PDFToMarkdownConverter(
        llama_cloud_api_key=llama_cloud_api_key,
        image_output_dir="./output/images",
        verbose=True,
    )

    result = converter.convert(pdf_path, "./output/markdown")

    print(f"\n转换结果:")
    print(f"  源文件: {result.source_file}")
    print(f"  Markdown 长度: {len(result.markdown_content)} 字符")
    print(f"  提取图片: {len(result.images)} 张")
    print(f"  图片目录: {result.image_dir}")

    # 显示前几张图片
    if result.images:
        print(f"\n前3张图片:")
        for i, img in enumerate(result.images[:3], 1):
            print(f"  {i}. {img.image_id}")
            print(f"     路径: {img.file_path}")
            print(f"     页码: {img.page_num}")
            if img.caption:
                print(f"     标题: {img.caption}")

    return result


def test_index_building(processed_doc):
    """测试索引构建"""
    print("\n" + "=" * 60)
    print("步骤 2: 构建索引")
    print("=" * 60)

    qwen_api_key = os.environ.get("QWEN_API_KEY")
    if not qwen_api_key:
        print("[错误] 请设置 QWEN_API_KEY 环境变量")
        return None

    # 创建索引构建器
    builder = MarkdownIndexBuilder(
        qwen_api_key=qwen_api_key,
        persist_dir="./output/index",
    )

    # 构建索引
    md_file = f"./output/markdown/{Path(processed_doc.source_file).stem}.md"
    index = builder.build_index_from_markdown(
        [md_file],
        {img.image_id: img for img in processed_doc.images}
    )

    # 保存索引
    builder.save_index(index)

    print(f"索引构建完成:")
    print(f"  节点数: {len(index.docstore.docs)}")
    print(f"  图片元数据: {len(builder.image_metadata)}")

    return index, builder


def test_query(index, builder):
    """测试查询"""
    print("\n" + "=" * 60)
    print("步骤 3: 测试查询")
    print("=" * 60)

    # 创建查询引擎
    query_engine = index.as_query_engine()

    # 测试查询
    questions = [
        "冰箱如何安装？",
        "安全注意事项有哪些？",
        "如何清洁冰箱？",
    ]

    for question in questions:
        print(f"\n问题: {question}")
        response = query_engine.query(question)
        print(f"回答: {str(response)[:200]}...")


def main():
    """主函数"""
    # 创建输出目录
    Path("./output").mkdir(exist_ok=True)
    Path("./output/markdown").mkdir(exist_ok=True)
    Path("./output/images").mkdir(exist_ok=True)
    Path("./output/index").mkdir(exist_ok=True)

    # 执行流程
    processed_doc = test_conversion()
    if not processed_doc:
        return

    index, builder = test_index_building(processed_doc)
    if not index:
        return

    test_query(index, builder)

    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)
    print("\n输出文件:")
    print("  - Markdown: ./output/markdown/")
    print("  - 图片: ./output/images/")
    print("  - 索引: ./output/index/")


if __name__ == "__main__":
    main()
