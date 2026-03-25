"""
多模态 RAG 系统
支持文字 + 图片的智能问答
"""
# 统一管道（推荐）
from .unified_pipeline import (
    UnifiedRAGSystem,
    PDFToMarkdownConverter,
    MarkdownParser,
    TOCItem,
    Section,
    ImageInfo,
    ProcessedDocument,
    process_pdf_and_build_index,
)

__all__ = [
    # 统一管道
    'UnifiedRAGSystem',
    'PDFToMarkdownConverter',
    'MarkdownParser',
    'TOCItem',
    'Section',
    'ImageInfo',
    'ProcessedDocument',
    'process_pdf_and_build_index',
]
