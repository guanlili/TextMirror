"""审阅校验、CAS 保存与私有临时文件导出；不调用模型、不扣配额。"""
import asyncio
import copy
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

from docx import Document
from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value
from starlette.background import BackgroundTask

from app.core.file_security import sanitize_filename
from app.models.proofread import ProofreadRecord
from app.models.uploaded_document import UploadedDocument
from app.schemas.review import (
    MAX_REVIEW_BYTES,
    MAX_VERSIONS,
    ReviewCompareModel,
    ReviewCompareState,
    ReviewCoverage,
    ReviewIssue,
    ReviewResponse,
    ReviewWriteRequest,
)
from app.services.document import ReviewDocxError, generate_reviewed_docx

TRAILING_PUNCTUATION = "，。！？；、,"
MAX_STATE_BYTES = 128 * 1024 * 1024


def derive_patches(source: str, issues: list[ReviewIssue]) -> list[dict]:
    """所有有定位的问题都核验原文；只接受明确采纳的非重叠修改。"""
    patches = []
    seen = set()
    decisions = {}
    for issue in issues:
        # 不同 type 可指向同一位置/建议；它们共享一个用户决策，而非各自修改原文。
        if issue.start is not None:
            decision_key = (issue.start, issue.end, issue.original, issue.suggestion)
            flags = (issue.accepted, issue.ignored)
            if decision_key in decisions and decisions[decision_key] != flags:
                raise HTTPException(422, "共享位置与建议的问题必须使用一致的采纳/忽略决策")
            decisions[decision_key] = flags
            if issue.end <= issue.start or issue.end > len(source) or source[issue.start:issue.end] != issue.original:
                raise HTTPException(422, "问题定位与不可变原文不匹配或越界")
        if not issue.accepted:
            continue
        end = issue.end
        if issue.type == "sensitive" and not issue.suggestion:
            if end < len(source) and source[end] in TRAILING_PUNCTUATION:
                end += 1
        key = (issue.start, end, issue.suggestion)
        if key in seen:
            continue
        seen.add(key)
        patches.append({
            "start": issue.start,
            "end": end,
            "original": source[issue.start:end],
            "replacement": issue.suggestion,
        })
    patches.sort(key=lambda patch: patch["start"])
    for previous, current in zip(patches, patches[1:]):
        if previous["end"] > current["start"]:
            raise HTTPException(409, "采纳的修改存在重叠（含删除时紧邻的标点），请先撤销冲突项")
    return patches


def apply_patches(source: str, patches: list[dict]) -> str:
    parts = []
    cursor = 0
    for patch in patches:
        parts.extend((source[cursor:patch["start"]], patch["replacement"]))
        cursor = patch["end"]
    parts.append(source[cursor:])
    return "".join(parts)


def validate_coverage(source: str, coverage: ReviewCoverage | None) -> None:
    # 仅存储客户端提交的审阅覆盖信息，不把它写回不可变模型 result，亦不生成 complete 声明。
    if coverage is not None:
        for chunk in coverage.failed_chunks:
            if chunk.end <= chunk.start or chunk.end > len(source) or source[chunk.start:chunk.end] != chunk.text:
                raise HTTPException(422, "失败分片定位与不可变原文不匹配或越界")


async def load_review_record(db: AsyncSession, record_id: int, user_id: int) -> ProofreadRecord:
    record = (await db.execute(select(ProofreadRecord).where(
        ProofreadRecord.id == record_id,
        ProofreadRecord.user_id == user_id,
        ProofreadRecord.type.in_(("text", "document")),
    ))).scalar_one_or_none()
    if record is None:
        raise HTTPException(404, "记录不存在或不支持审阅")
    return record


def original_issues(items: list[dict]) -> list[ReviewIssue]:
    """模型输出仅取审阅字段，剔除 source/found_by 等来源信息及所有用户 flags。"""
    fields = {field.alias or name for name, field in ReviewIssue.model_fields.items()}
    issues = []
    for item in items:
        value = {k: v for k, v in item.items() if k in fields}
        value.update(_accepted=False, _ignored=False)
        if value.get("start") is None or value.get("end") is None:
            value.update(start=None, end=None)
        value.setdefault("severity", "warning")
        issues.append(ReviewIssue.model_validate(value))
    return issues


