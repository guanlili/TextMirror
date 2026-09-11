"""开放 API 业务逻辑测试：幂等提交、配额计费/退款、词库匹配。"""
import io
import uuid as _uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.core import redis as redis_module
from app.core.database import async_session_factory
from app.core.rate_limit import _api_key_daily_redis_key
from app.core.security import hash_api_key
from app.models.api_key import ApiKey
from app.models.dictionary import Dictionary, DictionaryEntry, WhitelistWord
from app.models.global_word import GlobalWord
from app.models.llm_config import LLMConfig
from app.models.role import Role
from app.models.user import User
from app.services.proofread import scan_words_deterministic


@pytest.fixture
async def db():
    """直接返回异步数据库会话（client fixture 已负责建表）。"""
    async with async_session_factory() as session:
        yield session


@pytest.fixture
async def role(db):
    code = f"test_role_{_uuid.uuid4().hex[:8]}"
    role = Role(name="测试角色", code=code)
    db.add(role)
    await db.commit()
    return role


@pytest.fixture
async def user(db, role):
    user = User(
        employee_id=f"test_emp_{_uuid.uuid4().hex[:8]}",
        username="测试用户",
        password_hash="fake_hash",
        role_id=role.id,
        daily_quota=100,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    return user


@pytest.fixture
async def api_key(db, user):
    """返回 (明文 key, ApiKey ORM 对象)。"""
    plaintext = f"tm_{_uuid.uuid4().hex}"
    key = ApiKey(
        user_id=user.id,
        name="测试密钥",
        key_prefix=plaintext[:13],
        key_suffix=plaintext[-4:],
        key_hash=hash_api_key(plaintext),
        daily_quota=None,
        is_active=True,
    )
    db.add(key)
    await db.commit()
    return plaintext, key


@pytest.fixture
async def llm_config(db):
    # 全局只能有一个活跃配置，先关闭历史测试留下的活跃配置
    from sqlalchemy import update
    await db.execute(update(LLMConfig).where(LLMConfig.is_active.is_(True)).values(is_active=False))
    config = LLMConfig(
        name=f"test-config-{_uuid.uuid4().hex[:8]}",
        provider="openai",
        api_base="https://example.com/v1",
        api_key="sk-test",
        model="test-model",
        temperature=0.2,
        timeout=60,
        max_retries=0,
        is_active=True,
        is_enabled=True,
    )
    db.add(config)
    await db.commit()
    return config


@pytest.fixture
def auth(api_key):
    plaintext, _ = api_key
    return {"Authorization": f"Bearer {plaintext}"}


def _proofread_success_result(text: str):
    """构造一个成功的校对结果，供 mock 使用。"""
    return {
        "issues": [],
        "total_issues": 0,
        "chunks_count": 1,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "domain": "general",
        "check_types": ["typo", "grammar"],
        "depth": "standard",
    }


def _daily_key(api_key_obj):
    return _api_key_daily_redis_key(api_key_obj)


# ----------------------------------------------------------------------
# 词库匹配（确定性扫描层）
# ----------------------------------------------------------------------

async def test_scan_global_correction_word(client, db, user, api_key, llm_config, auth):
    """全局纠错词命中后返回对应的 typo issue。"""
    db.add(GlobalWord(word="电度表", type="correction", replacement="电能表", is_active=True))
    await db.commit()

    resp = await client.post(
        "/api/v1/open/proofread",
        json={"text": "使用电度表计量", "depth": "quick"},
        headers=auth,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_issues"] == 1
    issue = data["issues"][0]
    assert issue["original"] == "电度表"
    assert issue["suggestion"] == "电能表"
    assert issue["type"] == "typo"


async def test_scan_global_banned_word(client, db, user, api_key, llm_config, auth):
    """全局禁词命中后返回 sensitive issue 且建议为空（前端按删除处理）。"""
    db.add(GlobalWord(word="违禁词", type="banned", is_active=True))
    await db.commit()

    resp = await client.post(
        "/api/v1/open/proofread",
        json={"text": "包含违禁词的句子", "depth": "quick"},
        headers=auth,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_issues"] == 1
    issue = data["issues"][0]
    assert issue["original"] == "违禁词"
    assert issue["type"] == "sensitive"
    assert issue["suggestion"] == ""


async def test_scan_user_dictionary_correction(client, db, user, api_key, llm_config, auth):
    """用户自定义词库中的错→对映射命中后返回 issue。"""
    dictionary = Dictionary(user_id=user.id, name="测试词库", is_active=True)
    db.add(dictionary)
    await db.flush()
    db.add(DictionaryEntry(dictionary_id=dictionary.id, wrong_word="做业", correct_word="作业"))
    await db.commit()

    resp = await client.post(
        "/api/v1/open/proofread",
        json={"text": "做业很多", "depth": "quick"},
        headers=auth,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_issues"] == 1
    issue = data["issues"][0]
    assert issue["original"] == "做业"
    assert issue["suggestion"] == "作业"


async def test_user_whitelist_overrides_correction(client, db, user, api_key, llm_config, auth):
    """用户放行词优先于全局纠错词（用户显式放行则不报）。"""
    db.add(GlobalWord(word="做业", type="correction", replacement="作业", is_active=True))
    db.add(WhitelistWord(user_id=user.id, word="做业", type="permanent"))
    await db.commit()

    resp = await client.post(
        "/api/v1/open/proofread",
        json={"text": "做业很多", "depth": "quick"},
        headers=auth,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_issues"] == 0


def test_scan_words_deterministic_basic():
    """纯函数：词库扫描按 (original, type) 去重，用户词优先于全局词。"""
    global_words = {
        "sensitive": [{"word": "敏感"}],
        "banned": [{"word": "违禁"}],
        "correction": [{"word": "电度表", "replacement": "电能表"}],
        "whitelist": [{"word": "API"}],
    }
    user_words = {
        "correction": [{"word": "做业", "replacement": "作业"}],
        "whitelist": [{"word": "做业"}],
    }
    text = "电度表和做业以及违禁"
    issues = scan_words_deterministic(text, global_words, user_words)
    keys = {(i["original"], i["type"]) for i in issues}
    assert ("电度表", "typo") in keys
    assert ("做业", "typo") in keys
    assert ("违禁", "sensitive") in keys
    # 用户纠错词即使也在全局/用户白名单中仍保留（显式纠错意图优先）
    assert len([i for i in issues if i["original"] == "做业"]) == 1


# ----------------------------------------------------------------------
# 配额计费与退款
# ----------------------------------------------------------------------

async def test_open_proofread_charges_api_key_quota(client, db, user, api_key, llm_config, auth):
    """文本校对成功后密钥日配额 +1。"""
    _, key_obj = api_key
    with patch("app.api.v1.open.proofread_text", return_value=_proofread_success_result("测试文本")):
        resp = await client.post(
            "/api/v1/open/proofread",
            json={"text": "测试文本"},
            headers=auth,
        )
    assert resp.status_code == 200
    count = await redis_module.redis_client.get(_daily_key(key_obj))
    assert count is not None and int(count) == 1


async def test_open_proofread_refunds_on_service_failure(client, db, user, api_key, llm_config, auth):
    """服务端故障（大模型调用失败）后退还密钥日配额。"""
    _, key_obj = api_key
    with patch(
        "app.api.v1.open.proofread_text",
        side_effect=RuntimeError("大模型调用失败: connection timeout"),
    ):
        resp = await client.post(
            "/api/v1/open/proofread",
            json={"text": "测试文本"},
            headers=auth,
        )
    assert resp.status_code == 503
    count = await redis_module.redis_client.get(_daily_key(key_obj))
    assert count is None or int(count) == 0


async def test_open_proofread_no_refund_for_invalid_config(client, db, user, api_key, llm_config, auth):
    """用户指定无效 config_id 属于用户错误，已扣额度不退。"""
    _, key_obj = api_key
    with patch(
        "app.api.v1.open.proofread_text",
        side_effect=RuntimeError("指定的模型配置不存在或已停用 (id=999)"),
    ):
        resp = await client.post(
            "/api/v1/open/proofread",
            json={"text": "测试文本", "config_id": 999},
            headers=auth,
        )
    assert resp.status_code == 400
    count = await redis_module.redis_client.get(_daily_key(key_obj))
    assert count is not None and int(count) == 1


async def test_open_compare_partial_failure_refunds_failed_models(client, db, user, api_key, llm_config, auth):
    """多模型对比中 1 个模型失败时，退还该失败模型占用的额度。"""
    _, key_obj = api_key
    # 再创建一个可用模型
    cfg2 = LLMConfig(
        name="test-config-2",
        provider="openai",
        api_base="https://example.com/v1",
        api_key="sk-test",
        model="test-model-2",
        temperature=0.2,
        timeout=60,
        max_retries=0,
        is_active=False,
        is_enabled=True,
    )
    db.add(cfg2)
    await db.commit()

    async def _fake_compare(*args, **kwargs):
        from app.services.model_compare import cross_model_stats
        items = [
            {"config_id": llm_config.id, "config_name": llm_config.name, "model": llm_config.model, "success": True, "issues": [], "total_issues": 0, "error": None, "elapsed_ms": 10},
            {"config_id": cfg2.id, "config_name": cfg2.name, "model": cfg2.model, "success": False, "issues": [], "total_issues": 0, "error": "timeout", "elapsed_ms": 10},
        ]
        consensus, only_in = cross_model_stats(items)
        return items, consensus, only_in

    with patch("app.services.model_compare.run_proofread_compare", side_effect=_fake_compare):
        resp = await client.post(
            "/api/v1/open/proofread/compare",
            json={"text": "测试文本", "config_ids": [llm_config.id, cfg2.id]},
            headers=auth,
        )
    assert resp.status_code == 200
    # 2 个模型先扣 2，失败 1 个退 1，最终为 1
    count = await redis_module.redis_client.get(_daily_key(key_obj))
    assert count is not None and int(count) == 1


# ----------------------------------------------------------------------
# 异步文档提交幂等性
# ----------------------------------------------------------------------

async def test_document_submit_idempotent(client, db, user, api_key, auth):
    """相同 Idempotency-Key 重复提交返回同一 job_id 且只投递一次 Celery。"""
    with patch("app.api.v1.open_documents.async_proofread_document.apply_async") as mock_apply:
        headers = {**auth, "Idempotency-Key": "idem-key-001"}
        file_payload = ("test.txt", io.BytesIO("这是一段测试文本。".encode()), "text/plain")

        resp1 = await client.post(
            "/api/v1/open/documents",
            data={"domain": "general"},
            files={"file": file_payload},
            headers=headers,
        )
        assert resp1.status_code == 202
        job_id_1 = resp1.json()["job_id"]
        assert mock_apply.call_count == 1

        resp2 = await client.post(
            "/api/v1/open/documents",
            data={"domain": "general"},
            files={"file": ("test.txt", io.BytesIO("这是一段测试文本。".encode()), "text/plain")},
            headers=headers,
        )
        assert resp2.status_code == 202
        job_id_2 = resp2.json()["job_id"]
        assert job_id_1 == job_id_2
        assert mock_apply.call_count == 1


async def test_document_submit_different_idempotency_key_gets_new_job(client, db, user, api_key, auth):
    """不同 Idempotency-Key 生成不同的 job_id。"""
    with patch("app.api.v1.open_documents.async_proofread_document.apply_async") as mock_apply:
        file_payload = ("test.txt", io.BytesIO("测试文本。".encode()), "text/plain")

        resp1 = await client.post(
            "/api/v1/open/documents",
            data={"domain": "general"},
            files={"file": file_payload},
            headers={**auth, "Idempotency-Key": "idem-key-a"},
        )
        assert resp1.status_code == 202
        job_id_1 = resp1.json()["job_id"]

        resp2 = await client.post(
            "/api/v1/open/documents",
            data={"domain": "general"},
            files={"file": file_payload},
            headers={**auth, "Idempotency-Key": "idem-key-b"},
        )
        assert resp2.status_code == 202
        job_id_2 = resp2.json()["job_id"]

        assert job_id_1 != job_id_2
        assert mock_apply.call_count == 2


# ----------------------------------------------------------------------
# Celery 任务重试与最终失败退款
# ----------------------------------------------------------------------

async def test_celery_task_transient_error_marks_retrying(client, db, user, api_key):
    """瞬态错误使任务状态变为 RETRYING，等待 Celery 自动重试。"""
    from app.models.proofread_task import ProofreadTask
    from app.models.uploaded_document import UploadedDocument
    from app.tasks.proofread_task import ProofreadRetryableError, async_proofread_document

    _, key_obj = api_key
    file_id = str(_uuid.uuid4())
    doc = UploadedDocument(
        file_id=file_id,
        filename="test.txt",
        file_ext=".txt",
        file_size=10,
        file_path="/tmp/test.txt",
        text_length=4,
        extracted_text="测试文本",
        user_id=user.id,
        username=user.username,
        owner_kind="user",
        status="uploaded",
    )
    task = ProofreadTask(
        task_id=str(_uuid.uuid4()),
        document_id=file_id,
        owner_kind="api_key",
        owner_user_id=user.id,
        owner_api_key_id=key_obj.id,
        status="PENDING",
        params_json={"domain": "general"},
    )
    db.add_all([doc, task])
    await db.commit()

    with patch(
        "app.services.proofread.proofread_text",
        side_effect=RuntimeError("大模型调用失败: timeout"),
    ):
        with pytest.raises(ProofreadRetryableError):
            async_proofread_document.run(task.id)

    async with async_session_factory() as fresh_db:
        result = await fresh_db.execute(select(ProofreadTask).where(ProofreadTask.id == task.id))
        db_task = result.scalar_one()
    assert db_task.status == "RETRYING"
    assert db_task.error_code == "PROOFREAD_RETRYABLE"


async def test_celery_task_on_failure_refunds_once(client, db, user, api_key):
    """任务最终失败时 on_failure 只退一次密钥日配额。"""
    from app.models.proofread_task import ProofreadTask
    from app.models.uploaded_document import UploadedDocument
    from app.tasks.proofread_task import async_proofread_document

    _, key_obj = api_key
    refund_mock = MagicMock()

    file_id = str(_uuid.uuid4())
    doc = UploadedDocument(
        file_id=file_id,
        filename="test.txt",
        file_ext=".txt",
        file_size=10,
        file_path="/tmp/test.txt",
        text_length=4,
        extracted_text="测试文本",
        user_id=user.id,
        username=user.username,
        owner_kind="user",
        status="uploaded",
    )
    task = ProofreadTask(
        task_id=str(_uuid.uuid4()),
        document_id=file_id,
        owner_kind="api_key",
        owner_user_id=user.id,
        owner_api_key_id=key_obj.id,
        status="RETRYING",
        error_code="PROOFREAD_RETRYABLE",
        params_json={"domain": "general"},
    )
    db.add_all([doc, task])
    await db.commit()

    with patch("app.tasks.proofread_task._refund_key_daily_quota", refund_mock):
        async_proofread_document.on_failure(
            exc=RuntimeError("大模型调用失败: timeout"),
            task_id=str(_uuid.uuid4()),
            args=(task.id,),
            kwargs={},
            einfo=None,
        )

    async with async_session_factory() as fresh_db:
        result = await fresh_db.execute(select(ProofreadTask).where(ProofreadTask.id == task.id))
        db_task = result.scalar_one()
    assert db_task.status == "FAILURE"
    assert db_task.error_code == "PROOFREAD_RETRYABLE"
    refund_mock.assert_called_once_with(key_obj.id)


async def test_celery_task_on_failure_no_refund_for_invalid_config(client, db, user, api_key):
    """on_failure 遇到 INVALID_CONFIG 时不触发退款。"""
    from app.models.proofread_task import ProofreadTask
    from app.models.uploaded_document import UploadedDocument
    from app.tasks.proofread_task import async_proofread_document

    _, key_obj = api_key
    refund_mock = MagicMock()

    file_id = str(_uuid.uuid4())
    doc = UploadedDocument(
        file_id=file_id,
        filename="test.txt",
        file_ext=".txt",
        file_size=10,
        file_path="/tmp/test.txt",
        text_length=4,
        extracted_text="测试文本",
        user_id=user.id,
        username=user.username,
        owner_kind="user",
        status="uploaded",
    )
    task = ProofreadTask(
        task_id=str(_uuid.uuid4()),
        document_id=file_id,
        owner_kind="api_key",
        owner_user_id=user.id,
        owner_api_key_id=key_obj.id,
        status="FAILURE",
        error_code="INVALID_CONFIG",
        params_json={"domain": "general", "config_id": 999},
    )
    db.add_all([doc, task])
    await db.commit()

    with patch("app.tasks.proofread_task._refund_key_daily_quota", refund_mock):
        async_proofread_document.on_failure(
            exc=RuntimeError("指定的模型配置不存在或已停用 (id=999)"),
            task_id=str(_uuid.uuid4()),
            args=(task.id,),
            kwargs={},
            einfo=None,
        )

    refund_mock.assert_not_called()
