"""只评测人工确认快照，不保存审校记录、不扣配额、不产生新的 gold。"""
import asyncio
import time
from contextvars import ContextVar
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.models.llm_config import LLMConfig
from app.schemas.quality_evaluation import (
    EvaluationCase,
    EvaluationModel,
    EvaluationRequest,
    EvaluationSnapshot,
    FeedbackEvaluation,
)
from app.services.proofread import proofread_text

CASE_TIMEOUT_SECONDS = 90
REQUEST_TIMEOUT_SECONDS = 240
MAX_CONCURRENCY = 4
EVALUATION_ERROR = "评测未完成或结果无法可靠定位，请检查模型配置后重试。"
SAMPLES_ERROR = "请选择 1 至 10 条有效且已人工确认的样例。"
CONFIGS_ERROR = "请选择 1 至 4 个不同的有效且已启用的模型配置。"

# ContextVar 随本次评测的子任务传播；普通审校保持原有 Provider 行为。
_provider_slots: ContextVar[asyncio.Semaphore | None] = ContextVar("quality_evaluation_slots", default=None)
_provider_models: ContextVar[dict[int, str] | None] = ContextVar("quality_evaluation_models", default=None)
# 每个 case 独立的可变列表，分片/自检子任务的检查结果也能回传给 run_one。
_response_checks: ContextVar[list[bool] | None] = ContextVar("quality_evaluation_response_checks", default=None)


def configure_evaluation_provider(provider):
    """由 get_llm_provider 调用，仅约束评测，包含分片/自检的实际 HTTP 并发。"""
    slots = _provider_slots.get()
    if slots is None:
        return
    provider.usage_business = "evaluation"
    models = _provider_models.get()
    # 同 ID 的配置可能已改模型；必须在请求前核对实际 Provider，不能仅检查结果的 ID。
    if (models is None or type(provider.config_id) is not int or provider.config_id not in models
            or provider.model != models[provider.config_id]):
        raise ValueError(EVALUATION_ERROR)
    # 现有 Provider 的 max_retries 表示总尝试次数而非额外重试数。
    provider.max_retries = 1
    # 不因 404 再试其他 endpoint；遵循已验证地址或默认首选地址。
    provider._endpoints = provider._endpoints[:1]
    original_chat = provider.chat
    checks = _response_checks.get()

    async def single_attempt_chat(*args, **kwargs):
        async with slots:
            check_index = len(checks) if checks is not None else None
            if checks is not None:
                checks.append(False)
            response = await original_chat(*args, **kwargs)
            complete = getattr(response, "finish_reason", None) == "stop"
            if check_index is not None:
                checks[check_index] = complete
            if not complete:
                # 即使 content 是合法 []，截断/过滤/工具调用/缺失结束原因也不能算完整。
                raise ValueError(EVALUATION_ERROR)
            return response

    provider.chat = single_attempt_chat


async def load_confirmed_samples(db, feedback_ids=None):
    from app.services.quality_feedback import load_confirmed_samples as loader

    return await loader(db, feedback_ids)


async def prepare_evaluation(db, feedback_ids, config_ids):
    """在首次模型调用前验证完整请求，返回不含 ORM/密钥的本地快照。"""
    try:
        # CLI 可省略反馈 IDs（所有 confirmed），但一次仍不可超过 10 条。
        EvaluationRequest(feedback_ids=feedback_ids if feedback_ids is not None else [1], config_ids=config_ids)
    except ValidationError:
        raise HTTPException(422, "评测 ID 必须为不重复的正整数，样例最多 10 条、模型最多 4 个。") from None
    try:
        raw_samples = await load_confirmed_samples(db, feedback_ids)
        snapshots = [EvaluationSnapshot.model_validate(item) for item in raw_samples]
        ids = [item.id for item in snapshots]
        if not 1 <= len(ids) <= 10 or len(ids) != len(set(ids)):
            raise ValueError
        if feedback_ids is not None:
            if set(ids) != set(feedback_ids):
                raise ValueError
            by_id = {item.id: item for item in snapshots}
            snapshots = [by_id[id_] for id_ in feedback_ids]
    except (HTTPException, ValidationError, ValueError, TypeError):
        raise HTTPException(422, SAMPLES_ERROR) from None

    # 只 SELECT 可公开字段；不要把配置密钥带到请求快照或响应。
    result = await db.execute(select(LLMConfig.id, LLMConfig.name, LLMConfig.model, LLMConfig.is_enabled)
                              .where(LLMConfig.id.in_(config_ids)))
    configs = {row.id: row for row in result.all()}
    if any(id_ not in configs or not configs[id_].is_enabled for id_ in config_ids):
        raise HTTPException(422, CONFIGS_ERROR)
    models = [EvaluationModel(config_id=id_, config_name=configs[id_].name, model=configs[id_].model)
              for id_ in config_ids]
    return snapshots, models


