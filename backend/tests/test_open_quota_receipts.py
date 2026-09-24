"""开放 API 跨午夜退款凭据：仅使用 SQLite、fakeredis 和替代模型。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest
from fastapi import HTTPException
from test_open_polish import _create_user_with_key

from app.api.v1 import open as open_api
from app.api.v1 import open_common, open_polish
from app.core import rate_limit
from app.core import redis as redis_module
from app.core.database import async_session_factory
from app.models.llm_config import LLMConfig
from app.services import model_compare
from app.services.proofread import InvalidModelConfigError


@pytest.fixture
async def receipts(client, monkeypatch):
    user, plaintext, api_key = await _create_user_with_key(daily_quota=100)
    clock = SimpleNamespace(day="20000101")
    monkeypatch.setattr(rate_limit, "_daily_key", lambda prefix, subject: f"textmirror:{prefix}:{subject}:{clock.day}")
    old = (f"textmirror:user_daily:{user.id}:20000101", f"textmirror:apikey_daily:{api_key.id}:20000101")
    new = tuple(key.replace("20000101", "20000102") for key in old)
    redis = redis_module.redis_client
    for key, value in zip((*old, *new), (4, 5, 7, 8)):
        await redis.set(key, value, ex=172800)

    async def refund(key, weight=1):
        # 独立于 conftest 的旧对象签名，错误传播必须显式暴露。
        assert key is None or isinstance(key, str)
        if key is not None:
            count = int(await redis.get(key) or 0)
            if count > 0:
                await redis.decrby(key, min(count, weight))

    user_refund, key_refund = AsyncMock(side_effect=refund), AsyncMock(side_effect=refund)
    for module in (open_api, open_common, open_polish):
        monkeypatch.setattr(module, "refund_user_daily_quota", user_refund)
        if hasattr(module, "refund_api_key_daily_usage"):
            monkeypatch.setattr(module, "refund_api_key_daily_usage", key_refund)
    return SimpleNamespace(user=user, api_key=api_key, headers={"Authorization": f"Bearer {plaintext}"},
                           clock=clock, redis=redis, old=old, new=new,
                           user_refund=user_refund, key_refund=key_refund)


@pytest.mark.parametrize("case", ["proofread", "proofread_error", "invalid_config", "polish", "polish_error",
                                 "stream", "compare_partial", "compare_all_failed", "compare_error"])
@pytest.mark.parametrize("receipt_state", ["charged", "none", "expired"])
async def test_open_failure_refunds_only_original_receipts(client, receipts, monkeypatch, case, receipt_state):
    state = receipts
    if receipt_state == "none":
        # Redis 预扣失败、稍后恢复：旧计数仍存在，但这次没有扣费凭据。
        monkeypatch.setattr(state.redis, "incrby", AsyncMock(side_effect=ConnectionError("charge unavailable")))

    async def midnight():
        state.clock.day = "20000102"
        if receipt_state == "expired":
            await state.redis.delete(*state.old)

    async def failure(*args, **kwargs):
        await midnight()
        if case == "invalid_config":
            raise InvalidModelConfigError("指定的模型配置不存在或已停用")
        if case.endswith("_error"):
            raise ValueError("unexpected model failure")
        raise RuntimeError("model unavailable")

    async def stream(**kwargs):
        yield {"event": "meta", "style": "formal", "style_name": "正式规范"}
        await failure()

    monkeypatch.setattr(open_api, "proofread_text", AsyncMock(side_effect=failure))
    monkeypatch.setattr(open_polish, "polish_text", AsyncMock(side_effect=failure))
    monkeypatch.setattr(open_polish, "polish_text_stream", stream)
    body = {"text": "这是一段需要测试跨午夜退款的文本。"}
    weight, retained = 1, 0
    if case.startswith("compare"):
        async with async_session_factory() as db:
            configs = [LLMConfig(name=f"receipt-{index}", provider="openai", api_base="https://example.invalid/v1",
                                 api_key="unused", model=f"test-{index}", is_active=False, is_enabled=True)
                       for index in range(2)]
            db.add_all(configs)
            await db.commit()
        body["config_ids"] = [cfg.id for cfg in configs]
        path, weight = "/proofread/compare", 2
        retained = int(case == "compare_partial")

        async def compare(**kwargs):
            await midnight()
            if case == "compare_error":
                raise HTTPException(status_code=503, detail="compare unavailable")
            items = [{"config_id": cfg.id, "config_name": cfg.name, "model": cfg.model,
                      "success": bool(index == 0 and retained), "issues": [], "total_issues": 0,
                      "error": None if index == 0 and retained else "unavailable", "elapsed_ms": 1}
                     for index, cfg in enumerate(configs)]
            return items, [], {}

        monkeypatch.setattr(model_compare, "run_proofread_compare", compare)
        expected_status = 503 if case == "compare_error" else 200
    else:
        path = "/polish/stream" if case == "stream" else "/polish" if case.startswith("polish") else "/proofread"
        expected_status = 200 if case == "stream" else 400 if case == "invalid_config" else 500 if case.endswith("_error") else 503

    response = await client.post("/api/v1/open" + path, json=body, headers=state.headers)
    assert response.status_code == expected_status, response.text
    if case == "stream":
        assert '"event": "fatal"' in response.text
    assert await state.redis.mget(state.new) == ["7", "8"]
    expected_old = [str(4 + retained), str(5 + (1 if case == "invalid_config" else retained))]
    if receipt_state == "none":
        expected_old = ["4", "5"]
    elif receipt_state == "expired":
        expected_old = [None, None]
    assert await state.redis.mget(state.old) == expected_old
    expected_receipts = (None, None) if receipt_state == "none" else state.old
    assert state.user_refund.await_args.args[0] == expected_receipts[0]
    assert state.user_refund.await_count == 1
    if case == "invalid_config":
        state.key_refund.assert_not_awaited()
    else:
        assert state.key_refund.await_count == 1
        assert state.key_refund.await_args.args[0] == expected_receipts[1]
    if weight > 1:
        state.user_refund.assert_awaited_once_with(expected_receipts[0], weight - retained)
        state.key_refund.assert_awaited_once_with(expected_receipts[1], weight - retained)


@pytest.mark.parametrize("has_api_key", [False, True])
async def test_open_billing_returns_both_receipts_in_charge_order(monkeypatch, has_api_key):
    order = Mock()
    for name, result in (("check_api_key_rpm", None), ("_charge_user_quota_contract", "user-receipt"),
                         ("charge_api_key_daily", "key-receipt")):
        mock = AsyncMock(return_value=result)
        monkeypatch.setattr(open_common, name, mock)
        order.attach_mock(mock, name)
    user, api_key = object(), object() if has_api_key else None
    assert await open_common._open_billing(user, api_key, 2) == ("user-receipt", "key-receipt" if has_api_key else None)
    expected = [call._charge_user_quota_contract(user, 2)]
    if has_api_key:
        expected = [call.check_api_key_rpm(api_key), *expected, call.charge_api_key_daily(api_key, 2)]
    assert order.mock_calls == expected


async def test_key_rejection_after_midnight_refunds_original_user_receipt(receipts, monkeypatch):
    state = receipts
    charge = rate_limit.charge_api_key_daily
    state.api_key.daily_quota = 0

    async def reject_next_day(api_key, weight):
        state.clock.day = "20000102"
        return await charge(api_key, weight)

    monkeypatch.setattr(open_common, "charge_api_key_daily", reject_next_day)
    with pytest.raises(HTTPException) as error:
        await open_common._open_billing(state.user, state.api_key, 2)
    assert error.value.status_code == 429
    state.user_refund.assert_awaited_once_with(state.old[0], 2)
    assert await state.redis.mget((*state.old, *state.new)) == ["4", "5", "7", "8"]
