"""
TextMirror 通用 OpenAI 兼容 Provider
支持所有兼容 OpenAI Chat Completions API 的大模型供应商：
  - OpenAI (ChatGPT / GPT-4o)
  - DeepSeek
  - LiteLLM
  - Azure OpenAI
  - 通义千问（兼容模式）
  - 文心一言（兼容模式）
  - 其他自建/转发网关
"""
import asyncio
import json
import re
import weakref
from typing import AsyncIterator, Dict, List, Optional

import httpx
from loguru import logger

from app.services.llm.base import BaseLLMProvider, LLMResponse

# httpx.AsyncClient 绑定创建时的事件循环，不能跨循环复用：
# Web 进程单循环可长期复用连接池；Celery 子进程任务间复用同一循环时同样受益；
# 事件循环被回收后，对应条目随 WeakKeyDictionary 自动消失
_client_pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Dict[tuple, httpx.AsyncClient]]" = (
    weakref.WeakKeyDictionary()
)

# 各 api_base 已验证成功的 chat 路径，跨实例共享，避免每个新实例重新 404 试探。
# 无需加锁：仅单条 dict get/set（GIL 下原子），写入只在请求成功后发生（幂等），
# Web/Celery 各进程持有独立副本，最坏情况是重复探测一次。
_verified_endpoints: Dict[str, str] = {}


