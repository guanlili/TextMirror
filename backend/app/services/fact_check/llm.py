import asyncio
import json

from loguru import logger

from .constants import MAX_BYTES, MODEL_TIMEOUT
from .errors import FactCheckError
from .extraction import _claims_from_model


async def _chat_json(provider, system: str, data: dict, usage: dict, *, extraction_text: str | None = None, on_retry=None) -> dict:
    extraction = extraction_text is not None
    failed_extraction = None
    messages = [{"role": "system", "content": system + "\n仅输出一个合法的 json 对象，不要使用 Markdown 代码块或附加说明。"},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)}]
    options = {"response_format": {"type": "json_object"}} if getattr(provider, "provider_slug", None) == "volcengine" else {}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + MODEL_TIMEOUT
    try:
        async with asyncio.timeout(MODEL_TIMEOUT):
            for attempt in range(2):
                if attempt and on_retry is not None:
                    await on_retry()
                try:
                    response = await provider.chat(
                        messages=messages, temperature=0, max_tokens=8000, thinking=False,
                        timeout=deadline - loop.time(), **options,
                    )
                except Exception:
                    raise FactCheckError("MODEL_PROVIDER_ERROR", "事实核查模型调用失败或超时。") from None
                tokens = getattr(response, "usage", None) or {}
                if isinstance(tokens, dict):
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        value = tokens.get(key)
                        if key == "total_tokens" and value is None:
                            value = sum(tokens.get(k, 0) for k in ("prompt_tokens", "completion_tokens")
                                        if type(tokens.get(k, 0)) is int)
                        if type(value) is int and value >= 0:
                            usage[key] += value
                if getattr(response, "finish_reason", None) in {"length", "content_filter", "tool_calls"}:
                    raise FactCheckError("MODEL_FORMAT_ERROR", "事实核查模型输出被截断或未正常完成。")
                content = getattr(response, "content", None)
                issue = "invalid_content"
                try:
                    if not isinstance(content, str) or len(content) > MAX_BYTES:
                        raise ValueError
                    issue = "invalid_json"
                    result = json.loads(content)
                    if extraction and isinstance(result, list):
                        result = {"claims": result}
                    issue = "non_object"
                    if not isinstance(result, dict):
                        raise ValueError
                    if extraction:
                        claims, issues = _claims_from_model(result, extraction_text)
                        if not claims and issues and attempt == 0:
                            failed_extraction = result
                            issue = "invalid_location"
                            raise ValueError
                        if not claims and failed_extraction is not None:
                            return failed_extraction
                    return result
                except (ValueError, TypeError):
                    logger.warning("事实核查模型格式错误 stage={} attempt={} issue={} content_chars={}",
                                   "extract" if extraction else "judge", attempt + 1, issue,
                                   len(content) if isinstance(content, str) else 0)
                if attempt == 0:
                    # Regenerate from original material; untrusted output never becomes an instruction.
                    correction = ("上次提取项全部定位失败。重新核对输入的 segment_id 与对应句段逐字原文；禁止 start/end，不要用空数组掩盖定位错误。"
                                  if issue == "invalid_location" else "上次响应格式不合规。请依据同一输入重新生成完整 json 对象；不得补写未提供的证据。")
                    messages = [{"role": "system", "content": messages[0]["content"] + "\n" + correction}, messages[1]]
    except TimeoutError:
        raise FactCheckError("MODEL_PROVIDER_ERROR", "事实核查模型调用失败或超时。") from None
    raise FactCheckError("MODEL_FORMAT_ERROR", "事实核查模型在一次格式重试后仍未返回有效 JSON 对象。")
