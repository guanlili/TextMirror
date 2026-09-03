"""
TextMirror 大模型配置 Schema
"""
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


# 支持的供应商列表（models 为主流推荐，其他型号直接手输模型名即可；
# default_base 为官方 OpenAI 兼容地址，已逐一核验，用户无需填写；
# model_docs 为各家「模型列表/控制台」地址，供管理员查找模型名）
SUPPORTED_PROVIDERS = [
    {
        "code": "deepseek", "name": "DeepSeek",
        "default_base": "https://api.deepseek.com", "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner", "deepseek-v4-pro", "deepseek-v4-flash"],
        "model_docs": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
    },
    {
        "code": "openai", "name": "OpenAI (ChatGPT)",
        "default_base": "https://api.openai.com", "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "o4-mini"],
        "model_docs": "https://platform.openai.com/docs/models",
    },
    {
        "code": "volcengine", "name": "火山方舟 (豆包)",
        "default_base": "https://ark.cn-beijing.volces.com/api/v3", "default_model": "doubao-seed-1-6-250615",
        "models": ["doubao-seed-2-0-pro-260215", "doubao-seed-1-8-251228", "doubao-seed-1-6-250615", "doubao-1-5-pro-32k-250115"],
        "model_docs": "https://www.volcengine.com/docs/82379/1330310",
    },
    {
        "code": "qwen", "name": "阿里百炼 (通义千问)",
        "default_base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "default_model": "qwen3.8-max",
        "models": ["qwen3.8-max", "qwen3.7-plus", "qwen3.8-flash", "qwen-plus"],
        "model_docs": "https://help.aliyun.com/zh/model-studio/models",
    },
    {
        "code": "hunyuan", "name": "腾讯混元",
        "default_base": "https://api.hunyuan.cloud.tencent.com/v1", "default_model": "hunyuan-turbos-latest",
        "models": ["hunyuan-turbos-latest", "hunyuan-t1-latest", "hy3-preview", "hunyuan-large"],
        "model_docs": "https://cloud.tencent.com/document/product/1729/111007",
    },
    {
        "code": "zhipu", "name": "智谱 AI",
        "default_base": "https://open.bigmodel.cn/api/paas", "default_model": "glm-4.6",
        "models": ["glm-5.3", "glm-4.7", "glm-4.6", "glm-4.5-flash"],
        "model_docs": "https://docs.bigmodel.cn/cn/guide/start/model-overview",
    },
    {
        "code": "moonshot", "name": "月之暗面 (Kimi)",
        "default_base": "https://api.moonshot.cn/v1", "default_model": "kimi-k2.6",
        "models": ["kimi-k3", "kimi-k2.6", "kimi-latest", "moonshot-v1-128k"],
        "model_docs": "https://platform.moonshot.cn/docs/pricing/chat/",
    },
    {
        "code": "spark", "name": "讯飞星火",
        "default_base": "https://spark-api-open.xf-yun.com/v1", "default_model": "generalv3.5",
        "models": ["spark-x", "4.0Ultra", "generalv3.5", "lite"],
        "model_docs": "https://www.xfyun.cn/doc/spark/HTTP%E8%B0%83%E7%94%A8%E6%96%87%E6%A1%A3.html",
    },
    {
        "code": "qianfan", "name": "百度千帆 (文心一言)",
        "default_base": "https://qianfan.baidubce.com/v2", "default_model": "ernie-4.5-turbo-128k",
        "models": ["ernie-4.5-turbo-128k", "ernie-4.0-8k", "ernie-speed-128k", "ernie-3.5-8k"],
        "model_docs": "https://cloud.baidu.com/doc/qianfan/s/rmh4stn9m",
    },
    {
        "code": "siliconflow", "name": "硅基流动 SiliconFlow",
        "default_base": "https://api.siliconflow.cn/v1", "default_model": "Qwen/Qwen3-8B",
        "models": ["Qwen/Qwen3-8B", "deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen2.5-72B-Instruct"],
        "model_docs": "https://siliconflow.cn/models",
    },
    {
        "code": "minimax", "name": "MiniMax",
        "default_base": "https://api.minimax.cn/v1", "default_model": "MiniMax-M3",
        "models": ["MiniMax-M3", "MiniMax-M2.7"],
        "model_docs": "https://platform.minimax.cn/docs/introduce/model-list",
    },
    {
        "code": "stepfun", "name": "阶跃星辰 StepFun",
        "default_base": "https://api.stepfun.com/v1", "default_model": "step-3.7-flash",
        "models": ["step-3.7-flash", "step-3.5-flash", "step-2-mini"],
        "model_docs": "https://platform.stepfun.com/docs/zh/guide/quick-start",
    },
    {
        "code": "litellm", "name": "LiteLLM",
        "default_base": "http://localhost:4000", "default_model": "gpt-3.5-turbo",
        "models": [],
        "model_docs": "https://docs.litellm.ai/docs/",
    },
    {
        "code": "azure", "name": "Azure OpenAI",
        "default_base": "https://your-resource.openai.azure.com", "default_model": "gpt-4o",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1"],
        "model_docs": "https://learn.microsoft.com/azure/ai-services/openai/concepts/models",
    },
    {
        "code": "custom", "name": "自定义 (OpenAI 兼容)",
        "default_base": "", "default_model": "",
        "models": [],
        "model_docs": "",
    },
]


class LLMConfigCreate(BaseModel):
    """创建大模型配置"""
    name: str = Field(..., max_length=100, description="配置名称")
    provider: str = Field(..., max_length=50, description="供应商标识")
    api_base: str = Field(..., max_length=500, description="API 基础地址")
    api_key: str = Field(..., max_length=500, description="API 密钥")
    model: str = Field(..., max_length=100, description="模型名称")
    temperature: float = Field(default=0.3, ge=0, le=2, description="温度参数")
    max_tokens: Optional[int] = Field(None, ge=1, description="最大输出 token 数")
    timeout: int = Field(default=180, ge=10, le=600, description="超时（秒）")
    max_retries: int = Field(default=2, ge=0, le=10, description="重试次数")
    remark: Optional[str] = Field(None, max_length=500, description="备注")


class LLMConfigUpdate(BaseModel):
    """更新大模型配置"""
    name: Optional[str] = Field(None, max_length=100)
    provider: Optional[str] = Field(None, max_length=50)
    api_base: Optional[str] = Field(None, max_length=500)
    api_key: Optional[str] = Field(None, max_length=500)
    model: Optional[str] = Field(None, max_length=100)
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, ge=1)
    timeout: Optional[int] = Field(None, ge=10, le=600)
    max_retries: Optional[int] = Field(None, ge=0, le=10)
    is_enabled: Optional[bool] = None
    remark: Optional[str] = Field(None, max_length=500)


class LLMConfigResponse(BaseModel):
    """大模型配置响应"""
    id: int
    name: str
    provider: str
    api_base: str
    api_key_masked: str = ""    # 脱敏密钥（卡片展示用；明文密钥不回传）
    model: str
    temperature: float
    max_tokens: Optional[int] = None
    timeout: int
    max_retries: int
    is_active: bool
    is_enabled: bool
    remark: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class LLMTestResult(BaseModel):
    """连接测试结果"""
    success: bool
    model: str = ""
    message: str = ""
    usage: dict = {}


class LLMProviderOption(BaseModel):
    """供应商选项（前端下拉用）"""
    code: str
    name: str
    default_base: str
    default_model: str
    models: List[str] = []    # 主流推荐模型（其他型号手输即可）
    model_docs: str = ""      # 官方模型列表/文档地址（供管理员查找模型名）