class OpenAICompatProvider(BaseLLMProvider):
    """
    通用 OpenAI 兼容 Provider
    所有使用 /v1/chat/completions 接口格式的大模型均可使用此 Provider
    """

    def __init__(
        self,
        api_key: str,
        api_base: str,
        model: str,
        timeout: int = 60,
        max_retries: int = 3,
        provider_name: str = "OpenAI-Compatible",
    ):
        super().__init__(api_key, api_base, model, timeout, max_retries)
        self.provider_name = provider_name

        # 规范化 api_base：去掉末尾斜杠
        self.api_base = api_base.rstrip("/")

        # key 缺失或含非 ASCII 字符（如中文占位符）会导致 Authorization 头编码崩溃，
        # 构造时即拦截并给出可读错误
        if not api_key or not api_key.isascii():
            raise RuntimeError(
                f"[{provider_name}] API Key 未正确配置，请在管理后台填写有效密钥"
            )

        # 智能确定 endpoint 顺序：避免重复 /v1 路径
        # 若 api_base 已含版本段（/v1 或 /v3，如 LiteLLM、火山方舟），
        # 则优先使用 /chat/completions，避免每次都先 404 再 fallback 浪费时间
        if re.search(r"/v\d+$", self.api_base):
            self._endpoints = ["/chat/completions", "/v1/chat/completions"]
        else:
            self._endpoints = ["/v1/chat/completions", "/chat/completions"]
        # 已确认成功的 endpoint，跨实例共享，避免每次都试探
        self._verified_endpoint: Optional[str] = _verified_endpoints.get(self.api_base)

        # 优先复用共享 client（同一事件循环 + 相同 base/key/timeout），
        # 避免每次审校/润色请求都重建连接池、重做 TLS 握手
        self._owns_client = False
        client = self._get_shared_client()
        if client is None:
            # 无运行中的事件循环（罕见的同步上下文）：退回独享 client，由 close() 负责关闭
            self._owns_client = True
            client = self._new_client()
        self.client = client

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.api_base,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.timeout, connect=15),
        )

    def _get_shared_client(self) -> Optional[httpx.AsyncClient]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        pool = _client_pools.setdefault(loop, {})
        key = (self.api_base, self.api_key, self.timeout)
        client = pool.get(key)
        if client is None or client.is_closed:
            client = self._new_client()
            pool[key] = client
        return client

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """调用 Chat Completions API"""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        # 若已验证过 endpoint，则直接复用，避免每次都试探
        endpoints = (
            [self._verified_endpoint] if self._verified_endpoint else self._endpoints
        )

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            for endpoint in endpoints:
                try:
                    response = await self.client.post(endpoint, json=payload)
                    response.raise_for_status()
                    data = response.json()

                    # 记忆已验证的 endpoint，后续调用与新实例直接复用
                    self._verified_endpoint = endpoint
                    _verified_endpoints[self.api_base] = endpoint

                    choice = data["choices"][0]
                    usage = data.get("usage", {})

                    return LLMResponse(
                        content=choice["message"]["content"],
                        model=data.get("model", self.model),
                        usage={
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0),
                        },
                        finish_reason=choice.get("finish_reason"),
                    )

                except httpx.HTTPStatusError as e:
                    # 404 说明路径不对，尝试下一个 endpoint
                    if e.response.status_code == 404 and endpoint != endpoints[-1]:
                        continue
                    last_error = e
                    logger.error(
                        f"[{self.provider_name}] API 错误 (第{attempt}次, {endpoint}): "
                        f"status={e.response.status_code}, body={e.response.text[:300]}"
                    )
                    # 4xx 错误（非 404）不重试
                    if 400 <= e.response.status_code < 500 and e.response.status_code != 404:
                        raise RuntimeError(
                            f"[{self.provider_name}] API 返回 {e.response.status_code}: {e.response.text[:300]}"
                        )
                    break  # 非 404 的服务器错误，跳出 endpoint 循环进入重试

                except httpx.TimeoutException as e:
                    last_error = e
                    logger.warning(f"[{self.provider_name}] API 超时 (第{attempt}次): {e}")
                    break  # 超时跳出 endpoint 循环进入重试

                except Exception as e:
                    last_error = e
                    logger.error(f"[{self.provider_name}] API 异常 (第{attempt}次): {e}")
                    break

        raise RuntimeError(
            f"[{self.provider_name}] API 调用失败（已重试{self.max_retries}次）: {last_error}"
        )

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """
        流式调用 Chat Completions API，逐段 yield 文本增量
        失败时抛出 RuntimeError（与 chat() 一致的错误语义）
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        endpoints = (
            [self._verified_endpoint] if self._verified_endpoint else self._endpoints
        )

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            for endpoint in endpoints:
                try:
                    async with self.client.stream("POST", endpoint, json=payload) as response:
                        if response.status_code == 404 and endpoint != endpoints[-1]:
                            continue
                        if response.status_code >= 400:
                            # 流式模式需先 read() 才能读取错误响应体
                            body = (await response.aread()).decode("utf-8", errors="replace")
                            last_error = httpx.HTTPStatusError(
                                f"{response.status_code}: {body[:300]}",
                                request=response.request, response=response,
                            )
                            if 400 <= response.status_code < 500:
                                raise RuntimeError(
                                    f"[{self.provider_name}] API 返回 {response.status_code}: {body[:300]}"
                                )
                            break
                        self._verified_endpoint = endpoint
                        _verified_endpoints[self.api_base] = endpoint

                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[len("data: "):].strip()
                            if data == "[DONE]":
                                return
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content")
                            if content:
                                yield content
                        return

                except httpx.HTTPStatusError as e:
                    last_error = e
                    logger.error(
                        f"[{self.provider_name}] 流式 API 错误 (第{attempt}次, {endpoint}): "
                        f"status={e.response.status_code}, body={e.response.text[:300]}"
                    )
                    if 400 <= e.response.status_code < 500 and e.response.status_code != 404:
                        raise RuntimeError(
                            f"[{self.provider_name}] API 返回 {e.response.status_code}: {e.response.text[:300]}"
                        )
                    break

                except httpx.TimeoutException as e:
                    last_error = e
                    logger.warning(f"[{self.provider_name}] 流式 API 超时 (第{attempt}次): {e}")
                    break

                except Exception as e:
                    last_error = e
                    logger.error(f"[{self.provider_name}] 流式 API 异常 (第{attempt}次): {e}")
                    break

        raise RuntimeError(
            f"[{self.provider_name}] 流式 API 调用失败（已重试{self.max_retries}次）: {last_error}"
        )

    async def close(self):
        """释放 HTTP 客户端：共享 client 随事件循环生命周期管理，只关自有的"""
        if self._owns_client:
            await self.client.aclose()

    async def test_connection(self) -> Dict:
        """
        测试连接是否正常
        返回: {"success": bool, "model": str, "message": str, "usage": dict}
        """
        try:
            response = await self.chat(
                messages=[
                    {"role": "user", "content": "请回复'连接正常'四个字，不要有其他内容。"}
                ],
                temperature=0,
                max_tokens=20,
            )
            return {
                "success": True,
                "model": response.model,
                "message": response.content.strip(),
                "usage": response.usage,
            }
        except Exception as e:
            return {
                "success": False,
                "model": self.model,
                "message": str(e),
                "usage": {},
            }
