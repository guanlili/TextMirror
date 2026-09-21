"""HTTP/record/billing propagation for coverage using SQLite, fakeredis and fake LLMs."""
import json
import uuid
from types import SimpleNamespace

import fakeredis
import pytest
from sqlalchemy import func, select, update

from app.core.database import async_session_factory
from app.core.rate_limit import _api_key_daily_redis_key, _daily_key
from app.core.redis import get_redis
from app.core.security import create_access_token, hash_api_key
from app.models.api_key import ApiKey
from app.models.llm_config import LLMConfig
from app.models.proofread import ProofreadRecord
from app.models.proofread_task import ProofreadTask
from app.models.role import Role
from app.models.user import User
from app.services import proofread as service

TEXT = ("😀错词" + "甲" * 797) * 14


@pytest.fixture
async def account(client):
    async with async_session_factory() as db:
        role = Role(name="coverage test", code=f"coverage-{uuid.uuid4().hex}")
        db.add(role)
        await db.flush()
        user = User(employee_id=uuid.uuid4().hex, username="coverage", password_hash="unused",
                    role_id=role.id, daily_quota=20, is_active=True)
        db.add(user)
        await db.flush()
        token = f"tm_{uuid.uuid4().hex}"
        key = ApiKey(user_id=user.id, name="coverage", key_hash=hash_api_key(token),
                     key_prefix=token[:13], key_suffix=token[-4:], is_active=True)
        db.add(key)
        await db.execute(update(LLMConfig).values(is_active=False))
        configs = [LLMConfig(name=f"coverage-{i}-{uuid.uuid4().hex}", provider="openai",
                             model=f"fake-{i}", api_base="https://invalid.test", api_key="fake",
                             timeout=60, max_retries=0, temperature=0.2, is_enabled=True, is_active=(i == 0))
                   for i in range(2)]
        db.add_all(configs)
        await db.commit()
        return SimpleNamespace(user=user, key=key, configs=configs,
                               web_headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
                               open_headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def fake_llm(monkeypatch):
    state = {"mode": "partial", "calls": 0, "contexts": []}

    class Provider:
        def __init__(self, model, timeout, **kwargs):
            self.model = model
            self.timeout = timeout
            self.index = 0

        async def chat(self, messages, **kwargs):
            index = self.index
            self.index += 1
            state["calls"] += 1
            state["contexts"].append(messages[-1]["content"])
            if state["mode"] == "failed" or (state["mode"] == "partial" and self.model == "fake-0" and index == 1):
                raise RuntimeError("secret-provider-url-and-key")
            issues = []
            if index == 0 and "错词" in messages[-1]["content"]:
                issues = [{"o": "错词", "s": "对词", "t": "typo", "sv": "warning", "e": "测试"}]
            return SimpleNamespace(content=json.dumps(issues), usage={"total_tokens": 5})

        async def close(self):
            pass

    monkeypatch.setattr(service, "OpenAICompatProvider", Provider)
    monkeypatch.setattr("app.services.proofread.provider.OpenAICompatProvider", Provider)
    from app.tasks import proofread_task
    monkeypatch.setattr(proofread_task, "_get_sync_redis", lambda: fakeredis.FakeRedis(decode_responses=True))
    return state


def endpoint(kind, compare=False):
    if kind == "web":
        return "/api/v1/proofread/compare" if compare else "/api/v1/proofread/text"
    return "/api/v1/open/proofread/compare" if compare else "/api/v1/open/proofread"


async def records_for(account):
    async with async_session_factory() as db:
        return (await db.execute(select(ProofreadRecord).where(
            ProofreadRecord.user_id == account.user.id,
        ).order_by(ProofreadRecord.id))).scalars().all()


@pytest.mark.parametrize("kind", ["web", "open"])
async def test_text_http_record_and_paid_retry_preserve_full_source(client, account, fake_llm, kind):
    async with async_session_factory() as db:
        await db.execute(update(User).where(User.id == account.user.id).values(daily_quota=2))
        await db.commit()
    headers = getattr(account, f"{kind}_headers")
    response = await client.post(endpoint(kind), headers=headers, json={"text": TEXT, "domain": "auto"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["coverage"]["status"] == "partial"
    assert result["coverage"]["total_chunks"] == 14
    assert result["coverage"]["completed_chunks"] == 13
    assert result["config_id"] == account.configs[0].id
    assert result["domain"] == "general" and result["depth"] == "standard"
    assert result["issues"][0]["start"] == 1 and result["issues"][0]["end"] == 3
    failed = result["coverage"]["failed_chunks"][0]
    assert failed == {"chunk_index": 1, "start": 700, "end": 1600,
                      "text": TEXT[700:1600], "error_code": "MODEL_ERROR"}
    assert "secret-provider" not in response.text
    record = (await records_for(account))[0]
    assert record.original_text == TEXT and len(record.original_text) > 10000
    assert record.result["coverage"] == result["coverage"]
    assert record.result["config_id"] == result["config_id"]
    assert record.result["depth"] == result["depth"]
    assert record.result["issues"][0]["start"] == 1
    assert record.domain == "general"

    # 补查仅调用原有文本端点，每次仍按一次普通配额；坐标由调用者映回全文。
    fake_llm["mode"] = "complete"
    retry = await client.post(endpoint(kind), headers=headers, json={
        "text": failed["text"], "config_id": result["config_id"], "depth": result["depth"],
    })
    assert retry.status_code == 200, retry.text
    assert retry.json()["coverage"]["status"] == "complete"
    local_issue = retry.json()["issues"][0]
    assert local_issue["start"] == 101
    assert TEXT[failed["start"] + local_issue["start"]:failed["start"] + local_issue["end"]] == "错词"
    stored = await records_for(account)
    assert len(stored) == 2
    assert stored[0].result["coverage"] == result["coverage"]  # 服务端没有替换旧结果/审阅状态
    calls = fake_llm["calls"]
    blocked = await client.post(endpoint(kind), headers=headers, json={"text": failed["text"]})
    assert blocked.status_code == 429
    assert fake_llm["calls"] == calls
    redis = get_redis()
    assert int(await redis.get(_daily_key("user_daily", str(account.user.id)))) == 2
    if kind == "open":
        assert int(await redis.get(_api_key_daily_redis_key(account.key))) == 2


@pytest.mark.parametrize("kind", ["web", "open"])
async def test_compare_http_and_records_keep_all_per_model_metadata(client, account, fake_llm, kind):
    headers = getattr(account, f"{kind}_headers")
    response = await client.post(endpoint(kind, compare=True), headers=headers, json={
        "text": TEXT, "domain": "auto", "config_ids": [c.id for c in account.configs],
    })
    assert response.status_code == 200, response.text
    result = response.json()
    partial, complete = result["results"]
    assert partial["success"] is True and partial["complete"] is False
    assert partial["coverage"]["status"] == "partial"
    assert complete["success"] is True and complete["complete"] is True
    assert complete["coverage"]["status"] == "complete"
    assert partial["config_id"] == account.configs[0].id
    assert partial["issues"][0]["start"] == 1 and complete["issues"][0]["end"] == 3
    assert partial["domain"] == "general" and partial["depth"] == "standard"
    assert partial["usage"]["total_tokens"] == 65 and partial["chunks_count"] == 14
    assert result["consensus_originals"] == ["错词"]
    record = (await records_for(account))[0]
    assert result["record_id"] == record.id
    assert record.user_id == account.user.id
    assert record.api_key_id == (account.key.id if kind == "open" else None)
    assert record.review_state is None
    review = await client.get(f"/api/v1/history/{record.id}/review", headers=account.web_headers)
    assert review.status_code == 200, review.text
    assert [item["config_id"] for item in review.json()["compare"]["results"]] == [c.id for c in account.configs]
    assert review.json()["compare"]["results"][0]["coverage"] == partial["coverage"]
    assert record.original_text == TEXT
    assert record.quota_weight == 2  # partial 仍成功并正常计费，不当作完整模型。
    assert record.result["results"] == result["results"]
    assert record.result["models"] == [c.name for c in account.configs]
    assert record.result["domain"] == record.domain == "general"
    assert record.result["consensus_originals"] == ["错词"]
    assert record.result["issues"][0]["start"] == 1
    assert len(record.result["issues"][0]["found_by"]) == 2


@pytest.mark.parametrize("mode", ["complete", "failed"])
async def test_guest_compare_never_returns_or_creates_record(client, account, fake_llm, mode):
    from app.core.dependencies import get_current_user_optional
    from app.main import app

    fake_llm["mode"] = mode
    async with async_session_factory() as db:
        before = await db.scalar(select(func.count()).select_from(ProofreadRecord))
    app.dependency_overrides[get_current_user_optional] = lambda: None
    try:
        response = await client.post(endpoint("web", compare=True), json={
            "text": "😀错词", "config_ids": [c.id for c in account.configs],
        })
    finally:
        app.dependency_overrides.pop(get_current_user_optional, None)
    assert response.status_code == 200, response.text
    assert response.json()["record_id"] is None
    assert all(item["success"] == (mode == "complete") for item in response.json()["results"])
    async with async_session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ProofreadRecord)) == before
    assert not await get_redis().keys("textmirror:user_daily:*")


@pytest.mark.parametrize("kind", ["web", "open"])
@pytest.mark.parametrize("explicit_config", [True, False])
async def test_all_chunks_failed_refunds_even_with_explicit_config(client, account, fake_llm, kind, explicit_config):
    fake_llm["mode"] = "failed"
    body = {"text": TEXT}
    if explicit_config:
        body["config_id"] = account.configs[0].id
    response = await client.post(endpoint(kind), headers=getattr(account, f"{kind}_headers"), json=body)
    assert response.status_code == 503, response.text
    assert "secret-provider" not in response.text
    assert await records_for(account) == []
    redis = get_redis()
    assert int(await redis.get(_daily_key("user_daily", str(account.user.id))) or 0) == 0
    if kind == "open":
        assert int(await redis.get(_api_key_daily_redis_key(account.key)) or 0) == 0
        assert response.json()["detail"]["code"] == "MODEL_UNAVAILABLE"


@pytest.mark.parametrize("kind", ["web", "open"])
async def test_quick_http_metadata_propagates_without_chat(client, account, fake_llm, kind):
    response = await client.post(endpoint(kind), headers=getattr(account, f"{kind}_headers"),
                                 json={"text": "😀阶段:准备,执行。", "depth": "quick"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["coverage"]["status"] == "complete" and result["depth"] == "quick"
    assert result["config_id"] == account.configs[0].id
    assert result["issues"][0]["start"] is not None
    assert fake_llm["calls"] == 0
    assert (await records_for(account))[0].result["depth"] == "quick"


@pytest.mark.parametrize("kind", ["web", "open"])
async def test_compare_all_failed_retains_failure_spans_and_refunds(client, account, fake_llm, kind):
    fake_llm["mode"] = "failed"
    response = await client.post(endpoint(kind, compare=True), headers=getattr(account, f"{kind}_headers"),
                                 json={"text": TEXT, "config_ids": [c.id for c in account.configs]})
    assert response.status_code == 200, response.text
    assert response.json()["record_id"] is None
    for item in response.json()["results"]:
        assert item["success"] is False and item["complete"] is False
        assert item["coverage"]["status"] == "partial"
        assert item["coverage"]["completed_chunks"] == 0
        assert len(item["coverage"]["failed_chunks"]) == 14
        for chunk in item["coverage"]["failed_chunks"]:
            assert chunk["text"] == TEXT[chunk["start"]:chunk["end"]]
    assert "secret-provider" not in response.text
    assert await records_for(account) == []
    redis = get_redis()
    assert int(await redis.get(_daily_key("user_daily", str(account.user.id))) or 0) == 0
    if kind == "open":
        assert int(await redis.get(_api_key_daily_redis_key(account.key)) or 0) == 0


@pytest.mark.parametrize("kind", ["web", "open"])
async def test_invalid_config_keeps_existing_client_error_contract(client, account, fake_llm, kind):
    response = await client.post(endpoint(kind), headers=getattr(account, f"{kind}_headers"),
                                 json={"text": "测试", "config_id": -1})
    assert response.status_code == 400, response.text
    if kind == "open":
        assert response.json()["detail"]["code"] == "INVALID_CONFIG"
    assert fake_llm["calls"] == 0
    assert await records_for(account) == []


async def task_for(task_id):
    async with async_session_factory() as db:
        return (await db.execute(select(ProofreadTask).where(
            ProofreadTask.task_id == task_id,
        ))).scalar_one()


@pytest.mark.parametrize("depth", ["quick", "standard", "deep"])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_document_depth_reaches_service_and_record(client, account, fake_llm, depth, asynchronous):
    fake_llm["mode"] = "complete"
    text = "😀错词"
    upload = await client.post("/api/v1/document/upload", headers=account.web_headers,
                               files={"file": ("depth.txt", text.encode(), "text/plain")})
    assert upload.status_code == 200, upload.text
    path = "/api/v1/document/proofread" + ("/async" if asynchronous else "")
    response = await client.post(path, headers=account.web_headers, json={
        "file_id": upload.json()["file_id"], "depth": depth, "config_id": account.configs[0].id,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    if asynchronous:
        task = await task_for(result["task_id"])
        assert task.params_json["depth"] == depth
        assert task.status == "SUCCESS"
        assert task.output_path is None
        result = task.result_json
    assert result["depth"] == depth
    assert result["config_id"] == account.configs[0].id
    assert result["coverage"]["status"] == "complete"
    assert result["corrected_download_url"] is None
    assert fake_llm["calls"] == {"quick": 0, "standard": 1, "deep": 2}[depth]
    record = (await records_for(account))[0]
    assert record.result["depth"] == depth
    assert record.source_file_id == upload.json()["file_id"]


@pytest.mark.parametrize("auth_kind", ["web", "open"])
async def test_open_document_legacy_payload_keeps_standard_depth_and_download(client, account, fake_llm, auth_kind):
    fake_llm["mode"] = "complete"
    headers = getattr(account, f"{auth_kind}_headers")
    response = await client.post("/api/v1/open/documents", headers=headers,
                                 files={"file": ("compat.txt", "😀错词".encode(), "text/plain")})
    assert response.status_code == 202, response.text
    task = await task_for(response.json()["job_id"])
    assert "depth" not in task.params_json  # 既有开放 API 和旧排队任务不提供 depth。
    assert task.status == "SUCCESS"
    status = await client.get(response.json()["status_url"], headers=headers)
    assert status.status_code == 200, status.text
    result = status.json()["result"]
    assert result["depth"] == "standard" and result["coverage"]["status"] == "complete"
    assert "user_id" not in result
    assert task.output_path and result["corrected_download_url"]
    download = await client.get(result["corrected_download_url"])
    assert download.status_code == 200, download.text
    assert download.content.decode("utf-8-sig") == "😀对词"
    assert fake_llm["calls"] == 1


async def test_existing_authentication_is_not_bypassed(client, account, fake_llm):
    response = await client.post(endpoint("open"), json={"text": "补查文本"})
    assert response.status_code == 401
    async with async_session_factory() as db:
        await db.execute(update(User).where(User.id == account.user.id).values(is_active=False))
        await db.commit()
    for kind in ("web", "open"):
        response = await client.post(endpoint(kind), headers=getattr(account, f"{kind}_headers"),
                                     json={"text": "补查文本"})
        assert response.status_code == 403
    assert fake_llm["calls"] == 0
