"""
TextMirror 大模型 Provider 抽象基类
定义统一的调用接口，支持多家模型供应商
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class LLMResponse:
    """大模型响应数据结构"""
    content: str
    model: str
    usage: Dict[str, int]  # {"prompt_tokens": x, "completion_tokens": y, "total_tokens": z}
    finish_reason: Optional[str] = None


class BaseLLMProvider(ABC):
    """大模型 Provider 抽象基类"""

    def __init__(
        self,
        api_key: str,
        api_base: str,
        model: str,
        timeout: int = 60,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        # 默认采样温度；调用方可按场景覆盖（如校对场景设低值保证确定性）
        self.default_temperature: float = 0.3

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
        thinking: Optional[bool] = None,
        timeout: Optional[float] = None,
    ) -> LLMResponse:
        """
        发送聊天请求到大模型

        :param messages: 消息列表 [{"role": "system/user/assistant", "content": "..."}]
        :param temperature: 温度参数（0-2），越低越确定
        :param max_tokens: 最大生成 token 数
        :param thinking: 思考模式开关（None=不干预，供应商不支持时静默忽略）
        :param timeout: 本次请求超时覆盖（秒），None 用 Provider 配置值
        :return: LLMResponse
        """
        pass

    @abstractmethod
    async def close(self):
        """关闭连接"""
        pass
