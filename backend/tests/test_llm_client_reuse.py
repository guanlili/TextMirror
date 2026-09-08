"""LLM provider 的 httpx client 共享池语义。"""
from app.services.llm.openai_compat import OpenAICompatProvider


def _make(api_key="sk-test", api_base="https://example.com/v1", timeout=60):
    return OpenAICompatProvider(api_key=api_key, api_base=api_base, model="test-model", timeout=timeout)


async def test_same_config_shares_client():
    p1, p2 = _make(), _make()
    assert p1.client is p2.client


async def test_close_keeps_shared_client_open():
    p1, p2 = _make(), _make()
    await p1.close()
    assert not p2.client.is_closed


async def test_different_config_gets_isolated_client():
    base = _make()
    assert _make(api_key="sk-other").client is not base.client
    assert _make(api_base="https://other.example.com/v1").client is not base.client
    assert _make(timeout=30).client is not base.client


async def test_closed_client_is_replaced():
    p1 = _make()
    await p1.client.aclose()
    p2 = _make()
    assert p2.client is not p1.client
    assert not p2.client.is_closed
