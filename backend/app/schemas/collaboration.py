from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.proofread import Domain, ProofreadIssue, TextProofreadResponse

MAX_COLLABORATION_CHARS = 8000


class CollaborationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    text: str = Field(min_length=1, max_length=MAX_COLLABORATION_CHARS)
    domain: Domain = Domain.general
    config_id: int | None = Field(default=None, gt=0)
    request_id: UUID

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入待审校文本")
        return value


class CollaborationRole(BaseModel):
    id: Literal["rules", "language", "consistency", "reviewer"]
    name: str
    status: Literal["pending", "running", "success", "failed", "skipped", "cancelled"] = "pending"
    message: str = "等待执行"
    issue_count: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)
    usage: dict[str, int] = Field(default_factory=dict)


class CollaborationFinding(ProofreadIssue):
    review_status: Literal["not_reviewed", "confirmed", "disputed"] = "not_reviewed"
    review_note: str = ""


class CollaborationReport(BaseModel):
    status: Literal["running", "complete", "partial"] = "running"
    roles: list[CollaborationRole]
    findings: list[CollaborationFinding] = Field(default_factory=list)
    reviewed_count: int = Field(default=0, ge=0)
    review_limit: int = 20
    config_id: int | None = None
    model_name: str = ""


class CollaborationResult(TextProofreadResponse):
    collaboration: CollaborationReport


class CollaborationSubmit(BaseModel):
    task_id: str
    message: str