def has_complete_result(result):
    """旧响应 coverage 缺失也不是成功；显式失败优先于 complete。"""
    if not isinstance(result, dict):
        return False
    coverage = result.get("coverage")
    return (
        result.get("success", True) is True
        and not result.get("error")
        and result.get("complete", True) is True
        and isinstance(coverage, dict)
        and coverage.get("status") == "complete"
        and not coverage.get("failed_chunks")
        and ("total_chunks" not in coverage or coverage.get("completed_chunks") == coverage["total_chunks"])
        and isinstance(result.get("issues"), list)
        and all(isinstance(issue, dict) for issue in result["issues"])
    )


def locate_issue(text, issue):
    """坐标优先，按 Python Unicode 码点；仅无坐标且原文唯一时可回退。"""
    original = issue.get("original")
    if not isinstance(original, str) or not original:
        raise ValueError
    start, end = issue.get("start"), issue.get("end")
    if start is None and end is None:
        start = text.find(original)
        # find(start + 1) 也能发现重叠出现，不能使用 str.count。
        if start < 0 or text.find(original, start + 1) >= 0:
            raise ValueError
        return start, start + len(original)
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(text) or text[start:end] != original):
        raise ValueError
    return start, end


def target_issues(sample, result):
    """收集所有命中目标，不用 any 提前返回，以免正确项掩盖错误或无法定位项。"""
    if not has_complete_result(result):
        raise ValueError
    matches = []
    for issue in result["issues"]:
        start, end = locate_issue(sample.text, issue)
        if sample.issue_type and issue.get("type") != sample.issue_type:
            continue
        if ((start <= sample.start and sample.end <= end)
                or (sample.start <= start and end <= sample.end)):
            matches.append((issue, start, end))
    return matches


def detect_target(sample, result):
    """仅检出兼容入口；建议正确性由独立的人工 golden 比较判定。"""
    return bool(target_issues(sample, result))


def evaluate_suggestions(sample, matches):
    """比较替换后的同一上下文，无 LLM 判官、不做模糊匹配或语义猜测。"""
    if sample.expectation == "no_report":
        return "not_evaluated", "no_report"
    if not (sample.accepted_suggestions or sample.rejected_suggestions):
        return "not_evaluated", "no_constraints"
    if not matches:
        return "not_evaluated", "not_detected"

    def golden_context(replacement):
        return sample.text[:sample.start] + replacement + sample.text[sample.end:]

    accepted = {golden_context(value) for value in sample.accepted_suggestions}
    rejected = {golden_context(value) for value in sample.rejected_suggestions}
    outcomes = []
    for issue, start, end in matches:
        replacement = issue.get("suggestion")
        if not isinstance(replacement, str):
            outcomes.append(("not_evaluated", "invalid_suggestion"))
            continue
        if start <= sample.start and sample.end <= end:
            # 模型 original 可大于目标，但必须原样保留目标两侧上下文。
            # 若同时改动了目标外内容，就不能猜测哪段对应人工确认的替换。
            before = sample.text[start:sample.start]
            after = sample.text[sample.end:end]
            if (len(replacement) < len(before) + len(after)
                    or not replacement.startswith(before) or not replacement.endswith(after)):
                outcomes.append(("not_evaluated", "incomparable_context"))
                continue
        actual = sample.text[:start] + replacement + sample.text[end:]
        if actual in rejected:
            outcomes.append(("fail", "rejected"))
        elif accepted:
            outcomes.append(("pass", "all_accepted") if actual in accepted else ("fail", "not_accepted"))
        else:
            # 仅有禁止列表只能证伪，避开它不能证明建议正确。
            outcomes.append(("not_evaluated", "no_accepted_golden"))
    for status in ("fail", "not_evaluated"):
        for outcome in outcomes:
            if outcome[0] == status:
                return outcome
    return "pass", "all_accepted"


