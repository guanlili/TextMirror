"""人工确认样例评测契约；与前端 FeedbackEvaluation 保持一致。"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.quality_feedback import FeedbackSample

PositiveId = Annotated[int, Field(strict=True, gt=0, le=2147483647)]
Offset = Annotated[int, Field(strict=True, ge=0)]
Expectation = Literal["report", "no_report"]


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback_ids: list[PositiveId] = Field(min_length=1, max_length=10)
    config_ids: list[PositiveId] = Field(min_length=1, max_length=4)

    @field_validator("feedback_ids", "config_ids")
    @classmethod
    def unique_ids(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("ID 不可重复")
        return values


class EvaluationSample(FeedbackSample):
    # 装载后只保留公开样例字段；验证规则与人工审核完全相同。
    model_config = ConfigDict(extra="ignore")


class EvaluationSnapshot(BaseModel):
    id: PositiveId
    revision: Offset
    sample: EvaluationSample


class EvaluationCase(BaseModel):
    feedback_id: int
    revision: int
    expectation: Expectation
    # 无替换约束时兼容旧版：status 仅代表检出，不代表建议通过。
    status: Literal["pass", "fail", "error", "not_evaluated"]
    detected: bool | None
    detection_status: Literal["pass", "fail", "error"] = "error"
    suggestion_status: Literal["pass", "fail", "not_evaluated"] = "not_evaluated"
    suggestion_reason: Literal[
        "detection_error", "no_report", "no_constraints", "not_detected", "invalid_suggestion",
        "incomparable_context", "rejected", "not_accepted", "no_accepted_golden", "all_accepted",
    ] = "detection_error"
    error: str | None
    elapsed_ms: int


class EvaluationModel(BaseModel):
    config_id: int
    config_name: str
    model: str
    cases: list[EvaluationCase] = Field(default_factory=list)
    report_total: int = 0
    report_evaluated: int = 0
    missed: int = 0
    no_report_total: int = 0
    no_report_evaluated: int = 0
    false_positives: int = 0
    errors: int = 0  # 检出不可判定，不混入建议未评估。
    suggestion_evaluated: int = 0
    suggestion_passed: int = 0
    suggestion_failed: int = 0
    suggestion_not_evaluated: int = 0  # 包含无约束、未检出及无法比较的所有病例。


class FeedbackEvaluation(BaseModel):
    generated_at: str
    samples: list[EvaluationSnapshot]
    results: list[EvaluationModel]
