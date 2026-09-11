"""
TextMirror 开放 API——AI 润色模块（同步三版本 + SSE 流式）
"""
import json
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.open_common import ERROR_RESPONSES, _check_user_quota_contract
from app.core.database import async_session_factory, get_db
from app.core.dependencies import get_current_user_or_apikey
from app.core.rate_limit import charge_api_key_daily, check_api_key_rpm, refund_api_key_daily_usage
from app.models.api_key import ApiKey
from app.models.proofread import ProofreadRecord
from app.models.user import User
from app.schemas.open import OpenPolishRequest, OpenPolishResponse
from app.schemas.polish import PolishVersion
from app.services.audit_log import AuditTimer, record_audit_log
from app.services.polish import (
    VERSION_INSTRUCTIONS,
    estimate_tokens_by_chars,
    polish_text,
    polish_text_stream,
)

router = APIRouter(tags=["开放API"])


# ======================================================================
# AI 润色
# ======================================================================

async def _polish_billing(user, api_key, db: AsyncSession) -> None:
    """润色与审校同一计费顺序：RPM → 用户配额（免费检查）→ 密钥日配额（weight=1，与 Web 端单记录口径一致）"""
    if api_key is not None:
        await check_api_key_rpm(api_key)
    await _check_user_quota_contract(user, db)
    if api_key is not None:
        await charge_api_key_daily(api_key)


def _build_polish_record(
    user_id: int,
    api_key_id: Optional[int],
    text: str,
    style: str,
    style_name: str,
    versions: list,
    usage,
) -> ProofreadRecord:
    modified_text = "\n\n---\n\n".join(
        f"【{v['label']}】\n{v['content']}" for v in versions
    )
    return ProofreadRecord(
        user_id=user_id,
        api_key_id=api_key_id,
        type="polish",
        original_text=text,
        check_types=json.dumps([style]),
        domain=style,
        result={"versions": versions, "style": style, "style_name": style_name},
        modified_text=modified_text,
        total_issues=0,
        token_usage=usage,
    )


@router.post(
    "/polish",
    response_model=OpenPolishResponse,
    summary="AI 文本润色",
    description=(
        "对文本进行 AI 润色，返回三个版本（轻量/标准/深度改动）。\n\n"
        "**快速开始**：只需传 `text`，`style` 默认 formal（正式规范）——\n"
        "```json\n"
        '{"text": "这段文字需要更加正式的表达方式来呈现。"}\n'
        "```\n\n"
        "**style 可选值**（10 种）：formal 正式规范 / friendly 亲和自然 / plain 通俗易懂 /"
        " concise 精炼简洁 / evidence 论证充分 / strategic 战略高度 / practical 落地实操 /"
        " firm 严谨有力 / gentle 委婉得体 / action 行动导向\n\n"
        "**单版本失败**：对应 version 的 content 为占位提示文本，不影响其他版本，额度不退"
        "（可重试）；服务整体不可用返回 503 并退还当日额度。\n\n"
        "**限制**：文本 10-5000 字；限流/配额与审校端点同一口径（每分钟 12 次、每日配额随账号）。"
    ),
    responses=ERROR_RESPONSES,
)
async def open_polish(
    request: OpenPolishRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """开放 AI 润色端点（复用 Web 端同一润色服务，三版本并发）"""
    user, api_key = auth
    await _polish_billing(user, api_key, db)

    timer = AuditTimer()
    timer.start()
    audit_extra = {"style": request.style}
    if api_key is not None:
        audit_extra["api_key_id"] = api_key.id

    try:
        result = await polish_text(text=request.text, style=request.style)
    except RuntimeError as e:
        import traceback
        logger.error(f"[OpenAPI] 润色服务异常: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "api_polish", user=user,
            input_text=request.text, extra_params=audit_extra,
            status="failed", error_message=str(e), duration_ms=timer.elapsed_ms(),
        )
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "MODEL_UNAVAILABLE", "message": "润色服务暂时不可用，请稍后重试"},
        )
    except Exception as e:
        import traceback
        logger.error(f"[OpenAPI] 润色未知错误: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "api_polish", user=user,
            input_text=request.text, extra_params=audit_extra,
            status="failed", error_message=str(e), duration_ms=timer.elapsed_ms(),
        )
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": "润色过程发生错误，请稍后重试"},
        )

    versions = [
        PolishVersion(
            label=v["label"],
            level=v["level"],
            content=v["content"],
            **({"sensitive_words": v["sensitive_words"]} if v.get("sensitive_words") else {}),
        )
        for v in result["versions"]
    ]

    # 落库走请求作用域会话（与 Web 端同步润色同构）：API 调用必须入库——
    # 用户配额按记录计数 + 用量统计归属
    db.add(_build_polish_record(
        user_id=user.id,
        api_key_id=api_key.id if api_key is not None else None,
        text=request.text,
        style=request.style,
        style_name=result["style_name"],
        versions=result["versions"],
        usage=result.get("usage"),
    ))
    await db.flush()

    record_audit_log(
        http_request, "api_polish", user=user,
        input_text=request.text,
        output_text="; ".join(v["content"][:100] for v in result["versions"]),
        extra_params={**audit_extra, "style_name": result["style_name"]},
        token_usage=result.get("usage"),
        duration_ms=timer.elapsed_ms(),
    )

    return OpenPolishResponse(
        versions=versions,
        style=result["style"],
        style_name=result["style_name"],
        usage=result["usage"],
    )


