import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

FactCheckMode = Literal["web", "trusted"]
FactCheckSearchProvider = Literal["model", "tavily"]
FactCheckStatus = Literal["PENDING", "RUNNING", "SUCCESS", "FAILURE", "CANCELLED"]
MAX_TEXT_CHARS = 20000
DAILY_LIMIT = 20


class TrustedSource(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    domain: str = Field(min_length=3, max_length=253)
    path_prefix: str = Field(default="/", min_length=1, max_length=300)
    is_enabled: bool = True

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        try:
            value = value.lower().encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("信源域名无效") from exc
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value):
            raise ValueError("请输入公开域名，不含协议、端口、通配符或路径")
        if value.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
            raise ValueError("不能使用内网或保留域名")
        return value

    @field_validator("path_prefix")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if (not value.startswith("/") or value.startswith("//")
                or any(ch in value for ch in "?#%\\") or any(ord(ch) < 32 for ch in value)
                or any(part in (".", "..") for part in value.split("/"))):
            raise ValueError("栏目路径须以 / 开头，不能包含查询参数、编码或相对路径")
        return value.rstrip("/") or "/"


class FactCheckSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: FactCheckSearchProvider = Field(default="model", description="模型原生联网或 Tavily；失败时不自动切换服务")
    api_key: SecretStr | None = Field(default=None, max_length=1024, description="仅用于 Tavily，模型模式复用大模型密钥")
    max_claims: int = Field(default=10, ge=1, le=10)
    sources: list[TrustedSource] = Field(default_factory=list, max_length=20)

    @field_validator("sources")
    @classmethod
    def unique_sources(cls, sources: list[TrustedSource]) -> list[TrustedSource]:
        if len({source.id for source in sources}) != len(sources):
            raise ValueError("信源 ID 不可重复")
        if len({(source.domain, source.path_prefix) for source in sources}) != len(sources):
            raise ValueError("同一信源路径不可重复")
        return sources


class FactCheckSettingsResponse(BaseModel):
    enabled: bool
    provider: FactCheckSearchProvider = "model"
    api_key_configured: bool = Field(description="是否已保存 Tavily 密钥，与当前选择的检索服务无关")
    model_name: str
    model_search_supported: bool = Field(description="仅表示当前供应商/接口已适配；具体模型版本或账号权限仍可能在运行时失败，不自动回退")
    model_search_reason: str = Field(description="未适配或没有启用的活跃模型时的说明；已适配则为空")
    max_claims: int
    sources: list[TrustedSource]


class FactCheckOptions(BaseModel):
    available: bool
    unavailable_reason: str
    provider: FactCheckSearchProvider = "model"
    model_name: str
    max_claims: int
    max_text_chars: int = MAX_TEXT_CHARS
    daily_limit: int = DAILY_LIMIT
    sources: list[TrustedSource]


class FactCheckCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: int = Field(gt=0)
    mode: FactCheckMode = "web"
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    allow_external_search: Literal[True] = Field(description="确认内容可发送至外部检索服务，涉密材料禁止使用")
    request_id: UUID = Field(description="每次用户主动发起生成 UUID；重试同一请求须复用")

    @model_validator(mode="after")
    def validate_sources(self):
        if self.mode == "web" and self.source_ids:
            raise ValueError("联网模式不接受可信信源 ID")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("信源 ID 不可重复")
        return self


ConsistencyStatus = Literal["match", "mismatch", "unknown", "not_applicable"]


class FactConsistencyCheck(BaseModel):
    """Model assessment of supplied body text, not independently verified semantics."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: ConsistencyStatus
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("口径检查须说明正文依据或无法确认的原因")
        return value


class FactEvidenceChecks(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    subject: FactConsistencyCheck
    event_time: FactConsistencyCheck
    scope_unit: FactConsistencyCheck

    @field_validator("subject")
    @classmethod
    def subject_is_required(cls, value: FactConsistencyCheck) -> FactConsistencyCheck:
        if value.status == "not_applicable":
            raise ValueError("主体检查不能标为不适用")
        return value

    @property
    def comparable(self) -> bool:
        return (self.subject.status == "match"
                and self.event_time.status in {"match", "not_applicable"}
                and self.scope_unit.status in {"match", "not_applicable"})


class FactJudgmentEvidence(BaseModel):
    """Strict input for every NEW model judgment; never use the legacy report schema here."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    quote: str = Field(min_length=1, max_length=4000)
    stance: Literal["supports", "refutes", "context"]
    checks: FactEvidenceChecks