def error_case(snapshot, elapsed_ms=0):
    return EvaluationCase(feedback_id=snapshot.id, revision=snapshot.revision,
                          expectation=snapshot.sample.expectation, status="error", detected=None,
                          error=EVALUATION_ERROR, elapsed_ms=elapsed_ms)


async def run_evaluation(snapshots, models, *, deadline=None):
    """最多 10×4 个 case；排队计入总时限，实际运行各最多 90 秒，不重跑失败 case。"""
    started = time.monotonic()
    deadline = min(deadline or float("inf"), started + REQUEST_TIMEOUT_SECONDS)
    snapshots = [item.model_copy(deep=True) for item in snapshots]
    models = [item.model_copy(deep=True) for item in models]
    case_slots = asyncio.Semaphore(MAX_CONCURRENCY)
    for model in models:
        model.cases = [error_case(snapshot) for snapshot in snapshots]

    async def run_one(model, index, snapshot):
        async with case_slots:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            t0 = time.monotonic()
            case = error_case(snapshot)
            checks = []
            checks_token = _response_checks.set(checks)
            try:
                async with asyncio.timeout(min(CASE_TIMEOUT_SECONDS, remaining)):
                    result = await proofread_text(text=snapshot.sample.text, domain=snapshot.sample.domain,
                                                  config_id=model.config_id, user_id=None, depth="standard")
                    # 自检会吞掉异常；评测仍须拒绝本 case 任一次不完整的模型响应。
                    if not all(checks):
                        raise ValueError(EVALUATION_ERROR)
                    # 指定模型不允许以活跃模型替代，更不能把替代结果归给原模型。
                    if type(result.get("config_id")) is not int or result["config_id"] != model.config_id:
                        raise ValueError
                    sample = snapshot.sample
                    matches = target_issues(sample, result)
                    case.detected = bool(matches)
                    case.detection_status = "pass" if case.detected == (sample.expectation == "report") else "fail"
                    case.suggestion_status, case.suggestion_reason = evaluate_suggestions(sample, matches)
                    case.status = case.detection_status
                    if case.detected and sample.expectation == "report" and (sample.accepted_suggestions or sample.rejected_suggestions):
                        case.status = case.suggestion_status
                    case.error = None
            except Exception:
                # 不返回/记录原始 provider 异常（可能含路径、请求头、密钥）。
                pass
            finally:
                _response_checks.reset(checks_token)
                case.elapsed_ms = max(0, int((time.monotonic() - t0) * 1000))
                model.cases[index] = case

    tasks = []
    token = _provider_slots.set(asyncio.Semaphore(MAX_CONCURRENCY))
    models_token = _provider_models.set({model.config_id: model.model for model in models})
    try:
        tasks = [asyncio.create_task(run_one(model, index, snapshot))
                 for model in models for index, snapshot in enumerate(snapshots)]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(0, deadline - time.monotonic()))
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        try:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            _provider_models.reset(models_token)
            _provider_slots.reset(token)

    for model in models:
        model.report_total = sum(case.expectation == "report" for case in model.cases)
        model.no_report_total = len(model.cases) - model.report_total
        model.report_evaluated = sum(case.expectation == "report" and case.detection_status != "error" for case in model.cases)
        model.no_report_evaluated = sum(case.expectation == "no_report" and case.detection_status != "error" for case in model.cases)
        model.missed = sum(case.expectation == "report" and case.detected is False for case in model.cases)
        model.false_positives = sum(case.expectation == "no_report" and case.detected is True for case in model.cases)
        model.errors = sum(case.detection_status == "error" for case in model.cases)
        model.suggestion_passed = sum(case.suggestion_status == "pass" for case in model.cases)
        model.suggestion_failed = sum(case.suggestion_status == "fail" for case in model.cases)
        model.suggestion_evaluated = model.suggestion_passed + model.suggestion_failed
        model.suggestion_not_evaluated = len(model.cases) - model.suggestion_evaluated
    return FeedbackEvaluation(generated_at=datetime.now(timezone.utc).isoformat(), samples=snapshots, results=models)
