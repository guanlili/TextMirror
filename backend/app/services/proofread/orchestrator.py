import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from app.services.consistency import check_consistency
from app.services.format_rules import check_format_rules

from .cache import ChunkCache
from .chunking import split_text_into_chunk_spans
from .constants import (
    _DEEP_CHUNK_TIMEOUT_S,
    PROOFREAD_TYPES,
    PROOFREAD_USER_PROMPT,
)
from .errors import InvalidProofreadResponse, ModelProofreadError
from .locate import (
    _check_suggestion_effective,
    _filter_whitelist_issues,
    locate_issues,
    verify_llm_issues,
)
from .parsing import parse_proofread_result
from .prompts import build_system_prompt
from .scan import merge_issues, scan_words_deterministic
from .self_check import _needs_self_check, self_check_pass


def _report_progress(on_progress: Optional[Callable[[int, str], None]], percent: int, message: str):
    """进度上报：同步直达调用方，异常只记日志、绝不影响校对主流程。
    不走线程池——Celery 场景回调写 DB session，与 run_until_complete 的挂起主流程
    并发操作同一 session 会触发 "concurrent operations are not permitted"；
    回调在 await 点之间同步执行，天然与主流程串行。"""
    if on_progress is None:
        return
    try:
        on_progress(percent, message)
    except Exception as e:
        logger.warning(f"[校对] 进度回调失败（忽略）: {e}")


def _report_chunk_done(callback: Callable, chunk_index: int,
                       issues: List[Dict[str, Any]], total_issues: int):
    """分片完成回调：上报该分片的问题列表及累计问题数，供调用方流式推送。"""
    try:
        callback(chunk_index, issues, total_issues)
    except Exception as e:
        logger.warning(f"[校对] 分片回调失败（忽略）: {e}")


async def _gather_preparation(user_id: Optional[int], domain: str, config_id: Optional[int]):
    """审校准备阶段：词库 + 领域规则 + LLM Provider 三路并行"""
    from .prompts import _get_domain_rules
    from .provider import get_llm_provider
    from .words import _load_all_words
    (global_words, user_words), domain_rules, provider = await asyncio.gather(
        _load_all_words(user_id),
        _get_domain_rules(domain),
        get_llm_provider(config_id),
    )
    return (global_words, user_words, domain_rules), provider


