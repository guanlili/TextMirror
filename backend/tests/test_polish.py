"""同步润色回归：真实服务及 ASGI API，LLM 使用替身，不访问付费模型。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.api.v1 import polish as polish_api
from app.core.database import async_session_factory
from app.core.security import create_access_token, hash_password
from app.models.proofread import ProofreadRecord
from app.models.role import Role
from app.models.user import User
from app.services import polish as polish_service
from app.services.llm.base import LLMResponse

# 服务测试也使用隔离的 SQLite/Redis，敏感词扫描等逻辑不打桩。
pytestmark = pytest.mark.usefixtures("client")

TEXT = "这段文字需要更加正式的表达方式来呈现。"
PROVIDER_DETAIL = "provider response: sk-private-test-secret upstream.internal"
LEVELS = ("light", "standard", "deep")
LABELS = ("轻量润色", "标准润色", "深度润色")
USAGES = (
    {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    {"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22},
    {"prompt_tokens": 23, "completion_tokens": 7, "total_tokens": 30},
)
FAILURE_MODES = ("exceptions", "empty", "whitespace", "titles", "mixed")
SUCCESS_CASES = (
    pytest.param(
        ("standard",),
        {"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22},
        id="one-success",
    ),
    pytest.param(
        ("light", "deep"),
        {"prompt_tokens": 34, "completion_tokens": 10, "total_tokens": 44},
        id="two-successes",
    ),
    pytest.param(
        LEVELS,
        {"prompt_tokens": 51, "completion_tokens": 15, "total_tokens": 66},
        id="all-successful",
    ),
)


def _failure_outputs(mode):
    if mode == "exceptions":
        return (
            RuntimeError(PROVIDER_DETAIL),
            ValueError(PROVIDER_DETAIL),
            TimeoutError(PROVIDER_DETAIL),
        )
    if mode == "empty":
        return ("", "", "")
    if mode == "whitespace":
        return (" \t\n", "\r\n \t", "\u3000\n ")
    if mode == "titles":
        return (
            "【版本一：轻量润色】\n \t",
            " \n【标准润色】\n",
            "【深度润色】\n\n【版本三：深度润色】",
        )
    assert mode == "mixed"
    return (RuntimeError(PROVIDER_DETAIL), " \t\n", "【深度润色】\n")


def _successful_outputs(success_levels):
    outputs = list(_failure_outputs("mixed"))
    for i, level in enumerate(LEVELS):
        if level in success_levels:
            outputs[i] = f"【{LABELS[i]}】\n\n润色后的{level}正文。\n"
    return outputs


def _expected_versions(success_levels):
    return [
        {
            "label": label,
            "level": level,
            "content": (
                f"润色后的{level}正文。"
                if level in success_levels
                else f"（{label}生成失败，请重试）"
            ),
        }
        for level, label in zip(LEVELS, LABELS)
    ]


@pytest.fixture
def mock_provider(monkeypatch):
    def configure(outputs):
        async def chat(messages, **kwargs):
            # 按版本指令选择响应，不依赖并行任务的调用顺序。
            for i, level in enumerate(LEVELS):
                instruction = polish_service.VERSION_INSTRUCTIONS[level]["instruction"]
                if messages[0]["content"].endswith(instruction):
                    output = outputs[i]
                    if isinstance(output, Exception):
                        raise output
                    return LLMResponse(content=output, model="test-model", usage=USAGES[i])
            raise AssertionError("未知润色版本")

        provider = SimpleNamespace(chat=AsyncMock(side_effect=chat), close=AsyncMock())
        monkeypatch.setattr(polish_service, "get_llm_provider", AsyncMock(return_value=provider))
        return provider

    return configure


async def _authenticated_user():
    async with async_session_factory() as session:
        role = Role(name="润色测试角色", code=f"polish_{uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=f"polish_{uuid4().hex[:8]}",
            username="同步润色测试用户",
            password_hash=hash_password("Passw0rd!123"),
            role_id=role.id,
            daily_quota=10,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        return user.id, {"Authorization": f"Bearer {create_access_token(user.id)}"}


async def _records_for(user_id):
    async with async_session_factory() as session:
        return (await session.execute(
            select(ProofreadRecord).where(ProofreadRecord.user_id == user_id)
        )).scalars().all()


@pytest.mark.parametrize("failure_mode", FAILURE_MODES)
async def test_service_all_versions_failed_raises_safe_error(mock_provider, failure_mode):
    provider = mock_provider(_failure_outputs(failure_mode))

    try:
        with pytest.raises(RuntimeError, match="^润色生成失败，请稍后重试$") as exc:
            await polish_service.polish_text(TEXT)
        assert PROVIDER_DETAIL not in str(exc.value)
    finally:
        assert provider.chat.await_count == 3
        provider.close.assert_awaited_once_with()


@pytest.mark.parametrize("success_levels,expected_usage", SUCCESS_CASES)
async def test_service_keeps_versions_and_sums_success_usage(
    mock_provider, success_levels, expected_usage,
):
    provider = mock_provider(_successful_outputs(success_levels))

    result = await polish_service.polish_text(TEXT)

    assert provider.chat.await_count == 3
    provider.close.assert_awaited_once_with()
    assert provider.usage_business == "polish"
    assert result == {
        "versions": _expected_versions(success_levels),
        "style": "formal",
        "style_name": "正式规范",
        "usage": expected_usage,
    }


@pytest.mark.parametrize("failure_mode", FAILURE_MODES)
async def test_api_all_versions_failed_returns_503_refunds_without_history(
    client, monkeypatch, mock_provider, failure_mode,
):
    user_id, headers = await _authenticated_user()
    provider = mock_provider(_failure_outputs(failure_mode))
    # 仅观察退款路径，不绑定 user/quota_key 签名或 fakeredis 的 Lua 支持。
    refund = AsyncMock()
    monkeypatch.setattr(polish_api, "refund_user_daily_quota", refund)

    response = await client.post(
        "/api/v1/polish/text", json={"text": TEXT, "style": "formal"}, headers=headers,
    )

    assert provider.chat.await_count == 3
    provider.close.assert_awaited_once_with()
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == {
        "code": "POLISH_SERVICE_ERROR",
        "message": "润色服务暂时不可用，请稍后重试",
    }
    assert PROVIDER_DETAIL not in response.text
    refund.assert_awaited_once()
    assert await _records_for(user_id) == []


@pytest.mark.parametrize("success_levels,expected_usage", SUCCESS_CASES)
async def test_api_success_keeps_history_and_usage_without_refund(
    client, monkeypatch, mock_provider, success_levels, expected_usage,
):
    user_id, headers = await _authenticated_user()
    provider = mock_provider(_successful_outputs(success_levels))
    refund = AsyncMock()
    monkeypatch.setattr(polish_api, "refund_user_daily_quota", refund)

    response = await client.post(
        "/api/v1/polish/text", json={"text": TEXT, "style": "formal"}, headers=headers,
    )

    assert response.status_code == 200, response.text
    assert provider.chat.await_count == 3
    provider.close.assert_awaited_once_with()
    refund.assert_not_awaited()
    data = response.json()
    assert data["style"] == "formal"
    assert data["style_name"] == "正式规范"
    assert data["usage"] == expected_usage
    versions = [{k: v[k] for k in ("label", "level", "content")} for v in data["versions"]]
    assert versions == _expected_versions(success_levels)
    assert PROVIDER_DETAIL not in response.text
    records = await _records_for(user_id)
    assert len(records) == 1
    assert records[0].type == "polish"
    assert records[0].result["versions"] == versions
    assert records[0].token_usage == expected_usage
