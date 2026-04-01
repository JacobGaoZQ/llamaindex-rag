#!/usr/bin/env python3
"""
修复图片描述缺失问题的脚本
"""

import os
import sys
import json
from pathlib import Path

# 添加 src 目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.unified_pipeline import UnifiedRAGSystem
from src.image_descriptor import ImageDescriptor

def fix_missing_image_descriptions():
    """修复缺失的图片描述"""
    
    # 配置
    QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "sk-62c3f30ff4764eb9b3e1dc94bac59530")
    INDEX_DIR = "./unified_index"
    IMAGE_DIR = "./output/images"
    
    print("=" * 60)
    print("修复图片描述缺失问题")
    print("=" * 60)
    
    # 初始化系统
    rag_system = UnifiedRAGSystem(
        qwen_api_key=QWEN_API_KEY,
        persist_dir=INDEX_DIR,
        image_output_dir=IMAGE_DIR,
        verbose=True,
    )
    
    # 加载现有索引
    print("\n1. 加载现有索引...")
    if not rag_system.load_index():
        print("❌ 索引加载失败")
        return False
    
    print(f"✅ 索引加载成功")
    print(f"   当前图片描述数量: {len(rag_system.image_descriptions)}")
    
    # 检查哪些图片缺少描述
    images_needing_descriptions = []
    for img in rag_system.images:
        if img.image_id not in rag_system.image_descriptions:
            images_needing_descriptions.append({
                "image_id": img.image_id,
                "file_path": img.file_path,
                "context": "冰箱使用手册相关图片"  # 默认上下文
            })
    
    print(f"   需要生成描述的图片数量: {len(images_needing_descriptions)}")
    
    if not images_needing_descriptions:
        print("✅ 所有图片都有描述，无需修复")
        return True
    
    # 生成缺失的图片描述
    print("\n2. 生成图片描述...")
    try:
        image_descriptor = ImageDescriptor(
            api_key=QWEN_API_KEY,
            model="qwen-vl-max"
        )
        
        # 批量生成描述
        new_descriptions = image_descriptor.batch_describe(
            images_needing_descriptions,
            max_workers=3,
            progress_callback=lambda i, total: print(f"   进度: {i}/{total}")
        )
        
        # 更新图片描述映射
        for desc in new_descriptions:
            rag_system.image_descriptions[desc.image_id] = desc
            
        print(f"✅ 成功生成 {len(new_descriptions)} 个图片描述")
        
    except Exception as e:
        print(f"❌ 生成图片描述失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # 重新构建索引（只更新图片描述节点）
    print("\n3. 更新索引...")
    try:
        # 重新构建索引，这次包含图片描述
        processed_docs = []  # 需要从元数据重建
        
        # 从 document_metadata.json 重建 ProcessedDocument
        metadata_path = Path(INDEX_DIR) / "document_metadata.json"
        if metadata_path.exists():
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            
            # 这里需要重建 ProcessedDocument 对象
            # 为简化，我们可以直接重新处理 PDF
            print("   重新处理 PDF 以包含图片描述...")
            
            # 获取原始 PDF 路径
            if metadata.get("documents"):
                source_file = metadata["documents"][0]["source_file"]
                if Path(source_file).exists():
                    # 重新处理并构建索引
                    print(f"   处理文件: {source_file}")
                    processed_doc = rag_system.process_pdf(
                        source_file,
                        generate_descriptions=True
                    )
                    rag_system.build_index(processed_doc, force=True)
                    print("✅ 索引更新完成")
                    return True
                else:
                    print(f"❌ 原始 PDF 文件不存在: {source_file}")
                    return False
        else:
            print("❌ 无法找到元数据文件")
            return False
            
    except Exception as e:
        print(f"❌ 索引更新失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

def verify_fix():
    """验证修复结果"""
    print("\n4. 验证修复结果...")
    
    QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "sk-62c3f30ff4764eb9b3e1dc94bac59530")
    INDEX_DIR = "./unified_index"
    IMAGE_DIR = "./output/images"
    
    rag_system = UnifiedRAGSystem(
        qwen_api_key=QWEN_API_KEY,
        persist_dir=INDEX_DIR,
        image_output_dir=IMAGE_DIR,
        verbose=True,
    )
    
    if rag_system.load_index():
        print(f"   图片总数: {len(rag_system.images)}")
        print(f"   图片描述数: {len(rag_system.image_descriptions)}")
        
        # 测试查询
        result = rag_system.query("冰箱如何安装？")
        print(f"   查询结果中的图片数: {len(result['images'])}")
        
        if result['images']:
            first_img = result['images'][0]
            print(f"   第一张图片描述: {first_img.get('description', '无')[:50]}...")
            print(f"   第一张图片类别: {first_img.get('category', '无')}")
        
        return len(rag_system.image_descriptions) > 0
    return False

if __name__ == "__main__":
    success = fix_missing_image_descriptions()
    if success:
        if verify_fix():
            print("\n🎉 修复成功！图片描述功能已恢复正常。")
        else:
            print("\n⚠️  修复完成，但验证失败。")
    else:
        print("\n❌ 修复失败。")