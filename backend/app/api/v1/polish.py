"""
TextMirror AI润色 API
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.database import get_db, async_session_factory
from app.core.dependencies import get_current_user_optional
from app.core.rate_limit import check_guest_rate_limit, check_user_quota
from app.core.config import settings
from app.schemas.polish import PolishRequest, PolishResponse, PolishVersion
from app.services.polish import (
    polish_text, polish_text_stream, POLISH_STYLES,
    VERSION_INSTRUCTIONS, _clean_version_content, build_polish_prompt,
    estimate_tokens_by_chars,
)
from app.services.audit_log import record_audit_log, AuditTimer
from app.models.proofread import ProofreadRecord

router = APIRouter(prefix="/polish", tags=["AI润色"])


@router.get("/styles", summary='获取所有可用的润色风格列表')
async def get_polish_styles():
    """
    获取所有可用的润色风格列表
    """
    styles = []
    for key, val in POLISH_STYLES.items():
        styles.append({
            "key": key,
            "name": val["name"],
            "description": val["description"],
        })
    return {"styles": styles}


@router.get("/models", summary='获取可用于多模型对比的已启用模型配置（仅 id/名称/模型名，不含密钥）')
async def get_available_models():
    """
    获取可用于多模型对比的已启用模型配置（仅 id/名称/模型名，不含密钥）
    """
    from sqlalchemy import select as _select
    from app.core.database import async_session_factory
    from app.models.llm_config import LLMConfig

    async with async_session_factory() as db:
        result = await db.execute(
            _select(LLMConfig.id, LLMConfig.name, LLMConfig.model)
            .where(LLMConfig.is_enabled == True)
            .order_by(LLMConfig.is_active.desc(), LLMConfig.id)
        )
        rows = result.all()
    return {"models": [{"id": r.id, "name": r.name, "model": r.model} for r in rows]}


@router.post("/text", response_model=PolishResponse, summary='AI文本润色')
async def text_polish(
    request: PolishRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    """
    AI文本润色
    用户选择润色风格 → 输入原文 → AI输出3段不同变体
    支持游客使用（受限流限制）和登录用户使用
    """
    # 游客限流检查
    if current_user is None:
        await check_guest_rate_limit(http_request)
    else:
        await check_user_quota(current_user, db)

    # 校验风格参数
    if request.style not in POLISH_STYLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的润色风格: {request.style}，可选: {', '.join(POLISH_STYLES.keys())}",
        )

    timer = AuditTimer()
    timer.start()

    try:
        result = await polish_text(
            text=request.text,
            style=request.style,
        )
    except RuntimeError as e:
        import traceback
        logger.error(f"润色服务异常: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "polish", user=current_user,
            input_text=request.text,
            extra_params={"style": request.style},
            status="failed", error_message=str(e),
            duration_ms=timer.elapsed_ms(),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="润色服务暂时不可用，请稍后重试",
        )
    except Exception as e:
        import traceback
        logger.error(f"润色过程发生未知错误: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "polish", user=current_user,
            input_text=request.text,
            extra_params={"style": request.style},
            status="failed", error_message=str(e),
            duration_ms=timer.elapsed_ms(),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="润色过程发生错误，请稍后重试",
        )

    # 构建响应
    versions = [
        PolishVersion(
            label=v["label"],
            level=v["level"],
            content=v["content"],
        )
        for v in result["versions"]
    ]

    # 保存润色记录（已登录用户）
    if current_user:
        modified_text = "\n\n---\n\n".join(
            f"【{v['label']}】\n{v['content']}" for v in result["versions"]
        )
        record = ProofreadRecord(
            user_id=current_user.id,
            type="polish",
            original_text=request.text,
            check_types=json.dumps([request.style]),
            domain=request.style,
            result={"versions": result["versions"], "style": result["style"], "style_name": result["style_name"]},
            modified_text=modified_text,
            total_issues=0,
            token_usage=result.get("usage"),
        )
        db.add(record)
        await db.flush()

    # 记录审计日志（成功）
    output_summary = "\n---\n".join(
        f"【{v['label']}】{v['content'][:200]}" for v in result["versions"]
    )
    style_info = POLISH_STYLES.get(request.style, {})
    record_audit_log(
        http_request, "polish", user=current_user,
        input_text=request.text,
        output_text=output_summary,
        extra_params={"style": request.style, "style_name": style_info.get("name", "")},
        token_usage=result.get("usage"),
        duration_ms=timer.elapsed_ms(),
    )

    return PolishResponse(
        versions=versions,
        style=result["style"],
        style_name=result["style_name"],
        usage=result["usage"],
    )


@router.post("/text/stream", summary='AI文本润色（流式 SSE）')
async def text_polish_stream(
    request: PolishRequest,
    http_request: Request,
    current_user=Depends(get_current_user_optional),
):
    """
    AI文本润色（流式 SSE）
    事件流：meta → delta/done/error（light/standard/deep 交错）→ end
    """
    # 限流与配额检查（与同步接口同一口径）
    if current_user is None:
        await check_guest_rate_limit(http_request)
    else:
        async with async_session_factory() as db:
            await check_user_quota(current_user, db)

    if request.style not in POLISH_STYLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的润色风格: {request.style}",
        )

    timer = AuditTimer()
    timer.start()
    user_id = current_user.id if current_user else None
    style = request.style
    style_name = POLISH_STYLES[style]["name"]

    async def event_stream():
        # 流式无法拿到 token 用量（stream 不返回 usage），统计留空
        versions: dict = {}
        error = None
        try:
            async for evt in polish_text_stream(text=request.text, style=style):
                if evt["event"] == "done":
                    versions[evt["level"]] = {
                        "label": VERSION_INSTRUCTIONS[evt["level"]]["label"],
                        "level": evt["level"],
                        "content": evt["content"],
                    }
                if evt["event"] == "error":
                    error = evt.get("message", "")
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as e:
            import traceback
            logger.error(f"润色流式过程异常: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            error = str(e)
            yield f"data: {json.dumps({'event': 'fatal', 'message': '润色服务暂时不可用，请稍后重试'}, ensure_ascii=False)}\n\n"

        # 流结束后：保存记录（已登录用户）+ 审计日志
        try:
            if versions and user_id:
                # 流式响应无 usage，按原文+输出总字符估算 token 用量
                total_output_chars = len(request.text) + sum(len(v["content"]) for v in versions.values())
                usage = estimate_tokens_by_chars(total_output_chars)
                async with async_session_factory() as db:
                    modified_text = "\n\n---\n\n".join(
                        f"【{v['label']}】\n{v['content']}" for v in versions.values()
                    )
                    record = ProofreadRecord(
                        user_id=user_id,
                        type="polish",
                        original_text=request.text,
                        check_types=json.dumps([style]),
                        domain=style,
                        result={"versions": list(versions.values()), "style": style, "style_name": style_name},
                        modified_text=modified_text,
                        total_issues=0,
                        token_usage=usage,
                    )
                    db.add(record)
                    await db.commit()
            record_audit_log(
                http_request, "polish", user=current_user,
                input_text=request.text,
                output_text="; ".join(v["content"][:100] for v in versions.values()),
                extra_params={"style": style, "style_name": style_name, "stream": True},
                status="failed" if error else "success",
                error_message=error,
                duration_ms=timer.elapsed_ms(),
            )
        except Exception as e:
            logger.warning(f"润色流式收尾（记录/审计）失败: {e}")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class PolishCompareRequest(BaseModel):
    """多模型对比润色请求"""
    text: str = Field(..., min_length=10, max_length=5000)
    style: str = Field(..., description="润色风格")
    config_ids: List[int] = Field(..., min_length=2, max_length=4, description="参与对比的模型配置ID")


class ModelCompareItem(BaseModel):
    """单个模型的对比结果"""
    config_id: int
    config_name: str
    model: str
    content: str = ""
    success: bool = True
    error: Optional[str] = None
    elapsed_ms: int = 0


class PolishCompareResponse(BaseModel):
    """多模型对比响应"""
    style: str
    style_name: str
    results: List[ModelCompareItem]


def _build_compare_provider(config) -> "OpenAICompatProvider":
    from app.services.llm.openai_compat import OpenAICompatProvider
    return OpenAICompatProvider(
        api_key=config.api_key,
        api_base=config.api_base,
        model=config.model,
        timeout=config.timeout,
        max_retries=config.max_retries,
        provider_name=config.name,
    )


@router.post("/compare", response_model=PolishCompareResponse, summary='多模型对比润色：同一段文本用多个已配置模型并发执行「标准润色」')
async def text_polish_compare(
    request: PolishCompareRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    """
    多模型对比润色：同一段文本用多个已配置模型并发执行「标准润色」
    供用户横向对比不同模型的输出效果
    """
    if current_user is None:
        await check_guest_rate_limit(http_request)
    else:
        await check_user_quota(current_user, db)

    if request.style not in POLISH_STYLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的润色风格: {request.style}",
        )

    import time as _time

    # 加载选中的模型配置
    from sqlalchemy import select as _select
    from app.models.llm_config import LLMConfig
    result = await db.execute(_select(LLMConfig).where(LLMConfig.id.in_(request.config_ids)))
    configs = {c.id: c for c in result.scalars().all()}
    if len(configs) < 2:
        raise HTTPException(status_code=400, detail="所选模型配置不足 2 个有效项")

    style_config = POLISH_STYLES[request.style]
    system_prompt = build_polish_prompt(request.style, "standard")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": request.text},
    ]
    estimated_tokens = len(request.text) * 2 + 512
    max_tokens = max(4096, min(estimated_tokens, 16384))

    async def _run_one(config):
        t0 = _time.perf_counter()
        provider = None
        try:
            provider = _build_compare_provider(config)
            response = await provider.chat(messages, temperature=0.7, max_tokens=max_tokens)
            return ModelCompareItem(
                config_id=config.id,
                config_name=config.name,
                model=response.model,
                content=_clean_version_content(response.content),
                success=True,
                elapsed_ms=int((_time.perf_counter() - t0) * 1000),
            )
        except Exception as e:
            logger.error(f"[对比] 模型 {config.name} 失败: {e}")
            return ModelCompareItem(
                config_id=config.id,
                config_name=config.name,
                model=config.model,
                success=False,
                error=str(e)[:300],
                elapsed_ms=int((_time.perf_counter() - t0) * 1000),
            )
        finally:
            if provider:
                await provider.close()

    import asyncio as _asyncio
    items = await _asyncio.gather(*[_run_one(c) for c in configs.values()])
    items = sorted(items, key=lambda i: request.config_ids.index(i.config_id))

    # 保存到校对历史（已登录用户）：type=polish，versions 各项 label 带模型名
    if current_user and any(i.success for i in items):
        try:
            versions = [
                {
                    "label": f"{i.config_name} · 标准润色",
                    "level": "compare",
                    "content": i.content,
                }
                for i in items if i.success
            ]
            modified_text = "\n\n---\n\n".join(
                f"【{v['label']}】\n{v['content']}" for v in versions
            )
            record = ProofreadRecord(
                user_id=current_user.id,
                type="polish",
                original_text=request.text,
                check_types=json.dumps([request.style]),
                domain=request.style,
                result={
                    "versions": versions,
                    "style": request.style,
                    "style_name": style_config["name"],
                    "compare": True,
                    "models": [i.config_name for i in items],
                },
                modified_text=modified_text,
                total_issues=0,
                token_usage=estimate_tokens_by_chars(
                    len(request.text) + sum(len(i.content) for i in items if i.success)
                ),
            )
            db.add(record)
            await db.commit()
        except Exception as e:
            logger.warning(f"对比结果落库失败: {e}")

    record_audit_log(
        http_request, "polish_compare", user=current_user,
        input_text=request.text,
        extra_params={"style": request.style, "configs": [i.config_name for i in items]},
        status="success" if all(i.success for i in items) else "partial_failed",
        duration_ms=sum(i.elapsed_ms for i in items),
    )

    return PolishCompareResponse(
        style=request.style,
        style_name=style_config["name"],
        results=items,
    )


@router.post("/compare/stream")
async def text_polish_compare_stream(
    request: PolishCompareRequest,
    http_request: Request,
    current_user=Depends(get_current_user_optional),
):
    """
    多模型对比润色（流式 SSE）：各模型并发流式执行，逐模型推送增量
    事件流：meta → delta/done/error（带 config_id，各模型交错）→ end
    """
    if current_user is None:
        await check_guest_rate_limit(http_request)
    else:
        async with async_session_factory() as db:
            await check_user_quota(current_user, db)

    if request.style not in POLISH_STYLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的润色风格: {request.style}",
        )

    import time as _time
    import asyncio as _asyncio
    from sqlalchemy import select as _select
    from app.models.llm_config import LLMConfig

    async with async_session_factory() as db:
        result = await db.execute(_select(LLMConfig).where(LLMConfig.id.in_(request.config_ids)))
        configs = {c.id: c for c in result.scalars().all()}
    if len(configs) < 2:
        raise HTTPException(status_code=400, detail="所选模型配置不足 2 个有效项")

    style_config = POLISH_STYLES[request.style]
    system_prompt = build_polish_prompt(request.style, "standard")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": request.text},
    ]
    estimated_tokens = len(request.text) * 2 + 512
    max_tokens = max(4096, min(estimated_tokens, 16384))

    user_id = current_user.id if current_user else None
    style = request.style
    style_name = style_config["name"]

    async def event_stream():
        import time as __time
        queues: Dict[int, _asyncio.Queue] = {cid: _asyncio.Queue() for cid in configs}
        contents: Dict[int, list] = {cid: [] for cid in configs}

        async def _run_model(config):
            t0 = __time.perf_counter()
            provider = None
            try:
                provider = _build_compare_provider(config)
                async for delta in provider.chat_stream(messages, temperature=0.7, max_tokens=max_tokens):
                    contents[config.id].append(delta)
                    await queues[config.id].put(("delta", delta))
                await queues[config.id].put(("done", int((__time.perf_counter() - t0) * 1000)))
            except Exception as e:
                logger.error(f"[对比-流式] 模型 {config.name} 失败: {e}")
                await queues[config.id].put(("error", str(e)[:300]))
            finally:
                if provider:
                    await provider.close()

        tasks = [_asyncio.create_task(_run_model(c)) for c in configs.values()]

        try:
            yield "data: " + json.dumps({
                "event": "meta", "style": style, "style_name": style_name,
                "models": [{"config_id": c.id, "config_name": c.name, "model": c.model} for c in configs.values()],
            }, ensure_ascii=False) + "\n\n"

            finished = set()
            while len(finished) < len(configs):
                progressed = False
                for cid in configs:
                    if cid in finished:
                        continue
                    for _ in range(8):
                        try:
                            kind, payload = queues[cid].get_nowait()
                        except _asyncio.QueueEmpty:
                            break
                        progressed = True
                        cfg = configs[cid]
                        if kind == "delta":
                            yield "data: " + json.dumps({
                                "event": "delta", "config_id": cid, "content": payload,
                            }, ensure_ascii=False) + "\n\n"
                        elif kind == "done":
                            yield "data: " + json.dumps({
                                "event": "done", "config_id": cid,
                                "config_name": cfg.name, "model": cfg.model,
                                "content": _clean_version_content("".join(contents[cid])),
                                "elapsed_ms": payload,
                            }, ensure_ascii=False) + "\n\n"
                            finished.add(cid)
                        elif kind == "error":
                            yield "data: " + json.dumps({
                                "event": "error", "config_id": cid,
                                "config_name": cfg.name, "message": payload,
                            }, ensure_ascii=False) + "\n\n"
                            finished.add(cid)
                if not progressed:
                    await _asyncio.sleep(0.05)

            yield "data: " + json.dumps({"event": "end"}, ensure_ascii=False) + "\n\n"

            # 落库 + 审计（与同步对比接口同构）
            try:
                results = []
                for cid in configs:
                    if cid in contents and contents[cid]:
                        results.append({
                            "config_id": cid,
                            "config_name": configs[cid].name,
                            "content": _clean_version_content("".join(contents[cid])),
                            "success": True,
                        })
                if user_id and results:
                    versions = [
                        {"label": f"{r['config_name']} · 标准润色", "level": "compare", "content": r["content"]}
                        for r in results
                    ]
                    async with async_session_factory() as db:
                        record = ProofreadRecord(
                            user_id=user_id,
                            type="polish",
                            original_text=request.text,
                            check_types=json.dumps([style]),
                            domain=style,
                            result={
                                "versions": versions, "style": style, "style_name": style_name,
                                "compare": True, "models": [c.name for c in configs.values()],
                            },
                            modified_text="\n\n---\n\n".join(f"【{v['label']}】\n{v['content']}" for v in versions),
                            total_issues=0,
                            token_usage=estimate_tokens_by_chars(
                                len(request.text) + sum(len(r["content"]) for r in results)
                            ),
                        )
                        db.add(record)
                        await db.commit()
                record_audit_log(
                    http_request, "polish_compare", user=current_user,
                    input_text=request.text,
                    extra_params={"style": style, "configs": [c.name for c in configs.values()], "stream": True},
                    status="success" if len(results) == len(configs) else "partial_failed",
                )
            except Exception as e:
                logger.warning(f"对比流式收尾（落库/审计）失败: {e}")
        finally:
            for t in tasks:
                t.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
