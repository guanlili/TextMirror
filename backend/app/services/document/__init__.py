"""TextMirror 文档处理服务——子包入口，re-export 保持外部导入路径不变。"""
from .extract_html import extract_html_from_file
from .extract_text import _extract_docx, extract_text_from_file
from .generate import (
    ReviewDocxError,
    generate_corrected_docx,
    generate_corrected_txt,
    generate_reviewed_docx,
)

__all__ = [
    "extract_html_from_file",
    "extract_text_from_file",
    "_extract_docx",
    "ReviewDocxError",
    "generate_corrected_docx",
    "generate_corrected_txt",
    "generate_reviewed_docx",
]
