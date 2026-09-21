import asyncio
import json

import httpx

from app.services.fact_check_search import SearchResult, _titles

from .constants import SEARCH_URL
from .errors import FactCheckError, _FetchError, _SearchFailure
from .fetching import _hostname, _read_limited


async def _search(query: str, api_key: str, sources: list[dict] | None) -> SearchResult:
    payload = {"query": query, "max_results": 3, "search_depth": "basic",
               "include_answer": False, "include_raw_content": False}
    if sources is not None:
        payload["include_domains"] = list(dict.fromkeys(_hostname(source["domain"]) for source in sources))
    try:
        async with asyncio.timeout(20):
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, verify=True, timeout=15) as client:
                async with client.stream(
                    "POST", SEARCH_URL, json=payload,
                    headers={"Authorization": f"Bearer {api_key}", "Accept-Encoding": "identity"},
                ) as response:
                    if response.status_code in {401, 403}:
                        raise FactCheckError("SEARCH_AUTH_ERROR", "Tavily 检索鉴权失败，请检查 API 密钥。")
                    if response.status_code in {402, 429, 432, 433}:
                        raise FactCheckError("SEARCH_PROVIDER_ERROR", "Tavily 检索配额不足或服务限流。")
                    if response.status_code != 200:
                        raise _SearchFailure("SEARCH_HTTP_ERROR", "Tavily 检索服务返回异常状态。")
                    data = json.loads(await _read_limited(response))
                    if not isinstance(data, dict) or not isinstance(data.get("results"), list) or data.get("error"):
                        raise _SearchFailure("SEARCH_FORMAT_ERROR", "Tavily 检索响应格式错误。")
                    results = data["results"][:3]
                    if any(not isinstance(item, dict) or not isinstance(item.get("url"), str) for item in results):
                        raise _SearchFailure("SEARCH_FORMAT_ERROR", "Tavily 检索结果缺少 URL。")
                    # Titles are trace metadata only; snippets, dates and answers are discarded.
                    urls = [item["url"] for item in results]
                    return SearchResult(urls, {}, 1, _titles(results, urls))
    except _SearchFailure:
        raise
    except _FetchError:
        raise _SearchFailure("SEARCH_FORMAT_ERROR", "Tavily 检索响应超限或无法读取。") from None
    except (httpx.HTTPError, OSError, TimeoutError, ValueError):
        raise _SearchFailure("SEARCH_UNAVAILABLE", "Tavily 检索请求失败或超时。") from None
