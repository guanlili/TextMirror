"""配额与限流原子性：

- 登录用户配额：Redis 预扣（先 INCRBY 后判断），失败/部分失败退还，
  被拒请求不虚增计数（旧 check-then-write 并发可全部越过上限）
- 游客限流：先 INCR 后判断（并发不会全部放行），被拒请求抵消自增
- 游客模式关闭时文档校对/异步提交入口拒绝游客
"""
import asyncio
import uuid as _uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Request

from app.core import rate_limit
from app.core import redis as redis_module
from app.core.database import async_session_factory
from app.core.rate_limit import _daily_key, check_guest_rate_limit
from app.core.rate_limit import (
    refund_api_key_daily_usage as real_refund_api_key_daily_usage,
)
from app.core.rate_limit import (
    refund_user_daily_quota as real_refund_user_daily_quota,
)
from app.core.security import hash_password
from app.models.llm_config import LLMConfig
from app.models.role import Role
from app.models.uploaded_document import UploadedDocument
from app.models.user import User

PASSWORD = "Passw0rd!123"


def _fake_request(ip: str = "203.0.113.7") -> Request:
    return Request(scope={
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
        "client": (ip, 12345),
        "query_string": b"",
    })


def _proofread_result():
    return {
        "issues": [{
            "original": "错词", "type": "typo", "suggestion": "对词",
            "explanation": "", "severity": "warning", "chunk_index": 0,
        }],
        "total_issues": 1,
        "chunks_count": 1,
        "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        "domain": "general",
        "check_types": [],
    }


