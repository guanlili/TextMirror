"""
TextMirror 开放 API Schema（对外稳定契约，独立于 Web 端 schema 演进）
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.proofread import CheckType, Domain, ProofreadIssue


# ======================================================================
# 多模型对比
# ======================================================================

class OpenCompareRequest(BaseModel):
    """多模型对比请求"""
    model_config = ConfigDict(
        use_enum_values=True,
        json_schema_extra={
            "example": {
                "text": "随着人工智能技术的突飞猛进，各行各业都迎来了翻天复地的变革。",
                "domain": "general",
                "config_ids": [8, 10],
            }
        },
    )

    text: str = Field(..., min_length=1, max_length=100000, description="待审校文本")
    check_types: Optional[List[CheckType]] = Field(
        None,
        deprecated=True,
        description="（已废弃，传入无效果）历史参数：限定校对类型。现总是全量审校",
        examples=[["typo", "punctuation"]],
    )
    domain: Domain = Field(default=Domain.general, description="文本领域")
    config_ids: List[int] = Field(
        ...,
        min_length=2,
        max_length=4,
        description="参与对比的模型配置ID（2-4个）。先调用 GET /models 查看可用ID，不要凭空猜测",
        examples=[[8, 10]],
    )


class OpenCompareModelResult(BaseModel):
    """单模型的审校结果"""
    config_id: int = Field(..., description="模型配置ID")
    config_name: str = Field(..., description="模型配置名称")
    model: str = Field(..., description="模型名称")
    issues: List[ProofreadIssue] = Field(default_factory=list, description="问题列表")
    total_issues: int = Field(default=0, description="问题总数")
    success: bool = Field(default=True, description="该模型调用是否成功")
    error: Optional[str] = Field(None, description="失败原因（success=false 时）")
    elapsed_ms: int = Field(default=0, description="耗时（毫秒）")


class OpenCompareResponse(BaseModel):
    """多模型对比响应"""
    results: List[OpenCompareModelResult] = Field(..., description="各模型审校结果")
    consensus_originals: List[str] = Field(
        default_factory=list, description="所有成功模型均发现的问题原文（共识）"
    )
    only_in: Dict[int, List[str]] = Field(
        default_factory=dict, description="仅单一模型发现的问题原文（key=config_id）"
    )


# ======================================================================
# 异步文档审校
# ======================================================================

class OpenDocumentSubmitResponse(BaseModel):
    """文档提交响应（202）"""
    job_id: str = Field(..., description="任务ID，用于轮询进度与结果")
    filename: str = Field(..., description="文件名")
    text_length: int = Field(..., description="提取出的文本长度（字符数）")
    status: str = Field(default="queued", description="任务状态：queued")
    status_url: str = Field(..., description="轮询地址（相对路径）：GET 该地址获取进度")


class OpenJobStatusResponse(BaseModel):
    """任务状态响应"""
    job_id: str = Field(..., description="任务ID")
    status: str = Field(..., description="PENDING/PROGRESS/SUCCESS/FAILURE")
    progress: int = Field(default=0, description="进度百分比 0-100")
    message: str = Field(default="", description="状态说明")
    result: Optional[Dict[str, Any]] = Field(
        None, description="审校结果（status=SUCCESS 时返回，含 issues/total_issues/corrected_download_url 等）"
    )
    error: Optional[str] = Field(None, description="错误码（status=FAILURE 时，如 TASK_FAILED）")


# ======================================================================
# 可用模型列表
# ======================================================================

class OpenModelItem(BaseModel):
    """可用模型配置项（不含任何密钥信息）"""
    id: int = Field(..., description="模型配置ID，用于 config_id / config_ids 参数")
    name: str = Field(..., description="配置名称（管理员在后台设置的显示名）")
    model: str = Field(..., description="模型标识（如 doubao-seed-2-0-pro-260215）")
    is_active: bool = Field(..., description="是否为系统当前默认模型（不传 config_id 时使用它）")


class OpenModelsResponse(BaseModel):
    """可用模型列表"""
    models: List[OpenModelItem] = Field(..., description="已启用的模型配置列表")
