#!/usr/bin/env python3
"""
测试 MinerU 转换功能（无需 API 密钥）
"""
import os
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from src.mineru_converter import StableMinerUConverter


def test_mineru_conversion():
    """测试 MinerU 转换功能"""
    print("=" * 60)
    print("测试 MinerU 转换功能")
    print("=" * 60)
    
    # 查找测试 PDF
    data_dir = Path("./data")
    pdf_files = list(data_dir.glob("*.pdf"))
    
    if not pdf_files:
        print("[错误] data/ 目录中没有找到 PDF 文件")
        # 创建一个简单的测试 PDF
        print("创建测试 PDF...")
        create_test_pdf()
        pdf_files = list(data_dir.glob("*.pdf"))
    
    if not pdf_files:
        print("[错误] 仍然没有找到 PDF 文件")
        return False
    
    test_pdf = pdf_files[0]
    print(f"使用测试文件: {test_pdf}")
    
    try:
        # 创建转换器
        converter = StableMinerUConverter(
            image_output_dir="./test_output/images",
            verbose=True
        )
        
        # 转换 PDF
        markdown_content, toc, images, doc_info = converter.convert(str(test_pdf))
        
        print(f"\n转换完成:")
        print(f"  标题: {doc_info.get('title', 'Unknown')}")
        print(f"  总页数: {doc_info.get('total_pages', 0)}")
        print(f"  目录项数: {len(toc)}")
        print(f"  图片数: {len(images)}")
        print(f"  Markdown 长度: {len(markdown_content)} 字符")
        
        # 显示前几个目录项
        if toc:
            print(f"\n前 5 个目录项:")
            for i, item in enumerate(toc[:5]):
                print(f"  {i+1}. [{item.level}] {item.title} (第{item.page_num}页)")
        
        # 显示前几张图片信息
        if images:
            print(f"\n前 3 张图片:")
            for i, img in enumerate(images[:3]):
                print(f"  {i+1}. {img.image_id}")
                print(f"     页码: {img.page_num}")
                print(f"     尺寸: {img.width}x{img.height}")
                print(f"     位置: {img.bbox}")
        
        # 保存结果
        output_dir = Path("./test_output")
        output_dir.mkdir(exist_ok=True)
        
        md_file = output_dir / f"{test_pdf.stem}.md"
        md_file.write_text(markdown_content, encoding="utf-8")
        print(f"\nMarkdown 已保存到: {md_file}")
        
        return True
        
    except Exception as e:
        print(f"[错误] 转换失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def create_test_pdf():
    """创建一个简单的测试 PDF"""
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        
        data_dir = Path("./data")
        data_dir.mkdir(exist_ok=True)
        
        pdf_path = data_dir / "test_document.pdf"
        
        # 创建 PDF
        c = canvas.Canvas(str(pdf_path), pagesize=letter)
        width, height = letter
        
        # 第一页
        c.setFont("Helvetica", 16)
        c.drawString(50, height - 50, "测试文档")
        
        c.setFont("Helvetica", 12)
        c.drawString(50, height - 100, "这是第一段内容")
        c.drawString(50, height - 120, "包含一些测试文本")
        
        # 添加简单图形
        c.rect(100, 300, 200, 100, fill=1)
        c.drawString(150, 350, "这是一个矩形")
        
        c.showPage()
        
        # 第二页
        c.setFont("Helvetica", 16)
        c.drawString(50, height - 50, "第二章")
        
        c.setFont("Helvetica", 12)
        c.drawString(50, height - 100, "这是第二页的内容")
        c.drawString(50, height - 120, "更多测试文本")
        
        c.save()
        
        print(f"已创建测试 PDF: {pdf_path}")
        
    except ImportError:
        print("需要安装 reportlab: pip install reportlab")
        return False
    except Exception as e:
        print(f"创建测试 PDF 失败: {e}")
        return False


def main():
    """主函数"""
    success = test_mineru_conversion()
    
    print("\n" + "=" * 60)
    if success:
        print("✅ MinerU 转换测试通过")
    else:
        print("❌ MinerU 转换测试失败")
    print("=" * 60)
    
    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)