async def _create_user(daily_quota=None):
    async with async_session_factory() as session:
        role = Role(name="测试角色", code=f"r_{_uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=f"emp_{_uuid.uuid4().hex[:8]}",
            username="配额测试用户",
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
            daily_quota=daily_quota,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _login(client, user):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"employee_id": user.employee_id, "password": PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _user_quota_count(user) -> int:
    return int(await redis_module.redis_client.get(_daily_key("user_daily", str(user.id))) or 0)


async def _create_two_configs():
    async with async_session_factory() as session:
        from sqlalchemy import update

        await session.execute(
            update(LLMConfig).where(LLMConfig.is_active.is_(True)).values(is_active=False)
        )
        cfgs = []
        for i in range(2):
            cfg = LLMConfig(
                name=f"quota-test-{i}-{_uuid.uuid4().hex[:6]}",
                provider="openai",
                api_base="https://example.com/v1",
                api_key="sk-test",
                model="test-model",
                timeout=60,
                max_retries=0,
                is_enabled=True,
            )
            session.add(cfg)
            cfgs.append(cfg)
        await session.commit()
        return cfgs


def _compare_items(cfgs, success_flags):
    from app.services.model_compare import cross_model_stats

    items = [
        {
            "config_id": c.id, "config_name": c.name, "model": c.model,
            "success": ok,
            "issues": [
                {"original": "错词", "type": "typo", "suggestion": "对词",
                 "explanation": "", "severity": "warning"}
            ] if ok else [],
            "total_issues": 1 if ok else 0,
            "error": None if ok else "timeout",
            "elapsed_ms": 10,
        }
        for c, ok in zip(cfgs, success_flags)
    ]
    consensus, only_in = cross_model_stats(items)
    return items, consensus, only_in


async def test_user_quota_blocks_at_limit_without_inflation(client):
    user = await _create_user(daily_quota=2)
    headers = await _login(client, user)

    with patch("app.api.v1.proofread.proofread_text", return_value=_proofread_result()):
        for _ in range(2):
            resp = await client.post(
                "/api/v1/proofread/text", json={"text": "测试文本"}, headers=headers,
            )
            assert resp.status_code == 200, resp.text

        # 第三次：预扣后计数 3 > 2 → 429，且被拒请求不虚增计数
        resp = await client.post(
            "/api/v1/proofread/text", json={"text": "测试文本"}, headers=headers,
        )
        assert resp.status_code == 429
        assert await _user_quota_count(user) == 2


async def test_failed_proofread_refunds_quota(client):
    user = await _create_user(daily_quota=1)
    headers = await _login(client, user)

    with patch("app.api.v1.proofread.proofread_text", side_effect=RuntimeError("boom")):
        resp = await client.post(
            "/api/v1/proofread/text", json={"text": "测试文本"}, headers=headers,
        )
        assert resp.status_code == 503
    assert await _user_quota_count(user) == 0

    # 失败未消耗额度：剩余额度仍可成功校对一次
    with patch("app.api.v1.proofread.proofread_text", return_value=_proofread_result()):
        resp = await client.post(
            "/api/v1/proofread/text", json={"text": "测试文本"}, headers=headers,
        )
        assert resp.status_code == 200, resp.text
    assert await _user_quota_count(user) == 1


async def test_compare_refunds_failed_models(client):
    user = await _create_user(daily_quota=2)
    headers = await _login(client, user)
    cfgs = await _create_two_configs()

    def _compare(**kw):
        return _compare_items(cfgs, [True, False])

    with patch("app.services.model_compare.run_proofread_compare", side_effect=_compare):
        resp = await client.post(
            "/api/v1/proofread/compare",
            json={"text": "测试对比文本", "config_ids": [c.id for c in cfgs]},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    # 预扣 2、失败退 1：净消耗=成功模型数（与 DB 记录 quota_weight 同口径）
    assert await _user_quota_count(user) == 1

    # 剩余 1，全成功对比需再扣 2（1+2 > 2）→ 拒绝
    def _compare2(**kw):
        return _compare_items(cfgs, [True, True])

    with patch("app.services.model_compare.run_proofread_compare", side_effect=_compare2):
        resp = await client.post(
            "/api/v1/proofread/compare",
            json={"text": "测试对比文本", "config_ids": [c.id for c in cfgs]},
            headers=headers,
        )
    assert resp.status_code == 429
    assert await _user_quota_count(user) == 1  # 被拒请求不虚增计数


async def test_guest_rate_limit_atomic(client):
    """同一 IP 并发 5 个请求（限 3）：INCR 原子性保证只放行 3 个。

    旧实现先 GET 判断再 INCR，并发请求全部看到旧值 → 全部放行。
    """
    req = _fake_request()
    results = await asyncio.gather(
        *[check_guest_rate_limit(req, daily_limit=3) for _ in range(5)],
        return_exceptions=True,
    )
    allowed = sum(1 for r in results if not isinstance(r, BaseException))
    rejected = sum(
        1 for r in results
        if isinstance(r, HTTPException) and r.status_code == 429
    )
    assert allowed == 3
    assert rejected == 2
    # 被拒请求已抵消自增：计数器只记实际放行次数
    count = await redis_module.redis_client.get(_daily_key("guest", "ip:203.0.113.7"))
    assert int(count) == 3


async def test_guest_mode_disabled_rejects_document_proofread(client):
    async with async_session_factory() as session:
        doc = UploadedDocument(
            file_id=f"doc_{_uuid.uuid4().hex[:8]}",
            filename="t.txt",
            file_ext=".txt",
            file_size=10,
            file_path="/tmp/t.txt",
            text_length=2,
            extracted_text="文本",
            owner_kind="guest",
            status="uploaded",
        )
        session.add(doc)
        await session.commit()
        file_id = doc.file_id

    with patch("app.services.site_config.is_guest_mode_enabled", new=AsyncMock(return_value=False)):
        resp = await client.post("/api/v1/document/proofread", json={"file_id": file_id})
        assert resp.status_code == 403

        resp = await client.post("/api/v1/document/proofread/async", json={"file_id": file_id})
        assert resp.status_code == 403


@pytest.mark.parametrize("kind", ["user", "apikey"])
async def test_refund_uses_charge_receipt_after_midnight(client, monkeypatch, kind):
    subject = SimpleNamespace(id=71, daily_quota=20)
    day = ["20260924"]
    monkeypatch.setattr(rate_limit, "_daily_key", lambda prefix, ident: f"textmirror:{prefix}:{ident}:{day[0]}")
    prefix = "user_daily" if kind == "user" else "apikey_daily"
    old_key = f"textmirror:{prefix}:71:20260924"
    new_key = f"textmirror:{prefix}:71:20260925"
    redis = redis_module.redis_client
    await redis.set(old_key, 3, ex=172800)
    await redis.set(new_key, 9, ex=172800)
    charge = rate_limit.charge_user_daily_quota if kind == "user" else rate_limit.charge_api_key_daily
    refund = real_refund_user_daily_quota if kind == "user" else real_refund_api_key_daily_usage
    receipt = await charge(subject, 2)
    assert receipt == old_key
    day[0] = "20260925"

    async def eval_refund(script, numkeys, key, weight):
        assert script == rate_limit._REFUND_DAILY_LUA
        assert numkeys == 1
        count = int(await redis.get(key) or 0)
        if count:
            return await redis.decrby(key, min(count, weight))
        return 0

    evaluate = AsyncMock(side_effect=eval_refund)
    monkeypatch.setattr(redis, "eval", evaluate)
    await refund(receipt, 2)
    evaluate.assert_awaited_once_with(rate_limit._REFUND_DAILY_LUA, 1, old_key, 2)
    assert int(await redis.get(old_key)) == 3
    assert int(await redis.get(new_key)) == 9

    await redis.delete(old_key)
    await refund(receipt)
    assert await redis.get(old_key) is None
    assert int(await redis.get(new_key)) == 9
    evaluate.reset_mock()
    await refund(None)
    await refund(receipt, 0)
    evaluate.assert_not_awaited()


@pytest.mark.parametrize("kind", ["user", "apikey"])
@pytest.mark.parametrize("quota", [0, 10])
async def test_charge_retains_receipt_and_limit_when_ttl_fails(client, monkeypatch, kind, quota):
    subject = SimpleNamespace(id=72, daily_quota=quota)
    charge = rate_limit.charge_user_daily_quota if kind == "user" else rate_limit.charge_api_key_daily
    prefix = "user_daily" if kind == "user" else "apikey_daily"
    key = _daily_key(prefix, "72")
    redis = redis_module.redis_client
    monkeypatch.setattr(redis, "ttl", AsyncMock(side_effect=RuntimeError("TTL unavailable")))
    if quota:
        assert await charge(subject) == key
        assert int(await redis.get(key)) == 1
    else:
        with pytest.raises(HTTPException) as raised:
            await charge(subject)
        assert raised.value.status_code == 429
        assert int(await redis.get(key)) == 0


@pytest.mark.parametrize("endpoint,service,payload", [
    ("/proofread/text", "app.api.v1.proofread.proofread_text", {"text": "测试跨日审校退款"}),
    ("/polish/text", "app.api.v1.polish.polish_text", {"text": "这是一段用于测试跨日退款的润色原文。", "style": "formal"}),
])
async def test_web_failure_refunds_original_day(client, monkeypatch, endpoint, service, payload):
    user = await _create_user(daily_quota=10)
    headers = await _login(client, user)
    day = ["20260924"]
    monkeypatch.setattr(rate_limit, "_daily_key", lambda prefix, ident: f"textmirror:{prefix}:{ident}:{day[0]}")
    old_key = f"textmirror:user_daily:{user.id}:20260924"
    new_key = f"textmirror:user_daily:{user.id}:20260925"
    await redis_module.redis_client.set(old_key, 2, ex=172800)
    await redis_module.redis_client.set(new_key, 4, ex=172800)

    async def fail_after_midnight(**kwargs):
        day[0] = "20260925"
        raise RuntimeError("Model unavailable")

    with patch(service, side_effect=fail_after_midnight):
        response = await client.post(f"/api/v1{endpoint}", json=payload, headers=headers)
    assert response.status_code == 503, response.text
    assert int(await redis_module.redis_client.get(old_key)) == 2
    assert int(await redis_module.redis_client.get(new_key)) == 4


async def test_web_polish_stream_refunds_original_day(client, monkeypatch):
    user = await _create_user(daily_quota=10)
    headers = await _login(client, user)
    day = ["20260924"]
    monkeypatch.setattr(rate_limit, "_daily_key", lambda prefix, ident: f"textmirror:{prefix}:{ident}:{day[0]}")
    old_key = f"textmirror:user_daily:{user.id}:20260924"
    new_key = f"textmirror:user_daily:{user.id}:20260925"
    await redis_module.redis_client.set(old_key, 2, ex=172800)
    await redis_module.redis_client.set(new_key, 4, ex=172800)

    async def fail_after_midnight(**kwargs):
        yield {"event": "meta", "style": "formal", "style_name": "正式规范"}
        day[0] = "20260925"
        raise RuntimeError("Model unavailable")

    monkeypatch.setattr("app.api.v1.polish.polish_text_stream", fail_after_midnight)
    response = await client.post(
        "/api/v1/polish/text/stream",
        json={"text": "这是一段用于测试跨日退款的润色原文。", "style": "formal"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert '"event": "fatal"' in response.text
    assert int(await redis_module.redis_client.get(old_key)) == 2
    assert int(await redis_module.redis_client.get(new_key)) == 4


@pytest.mark.parametrize("endpoint", ["/proofread/compare", "/polish/compare", "/polish/compare/stream"])
async def test_web_compare_partial_refund_uses_original_day(client, monkeypatch, endpoint):
    user = await _create_user(daily_quota=10)
    headers = await _login(client, user)
    configs = await _create_two_configs()
    day = ["20260924"]
    monkeypatch.setattr(rate_limit, "_daily_key", lambda prefix, ident: f"textmirror:{prefix}:{ident}:{day[0]}")
    old_key = f"textmirror:user_daily:{user.id}:20260924"
    new_key = f"textmirror:user_daily:{user.id}:20260925"
    await redis_module.redis_client.set(old_key, 2, ex=172800)
    await redis_module.redis_client.set(new_key, 4, ex=172800)

    async def compare(**kwargs):
        day[0] = "20260925"
        return _compare_items(configs, [True, False])

    def build_provider(config):
        async def chat(*args, **kwargs):
            day[0] = "20260925"
            if config.id == configs[1].id:
                raise RuntimeError("Model unavailable")
            return SimpleNamespace(content="测试润色正文", model=config.model)

        async def stream(*args, **kwargs):
            result = await chat()
            yield result.content

        return SimpleNamespace(chat=chat, chat_stream=stream, close=AsyncMock())

    monkeypatch.setattr("app.services.model_compare.run_proofread_compare", compare)
    monkeypatch.setattr("app.api.v1.polish._build_compare_provider", build_provider)
    payload = {"text": "这是一段用于测试跨日对比退款的原文。", "config_ids": [c.id for c in configs]}
    if endpoint.startswith("/polish"):
        payload["style"] = "formal"
    response = await client.post(f"/api/v1{endpoint}", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    if endpoint.endswith("/stream"):
        assert '"event": "done"' in response.text
        assert '"event": "error"' in response.text
    else:
        assert [item["success"] for item in response.json()["results"]] == [True, False]
    assert int(await redis_module.redis_client.get(old_key)) == 3
    assert int(await redis_module.redis_client.get(new_key)) == 4
