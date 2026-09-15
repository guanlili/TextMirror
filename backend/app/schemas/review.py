"""坐标审阅契约：位置为不可变原文的 Unicode 码点 [start, end)。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.collaboration import CollaborationReport

MAX_ISSUES = 5000
MAX_REVIEW_BYTES = 8 * 1024 * 1024
MAX_VERSIONS = 20


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    original: str = Field(min_length=1, max_length=100000)
    type: str = Field(min_length=1, max_length=64)
    suggestion: str = Field(max_length=100000)
    explanation: str | None = Field(default="", max_length=20000)
    severity: str = Field(max_length=32)
    chunk_index: int | None = Field(default=None, ge=0, strict=True)
    start: int | None = Field(default=None, ge=0, strict=True)
    end: int | None = Field(default=None, ge=0, strict=True)
    accepted: bool = Field(default=False, alias="_accepted", strict=True)
    ignored: bool = Field(default=False, alias="_ignored", strict=True)

    @model_validator(mode="after")
    def validate_flags(self):
        if self.accepted and self.ignored:
            raise ValueError("不能同时采纳和忽略同一问题")
        if (self.start is None) != (self.end is None):
            raise ValueError("start/end 必须同时提供或同时为空")
        if self.accepted and self.start is None:
            raise ValueError("未定位的问题不可采纳")
        if self.accepted and not self.suggestion and self.type != "sensitive":
            raise ValueError("普通修改的 suggestion 不可为空")
        return self


class FailedChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_index: int = Field(ge=0, strict=True)
    start: int = Field(ge=0, strict=True)
    end: int = Field(ge=0, strict=True)
    text: str = Field(max_length=100000)
    error_code: str | None = Field(default=None, max_length=200)


class ReviewCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["complete", "partial"]
    total_chunks: int = Field(ge=0, le=100000, strict=True)
    completed_chunks: int = Field(ge=0, le=100000, strict=True)
    failed_chunks: list[FailedChunk] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def validate_counts(self):
        if self.completed_chunks + len(self.failed_chunks) != self.total_chunks:
            raise ValueError("覆盖统计与失败分片数不一致")
        expected = "partial" if self.failed_chunks else "complete"
        if self.status != expected:
            raise ValueError("覆盖状态与失败分片不一致")
        indices = [chunk.chunk_index for chunk in self.failed_chunks]
        if len(set(indices)) != len(indices) or any(i >= self.total_chunks for i in indices):
            raise ValueError("失败分片序号重复或越界")
        return self


class ReviewCompareModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_id: int = Field(gt=0, strict=True)
    config_name: str
    model: str
    domain: str = "general"
    depth: Literal["quick", "standard", "deep"] = "standard"
    success: bool = Field(strict=True)
    error: str | None = None
    elapsed_ms: int = Field(default=0, ge=0, strict=True)
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=MAX_ISSUES)
    coverage: ReviewCoverage | None = None

    @model_validator(mode="after")
    def validate_report_flags(self):
        if any(issue.accepted or issue.ignored for issue in self.issues):
            raise ValueError("逐模型报告不可保存采纳/忽略决策，请使用顶层 issues")
        return self


class ReviewCompareState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[ReviewCompareModel] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def validate_models(self):
        ids = [item.config_id for item in self.results]
        if len(set(ids)) != len(ids):
            raise ValueError("对比模型 config_id 不可重复")
        return self


class ReviewWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, le=2147483646, strict=True)
    issues: list[ReviewIssue] = Field(max_length=MAX_ISSUES)
    coverage: ReviewCoverage | None = None
    compare: ReviewCompareState | None = None
    depth: Literal["quick", "standard", "deep"] | None = None
    config_id: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def validate_size(self):
        if len(self.model_dump_json(by_alias=True).encode("utf-8")) > MAX_REVIEW_BYTES:
            raise ValueError("审阅 JSON 超过 8 MiB 限制")
        return self


class ReviewVersionRequest(ReviewWriteRequest):
    label: str | None = Field(default=None, max_length=200)


class ReviewExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    version_id: str | None = Field(default=None, min_length=1, max_length=100)
    format: Literal["docx", "txt"]


class DocumentReviewExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: list[ReviewIssue] = Field(max_length=MAX_ISSUES)
    format: Literal["docx", "txt"]

    @model_validator(mode="after")
    def validate_size(self):
        if len(self.model_dump_json(by_alias=True).encode("utf-8")) > MAX_REVIEW_BYTES:
            raise ValueError("审阅 JSON 超过 8 MiB 限制")
        return self


class ReviewVersion(BaseModel):
    id: str
    label: str
    created_at: datetime
    issues: list[ReviewIssue]
    coverage: ReviewCoverage | None = None
    compare: ReviewCompareState | None = None
    modified_text: str


class ReviewResponse(BaseModel):
    record_id: int
    revision: int
    original_text: str
    source_file_id: str | None = None
    source_filename: str | None = None
    domain: str
    depth: str | None = None
    config_id: int | None = None
    issues: list[ReviewIssue]
    coverage: ReviewCoverage | None = None
    compare: ReviewCompareState | None = None
    collaboration: CollaborationReport | None = None
    modified_text: str
    versions: list[ReviewVersion] = Field(default_factory=list)
