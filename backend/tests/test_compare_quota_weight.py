"""对比端点用量口径：quota_weight 计量。

配额从「只预检不消耗」改为真正计量：对比按成功模型数落一条带权重的记录，
用户配额/用量统计按 SUM(quota_weight) 计。全部失败零消耗不落库。
"""
import uuid as _uuid
from unittest.mock import patch

from app.core.database import async_session_factory
from app.core.rate_limit import _count_today_records
from app.core.security import hash_api_key, hash_password
from app.models.api_key import ApiKey
from app.models.llm_config import LLMConfig
from app.models.proofread import ProofreadRecord
from app.models.role import Role
from app.models.user import User

PASSWORD = "Passw0rd!123"


async def _create_user_with_key(daily_quota=None):
    async with async_session_factory() as session:
        role = Role(name="测试角色", code=f"r_{_uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=f"emp_{_uuid.uuid4().hex[:8]}",
            username="对比测试用户",
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
            daily_quota=daily_quota,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        plaintext = f"tm_{_uuid.uuid4().hex}"
        key = ApiKey(
            user_id=user.id,
            name="对比测试密钥",
            key_prefix=plaintext[:13],
            key_suffix=plaintext[-4:],
            key_hash=hash_api_key(plaintext),
            is_active=True,
        )
        session.add(key)
        await session.commit()
        await session.refresh(user)
        await session.refresh(key)
        return user, plaintext, key


async def _create_two_configs():
    async with async_session_factory() as session:
        from sqlalchemy import update

        await session.execute(update(LLMConfig).where(LLMConfig.is_active.is_(True)).values(is_active=False))
        cfgs = []
        for i in range(2):
            cfg = LLMConfig(
                name=f"cmp-test-{i}-{_uuid.uuid4().hex[:6]}",
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
        for c in cfgs:
            await session.refresh(c)
        return cfgs


def _fake_compare_dupe_result(cfgs, success_flags):
    """两模型各报 1 个相同错误 + 模型 A 多报 1 个独有错误"""
    from app.services.model_compare import cross_model_stats

    items = []
    for idx, (c, ok) in enumerate(zip(cfgs, success_flags)):
        issues = []
        if ok:
            issues.append({"original": "错词", "type": "typo", "suggestion": "对词", "explanation": "", "severity": "warning"})
            if idx == 0:
                issues.append({"original": "独有错", "type": "typo", "suggestion": "独有对", "explanation": "", "severity": "warning"})
        items.append({
            "config_id": c.id, "config_name": c.name, "model": c.model,
            "success": ok, "issues": issues,
            "total_issues": len(issues),
            "error": None if ok else "timeout", "elapsed_ms": 10,
        })
    consensus, only_in = cross_model_stats(items)
    return items, consensus, only_in


def _fake_compare_result(cfgs, success_flags):
    from app.services.model_compare import cross_model_stats

    items = [
        {
            "config_id": c.id,
            "config_name": c.name,
            "model": c.model,
            "success": ok,
            "issues": [{"original": "错词", "type": "typo", "suggestion": "对词", "explanation": "", "severity": "warning"}] if ok else [],
            "total_issues": 1 if ok else 0,
            "error": None if ok else "timeout",
            "elapsed_ms": 10,
        }
        for c, ok in zip(cfgs, success_flags)
    ]
    consensus, only_in = cross_model_stats(items)
    return items, consensus, only_in


async def test_compare_records_weighted_consumption(client):
    user, plaintext, key = await _create_user_with_key(daily_quota=10)
    cfgs = await _create_two_configs()

    with patch("app.services.model_compare.run_proofread_compare",
               side_effect=lambda **kw: _fake_compare_result(cfgs, [True, False])):
        resp = await client.post(
            "/api/v1/open/proofread/compare",
            json={"text": "测试对比文本", "config_ids": [c.id for c in cfgs]},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    assert resp.status_code == 200, resp.text

    # 2 模型 1 成功：权重=1（失败模型不计量，与密钥日配额按成功数结算同口径）
    async with async_session_factory() as session:
        used = await _count_today_records(user.id, session)
    assert used == 1

    from sqlalchemy import select

    async with async_session_factory() as session:
        record = (await session.execute(
            select(ProofreadRecord).where(ProofreadRecord.user_id == user.id)
        )).scalars().one()
        assert record.api_key_id == key.id
        assert record.quota_weight == 1
        assert record.result["compare"] is True
        assert record.total_issues == 1


async def test_compare_all_success_counts_all_models(client):
    user, plaintext, _ = await _create_user_with_key(daily_quota=10)
    cfgs = await _create_two_configs()

    with patch("app.services.model_compare.run_proofread_compare",
               side_effect=lambda **kw: _fake_compare_result(cfgs, [True, True])):
        resp = await client.post(
            "/api/v1/open/proofread/compare",
            json={"text": "测试对比文本", "config_ids": [c.id for c in cfgs]},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    assert resp.status_code == 200

    async with async_session_factory() as session:
        used = await _count_today_records(user.id, session)
    assert used == 2  # 2 个模型全部成功 = 消耗 2


async def test_compare_all_failed_no_consumption(client):
    user, plaintext, _ = await _create_user_with_key(daily_quota=10)
    cfgs = await _create_two_configs()

    with patch("app.services.model_compare.run_proofread_compare",
               side_effect=lambda **kw: _fake_compare_result(cfgs, [False, False])):
        resp = await client.post(
            "/api/v1/open/proofread/compare",
            json={"text": "测试对比文本", "config_ids": [c.id for c in cfgs]},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    assert resp.status_code == 200

    from sqlalchemy import select

    async with async_session_factory() as session:
        records = (await session.execute(
            select(ProofreadRecord).where(ProofreadRecord.user_id == user.id)
        )).scalars().all()
    assert records == []  # 全失败零消耗不落库


async def test_quota_blocks_when_weighted_usage_reaches_limit(client):
    user, plaintext, _ = await _create_user_with_key(daily_quota=1)
    cfgs = await _create_two_configs()

    # 已用 1（直接造一条权重 1 的记录），配额=1 → 对比预检（需 2）应拒绝
    async with async_session_factory() as session:
        session.add(ProofreadRecord(
            user_id=user.id,
            type="text",
            original_text="占位",
            domain="general",
            result={},
            total_issues=0,
            quota_weight=1,
        ))
        await session.commit()

    with patch("app.services.model_compare.run_proofread_compare",
               side_effect=lambda **kw: _fake_compare_result(cfgs, [True, True])):
        resp = await client.post(
            "/api/v1/open/proofread/compare",
            json={"text": "测试对比文本", "config_ids": [c.id for c in cfgs]},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "QUOTA_EXCEEDED"


async def test_compare_record_issues_deduped(client):
    """两模型发现同一错误只落一条，found_by 记录发现它的模型；独有错误保留"""
    user, plaintext, _ = await _create_user_with_key(daily_quota=10)
    cfgs = await _create_two_configs()

    with patch("app.services.model_compare.run_proofread_compare",
               side_effect=lambda **kw: _fake_compare_dupe_result(cfgs, [True, True])):
        resp = await client.post(
            "/api/v1/open/proofread/compare",
            json={"text": "测试对比文本", "config_ids": [c.id for c in cfgs]},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    assert resp.status_code == 200, resp.text

    from sqlalchemy import select

    async with async_session_factory() as session:
        record = (await session.execute(
            select(ProofreadRecord).where(ProofreadRecord.user_id == user.id)
        )).scalars().one()
        issues = record.result["issues"]
        # 共同错误去重为 1 条 + 模型 A 独有 1 条 = 2（不去重会是 3）
        assert record.total_issues == 2
        assert len(issues) == 2
        by_original = {i["original"]: i for i in issues}
        assert len(by_original["错词"]["found_by"]) == 2  # 两个模型都发现了它
        assert len(by_original["独有错"]["found_by"]) == 1
