from dataclasses import dataclass
from typing import List

from .constants import _DOMAIN_FEATURES


@dataclass(frozen=True)
class ChunkSpan:
    """Unicode 码点坐标：[start, end) 为完整上下文，core_start 起为本片正文。"""

    start: int
    end: int
    core_start: int


def split_text_into_chunk_spans(text: str, max_chunk_size: int = 800,
                                overlap: int = 100) -> List[ChunkSpan]:
    """正文最多 max_chunk_size 字，优先在上限前最近句界/换行切分，否则硬切。

    上下文直接取原文前 overlap 字；不重建文本，不丢空白，不靠查找片段反推坐标。
    各 core 连续覆盖全文，只有 prefix 重叠。
    """
    if max_chunk_size <= 0 or overlap < 0:
        raise ValueError("max_chunk_size must be positive and overlap non-negative")
    if not text:
        return [ChunkSpan(0, 0, 0)]
    spans = []
    core_start = 0
    while core_start < len(text):
        end = min(core_start + max_chunk_size, len(text))
        if end < len(text):
            boundary = max(text.rfind(sep, core_start, end) for sep in "。！？；\r\n")
            if boundary >= core_start:
                end = boundary + 1
        spans.append(ChunkSpan(max(0, core_start - overlap), end, core_start))
        core_start = end
    return spans


def split_text_into_chunks(text: str, max_chunk_size: int = 800,
                           overlap: int = 100) -> List[str]:
    """兼容公开 API；每片正文加前置上下文始终为原文的精确切片。"""
    return [text[span.start:span.end]
            for span in split_text_into_chunk_spans(text, max_chunk_size, overlap)]


def detect_domain(text: str) -> str:
    """
    特征词路由：统计各领域特征词命中数，最高分且超过阈值才切换；
    否则 general（保守策略：识别不准不如不识别）。
    """
    best_domain, best_score = "general", 0
    for domain, words in _DOMAIN_FEATURES.items():
        score = sum(text.count(w) for w in words)
        if score > best_score:
            best_domain, best_score = domain, score
    return best_domain if best_score >= 2 else "general"