async def proofread_text(
    text: str,
    domain: str = "general",
    config_id: Optional[int] = None,
    user_id: Optional[int] = None,
    check_types: Optional[List[str]] = None,
    depth: str = "standard",
    on_progress: Optional[Callable[[int, str], None]] = None,
    chunk_cache: Optional[ChunkCache] = None,
    on_chunk_done: Optional[Callable[[int, List[Dict[str, Any]], int], None]] = None,
) -> Dict[str, Any]:
    """
    执行文本校对（check_types 已废弃：不再影响审校范围，仅为兼容旧调用保留入参）

    :param depth: 审校深度——quick 仅确定性层（零 LLM 成本秒回，适合批量初筛）；
                  standard 全流程+LLM 关思考模式（默认，秒级响应，思考在干净文本上
                  反而制造大量误报）；deep LLM 开思考+强制二次自检（质量档，误报抑制
                  更强但耗时数倍，实测 32 样本集零误报 7/9 vs 关思考 2/9）
    :param text: 待校对文本
    :param domain: 领域
    :param config_id: 指定模型配置ID（None 用当前活跃模型）
    :param user_id: 归属用户ID（注入其个性化词库与放行词；游客为 None）
    :param on_progress: 进度回调 (percent, message)，长文本分片粒度上报；
                        线程池执行、失败不影响校对；同步调用方（Web API）不传
    :return: 校对结果
    """
    from .chunking import detect_domain

    t0 = time.perf_counter()

    # 领域自动识别（仅 auto 时；特征词≥2 命中才切换，保守策略）
    if domain == "auto":
        domain = detect_domain(text)
        logger.info(f"[校对] 领域自动识别 → {domain}")

    # 保留每片完整上下文在原文中的位置，失败补查及问题定位共用。
    chunk_spans = split_text_into_chunk_spans(text)
    chunks = [text[span.start:span.end] for span in chunk_spans]
    coverage = {
        "status": "complete", "total_chunks": len(chunks),
        "completed_chunks": len(chunks), "failed_chunks": [],
    }
    logger.info(f"[校对] 总长度={len(text)} 分片数={len(chunks)} 领域={domain} user_id={user_id}")

    # 并行：加载词库/领域规则配置 + 获取大模型 Provider，避免串行等待
    t1 = time.perf_counter()
    (global_words, user_words, domain_rules), provider = await _gather_preparation(user_id, domain, config_id)
    actual_config_id = getattr(provider, "config_id", config_id)
    t2 = time.perf_counter()
    logger.info(f"[校对] 准备阶段耗时={t2-t1:.2f}s "
                f"(全局: 敏感={len(global_words['sensitive'])} 禁={len(global_words['banned'])} "
                f"纠错={len(global_words['correction'])} 放行={len(global_words['whitelist'])}; "
                f"用户: 纠错={len(user_words['correction'])} 放行={len(user_words['whitelist'])})")

    # 构建 Prompt（词库类检查已改为确定性扫描，不再注入 prompt；放行词仍由后置过滤保证）
    system_prompt = build_system_prompt(domain, global_words, user_words, domain_rules)
    logger.info(f"[校对] system_prompt 长度={len(system_prompt)} 字符")

    # 词库确定性扫描：敏感词/禁词/纠错词字符串匹配，召回 100%、零 LLM 成本
    scanned_issues = scan_words_deterministic(text, global_words, user_words)
    # 跨片一致性检查（纯规则）：金额/称谓/编号的全文级矛盾——分片送审抓不到
    scanned_issues.extend(check_consistency(text))
    # 格式规则引擎（纯规则）：日期/号码/金额量级/编号样式——LLM 对格式类不稳定
    scanned_issues.extend(check_format_rules(text))
    if scanned_issues:
        logger.info(f"[校对] 确定性扫描命中 {len(scanned_issues)} 项")

    # 快查模式：仅确定性层（零 LLM 成本），放行词过滤后直接返回——批量初筛场景
    if depth == "quick":
        await provider.close()  # quick 不用 LLM，释放已创建的连接
        scanned_issues = _filter_whitelist_issues(scanned_issues, global_words, user_words)
        scanned_issues = locate_issues(text, verify_llm_issues(text, scanned_issues))
        scanned_issues.sort(key=lambda i: {"error": 0, "warning": 1, "info": 2}.get(i.get("severity", "warning"), 1))
        logger.info(f"[校对][快查] 完成 问题={len(scanned_issues)} 耗时={time.perf_counter()-t0:.2f}s（零LLM）")
        return {
            "issues": scanned_issues,
            "total_issues": len(scanned_issues),
            "chunks_count": len(chunks),
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "domain": domain,
            "check_types": list(PROOFREAD_TYPES.keys()),
            "depth": "quick",
            "config_id": actual_config_id,
            "coverage": coverage,
        }

    # 调用大模型（并发校对所有分片，加速整体响应）
    # 思考模式按档位注入：standard 关（快，干净文本误报少），deep 开（质量档）。
    # 供应商不支持思考参数时 provider.chat 静默忽略，按模型默认执行。
    deep = depth == "deep"
    # 思考模式下生成 token 数倍增（推理 token 以 ~110/s 吐出），800 字分片实测
    # 可超 60s 配置值，先超时再重试只会放大延迟——deep 档放宽到不低于 180s
    chunk_timeout = max(provider.timeout, _DEEP_CHUNK_TIMEOUT_S) if deep else None
    all_issues = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    semaphore = asyncio.Semaphore(min(4, len(chunks)))
    done_chunks = 0
    streamed_issue_count = 0
    # 进度映射：分片全部完成 → 70%，留 72~95 给自检/后处理（任务侧再映射到自己的进度条）
    _PROOFREAD_END_PCT = 70
    _SELF_CHECK_PCT = 72

    async def _process_chunk(idx: int, chunk: str):
        nonlocal done_chunks, streamed_issue_count
        import hashlib as _hashlib
        chunk_text_hash = _hashlib.sha256(chunk.encode()).hexdigest()[:16]

        async with semaphore:
            cstart = time.perf_counter()

            if chunk_cache:
                cached = await asyncio.to_thread(chunk_cache.get_chunk, idx, chunk_text_hash)
                if cached is not None:
                    issues = cached["issues"]
                    for issue in issues:
                        issue["chunk_index"] = idx
                    usage = cached.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
                    done_chunks += 1
                    streamed_issue_count += len(issues)
                    if len(chunks) > 1:
                        _report_progress(
                            on_progress,
                            min(int(done_chunks / len(chunks) * _PROOFREAD_END_PCT), _PROOFREAD_END_PCT),
                            f"已完成 {done_chunks}/{len(chunks)} 段文本校对",
                        )
                    if on_chunk_done:
                        _report_chunk_done(on_chunk_done, idx, issues, streamed_issue_count)
                    logger.info(f"[校对] 分片 {idx+1}/{len(chunks)} 缓存命中 "
                                f"耗时={time.perf_counter()-cstart:.3f}s")
                    return issues, usage

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": PROOFREAD_USER_PROMPT.format(text=chunk)},
            ]
            response = await provider.chat(
                messages,
                temperature=provider.default_temperature,
                thinking=deep,
                timeout=chunk_timeout,
            )
            issues = parse_proofread_result(response.content)
            for issue in issues:
                issue["chunk_index"] = idx
            done_chunks += 1
            streamed_issue_count += len(issues)
            if len(chunks) > 1:
                _report_progress(
                    on_progress,
                    min(int(done_chunks / len(chunks) * _PROOFREAD_END_PCT), _PROOFREAD_END_PCT),
                    f"已完成 {done_chunks}/{len(chunks)} 段文本校对",
                )
            if on_chunk_done:
                _report_chunk_done(on_chunk_done, idx, issues, streamed_issue_count)
            logger.info(f"[校对] 分片 {idx+1}/{len(chunks)} 长度={len(chunk)} "
                        f"耗时={time.perf_counter()-cstart:.2f}s tokens={response.usage}")

            if chunk_cache:
                await asyncio.to_thread(chunk_cache.set_chunk, idx, chunk_text_hash,
                                        issues, dict(response.usage))

            return issues, response.usage

    try:
        tasks = [_process_chunk(i, c) for i, c in enumerate(chunks)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, r in enumerate(results):
            if isinstance(r, Exception):
                span = chunk_spans[idx]
                coverage["failed_chunks"].append({
                    "chunk_index": idx, "start": span.start, "end": span.end,
                    "text": text[span.start:span.end],
                    "error_code": "INVALID_RESPONSE" if isinstance(r, InvalidProofreadResponse) else "MODEL_ERROR",
                })
                logger.error(f"[校对] 分片 {idx} 失败: {r}")
                continue
            issues, usage = r
            all_issues.extend(issues)
            for key in total_usage:
                total_usage[key] += usage.get(key, 0)

        failed_chunks = len(coverage["failed_chunks"])
        coverage["completed_chunks"] = len(chunks) - failed_chunks
        coverage["status"] = "partial" if failed_chunks else "complete"
        # 全失败保留既有失败/退款语义，不暴露 provider 异常细节。
        if failed_chunks == len(chunks):
            raise ModelProofreadError(coverage, actual_config_id)
        if failed_chunks:
            logger.warning(f"[校对] {failed_chunks}/{len(chunks)} 个分片失败，结果可能不完整")

        # 高危文本二次自检（error 级问题密集时触发；deep 模式强制）：provider 尚未关闭，
        # 把第一轮问题清单喂回 LLM 复查遗漏——尽力而为的增益层
        merged_early = merge_issues(list(all_issues), list(scanned_issues))
        will_self_check = (depth == "deep") or _needs_self_check(merged_early)
        if will_self_check:
            _report_progress(on_progress, _SELF_CHECK_PCT, "正在二次复查遗漏问题...")
        self_check_extra = await self_check_pass(
            text, merged_early, provider, force=(depth == "deep"),
            chunk_spans=chunk_spans, chunks=chunks,
            partial=bool(coverage["failed_chunks"]),
        )
        all_issues.extend(self_check_extra)
        for key in total_usage:
            pass  # 自检用量计入 provider 返回但保持简单，不计入展示
    finally:
        await provider.close()

    # 放行词确定性后处理：模型报的问题里 original 命中放行词的直接过滤，
    # 不依赖模型自觉遵守 prompt 中的放行规则（扫描结果同样过滤；
    # 用户纠错映射命中的项例外保留——显式"要改"压过"别报"）
    all_issues = _filter_whitelist_issues(all_issues, global_words, user_words)
    scanned_issues = _filter_whitelist_issues(scanned_issues, global_words, user_words)

    # 先在各自作用域内 post-verify、定位，再合并；不能在定位前按原文去重。
    all_issues = locate_issues(text, verify_llm_issues(text, all_issues, chunk_spans), chunk_spans)
    scanned_issues = locate_issues(text, verify_llm_issues(text, scanned_issues))
    all_issues = merge_issues(all_issues, scanned_issues)

    # 建议有效性自检：改写类建议若没修掉错误核心，降级 warning 提示人工核对
    all_issues = _check_suggestion_effective(all_issues)

    logger.info(f"[校对] 完成 问题={len(all_issues)} 总耗时={time.perf_counter()-t0:.2f}s 用量={total_usage}")

    return {
        "issues": all_issues,
        "total_issues": len(all_issues),
        "chunks_count": len(chunks),
        "usage": total_usage,
        "domain": domain,
        "check_types": list(PROOFREAD_TYPES.keys()),  # 已废弃字段，恒为全量，仅为响应兼容保留
        "depth": depth,
        "config_id": actual_config_id,
        "coverage": coverage,
    }