def issue_key(issue: ReviewIssue) -> tuple:
    return (issue.start, issue.end, issue.original, issue.type, issue.suggestion)


def original_compare(result: dict | None) -> ReviewCompareState | None:
    result = result or {}
    if result.get("compare") is not True or not result.get("results"):
        # 没有逐模型报告的老记录仍是普通草稿，不推测模型数据。
        return None
    models = []
    for item in result["results"]:
        value = {k: v for k, v in item.items() if k in ReviewCompareModel.model_fields}
        value["issues"] = original_issues(item.get("issues") or [])
        models.append(ReviewCompareModel.model_validate(value))
    return ReviewCompareState(results=models)


def validate_compare(
    record: ProofreadRecord, compare: ReviewCompareState | None, issues: list[ReviewIssue],
) -> None:
    original = original_compare(record.result)
    if original is None:
        if compare is not None:
            raise HTTPException(422, "普通记录不接受多模型审阅数据")
        return
    if compare is None:
        raise HTTPException(422, "对比记录不可丢弃逐模型报告")
    originals = {item.config_id: item for item in original.results}
    if {item.config_id for item in compare.results} != originals.keys():
        raise HTTPException(422, "不可更换对比记录的模型集合")
    report_keys = set()
    for item in compare.results:
        baseline = originals[item.config_id]
        mutable = {"issues", "coverage"}
        if item.model_dump(exclude=mutable) != baseline.model_dump(exclude=mutable):
            raise HTTPException(422, "不可修改原始模型身份、成功状态或运行元数据")
        if not item.success and (item.issues != baseline.issues or item.coverage != baseline.coverage):
            raise HTTPException(422, "失败模型的报告不可修改或重跑")
        derive_patches(record.original_text, item.issues)
        validate_coverage(record.original_text, item.coverage)
        if item.success:
            report_keys.update(issue_key(issue) for issue in item.issues)
    if {issue_key(issue) for issue in issues} != report_keys:
        raise HTTPException(422, "顶层 issues 必须与成功模型报告的问题集合一致")


def review_content(result: dict | None, state: dict | None) -> tuple[list[ReviewIssue], ReviewCompareState | None]:
    """审阅与历史共用的问题/决策口径，不读取正文或构建版本响应。"""
    result = result or {}
    data = state if state is not None else result
    compare = data.get("compare") if state is not None else None
    if compare is None:
        compare = original_compare(result)
    else:
        compare = ReviewCompareState.model_validate(compare)
    issues = data.get("issues") or []
    if state is None:
        # 协作 findings 是已合并发现，不按角色 issue_count 相加；空的已保存草稿不回填。
        if "issues" not in data:
            issues = (result.get("collaboration") or {}).get("findings") or []
        # 模型输出不是用户决策，不能沿用其中的 flags 或历史自动生成的成稿。
        issues = original_issues(issues)
    else:
        issues = [ReviewIssue.model_validate(item) for item in issues]
    if compare is not None and (state is None or state.get("compare") is None):
        # 旧汇总可能丢失类型；补齐报告时只继承同一已定位修改的用户决策。
        saved_issues = [ReviewIssue.model_validate(item) for item in issues] if state is not None else []
        merged = {issue_key(issue): issue for issue in saved_issues}
        decisions = {
            (issue.start, issue.end, issue.original, issue.suggestion): {
                "accepted": issue.accepted, "ignored": issue.ignored,
            }
            for issue in saved_issues if issue.start is not None
        }
        for item in compare.results:
            if item.success:
                for issue in item.issues:
                    flags = decisions.get((issue.start, issue.end, issue.original, issue.suggestion), {})
                    merged.setdefault(issue_key(issue), issue.model_copy(update=flags))
        issues = list(merged.values())
    return issues, compare


