"""质量反馈候选、人工 CAS 审核和 confirmed 样例装载；不写原报告、不调用模型。"""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_config import LLMConfig
from app.models.proofread import ProofreadRecord
from app.models.quality_feedback import QualityFeedback
from app.schemas.quality_feedback import FeedbackReviewRequest, FeedbackSample, FeedbackStatus, QualityFeedbackCreate


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def _reports(value) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _matches(issue: dict, data: QualityFeedbackCreate) -> bool:
    return (
        type(issue.get("start")) is int and type(issue.get("end")) is int
        and issue["start"] == data.start and issue["end"] == data.end
        and issue.get("original") == data.original and issue.get("suggestion") == data.suggestion
        and issue.get("type") == data.issue_type
    )


def _compare_reports(result: dict, state: dict) -> tuple[list[dict], list[dict]]:
    original = _reports(result.get("results")) if result.get("compare") is True else []
    saved = _reports(_object(state.get("compare")).get("results"))
    return original, saved


def _has_report(record: ProofreadRecord, data: QualityFeedbackCreate) -> bool:
    result, state = _object(record.result), _object(record.review_state)
    original, saved = _compare_reports(result, state)
    reports = [result, state, *(item for item in original + saved if item.get("success") is True)]
    return any(_matches(issue, data) for report in reports for issue in _reports(report.get("issues")))


def _coverage(report: dict) -> str:
    status = _object(report.get("coverage")).get("status")
    return status if status in ("complete", "partial") else "unknown"


def _observed(issue: dict, data: QualityFeedbackCreate, source: str) -> bool | None:
    if data.kind != "missed":
        if _matches(issue, data):
            return True
        if (issue.get("original") == data.original and issue.get("suggestion") == data.suggestion
                and issue.get("type") == data.issue_type
                and (type(issue.get("start")) is not int or type(issue.get("end")) is not int)):
            return None
        return False
    if data.issue_type and issue.get("type") != data.issue_type:
        return False
    original = issue.get("original")
    if not isinstance(original, str) or not original:
        return None
    start, end = issue.get("start"), issue.get("end")
    if start is None and end is None:
        start = source.find(original)
        if start < 0 or source.find(original, start + 1) >= 0:
            return None
        end = start + len(original)
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source)
            or source[start:end] != original):
        return None
    # 漏检反馈可能不填写改法，模型是否报告目标只取决于位置与类型。
    return (start <= data.start and data.end <= end) or (data.start <= start and end <= data.end)


def _reported(reports: list[dict], success: bool | None, data: QualityFeedbackCreate, source: str) -> bool | None:
    if success is False:
        return None
    observations = [_observed(issue, data, source) for report in reports for issue in _reports(report.get("issues"))]
    if True in observations:
        return True
    if None in observations:
        return None
    latest = reports[-1]
    # 失败、旧记录无覆盖信息、部分覆盖均不生成负例。
    if success is True and _coverage(latest) == "complete" and isinstance(latest.get("issues"), list):
        return False
    return None


def _config_id(value) -> int | None:
    return value if type(value) is int and 0 < value <= 2147483647 else None


def _identity(value) -> str:
    return value if isinstance(value, str) else ""


async def _model_snapshot(db: AsyncSession, record: ProofreadRecord, data: QualityFeedbackCreate) -> list[dict]:
    result, state = _object(record.result), _object(record.review_state)
    original, saved = _compare_reports(result, state)
    if original:
        saved_by_id = {item.get("config_id"): item for item in saved if _config_id(item.get("config_id"))}
        snapshots = []
        for baseline in original:
            config_id = _config_id(baseline.get("config_id"))
            success = baseline.get("success") if type(baseline.get("success")) is bool else None
            reports = [baseline]
            # 身份和 success 始终来自不可变原报告；草稿仅补充逐模型问题和覆盖。
            if config_id in saved_by_id and success is True:
                reports.append(saved_by_id[config_id])
            snapshots.append({
                "config_id": config_id,
                "config_name": _identity(baseline.get("config_name")),
                "model": _identity(baseline.get("model")),
                "success": success,
                "coverage_status": _coverage(reports[-1]),
                "reported": _reported(reports, success, data, record.original_text),
            })
        return snapshots
    if result.get("compare") is True:
        # 旧汇总缺少逐模型报告：仅保留已有名称，不按汇总或用户 model_id 归因。
        names = result.get("models")
        return [{
            "config_id": None, "config_name": name, "model": "", "success": None,
            "coverage_status": "unknown", "reported": None,
        } for name in names if isinstance(name, str)] if isinstance(names, list) else []

    config_id = _config_id(result.get("config_id"))
    config_name, model = _identity(result.get("config_name")), _identity(result.get("model"))
    if config_id is not None and (not config_name or not model):
        # 仅选择身份字段，不加载 api_key、api_base 或其它运行配置。
        identity = (await db.execute(select(LLMConfig.name, LLMConfig.model).where(LLMConfig.id == config_id))).first()
        if identity is not None:
            config_name, model = config_name or identity.name, model or identity.model
    success = result.get("success")
    if type(success) is not bool:
        success = True if isinstance(result.get("issues"), list) else None
    reported = _reported([result], success, data, record.original_text)
    if reported is not True and any(_observed(issue, data, record.original_text) is not False
                                    for issue in _reports(state.get("issues"))):
        # 普通草稿的 config_id 可编辑，补查顶层报告没有可信逐模型来源，不归因给原模型。
        reported = None
    return [{
        "config_id": config_id, "config_name": config_name, "model": model, "success": success,
        "coverage_status": _coverage(result), "reported": reported,
    }]


