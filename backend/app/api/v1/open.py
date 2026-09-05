"""
TextMirror 开放 API（对外稳定契约）
认证：Authorization: Bearer tm_...（API 密钥，个人中心创建）或 JWT 登录 Token
错误契约：非 2xx 响应的 detail 为 {"code": "...", "message": "..."}（含 422，见 validation_exception_handler）
"""
import asyncio
import json
import os
import time
import uuid
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from celery.result import AsyncResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.celery_app import celery_app
from app.core.database import get_db
from app.core.dependencies import get_current_user_or_apikey
from app.core.file_security import sanitize_filename, safe_upload_path
from app.core.rate_limit import (
    charge_api_key_daily,
    check_api_key_rate_limit,
    check_api_key_rpm,
    check_user_quota,
    check_user_quota_n_times,
    refund_api_key_daily_usage,
)
from app.models.api_key import ApiKey
from app.models.llm_config import LLMConfig
from app.models.proofread import ProofreadRecord
from app.models.uploaded_document import UploadedDocument
from app.models.user import User
from app.schemas.open import (
    OpenCompareRequest,
    OpenCompareResponse,
    OpenCompareModelResult,
    OpenDocumentSubmitResponse,
    OpenJobStatusResponse,
    OpenModelsResponse,
)
from app.schemas.proofread import (
    CheckType,
    Domain,
    TextProofreadRequest,
    TextProofreadResponse,
    ProofreadIssue,
)
from app.services.document import extract_text_from_file
from app.services.proofread import proofread_text
from app.services.audit_log import record_audit_log, AuditTimer
from app.tasks.proofread_task import async_proofread_document

router = APIRouter(tags=["开放API"])

# 错误响应示例（对外契约的一部分，写进 OpenAPI 文档）
def _error_example(code: str, msg: str) -> dict:
    return {
        "description": msg,
        "content": {"application/json": {"example": {"detail": {"code": code, "message": msg}}}},
    }

ERROR_RESPONSES = {
    400: _error_example("INVALID_CONFIG", "指定的模型配置不存在或已停用"),
    401: _error_example("UNAUTHORIZED", "未提供认证凭证 / API 密钥无效"),
    403: _error_example("API_KEY_REVOKED", "密钥已吊销 / 已过期 / 账号被禁用"),
    422: _error_example("VALIDATION_ERROR", "参数错误：check_types 非法值"),
    429: _error_example("RATE_LIMITED", "频率超限（每分钟12次）或配额用尽"),
    503: _error_example("MODEL_UNAVAILABLE", "审校服务暂时不可用，请稍后重试"),
}

DOC_ERROR_RESPONSES = {
    **ERROR_RESPONSES,
    400: _error_example("INVALID_FILE", "文件格式不支持 / 文件损坏 / 未提取到文本"),
    503: _error_example("TASK_QUEUE_UNAVAILABLE", "任务队列暂时不可用，请稍后重试"),
}

