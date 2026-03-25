"""
多模态 RAG 系统
支持文字 + 图片的智能问答
"""
# 统一管道（推荐）
from .unified_pipeline import (
    UnifiedRAGSystem,
    MarkdownParser,
    TOCItem,
    Section,
    ImageInfo,
    ProcessedDocument,
    process_pdf_and_build_index,
)

# 稳定的 MinerU 转换器
from .mineru_converter import (
    StableMinerUConverter,
    convert_pdf_with_mineru,
)

__all__ = [
    # 统一管道
    'UnifiedRAGSystem',
    'MarkdownParser',
    'TOCItem',
    'Section',
    'ImageInfo',
    'ProcessedDocument',
    'process_pdf_and_build_index',
    
    # MinerU 转换器
    'StableMinerUConverter',
    'convert_pdf_with_mineru',
]