def history_review_summary(
    result: dict | None, state: dict | None,
    *, content: tuple[list[ReviewIssue], ReviewCompareState | None] | None = None,
) -> dict:
    """仅聚合当前草稿；失败优先于未知，未知优先于完整。"""
    result = result or {}
    data = state if state is not None else result
    issues, compare = content if content is not None else review_content(result, state)
    collaboration = result.get("collaboration")
    mode = "collaboration" if collaboration is not None else "compare" if result.get("compare") is True or compare else "single"
    failed_models = 0
    if mode == "compare":
        models = compare.results if compare else []
        failed_models = sum(not model.success for model in models)
        statuses = [model.coverage.status if model.coverage else "unknown" for model in models]
        if failed_models:
            statuses.append("partial")
        # 没有逐模型覆盖时，不用旧顶层 complete 字段冒充对比完整。
    else:
        statuses = [(data.get("coverage") or {}).get("status", "unknown")]
        if collaboration is not None:
            statuses.append(collaboration.get("status", "unknown"))
            if any(role.get("status") in {"failed", "cancelled"} for role in collaboration.get("roles", [])):
                statuses.append("partial")
    coverage_status = "partial" if "partial" in statuses else "complete" if statuses and all(s == "complete" for s in statuses) else "unknown"
    accepted = sum(issue.accepted for issue in issues)
    ignored = sum(issue.ignored for issue in issues)
    return {
        "mode": mode,
        "coverage_status": coverage_status,
        "review_summary": {
            "total": len(issues), "accepted": accepted, "ignored": ignored,
            "pending": len(issues) - accepted - ignored, "failed_models": failed_models,
        },
    }


def review_response(record: ProofreadRecord) -> ReviewResponse:
    state = record.review_state
    data = state if state is not None else (record.result or {})
    issues, compare = review_content(record.result, state)
    return ReviewResponse(
        record_id=record.id,
        revision=record.review_revision or 0,
        original_text=record.original_text,
        source_file_id=record.source_file_id,
        source_filename=record.source_filename,
        domain=record.domain,
        depth=data.get("depth"),
        config_id=data.get("config_id"),
        issues=issues,
        coverage=data.get("coverage"),
        compare=compare,
        collaboration=(record.result or {}).get("collaboration"),
        modified_text=record.modified_text if state is not None and record.modified_text is not None else record.original_text,
        versions=(state or {}).get("versions", []),
    )