class FactSearchRound(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["initial", "counter"]
    query: str
    status: Literal["pending", "searching", "fetching", "complete", "partial", "failed"]
    pages_fetched: int = Field(default=0, ge=0, le=3)
    error_codes: list[str] = Field(default_factory=list)


class FactEvidence(BaseModel):
    id: str
    title: str
    url: str
    quote: str = Field(min_length=1, max_length=4000)
    published_at: str | None = None
    retrieved_at: str
    publisher: str
    stance: Literal["supports", "refutes", "context"]
    # Optional only for reading historical reports, not for new model judgments.
    checks: FactEvidenceChecks | None = None
    body_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    body_hash_scope: Literal["normalized_model_visible_text_utf8"] | None = Field(
        default=None, description="SHA-256 of UTF-8 normalized body prefix supplied to the model, not raw HTML or the full page")
    body_text_length: int | None = Field(default=None, ge=1, le=16000)
    quote_start: int | None = Field(default=None, ge=0, description="First exact match, Unicode code points, inclusive")
    quote_end: int | None = Field(default=None, gt=0, description="Exclusive Unicode code-point position")
    context_before: str | None = Field(default=None, max_length=120)
    context_after: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_audit_fields(self):
        if self.checks is not None and self.stance != "context" and not self.checks.comparable:
            raise ValueError("主体、事件时间或统计口径不一致/不明的证据只能作为背景")
        audit = (self.body_sha256, self.body_hash_scope, self.body_text_length,
                 self.quote_start, self.quote_end, self.context_before, self.context_after)
        if any(value is not None for value in audit):
            if any(value is None for value in audit):
                raise ValueError("正文指纹与引文位置上下文须完整提供")
            if (self.quote_end > self.body_text_length or self.quote_end - self.quote_start != len(self.quote)
                    or len(self.context_before) > self.quote_start
                    or len(self.context_after) > self.body_text_length - self.quote_end):
                raise ValueError("引文位置或上下文超出记录的正文范围")
        return self

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("证据链接必须是无凭据的 HTTP(S) 地址")
        return value


class FactClaim(BaseModel):
    id: str
    original: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    statement: str
    verdict: Literal["supported", "refuted", "insufficient", "conflicting"]
    reason: str
    suggestion: str | None = None
    evidence: list[FactEvidence] = Field(default_factory=list)
    checked: bool
    search_rounds: list[FactSearchRound] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def validate_span(self):
        if self.end <= self.start or self.end - self.start != len(self.original):
            raise ValueError("事实项位置与原文长度不匹配")
        if not self.checked and self.verdict != "insufficient":
            raise ValueError("未核查的事实项不能给出确定结论")
        if self.suggestion and self.verdict != "refuted":
            raise ValueError("只有有证据反驳的事实项可以提供修改建议")
        stances = {e.stance for e in self.evidence}
        if ((self.verdict == "supported" and "supports" not in stances)
                or (self.verdict == "refuted" and "refutes" not in stances)
                or (self.verdict == "conflicting" and not {"supports", "refutes"} <= stances)):
            raise ValueError("核查结论缺少对应证据")
        return self


class FactCoverage(BaseModel):
    extracted: int = Field(ge=0)
    checked: int = Field(ge=0)
    unverified: int = Field(ge=0)
    status: Literal["complete", "partial"]
    reason: str = ""


class FactCheckReport(BaseModel):
    claims: list[FactClaim]
    coverage: FactCoverage
    usage: dict[str, int]
    checked_at: str

    @model_validator(mode="after")
    def validate_counts(self):
        checked = sum(claim.checked for claim in self.claims)
        if (self.coverage.extracted != len(self.claims) or self.coverage.checked != checked
                or self.coverage.unverified != len(self.claims) - checked):
            raise ValueError("核查覆盖计数不一致")
        if len({claim.id for claim in self.claims}) != len(self.claims):
            raise ValueError("事实项 ID 不可重复")
        if self.coverage.status == "complete" and self.coverage.unverified:
            raise ValueError("尚有未核查事实项，不能标记全部完成")
        return self


class FactCheckRunResponse(BaseModel):
    id: int
    record_id: int
    mode: FactCheckMode
    provider: FactCheckSearchProvider
    status: FactCheckStatus
    progress: int
    message: str
    error_code: str | None
    result: FactCheckReport | None
    source_hash: str
    source_ids: list[str]
    created_at: datetime
    finished_at: datetime | None
