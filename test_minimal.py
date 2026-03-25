#!/usr/bin/env python3
"""
最小化测试脚本
"""
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

def test_imports():
    """测试导入功能"""
    print("测试模块导入...")
    
    try:
        from src.mineru_converter import StableMinerUConverter
        print("✅ StableMinerUConverter 导入成功")
    except Exception as e:
        print(f"❌ StableMinerUConverter 导入失败: {e}")
        return False
    
    try:
        from src.unified_pipeline import UnifiedRAGSystem
        print("✅ UnifiedRAGSystem 导入成功")
    except Exception as e:
        print(f"❌ UnifiedRAGSystem 导入失败: {e}")
        return False
    
    try:
        from src.image_descriptor import ImageDescriptor
        print("✅ ImageDescriptor 导入成功")
    except Exception as e:
        print(f"❌ ImageDescriptor 导入失败: {e}")
        return False
    
    return True

def test_dataclass():
    """测试数据类"""
    print("\n测试数据类...")
    
    try:
        from src.mineru_converter import TOCItem, ImageInfo, Section, ProcessedDocument
        print("✅ 数据类导入成功")
        
        # 创建测试实例
        toc_item = TOCItem(level=1, title="测试", page_num=1, anchor="test")
        print(f"✅ TOCItem 创建成功: {toc_item}")
        
        image_info = ImageInfo(
            image_id="test_001",
            file_path="/tmp/test.png",
            page_num=1,
            bbox=(0, 0, 100, 100),
            width=100,
            height=100
        )
        print(f"✅ ImageInfo 创建成功: {image_info.image_id}")
        
        return True
    except Exception as e:
        print(f"❌ 数据类测试失败: {e}")
        return False

def main():
    """主函数"""
    print("MinerU RAG 系统最小化测试")
    print("=" * 50)
    
    success_count = 0
    total_tests = 2
    
    if test_imports():
        success_count += 1
    
    if test_dataclass():
        success_count += 1
    
    print("\n" + "=" * 50)
    print(f"测试结果: {success_count}/{total_tests} 通过")
    
    if success_count == total_tests:
        print("🎉 所有基本测试通过！")
        return True
    else:
        print("❌ 部分测试失败")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)