async def save_review(
    db: AsyncSession, record: ProofreadRecord, user_id: int,
    request: ReviewWriteRequest, *, create_version: bool = False, label: str | None = None,
) -> ReviewResponse:
    if record.review_revision != request.revision:
        raise HTTPException(409, "审阅版本已更新，请重新加载后重试")
    current = review_response(record)
    patches = derive_patches(record.original_text, request.issues)
    coverage = request.coverage if "coverage" in request.model_fields_set else current.coverage
    compare = request.compare if "compare" in request.model_fields_set else current.compare
    validate_coverage(record.original_text, coverage)
    validate_compare(record, compare, request.issues)
    modified_text = apply_patches(record.original_text, patches)
    state = {
        "issues": [issue.model_dump(by_alias=True) for issue in request.issues],
        "coverage": coverage.model_dump() if coverage is not None else None,
        "compare": compare.model_dump(by_alias=True) if compare is not None else None,
        "depth": request.depth if "depth" in request.model_fields_set else current.depth,
        "config_id": request.config_id if "config_id" in request.model_fields_set else current.config_id,
        "versions": copy.deepcopy((record.review_state or {}).get("versions", [])),
    }
    draft_size = len(json.dumps({k: v for k, v in state.items() if k != "versions"}, ensure_ascii=False).encode("utf-8"))
    if draft_size > MAX_REVIEW_BYTES:
        raise HTTPException(422, "审阅 JSON 超过 8 MiB 限制")
    if create_version:
        if len(state["versions"]) >= MAX_VERSIONS:
            raise HTTPException(422, "最多保存20个版本；已有版本不会被自动删除")
        now = datetime.now(timezone.utc)
        state["versions"].append({
            "id": str(uuid.uuid4()),
            "label": (label or "").strip() or f"版本 {len(state['versions']) + 1} · {now:%Y-%m-%d %H:%M UTC}",
            "created_at": now.isoformat(),
            "issues": copy.deepcopy(state["issues"]),
            "coverage": copy.deepcopy(state["coverage"]),
            "compare": copy.deepcopy(state["compare"]),
            "modified_text": modified_text,
        })
    if len(json.dumps(state, ensure_ascii=False).encode("utf-8")) > MAX_STATE_BYTES:
        raise HTTPException(422, "草稿及版本总 JSON 超过 128 MiB 限制")
    updated = await db.execute(
        update(ProofreadRecord).where(
            ProofreadRecord.id == record.id,
            ProofreadRecord.user_id == user_id,
            ProofreadRecord.review_revision == request.revision,
        ).values(
            review_revision=request.revision + 1,
            review_state=state,
            modified_text=modified_text,
        ).execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        raise HTTPException(409, "审阅版本已更新，请重新加载后重试")
    # CAS 已写库；避免 ORM 再发出不带 revision 条件的 UPDATE。提交/回滚交给 get_db。
    set_committed_value(record, "review_revision", request.revision + 1)
    set_committed_value(record, "review_state", state)
    set_committed_value(record, "modified_text", modified_text)
    return review_response(record)


async def load_review_source(db: AsyncSession, record: ProofreadRecord, user_id: int):
    if not record.source_file_id:
        if record.type == "document":
            raise HTTPException(410, "该记录没有可靠的源文件关联，无法导出 DOCX；请导出 TXT")
        return None
    document = (await db.execute(select(UploadedDocument).where(
        UploadedDocument.file_id == record.source_file_id,
        UploadedDocument.user_id == user_id,
        UploadedDocument.owner_kind == "user",
        UploadedDocument.status != "deleted",
    ))).scalar_one_or_none()
    if document is None or not os.path.isfile(document.file_path):
        raise HTTPException(410, "源文件已删除、过期或不可访问；请导出 TXT")
    return document


def _write_export(source, patches, format, output_path, original_path, file_ext):
    modified = apply_patches(source, patches)
    if format == "txt":
        with open(output_path, "w", encoding="utf-8", newline="") as stream:
            stream.write(modified)
    elif original_path and file_ext.lower() == ".docx":
        generate_reviewed_docx(original_path, source, patches, output_path)
    else:
        document = Document()
        for line in modified.split("\n"):
            document.add_paragraph(line)
        document.save(output_path)


async def export_review_file(
    source: str, issues: list[ReviewIssue], format: str, filename: str | None,
    *, original_path: str | None = None, file_ext: str = "",
) -> FileResponse:
    patches = derive_patches(source, issues)
    if format == "docx" and original_path and not os.path.isfile(original_path):
        raise HTTPException(410, "源文件已删除或过期；请导出 TXT")
    # 不写上传/public 目录，不颁发签名 URL；下载完成或生成失败均清理临时文件。
    temporary = tempfile.TemporaryDirectory(prefix="textmirror-review-")
    path = os.path.join(temporary.name, f"review.{format}")
    try:
        await asyncio.to_thread(_write_export, source, patches, format, path, original_path, file_ext)
        name = sanitize_filename(f"审阅稿_{os.path.splitext(filename or '文本')[0]}.{format}")
        media = "text/plain; charset=utf-8" if format == "txt" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return FileResponse(path, filename=name, media_type=media, background=BackgroundTask(temporary.cleanup))
    except FileNotFoundError:
        temporary.cleanup()
        raise HTTPException(410, "源文件已删除或过期；请导出 TXT")
    except ReviewDocxError as exc:
        temporary.cleanup()
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        temporary.cleanup()
        raise HTTPException(422, "无法安全生成审阅文档；请尝试导出 TXT")
