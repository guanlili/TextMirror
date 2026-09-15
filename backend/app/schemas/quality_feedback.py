"""独立质量候选契约；所有位置均为 Unicode 码点，不代表整篇文档的质量标签。"""
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FeedbackKind = Literal["false_positive", "bad_suggestion", "preference", "other", "missed"]
FeedbackStatus = Literal["pending", "confirmed", "rejected"]
FeedbackDomain = Literal["general", "official", "legal"]
PositiveId = Annotated[int, Field(strict=True, ge=1, le=2147483647)]
Position = Annotated[int, Field(strict=True, ge=0, le=2147483647)]


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, from_attributes=True)


class QualityFeedbackCreate(StrictSchema):
    record_id: PositiveId
    kind: FeedbackKind
    original: str = Field(min_length=1, max_length=500)
    suggestion: str = Field(max_length=500)
    issue_type: str = Field(max_length=64)
    start: Position
    end: Position
    note: str = Field(max_length=1000)

    @model_validator(mode="after")
    def validate_span(self):
        if self.end <= self.start or self.end - self.start != len(self.original):
            raise ValueError("问题位置必须与 original 的 Unicode 码点长度一致")
        return self


class FeedbackSample(StrictSchema):
    text: str = Field(min_length=1, max_length=4000)
    domain: FeedbackDomain
    start: Position
    end: Position
    original: str = Field(min_length=1, max_length=500)
    expectation: Literal["report", "no_report"]
    issue_type: str = Field(max_length=64)
    # 人工 golden：替换目标 original 的精确文本；空字符串表示删除，不做 trim/归一化。
    accepted_suggestions: list[Annotated[str, Field(strict=True, max_length=500)]] = Field(default_factory=list, max_length=10)
    rejected_suggestions: list[Annotated[str, Field(strict=True, max_length=500)]] = Field(default_factory=list, max_length=10)

    @field_validator("accepted_suggestions", "rejected_suggestions")
    @classmethod
    def unique_suggestions(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("替换约束不可重复")
        return values

    @field_validator("issue_type", mode="before")
    @classmethod
    def empty_issue_type(cls, value):
        # 未指定类型过滤时输出 TS 契约的空字符串；兼容人工确认传入 null。
        return "" if value is None else value

    @model_validator(mode="after")
    def validate_target(self):
        if self.end <= self.start or self.end > len(self.text) or self.text[self.start:self.end] != self.original:
            raise ValueError("样例目标位置必须与样例 text 的 Unicode 码点片段完全一致")
        if set(self.accepted_suggestions) & set(self.rejected_suggestions):
            raise ValueError("认可与禁止替换必须互斥")
        if self.expectation == "no_report" and (self.accepted_suggestions or self.rejected_suggestions):
            raise ValueError("no_report 样例不能附带替换约束")
        return self


class FeedbackModelSnapshot(StrictSchema):
    config_id: PositiveId | None
    config_name: str
    model: str
    success: bool | None
    coverage_status: Literal["complete", "partial", "unknown"]
    reported: bool | None


class FeedbackReviewRequest(StrictSchema):
    revision: int = Field(ge=0, le=2147483646)
    status: Literal["confirmed", "rejected"]
    sample: FeedbackSample | None
    review_note: str = Field(max_length=1000)

    @model_validator(mode="after")
    def validate_confirmation(self):
        if (self.status == "confirmed") != (self.sample is not None):
            raise ValueError("confirmed 必须提供 sample；rejected 的 sample 必须为 null")
        return self


class QualityFeedback(StrictSchema):
    id: int
    record_id: int | None
    user_id: int
    kind: FeedbackKind
    original: str
    suggestion: str
    issue_type: str
    start: int
    end: int
    note: str
    revision: int
    status: FeedbackStatus
    context_text: str
    context_start: int
    domain: FeedbackDomain
    model_snapshot: list[FeedbackModelSnapshot]
    sample: FeedbackSample | None
    review_note: str
    reviewer_id: int | None
    reviewed_at: datetime | None
    created_at: datetime

    @field_validator("reviewed_at", "created_at")
    @classmethod
    def utc_timestamp(cls, value):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class QualityFeedbackList(StrictSchema):
    items: list[QualityFeedback]
    total: int
