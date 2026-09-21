"""
TextMirror 文档校对相关 Schema
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.proofread import ProofreadCoverage


class DocumentUploadResponse(BaseModel):
    """文档上传响应（不含全文，正文通过 GET /{file_id}/extracted-text 按需获取）"""
    file_id: str = Field(..., description="文件唯一标识")
    filename: str = Field(..., description="原始文件名")
    file_size: int = Field(..., description="文件大小(字节)")
    file_ext: str = Field(..., description="文件扩展名")
    text_length: int = Field(..., description="提取的文本字数")
    text_preview: str = Field(default="", description="文本预览(前200字)")


class DocumentExtractedTextResponse(BaseModel):
    """文档提取文本响应（轻量，供前端恢复会话时按需取全文）"""
    file_id: str
    extracted_text: str
    extracted_html: str = ""


class DocumentProofreadRequest(BaseModel):
    """文档校对请求"""
    file_id: str = Field(..., description="文件唯一标识")
    check_types: Optional[List[str]] = Field(
        None,
        deprecated=True,
        description="（已废弃，传入无效果）历史参数：限定校对类型。总是全量审校，任何值都被静默忽略",
    )
    domain: Literal["auto", "general", "official", "legal"] = Field(default="general", description="领域")
    config_id: Optional[int] = Field(
        None,
        description="指定模型配置ID（不填用管理后台设的当前模型）"
    )
    depth: Literal["quick", "standard", "deep"] = "standard"

    @field_validator("check_types", mode="before")
    @classmethod
    def _ignore_check_types(cls, v):
        return None


class DocumentProofreadResponse(BaseModel):
    """文档校对响应"""
    file_id: str
    filename: str
    issues: List[Dict[str, Any]] = Field(default_factory=list)
    total_issues: int = 0
    chunks_count: int = 1
    usage: Dict[str, int] = Field(default_factory=dict)
    domain: str = "general"
    record_id: Optional[int] = None
    corrected_download_url: Optional[str] = None
    coverage: Optional[ProofreadCoverage] = None
    config_id: Optional[int] = None
    depth: Literal["quick", "standard", "deep"] = "standard"
    check_types: List[str] = Field(default_factory=list)


class ExportRevisedTextRequest(BaseModel):
    """导出修订文本为 Word"""
    text: str = Field(..., max_length=200000, description="修订后的全文")
    filename: str = Field(default="修订文本", max_length=200, description="导出文件名（不含扩展名）")


class ReportIssueItem(BaseModel):
    """报告中的单条问题"""
    type: str = Field(max_length=50)
    severity: str = Field(max_length=20)
    original: str = Field(max_length=5000)
    suggestion: str = Field(max_length=5000)
    explanation: str = Field(default="", max_length=2000)
    context: str = Field(default="", max_length=500)
    status: str = Field(default="pending", max_length=20, description="pending/accepted/ignored")


class CoverageChunk(BaseModel):
    start: int
    end: int
    error_code: str = Field(default="", max_length=50)


class ReportCoverage(BaseModel):
    total_chunks: int
    completed_chunks: int
    failed_chunks: List[CoverageChunk] = Field(default_factory=list, max_length=1000)


class ExportReportRequest(BaseModel):
    """导出问题报告为 Word"""
    filename: str = Field(default="校对报告", max_length=200, description="导出文件名（不含扩展名）")
    status: str = Field(default="unknown", max_length=20, description="completed/partial/unknown")
    total_issues: int = 0
    accepted_count: int = 0
    ignored_count: int = 0
    pending_count: int = 0
    coverage: Optional[ReportCoverage] = None
    issues: List[ReportIssueItem] = Field(default_factory=list, max_length=5000)