def _dedupe_key(user_id: int, data: QualityFeedbackCreate) -> str:
    target = [user_id, data.record_id, data.kind, data.start, data.end, data.original, data.suggestion, data.issue_type]
    return hashlib.sha256(json.dumps(target, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


async def create_quality_feedback(db: AsyncSession, user_id: int, data: QualityFeedbackCreate) -> QualityFeedback:
    record = (await db.execute(select(ProofreadRecord).where(
        ProofreadRecord.id == data.record_id,
        ProofreadRecord.user_id == user_id,
        ProofreadRecord.type.in_(("text", "document")),
    ))).scalar_one_or_none()
    if record is None:
        raise HTTPException(404, "记录不存在或不支持质量反馈")
    source = record.original_text
    if data.end > len(source) or source[data.start:data.end] != data.original:
        raise HTTPException(422, "问题定位与不可变原文不匹配或越界（位置使用 Unicode 码点）")
    key = _dedupe_key(user_id, data)
    lookup = select(QualityFeedback).where(QualityFeedback.dedupe_key == key)
    existing = (await db.execute(lookup)).scalar_one_or_none()
    if existing is not None:
        return existing
    if data.kind != "missed" and not _has_report(record, data):
        raise HTTPException(422, "原报告或已保存草稿中没有该问题；补查问题请先保存草稿再提交")
    context_start = max(0, data.start - 200)
    feedback = QualityFeedback(
        **data.model_dump(), user_id=user_id, dedupe_key=key,
        context_text=source[context_start:data.end + 200], context_start=context_start,
        domain=record.domain if record.domain in ("general", "official", "legal") else "general",
        model_snapshot=await _model_snapshot(db, record, data),
    )
    try:
        # 唯一约束处理并发重复；savepoint 回滚不破坏外层事务，不覆盖已审核内容。
        async with db.begin_nested():
            db.add(feedback)
            await db.flush()
    except IntegrityError:
        existing = (await db.execute(lookup.with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        if existing is None:
            raise
        return existing
    await db.refresh(feedback)
    return feedback


async def list_quality_feedback(
    db: AsyncSession, status: FeedbackStatus | None, page: int, page_size: int,
) -> dict:
    query = select(QualityFeedback)
    if status is not None:
        query = query.where(QualityFeedback.status == status)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    items = (await db.execute(query.order_by(QualityFeedback.id.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": items, "total": total}


async def review_quality_feedback(
    db: AsyncSession, feedback_id: int, reviewer_id: int, data: FeedbackReviewRequest,
) -> QualityFeedback:
    changed = await db.execute(update(QualityFeedback).where(
        QualityFeedback.id == feedback_id, QualityFeedback.revision == data.revision,
    ).values(
        status=data.status, sample=data.sample.model_dump() if data.sample is not None else None,
        review_note=data.review_note, reviewer_id=reviewer_id, reviewed_at=datetime.now(timezone.utc),
        revision=data.revision + 1,
    ).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        if await db.scalar(select(QualityFeedback.id).where(QualityFeedback.id == feedback_id)) is None:
            raise HTTPException(404, "质量反馈不存在")
        raise HTTPException(409, "审核版本已更新，请重新加载后重试")
    return (await db.execute(select(QualityFeedback).where(
        QualityFeedback.id == feedback_id,
    ).execution_options(populate_existing=True))).scalar_one()


async def load_confirmed_samples(db: AsyncSession, feedback_ids: list[int] | None = None) -> list[dict]:
    """只导出人工确认的独立目标位置样例；指定 ID 不存在/未确认时整体拒绝。"""
    if feedback_ids is not None:
        if not isinstance(feedback_ids, list) or any(_config_id(value) is None for value in feedback_ids):
            raise HTTPException(422, "feedback_ids 必须是正整数 ID 列表")
        if not feedback_ids:
            return []
    query = select(QualityFeedback).where(QualityFeedback.status == "confirmed").order_by(QualityFeedback.id)
    if feedback_ids is not None:
        query = query.where(QualityFeedback.id.in_(feedback_ids))
    rows = (await db.execute(query.execution_options(populate_existing=True))).scalars().all()
    by_id = {row.id: row for row in rows}
    if feedback_ids is not None:
        if any(value not in by_id for value in feedback_ids):
            raise HTTPException(422, "指定质量反馈不存在或尚未人工确认")
        rows = [by_id[value] for value in feedback_ids]
    try:
        return [{"id": row.id, "revision": row.revision, "sample": FeedbackSample.model_validate(row.sample).model_dump()}
                for row in rows]
    except ValidationError as exc:
        raise HTTPException(422, "已确认反馈的样例无效，请重新审核") from exc
