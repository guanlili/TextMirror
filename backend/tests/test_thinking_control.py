"""审校深度三档的思考模式注入与供应商方言映射。

背景：deepseek-v4-flash 等混合思考模型默认开思考，推理 token 以 ~110/s 生成，
一次 800 字分片可耗时 60~180s（32 样本 A/B：关思考提速 ~10 倍但零误报 2/9，
开思考 7/9）。方案：standard 关思考、deep 开思考、自检恒关。
"""
import httpx
import pytest

from app.services.llm.base import LLMResponse
from app.services.llm.openai_compat import OpenAICompatProvider
from app.services.proofread import proofread_text


class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {
            "choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}],
            "usage": {},
            "model": "test-model",
        }


@pytest.fixture
def captured_posts(monkeypatch):
    calls = []

    async def fake_post(self, endpoint, **kwargs):
        calls.append(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return calls


def _provider(slug=None):
    return OpenAICompatProvider(
        api_key="sk-test",
        api_base="https://example.com/v1",
        model="test-model",
        provider_slug=slug,
    )


async def test_volcengine_thinking_off(captured_posts):
    await _provider("volcengine").chat([{"role": "user", "content": "hi"}], thinking=False)
    assert captured_posts[0]["json"]["thinking"] == {"type": "disabled"}


async def test_volcengine_thinking_on(captured_posts):
    await _provider("volcengine").chat([{"role": "user", "content": "hi"}], thinking=True)
    assert captured_posts[0]["json"]["thinking"] == {"type": "enabled"}


async def test_qwen_thinking_off(captured_posts):
    await _provider("qwen").chat([{"role": "user", "content": "hi"}], thinking=False)
    assert captured_posts[0]["json"]["enable_thinking"] is False


async def test_unknown_slug_not_injected(captured_posts):
    # 未收录方言的供应商不注入参数，避免不认识该字段的网关 400
    await _provider("deepseek").chat([{"role": "user", "content": "hi"}], thinking=False)
    payload = captured_posts[0]["json"]
    assert "thinking" not in payload and "enable_thinking" not in payload


async def test_thinking_none_keeps_model_default(captured_posts):
    # thinking=None（润色/测试连接等既有调用方）不得改变请求体
    await _provider("volcengine").chat([{"role": "user", "content": "hi"}])
    assert "thinking" not in captured_posts[0]["json"]


async def test_per_request_timeout_override(captured_posts):
    await _provider("volcengine").chat([{"role": "user", "content": "hi"}], timeout=180)
    assert captured_posts[0]["timeout"] == httpx.Timeout(180, connect=15)


async def test_no_timeout_kwarg_by_default(captured_posts):
    await _provider().chat([{"role": "user", "content": "hi"}])
    assert "timeout" not in captured_posts[0]


# ----------------------------------------------------------------------
# depth 接线：proofread_text 按档位注入 thinking/timeout
# ----------------------------------------------------------------------

class _RecordingProvider:
    default_temperature = 0.2

    def __init__(self, timeout=60):
        self.timeout = timeout
        self.calls = []

    async def chat(self, messages, temperature=0.3, max_tokens=None,
                   thinking=None, timeout=None):
        self.calls.append({"thinking": thinking, "timeout": timeout})
        return LLMResponse(content="[]", model="fake", usage={})

    async def close(self):
        pass


async def _run_proofread(monkeypatch, depth, timeout=60):
    provider = _RecordingProvider(timeout=timeout)

    async def fake_prep(user_id, domain, config_id):
        global_words = {"sensitive": [], "banned": [], "correction": [], "whitelist": []}
        user_words = {"correction": [], "whitelist": []}
        return (global_words, user_words, ""), provider

    monkeypatch.setattr("app.services.proofread._gather_preparation", fake_prep)
    result = await proofread_text(text="这是一段测试文本，包含明确的校对内容。" * 3, depth=depth)
    return result, provider


async def test_standard_disables_thinking(monkeypatch):
    _, provider = await _run_proofread(monkeypatch, "standard")
    assert provider.calls[0]["thinking"] is False
    assert provider.calls[0]["timeout"] is None


async def test_deep_enables_thinking_and_extends_timeout(monkeypatch):
    _, provider = await _run_proofread(monkeypatch, "deep")
    assert provider.calls[0]["thinking"] is True
    assert provider.calls[0]["timeout"] == 180


async def test_deep_timeout_respects_larger_config(monkeypatch):
    # 管理员配置了更长的超时（如 300s）时取配置值，不用 180 覆盖
    _, provider = await _run_proofread(monkeypatch, "deep", timeout=300)
    assert provider.calls[0]["timeout"] == 300


async def test_deep_self_check_always_disables_thinking(monkeypatch):
    _, provider = await _run_proofread(monkeypatch, "deep")
    # deep 强制自检：第二次 chat 调用是自检，思考恒关
    assert len(provider.calls) == 2
    assert provider.calls[1]["thinking"] is False


async def test_standard_no_self_check_when_not_high_risk(monkeypatch):
    # 无 error 级问题不触发自检（fake 返回空清单）
    _, provider = await _run_proofread(monkeypatch, "standard")
    assert len(provider.calls) == 1


async def test_quick_never_calls_llm(monkeypatch):
    _, provider = await _run_proofread(monkeypatch, "quick")
    assert provider.calls == []
