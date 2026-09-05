"""
TextMirror 校对相关 Schema
"""
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field


class CheckType(str, Enum):
    """校对类型枚举（对外 API 契约，非法值直接 422）"""
    typo = "typo"                # 错别字
    grammar = "grammar"          # 语法错误
    punctuation = "punctuation"  # 标点符号
    style = "style"              # 表达优化
    sensitive = "sensitive"      # 敏感词
    logic = "logic"              # 逻辑问题


class Domain(str, Enum):
    """领域枚举（开源默认只保留通用场景；行业特化由管理员在「审校规则」后台自定义规则实现）"""
    general = "general"    # 通用
    official = "official"  # 公文
    legal = "legal"        # 法律


class TextProofreadRequest(BaseModel):
    """文本校对请求"""
    # use_enum_values：校验后枚举转回纯字符串，下游（提示词拼接/JSON 落库/审计）拿到的与从前一致
    # example：Swagger Try it out 的预填请求体（不填 check_types/config_id，引导最简用法）
    model_config = ConfigDict(
        use_enum_values=True,
        json_schema_extra={
            "example": {
                "text": "随着人工智能技术的突飞猛进，各行各业都迎来了翻天复地的变革。",
                "domain": "general",
            }
        },
    )

    text: str = Field(
        ...,
        min_length=1,
        max_length=100000,
        description="待校对文本",
        examples=["随着人工智能技术的突飞猛进，各行各业都迎来了翻天复地的变革。"],
    )
    check_types: Optional[List[CheckType]] = Field(
        None,
        deprecated=True,
        description="（已废弃，传入无效果）历史参数：限定校对类型。模型不遵循且类型间存在交叉，现总是全量审校",
        examples=[["typo", "punctuation"]],
    )
    domain: Domain = Field(
        default=Domain.general,
        description="文本领域（影响校对规则侧重）",
        examples=["general"],
    )
    config_id: Optional[int] = Field(
        None,
        description="指定模型配置ID（可选，不填=系统当前默认模型；普通集成方无需关心）",
        examples=[None],
    )


class ProofreadIssue(BaseModel):
    """单个校对问题"""
    original: str = Field(..., description="原文片段")
    type: str = Field(..., description="问题类型")
    suggestion: str = Field(..., description="修改建议")
    explanation: str = Field(default="", description="解释")
    severity: str = Field(default="warning", description="严重程度: error/warning/info")
    chunk_index: int = Field(default=0, description="分片序号")


class TextProofreadResponse(BaseModel):
    """文本校对响应"""
    issues: List[ProofreadIssue] = Field(default_factory=list, description="问题列表")
    total_issues: int = Field(default=0, description="问题总数")
    chunks_count: int = Field(default=1, description="分片数")
    usage: Dict[str, int] = Field(default_factory=dict, description="Token用量")
    domain: str = Field(default="general", description="领域")
    check_types: List[str] = Field(default_factory=list, description="校对类型")
    record_id: Optional[int] = Field(None, description="校对记录ID")


class ProofreadRecordResponse(BaseModel):
    """校对历史记录响应"""
    id: int
    type: str
    original_text: str
    domain: str
    total_issues: int
    result: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}