@router.post(
    "/polish/stream",
    summary="AI 文本润色（流式 SSE）",
    description=(
        "同 `POST /polish`，但以 SSE 事件流逐字返回，适合前端实时渲染场景。\n\n"
        "**事件流**：`meta`（风格信息）→ `start`（版本开始：level=light/standard/deep）→"
        " `delta`（增量文本，三版本交错）→ `done`（版本完整文本）→ `end`（结束）。\n\n"
        "单版本失败发 `error` 事件（带 level），流正常结束；整体故障发 `fatal` 事件。\n\n"
        "**计费**：与同步版一致（开始流式前完成限流与扣额；一个版本都没产出即 fatal 时退还当日额度）。"
        "鉴权/参数错误在流开始前按统一错误契约返回（HTTP 状态码 + `detail.code`）。\n\n"
        "```bash\n"
        'curl -N -X POST .../api/v1/open/polish/stream -H "Authorization: Bearer tm_..." \\\n'
        '  -H "Content-Type: application/json" -d \'{"text": "...", "style": "formal"}\'\n'
        "```"
    ),
    responses=ERROR_RESPONSES,
)
async def open_polish_stream(
    request: OpenPolishRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """开放 AI 润色流式端点（SSE，事件流与 Web 端 /polish/text/stream 同构）"""
    user, api_key = auth

    # 限流/配额/扣额在流开始前完成（流式响应无法回传 HTTP 错误码）
    async with async_session_factory() as billing_db:
        await _polish_billing(user, api_key, billing_db)

    # 结束鉴权依赖遗留的读事务再开流：db 与鉴权依赖是同一缓存会话，
    # 流结束才随请求关闭，SQLite 下挂着读锁会让流内落库 database is locked（PG 无此问题）
    await db.commit()

    style = request.style
    timer = AuditTimer()
    timer.start()
    user_id = user.id
    api_key_id = api_key.id if api_key is not None else None
    audit_extra = {"style": style, "stream": True}
    if api_key is not None:
        audit_extra["api_key_id"] = api_key.id

    async def event_stream():
        versions: dict = {}
        style_name = ""
        fatal = False
        try:
            async for evt in polish_text_stream(text=request.text, style=style):
                if evt["event"] == "meta":
                    style_name = evt.get("style_name", "")
                if evt["event"] == "done":
                    versions[evt["level"]] = {
                        "label": VERSION_INSTRUCTIONS[evt["level"]]["label"],
                        "level": evt["level"],
                        "content": evt["content"],
                    }
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as e:
            import traceback
            logger.error(f"[OpenAPI] 润色流式异常: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            fatal = True
            yield f"data: {json.dumps({'event': 'fatal', 'message': '润色服务暂时不可用，请稍后重试'}, ensure_ascii=False)}\n\n"

        # 流结束后：零产出退还当日额度 → 落库 → 审计
        try:
            if fatal and not versions and api_key is not None:
                await refund_api_key_daily_usage(api_key)
            if versions:
                # 流式收尾在响应流内执行，用独立会话落库（请求会话此刻仍被流持有）
                async with async_session_factory() as db:
                    db.add(_build_polish_record(
                        user_id=user_id,
                        api_key_id=api_key_id,
                        text=request.text,
                        style=style,
                        style_name=style_name,
                        versions=list(versions.values()),
                        usage=estimate_tokens_by_chars(
                            len(request.text) + sum(len(v["content"]) for v in versions.values())
                        ),
                    ))
                    await db.commit()
            record_audit_log(
                http_request, "api_polish", user=user,
                input_text=request.text,
                output_text="; ".join(v["content"][:100] for v in versions.values()),
                extra_params=audit_extra,
                status="failed" if fatal else "success",
                error_message="stream fatal" if fatal else None,
                duration_ms=timer.elapsed_ms(),
            )
        except Exception as e:
            logger.warning(f"[OpenAPI] 润色流式收尾（额度退还/记录/审计）失败: {e}")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