JOBS_ERROR_RESPONSES = {
    **ERROR_RESPONSES,
    404: _error_example("JOB_NOT_FOUND", "任务不存在"),
}


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    422 参数校验错误转换为本 API 的 code+message 契约
    （默认的 errors 数组格式对外部集成方不友好，且与其他错误格式不一致）
    """
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = ".".join(str(l) for l in first.get("loc", []) if l not in ("body", "form"))
    msg = first.get("msg", "请求参数错误")
    message = f"参数错误：{loc} {msg}" if loc else f"参数错误：{msg}"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": {"code": "VALIDATION_ERROR", "message": message}},
    )


async def internal_exception_handler(request: Request, exc: Exception):
    """
    子应用兜底 500：未捕获异常也保持 code+message 契约
    （默认的 "Internal Server Error" 纯文本不符合对外 API 格式）
    """
    logger.error(f"[OpenAPI] 未捕获异常 {request.method} {request.url.path}: {type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": {"code": "INTERNAL_ERROR", "message": "服务器内部错误，请稍后重试"}},
    )


async def _check_user_quota_contract(user, db: AsyncSession, n: int = 1) -> None:
    """用户每日配额检查，429 转换为 code+message 契约（n>1 为多模型对比预检）"""
    try:
        if n > 1:
            await check_user_quota_n_times(user, db, n)
        else:
            await check_user_quota(user, db)
    except HTTPException as e:
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "QUOTA_EXCEEDED", "message": str(e.detail)},
            )
        raise


def _parse_form_check_types(raw: Optional[str]) -> Optional[List[str]]:
    """解析 multipart 表单里的 check_types（逗号分隔，如 typo,punctuation）"""
    if raw is None or not raw.strip():
        return None
    valid = {t.value for t in CheckType}
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    invalid = [p for p in parts if p not in valid]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": f"check_types 含非法值: {', '.join(invalid)}（可选: {', '.join(sorted(valid))}）"},
        )
    return parts


def _validate_form_domain(raw: str) -> str:
    valid = {d.value for d in Domain}
    if raw not in valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": f"domain 非法: {raw}（可选: {', '.join(sorted(valid))}）"},
        )
    return raw


@router.post(
    "/proofread",
    response_model=TextProofreadResponse,
    summary="文本审校",
    description=(
        "对文本进行智能审校，返回逐条问题（原文片段 + 修改建议 + 解释）。\n\n"
        "**认证**：请求头 `Authorization: Bearer tm_...`（API 密钥，网页端「API 密钥」页创建）。\n\n"
        "**快速开始**：只需传 `text`，其余参数全部可选——\n"
        "```json\n"
        '{"text": "这是一段需要审校的文本。"}\n'
        "```\n\n"
        "**参数说明**：\n"
        "- `domain`：文本领域（general/official/legal/...），影响校对规则侧重，默认 general\n"
        "- `config_id`：指定模型配置，普通集成方无需关心\n"
        "- `check_types`：已废弃，传入无效果（总是全量审校）\n\n"
        "**限制**：单密钥每分钟 12 次（`429 RATE_LIMITED`）；"
        "每日配额随归属账号（`429 KEY_QUOTA_EXCEEDED / QUOTA_EXCEEDED`）。\n\n"
        "**错误格式**：非 2xx 时 `detail` 统一为 `{'code': ..., 'message': ...}`，见下方各状态码示例。"
    ),
    responses=ERROR_RESPONSES,
)
async def open_proofread(
    request: TextProofreadRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """
    开放文本审校端点（复用 Web 端同一校对服务）
    """
    user, api_key = auth

    # 顺序：RPM → 用户配额（免费检查）→ 密钥日配额计费。
    # 用户配额不足时直接拒绝，不扣密钥额度
    if api_key is not None:
        await check_api_key_rpm(api_key)
    await _check_user_quota_contract(user, db)
    if api_key is not None:
        await charge_api_key_daily(api_key)

    timer = AuditTimer()
    timer.start()
    audit_extra = {"check_types": request.check_types, "domain": request.domain}
    if api_key is not None:
        audit_extra["api_key_id"] = api_key.id
        audit_extra["api_key_prefix"] = api_key.key_prefix

    try:
        result = await proofread_text(
            text=request.text,
            check_types=request.check_types,
            domain=request.domain,
            config_id=request.config_id,
            user_id=user.id,
        )
    except RuntimeError as e:
        import traceback
        logger.error(f"[OpenAPI] 校对服务异常: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "api_proofread", user=user,
            input_text=request.text, extra_params=audit_extra,
            status="failed", error_message=str(e), duration_ms=timer.elapsed_ms(),
        )
        if request.config_id is not None:
            # 用户指定了无效 config_id：用户错误，不退还配额
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_CONFIG", "message": str(e)},
            )
        # 服务端故障（如未配置活跃模型）：退还密钥日配额
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "MODEL_UNAVAILABLE", "message": "审校服务暂时不可用，请稍后重试"},
        )
    except Exception as e:
        import traceback
        logger.error(f"[OpenAPI] 校对未知错误: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "api_proofread", user=user,
            input_text=request.text, extra_params=audit_extra,
            status="failed", error_message=str(e), duration_ms=timer.elapsed_ms(),
        )
        # 服务端错误：退还密钥日配额
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": "审校过程发生错误，请稍后重试"},
        )

    # 落校对记录：用户配额按 ProofreadRecord 计数，API 调用必须入库
    record = ProofreadRecord(
        user_id=user.id,
        type="text",
        original_text=request.text,
        check_types=json.dumps(request.check_types or []),
        domain=request.domain,
        result=result,
        total_issues=result["total_issues"],
        token_usage=result["usage"],
    )
    db.add(record)
    await db.flush()

    issues = [
        ProofreadIssue(
            original=item.get("original", ""),
            type=item.get("type", "unknown"),
            suggestion=item.get("suggestion", ""),
            explanation=item.get("explanation", ""),
            severity=item.get("severity", "warning"),
            chunk_index=item.get("chunk_index", 0),
        )
        for item in result["issues"]
    ]

    record_audit_log(
        http_request, "api_proofread", user=user,
        input_text=request.text,
        output_text=f"发现{result['total_issues']}个问题",
        extra_params={**audit_extra, "total_issues": result["total_issues"]},
        token_usage=result.get("usage"),
        duration_ms=timer.elapsed_ms(),
    )

    return TextProofreadResponse(
        issues=issues,
        total_issues=result["total_issues"],
        chunks_count=result["chunks_count"],
        usage=result["usage"],
        domain=result["domain"],
        check_types=result["check_types"],
        record_id=record.id,
    )


# ======================================================================
# 多模型并发对比
# ======================================================================

@router.get(
    "/models",
    response_model=OpenModelsResponse,
    summary="可用模型列表",
    description=(
        "返回当前已启用的模型配置（id/名称/模型标识，不含密钥）。\n\n"
        "`id` 用于：\n"
        "- `POST /proofread` 与 `POST /documents` 的 `config_id`（指定单模型，不填=系统默认，即 `is_active=true` 的那条）\n"
        "- `POST /proofread/compare` 的 `config_ids`（2-4 个）\n\n"
        "```bash\n"
        'curl -H "Authorization: Bearer tm_..." .../api/v1/open/models\n'
        "```\n\n"
        "调用对比接口前先查此列表拿 ID。列表内容由管理员在后台维护，"
        "若不足 2 个可用模型请联系管理员启用。"
    ),
    responses=ERROR_RESPONSES,
)
async def open_list_models(
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """可用模型列表（供集成方获取 config_id / config_ids 取值）"""
    result = await db.execute(
        select(LLMConfig.id, LLMConfig.name, LLMConfig.model, LLMConfig.is_active)
        .where(LLMConfig.is_enabled == True)
        .order_by(LLMConfig.is_active.desc(), LLMConfig.id)
    )
    rows = result.all()
    return OpenModelsResponse(
        models=[{"id": r.id, "name": r.name, "model": r.model, "is_active": r.is_active} for r in rows]
    )


@router.post(
    "/proofread/compare",
    response_model=OpenCompareResponse,
    summary="多模型对比审校",
    description=(
        "同一文本用多个模型并发审校，返回各模型结果及交叉统计（共识/独有）。\n\n"
        "**第一步**：先调用 `GET /models` 获取可用模型的 `id`。\n\n"
        "**额度**：按**成功**的模型数计（如 2 个模型全部成功 = 消耗 2 次额度；"
        "1 个失败则只消耗 1 次，失败模型自动退还）。\n\n"
        "**config_ids**：2-4 个模型ID（来自 `GET /models`）。\n\n"
        "单模型调用失败不影响其他模型：对应 result 项 `success=false` 并带 `error` 说明。"
    ),
    responses=ERROR_RESPONSES,
)
async def open_proofread_compare(
    request: OpenCompareRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """
    开放多模型对比审校端点（复用 Web 端同一对比逻辑）
    """
    user, api_key = auth

    # 加载模型配置（仅启用的可参与对比）
    cfg_result = await db.execute(
        select(LLMConfig).where(
            LLMConfig.id.in_(request.config_ids),
            LLMConfig.is_enabled == True,
        )
    )
    configs = {c.id: c for c in cfg_result.scalars().all()}
    if len(configs) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_CONFIG",
                "message": "所选模型配置不足 2 个有效项。请先调用 GET /open/models 查看可用模型ID（已停用的配置不可用）",
            },
        )

    n = len(request.config_ids)
    # 顺序与文本端点一致：RPM → 用户配额预检（免费）→ 密钥计费（按模型数）
    if api_key is not None:
        await check_api_key_rpm(api_key)
    await _check_user_quota_contract(user, db, n)
    # 对比一次消耗 n 倍额度：密钥日配额按模型数计
    if api_key is not None:
        await charge_api_key_daily(api_key, n)

    timer = AuditTimer()
    timer.start()
    audit_extra = {"domain": request.domain, "configs": [c.name for c in configs.values()]}
    if api_key is not None:
        audit_extra["api_key_id"] = api_key.id

    async def _run_one(config):
        t0 = time.perf_counter()
        try:
            r = await proofread_text(
                text=request.text,
                check_types=request.check_types,
                domain=request.domain,
                config_id=config.id,
                user_id=user.id,
            )
            return OpenCompareModelResult(
                config_id=config.id,
                config_name=config.name,
                model=config.model,
                issues=r["issues"],
                total_issues=r["total_issues"],
                success=True,
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as e:
            # 异常原文可能含密钥片段/内部路径：详情记日志，调用方只给友好提示
            logger.error(f"[OpenAPI对比] 模型 {config.name} 失败: {e}")
            return OpenCompareModelResult(
                config_id=config.id,
                config_name=config.name,
                model=config.model,
                success=False,
                error=f"模型 {config.name} 调用失败，请检查该配置的密钥与模型名（详情见服务端日志）",
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )

    items = await asyncio.gather(*[_run_one(c) for c in configs.values()])
    items = sorted(items, key=lambda i: request.config_ids.index(i.config_id))

    # 部分模型失败：失败模型退还密钥日配额（按成功数结算，失败的不计费）
    if api_key is not None:
        failed = sum(1 for i in items if not i.success)
        if failed > 0:
            await refund_api_key_daily_usage(api_key, failed)

    # 交叉统计：按 original 文本对齐（成功模型 ≥2 才有意义）
    ok_results = [i for i in items if i.success and i.issues]
    consensus: List[str] = []
    only_in: Dict[int, List[str]] = {}
    if len([i for i in items if i.success]) >= 2:
        owner_map: Dict[str, List[int]] = {}
        for i in ok_results:
            for iss in i.issues:
                orig = (iss.original or "").strip()
                if orig:
                    owner_map.setdefault(orig, []).append(i.config_id)
        for orig, owners in owner_map.items():
            ok_ids = [i.config_id for i in items if i.success]
            if len(set(owners)) == len(ok_ids):
                consensus.append(orig)
            elif len(set(owners)) == 1:
                only_in.setdefault(owners[0], []).append(orig)

    record_audit_log(
        http_request, "api_proofread_compare", user=user,
        input_text=request.text,
        extra_params={**audit_extra, "issues_per_model": {str(i.config_id): i.total_issues for i in items}},
        duration_ms=timer.elapsed_ms(),
    )

    return OpenCompareResponse(results=items, consensus_originals=consensus, only_in=only_in)


# ======================================================================
# 异步文档审校
# ======================================================================

ALLOWED_DOC_EXTENSIONS = {".doc", ".docx", ".pdf", ".txt"}


def _remove_file_silently(file_path: str) -> None:
    """失败路径清理已落盘的文件及其所属 file_id 目录（不存在/删除失败都不抛出）"""
    try:
        import shutil
        dir_path = os.path.dirname(file_path)
        if os.path.isfile(file_path):
            os.remove(file_path)
        # 目录内已无其他文件时一并移除（上传目录按 file_id 隔离，属本请求独有）
        if os.path.isdir(dir_path) and not os.listdir(dir_path):
            os.rmdir(dir_path)
    except OSError as e:
        logger.warning(f"[OpenAPI] 清理失败路径文件异常: {file_path}: {e}")


@router.post(
    "/documents",
    response_model=OpenDocumentSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="文档审校（异步）",
    description=(
        "上传文档并提交异步审校任务，立即返回 `job_id`。\n\n"
        "支持格式：`.doc` / `.docx` / `.pdf` / `.txt`，大小上限 20MB。\n\n"
        "**快速开始**（curl）：\n"
        "```bash\n"
        'curl -X POST .../api/v1/open/documents -H "Authorization: Bearer tm_..." \\\n'
        '  -F "file=@报告.docx"\n'
        "```\n\n"
        "**表单参数**（均可选）：`domain`（默认 general）、`config_id`"
        "（`check_types` 已废弃，传入无效果）。\n\n"
        "之后轮询 `status_url`（即 `GET /open/jobs/{job_id}`），"
        "`status=SUCCESS` 时 `result` 字段含审校结果与修订文档下载地址。"
    ),
    responses=DOC_ERROR_RESPONSES,
)
async def open_submit_document(
    http_request: Request,
    file: UploadFile = File(..., description="待审校文档：.doc/.docx/.pdf/.txt"),
    check_types: Optional[str] = Form(None, deprecated=True, description="（已废弃，传入无效果）历史参数：校对类型"),
    domain: str = Form("general", description="文本领域"),
    config_id: Optional[int] = Form(None, description="指定模型配置ID（可选）"),
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """
    开放文档异步审校：上传 → 提交 Celery 任务 → 返回 job_id
    """
    from app.core.config import settings as app_settings

    user, api_key = auth

    parsed_check_types = _parse_form_check_types(check_types)
    parsed_domain = _validate_form_domain(domain)

    if api_key is not None:
        await check_api_key_rpm(api_key)

    # ---- 文件校验 ----
    if not file.filename:
        raise HTTPException(status_code=400, detail={"code": "INVALID_FILE", "message": "文件名不能为空"})
    filename = sanitize_filename(file.filename)
    if not filename:
        raise HTTPException(status_code=400, detail={"code": "INVALID_FILE", "message": "文件名不合法"})
    _, file_ext = os.path.splitext(filename)
    file_ext = file_ext.lower()
    if file_ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_FILE", "message": f"不支持的文件格式: {file_ext}，仅支持 .doc / .docx / .pdf / .txt"},
        )

    content = await file.read()
    file_size = len(content)
    max_size = app_settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if file_size > max_size:
        raise HTTPException(
            status_code=400,
            detail={"code": "FILE_TOO_LARGE", "message": f"文件大小超过限制（最大 {app_settings.MAX_UPLOAD_SIZE_MB}MB）"},
        )

    # ---- 保存并提取文本 ----
    file_id = str(uuid.uuid4())
    file_path = safe_upload_path(file_id, filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "wb") as f:
        f.write(content)

    # 此后任何失败路径都不该在磁盘留下孤儿文件
    try:
        try:
            extracted_text = extract_text_from_file(file_path, file_ext)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"code": "INVALID_FILE", "message": str(e)})
        except Exception as e:
            logger.error(f"[OpenAPI] 文档文本提取失败: {e}")
            raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": "文档文本提取失败，请检查文件是否损坏"})
        if not extracted_text.strip():
            raise HTTPException(status_code=400, detail={"code": "INVALID_FILE", "message": "文件中未提取到有效文本内容"})

        # ---- 配额（用户输入校验完成后才计费）----
        try:
            await _check_user_quota_contract(user, db)
        except HTTPException:
            raise
        if api_key is not None:
            await charge_api_key_daily(api_key, 1)
    except HTTPException:
        _remove_file_silently(file_path)
        raise
    except Exception:
        _remove_file_silently(file_path)
        raise

    # ---- 上传记录（管理后台可见）----
    doc_record = UploadedDocument(
        file_id=file_id,
        filename=filename,
        file_ext=file_ext,
        file_size=file_size,
        file_path=file_path,
        text_length=len(extracted_text),
        extracted_text=extracted_text,
        user_id=user.id,
        username=user.username,
        status="uploaded",
    )
    db.add(doc_record)
    await db.flush()

    # ---- 提交异步任务 ----
    try:
        task = async_proofread_document.delay(
            text=extracted_text,
            check_types=parsed_check_types,
            domain=parsed_domain,
            file_id=file_id,
            filename=filename,
            file_path=file_path,
            file_ext=file_ext,
            user_id=user.id,
            config_id=config_id,
            api_key_id=api_key.id if api_key else None,
        )
    except Exception as e:
        logger.error(f"[OpenAPI] 任务队列不可用: {e}")
        # 队列故障：退还密钥日配额 + 清理已落盘文件与上传记录
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        _remove_file_silently(file_path)
        try:
            await db.delete(doc_record)
            await db.flush()
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "TASK_QUEUE_UNAVAILABLE", "message": "任务队列暂时不可用，请稍后重试"},
        )

    # 归属映射：FAILURE 状态 Celery 只存异常（meta 丢失），轮询归属校验依赖此记录
    try:
        from app.core.redis import get_redis
        await get_redis().set(f"textmirror:openjob:{task.id}", str(user.id), ex=172800)
    except Exception as e:
        logger.warning(f"[OpenAPI] 任务归属记录写入失败（轮询将降级为 meta 校验）: {e}")

    record_audit_log(
        http_request, "api_proofread_doc", user=user,
        input_text=extracted_text[:200],
        extra_params={
            "action": "submit",
            "text_length": len(extracted_text),
            "domain": parsed_domain,
            "check_types": parsed_check_types,
            "job_id": task.id,
        },
        file_id=file_id,
        file_name=filename,
        file_path=file_path,
        file_size=file_size,
    )

    return OpenDocumentSubmitResponse(
        job_id=task.id,
        filename=filename,
        text_length=len(extracted_text),
        status="queued",
        status_url=f"/api/v1/open/jobs/{task.id}",
    )


def _task_user_id(result: AsyncResult):
    """提取任务归属（PROGRESS meta / SUCCESS 结果中的 user_id）"""
    if result.state == "PROGRESS" and isinstance(result.info, dict):
        return result.info.get("user_id")
    if result.state == "SUCCESS" and isinstance(result.result, dict):
        return result.result.get("user_id")
    return None


def _build_job_payload(job_id: str, result: AsyncResult) -> OpenJobStatusResponse:
    """构造任务状态响应（任务失败体现在 payload，HTTP 错误仅用于认证/404）"""
    payload = OpenJobStatusResponse(job_id=job_id, status=result.state, progress=0, message="")
    if result.state == "PENDING":
        payload.message = "任务排队中..."
    elif result.state == "PROGRESS":
        meta = result.info or {}
        payload.progress = meta.get("progress", 0)
        payload.message = meta.get("message", "处理中...")
    elif result.state == "SUCCESS":
        payload.progress = 100
        payload.message = "审校完成"
        data = dict(result.result) if isinstance(result.result, dict) else {}
        data.pop("user_id", None)
        payload.result = data or None
    elif result.state == "FAILURE":
        payload.message = "任务失败，请重新提交（如持续失败请联系管理员）"
        payload.error = "TASK_FAILED"
    else:
        payload.progress = 5
        payload.message = f"状态: {result.state}"
    return payload


async def _job_owner(job_id: str) -> Optional[int]:
    """任务归属：优先读提交时写入的 Redis 映射（FAILURE 状态 meta 丢失），无记录返回 None"""
    try:
        from app.core.redis import get_redis
        owner = await get_redis().get(f"textmirror:openjob:{job_id}")
        if owner is not None:
            return int(owner)
    except Exception as e:
        logger.warning(f"[OpenAPI] 读取任务归属 Redis 异常: {e}")
    return None


@router.get(
    "/jobs/{job_id}",
    response_model=OpenJobStatusResponse,
    summary="查询任务状态",
    description=(
        "轮询异步文档审校任务：\n\n"
        "- `PENDING`：排队中（提交后立即轮询会出现）\n"
        "- `PROGRESS`：处理中，`progress` 为百分比\n"
        "- `SUCCESS`：完成，`result` 含 issues / total_issues / corrected_download_url（修订文档下载地址，相对路径，带签名有时效）\n"
        "- `FAILURE`：失败（`error=TASK_FAILED`），失败任务不消耗密钥当日配额\n\n"
        "建议轮询间隔 2-5 秒。"
    ),
    responses=JOBS_ERROR_RESPONSES,
)
async def open_get_job(
    job_id: str,
    http_request: Request,
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """
    查询异步任务状态（仅任务提交者可查看）
    """
    user, _api_key = auth

    result = AsyncResult(job_id, app=celery_app)

    # 归属：优先 Redis 映射（覆盖所有状态含 FAILURE）
    owner = await _job_owner(job_id)
    if owner is not None:
        if owner != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"},
            )
        return _build_job_payload(job_id, result)

    # 无 Redis 记录（非本 API 提交 / Redis 异常 / 记录过期）：降级 meta 校验
    if result.state == "PENDING":
        # 无法归属的排队态：仅返回状态，不含任何数据
        return _build_job_payload(job_id, result)
    task_user_id = _task_user_id(result)
    if task_user_id is None or task_user_id != user.id:
        # FAILURE 等无法归属的任务一律 404，防止窥探他人失败任务
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"},
        )

    return _build_job_payload(job_id, result)
