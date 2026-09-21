from typing import Any, Dict, List, Optional

from loguru import logger

from .chunking import ChunkSpan
from .constants import (
    _SELF_CHECK_ERROR_THRESHOLD,
    _SELF_CHECK_MAX_CHUNK_CALLS,
    _SELF_CHECK_TEXT_LIMIT,
    SELF_CHECK_PROMPT,
)
from .parsing import parse_proofread_result


def _needs_self_check(issues: List[Dict[str, Any]]) -> bool:
    """高危判定：error 级问题达到阈值（密集错误文本=漏检风险最高）"""
    error_count = sum(1 for i in issues if i.get("severity") == "error")
    return error_count >= _SELF_CHECK_ERROR_THRESHOLD


async def self_check_pass(
    text: str,
    issues: List[Dict[str, Any]],
    provider,
    force: bool = False,
    chunk_spans: Optional[List[ChunkSpan]] = None,
    chunks: Optional[List[str]] = None,
    partial: bool = False,
) -> List[Dict[str, Any]]:
    """
    二次自检：把第一轮问题清单喂回 LLM 复查，返回补充遗漏的 issue。
    长文档按分片聚焦复查——优先复查 error 密集的分片，而非只看开头 3000 字。
    覆盖不完整（有分片失败）时聚焦复查会漏掉从未检查的失败区域，退回整段复查。
    异常自捕获——二次检查失败不影响第一轮结果（尽力而为的增益层）。
    """
    if not force and not _needs_self_check(issues):
        return []

    use_chunk_mode = chunk_spans is not None and chunks and len(chunks) > 1 and not partial
    if use_chunk_mode:
        return await _self_check_chunks(text, issues, provider, chunk_spans, chunks)

    return await _self_check_single(text, issues, provider)


async def _self_check_single(
    text: str,
    issues: List[Dict[str, Any]],
    provider,
) -> List[Dict[str, Any]]:
    """短文档/单分片：整段送 LLM 复查（保持原有行为）"""
    sample = issues[:15]
    issues_desc = "\n".join(
        f"{n}. 原文「{i.get('original', '')[:40]}」→ 建议「{i.get('suggestion', '')[:40]}」（{i.get('type')}）"
        for n, i in enumerate(sample, 1)
    )
    prompt = SELF_CHECK_PROMPT.format(text=text[:_SELF_CHECK_TEXT_LIMIT], issues=issues_desc)
    return await _run_self_check_call(prompt, provider, chunk_index=None)


async def _self_check_chunks(
    text: str,
    issues: List[Dict[str, Any]],
    provider,
    chunk_spans: List[ChunkSpan],
    chunks: List[str],
) -> List[Dict[str, Any]]:
    """长文档分片聚焦复查：按 error 密度选分片，逐片送 LLM 复查"""
    from collections import defaultdict

    chunk_issues: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for issue in issues:
        idx = issue.get("chunk_index")
        if isinstance(idx, int) and 0 <= idx < len(chunks):
            chunk_issues[idx].append(issue)

    if not chunk_issues:
        return []

    def _chunk_score(idx: int) -> int:
        return sum(1 for i in chunk_issues[idx] if i.get("severity") == "error")

    ranked = sorted(chunk_issues.keys(), key=_chunk_score, reverse=True)
    targets = ranked[:_SELF_CHECK_MAX_CHUNK_CALLS]

    all_additions: List[Dict[str, Any]] = []
    for idx in targets:
        chunk_text = chunks[idx]
        chunk_issue_list = chunk_issues[idx][:15]
        issues_desc = "\n".join(
            f"{n}. 原文「{i.get('original', '')[:40]}」→ 建议「{i.get('suggestion', '')[:40]}」（{i.get('type')}）"
            for n, i in enumerate(chunk_issue_list, 1)
        )
        prompt = SELF_CHECK_PROMPT.format(text=chunk_text, issues=issues_desc)
        additions = await _run_self_check_call(prompt, provider, chunk_index=idx)
        all_additions.extend(additions)
        logger.info(f"[自检] 分片 {idx+1}/{len(chunks)} 复查补充 {len(additions)} 条遗漏")

    return all_additions


async def _run_self_check_call(
    prompt: str,
    provider,
    chunk_index: Optional[int],
) -> List[Dict[str, Any]]:
    """执行单次自检 LLM 调用，返回带 source/chunk_index 标记的 additions"""
    from app.services.llm.usage import usage_operation

    try:
        with usage_operation("self_check"):
            response = await provider.chat(
                [{"role": "user", "content": prompt}],
                temperature=provider.default_temperature,
                thinking=False,
            )
        extra = parse_proofread_result(response.content)
        additions = [
            {**i, "source": "self_check"} for i in extra
            if i.get("review") == "new" and i.get("original")
        ]
        for a in additions:
            a.pop("review", None)
            if chunk_index is not None:
                a["chunk_index"] = chunk_index
        return additions
    except Exception as e:
        logger.warning(f"[自检] 复查失败（跳过，不影响第一轮结果）: {e}")
        return []
