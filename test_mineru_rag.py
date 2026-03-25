#!/usr/bin/env python3
"""
测试 MinerU RAG 系统完整流程
"""
import os
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from src.mineru_converter import convert_pdf_with_mineru
from src.unified_pipeline import UnifiedRAGSystem


def test_single_document():
    """测试单个文档处理"""
    print("=" * 60)
    print("测试单个文档处理")
    print("=" * 60)
    
    # 检查环境变量
    qwen_api_key = os.environ.get("QWEN_API_KEY")
    if not qwen_api_key:
        print("[错误] 请设置 QWEN_API_KEY 环境变量")
        return False
    
    # 查找测试 PDF
    data_dir = Path("./data")
    pdf_files = list(data_dir.glob("*.pdf"))
    
    if not pdf_files:
        print("[错误] data/ 目录中没有找到 PDF 文件")
        return False
    
    test_pdf = pdf_files[0]
    print(f"使用测试文件: {test_pdf}")
    
    try:
        # 处理 PDF
        processed_doc = convert_pdf_with_mineru(
            pdf_path=str(test_pdf),
            output_dir="./output",
            image_dir="./output/images",
            generate_descriptions=True,
            qwen_api_key=qwen_api_key
        )
        
        print(f"\n处理完成:")
        print(f"  标题: {processed_doc.title}")
        print(f"  章节数: {len(processed_doc.sections)}")
        print(f"  图片数: {len(processed_doc.images)}")
        print(f"  目录项: {len(processed_doc.toc)}")
        
        # 显示前几个章节
        print(f"\n前 3 个章节:")
        for i, section in enumerate(processed_doc.sections[:3]):
            print(f"  {i+1}. [{section.level}] {section.title} (第{section.page_num}页)")
            print(f"     图片数: {len(section.images)}")
            print(f"     描述数: {len(section.image_descriptions)}")
        
        # 显示前几张图片
        print(f"\n前 3 张图片:")
        for i, img in enumerate(processed_doc.images[:3]):
            print(f"  {i+1}. {img.image_id}")
            print(f"     页码: {img.page_num}")
            print(f"     尺寸: {img.width}x{img.height}")
            print(f"     位置: {img.bbox}")
        
        return True
        
    except Exception as e:
        print(f"[错误] 处理失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_rag_system():
    """测试完整的 RAG 系统"""
    print("\n" + "=" * 60)
    print("测试完整的 RAG 系统")
    print("=" * 60)
    
    qwen_api_key = os.environ.get("QWEN_API_KEY")
    if not qwen_api_key:
        print("[错误] 请设置 QWEN_API_KEY 环境变量")
        return False
    
    # 查找测试 PDF
    data_dir = Path("./data")
    pdf_files = list(data_dir.glob("*.pdf"))
    
    if not pdf_files:
        print("[错误] data/ 目录中没有找到 PDF 文件")
        return False
    
    try:
        # 创建 RAG 系统
        rag_system = UnifiedRAGSystem(
            qwen_api_key=qwen_api_key,
            persist_dir="./test_index",
            image_output_dir="./test_images",
            verbose=True,
        )
        
        # 处理所有 PDF
        all_processed_docs = []
        for pdf_file in pdf_files:
            print(f"\n处理: {pdf_file.name}")
            processed_doc = rag_system.process_pdf(str(pdf_file), generate_descriptions=True)
            all_processed_docs.append(processed_doc)
        
        if not all_processed_docs:
            print("[错误] 没有成功处理的文档")
            return False
        
        # 构建索引
        print(f"\n构建索引（{len(all_processed_docs)} 个文档）...")
        rag_system.build_index(all_processed_docs)
        
        # 测试查询
        test_queries = [
            "这份文档的主要内容是什么？",
            "文档中提到了哪些关键技术？",
        ]
        
        print(f"\n测试查询:")
        for query in test_queries:
            print(f"\n问题: {query}")
            try:
                result = rag_system.query(query)
                print(f"回答: {result['text'][:200]}...")
                print(f"置信度: {result['confidence']:.2%}")
                print(f"相关图片: {len(result['images'])} 张")
            except Exception as e:
                print(f"[查询失败] {e}")
        
        return True
        
    except Exception as e:
        print(f"[错误] RAG 系统测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    print("MinerU RAG 系统测试")
    print("=" * 60)
    
    success_count = 0
    total_tests = 2
    
    # 测试 1: 单文档处理
    if test_single_document():
        success_count += 1
    
    # 测试 2: 完整 RAG 系统
    if test_rag_system():
        success_count += 1
    
    print("\n" + "=" * 60)
    print(f"测试完成: {success_count}/{total_tests} 通过")
    print("=" * 60)
    
    return success_count == total_tests


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)