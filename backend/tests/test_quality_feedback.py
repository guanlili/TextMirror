"""独立质量闭环：SQLite/fakeredis，真实权限依赖，无 LLM/配额/生产服务。"""
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core import redis as redis_module
from app.core.database import async_session_factory
from app.core.dependencies import get_current_user, get_current_user_optional
from app.main import app
from app.models.issue_feedback import IssueFeedback
from app.models.llm_config import LLMConfig
from app.models.proofread import ProofreadRecord
from app.models.quality_feedback import QualityFeedback
from app.models.role import Permission, Role, RolePermission
from app.models.user import User
from app.schemas.quality_feedback import FeedbackReviewRequest, QualityFeedbackCreate
from app.services.quality_feedback import create_quality_feedback, load_confirmed_samples, review_quality_feedback

POST = "/api/v1/proofread/quality-feedback"
ADMIN = "/api/v1/admin/global-dict/quality-feedback"
SOURCE = "甲😀错词，乙错词。尾"
SAFE_MODEL_FIELDS = {"config_id", "config_name", "model", "success", "coverage_status", "reported"}


def issue(**changes):
    return {"start": 6, "end": 8, "original": "错词", "suggestion": "正词", "type": "typo", **changes}


def payload(record, **changes):
    return {"record_id": record.id, "kind": "false_positive", "start": 6, "end": 8,
            "original": "错词", "suggestion": "正词", "issue_type": "typo", "note": "保留原表达", **changes}


def sample(**changes):
    # 独立脱敏样例，位置不沿用原记录；其它同文位置不被标记为 no_report。
    return {"text": "😀匿名错词和错词", "domain": "legal", "start": 3, "end": 5,
            "original": "错词", "expectation": "no_report", "issue_type": "typo",
            "accepted_suggestions": [], "rejected_suggestions": [], **changes}


def review(**changes):
    return {"revision": 0, "status": "confirmed", "sample": sample(), "review_note": "人工核验，已脱敏", **changes}


def model(config_id, **changes):
    return {"config_id": config_id, "config_name": f"配置{config_id}", "model": f"model-{config_id}",
            "success": True, "issues": [], "coverage": {"status": "complete"}, **changes}


@pytest.fixture
async def actors(client):
    async with async_session_factory() as db:
        users = []
        for name in ("owner", "reviewer", "stranger"):
            role = Role(name=name, code=uuid.uuid4().hex)
            db.add(role)
            await db.flush()
            user = User(employee_id=uuid.uuid4().hex, username=name, password_hash="unused", role_id=role.id)
            db.add(user)
            users.append(user)
            if name == "reviewer":
                permission = await db.scalar(select(Permission).where(Permission.code == "admin:global_dict:edit"))
                if permission is None:
                    permission = Permission(name="编辑全局词库", code="admin:global_dict:edit", type="button")
                    db.add(permission)
                    await db.flush()
                db.add(RolePermission(role_id=role.id, permission_id=permission.id))
        await db.commit()
    state = SimpleNamespace(owner=users[0], reviewer=users[1], stranger=users[2], current=users[0])
    app.dependency_overrides[get_current_user] = lambda: state.current
    app.dependency_overrides[get_current_user_optional] = lambda: state.current
    yield state
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_user_optional, None)


async def record_for(owner, **changes):
    values = {"user_id": owner.id, "type": "text", "original_text": SOURCE, "domain": "official",
              "result": {"issues": [issue()], "config_id": 987654321, "coverage": {"status": "complete"}},
              **changes}
    async with async_session_factory() as db:
        record = ProofreadRecord(**values)
        db.add(record)
        await db.commit()
        return record


async def submit(client, record, **changes):
    response = await client.post(POST, json=payload(record, **changes))
    assert response.status_code == 200, response.text
    return response.json()


async def test_pending_unicode_idempotency_and_no_source_or_quota_changes(client, actors, monkeypatch):
    from app.api.v1 import proofread as proofread_api

    no_llm = AsyncMock(side_effect=AssertionError("quality feedback must not call LLM"))
    no_charge = AsyncMock(side_effect=AssertionError("quality feedback must not charge quota"))
    monkeypatch.setattr(proofread_api, "proofread_text", no_llm)
    monkeypatch.setattr(proofread_api, "charge_user_daily_quota", no_charge)
    record = await record_for(actors.owner, review_state={"issues": [{**issue(), "_ignored": True}]}, review_revision=3,
                              modified_text="已有成稿")
    created = await submit(client, record)
    assert created["status"] == "pending" and created["revision"] == 0
    assert created["sample"] is None and created["reviewer_id"] is None and created["reviewed_at"] is None
    assert created["context_text"] == SOURCE and created["context_start"] == 0
    assert created["domain"] == "official" and created["record_id"] == record.id
    assert set(created["model_snapshot"][0]) == SAFE_MODEL_FIELDS
    assert created["model_snapshot"][0]["reported"] is True
    assert "dedupe_key" not in created and "result" not in created
    duplicate = await submit(client, record, note="重复上报不覆盖")
    assert duplicate == created
    async with async_session_factory() as db:
        row = await db.get(ProofreadRecord, record.id)
        assert (row.original_text, row.result, row.review_state, row.review_revision, row.modified_text, row.quota_weight) == (
            SOURCE, record.result, record.review_state, 3, "已有成稿", 1,
        )
        assert await db.scalar(select(func.count()).select_from(QualityFeedback).where(QualityFeedback.record_id == record.id)) == 1
        assert await db.scalar(select(func.count()).select_from(IssueFeedback).where(IssueFeedback.user_id == actors.owner.id)) == 0
        assert created["id"] not in [entry["id"] for entry in await load_confirmed_samples(db)]
        with pytest.raises(HTTPException) as error:
            await load_confirmed_samples(db, [created["id"]])
        assert error.value.status_code == 422
    no_llm.assert_not_awaited()
    no_charge.assert_not_awaited()
    assert not await redis_module.redis_client.keys("textmirror:user_daily:*")


async def test_login_rbac_and_ownership(client, actors):
    record = await record_for(actors.owner)
    item = await submit(client, record)
    assert (await client.get(ADMIN)).status_code == 403
    assert (await client.put(f"{ADMIN}/{item['id']}", json=review())).status_code == 403
    actors.current = actors.stranger
    assert (await client.post(POST, json=payload(record))).status_code == 404
    for overrides in ({"user_id": None}, {"type": "polish"}):
        unsupported = await record_for(actors.stranger, **overrides)
        assert (await client.post(POST, json=payload(unsupported))).status_code == 404
    assert (await client.post(POST, json=payload(record, record_id=2147483647))).status_code == 404
    actors.current = actors.reviewer
    assert (await client.get(ADMIN)).status_code == 200
    assert (await client.put(f"{ADMIN}/{item['id']}", json=review())).status_code == 200
    app.dependency_overrides.pop(get_current_user)
    assert (await client.post(POST, json=payload(record))).status_code == 401
    assert (await client.get(ADMIN)).status_code == 401
    assert (await client.put(f"{ADMIN}/{item['id']}", json=review())).status_code == 401


@pytest.mark.parametrize("changes", [
    {"start": -1}, {"start": 6.0}, {"start": True}, {"start": "6"}, {"start": None},
    {"end": 2147483648}, {"end": 6}, {"end": 99}, {"end": False}, {"end": "8"},
    {"start": 7, "end": 9}, {"start": 10, "end": 12}, {"original": "不符"},
    {"original": ""}, {"original": "甲" * 501}, {"suggestion": "甲" * 501},
    {"note": "甲" * 1001}, {"issue_type": "a" * 65}, {"issue_type": None},
    {"suggestion": None}, {"note": 1}, {"kind": "ignore"}, {"kind": True},
    {"record_id": False}, {"record_id": 1.0}, {"record_id": "1"}, {"record_id": 0},
    {"record_id": 2147483648}, {"model_id": 7}, {"status": "confirmed"}, {"sample": sample()},
])
async def test_create_strict_boundaries(client, actors, changes):
    record = await record_for(actors.owner)
    response = await client.post(POST, json=payload(record, **changes))
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("kind", ["false_positive", "bad_suggestion", "preference", "other", "missed"])
async def test_kinds_and_maximum_context_bounds(client, actors, kind):
    source = "外" * 350 + "😀" * 500 + "后" * 350
    target = issue(start=350, end=850, original="😀" * 500, suggestion="新" * 500, type="t" * 64)
    record = await record_for(actors.owner, type="document", original_text=source, domain="auto", result={"issues": [target]})
    created = await submit(client, record, kind=kind, start=350, end=850, original=target["original"],
                           suggestion=target["suggestion"], issue_type=target["type"], note="注" * 1000)
    assert len(created["context_text"]) == 900
    assert created["context_start"] == 150 and created["context_text"] == source[150:1050]
    assert created["domain"] == "general"


@pytest.mark.parametrize("changes", [
    {"start": 2, "end": 4}, {"suggestion": "另一建议"}, {"issue_type": "grammar"},
])
async def test_exact_original_report_not_another_occurrence(client, actors, changes):
    record = await record_for(actors.owner)
    response = await client.post(POST, json=payload(record, **changes))
    assert response.status_code == 422 and "先保存草稿" in response.text
    missed = await submit(client, record, kind="missed", **changes)
    assert missed["kind"] == "missed"


@pytest.mark.parametrize("changes,expected", [
    ({}, True),
    ({"start": 2, "end": 4}, False),
    ({"original": "乙错词", "start": 5, "end": 8}, True),
    ({"original": "词", "start": 7, "end": 8}, True),
    ({"type": "grammar"}, False),
    ({"start": None, "end": None}, None),
])
async def test_missed_snapshot_uses_target_not_user_suggestion(client, actors, changes, expected):
    record = await record_for(actors.owner, result={"issues": [issue(**changes)], "coverage": {"status": "complete"}})
    created = await submit(client, record, kind="missed", suggestion="")
    assert created["model_snapshot"][0]["reported"] is expected


async def test_missing_report_and_saved_draft_without_trusting_config_id(client, actors):
    record = await record_for(actors.owner, result={"config_id": 42, "issues": [], "coverage": {"status": "complete"}})
    assert (await client.post(POST, json=payload(record))).status_code == 422
    async with async_session_factory() as db:
        row = await db.get(ProofreadRecord, record.id)
        row.review_state = {"config_id": 999, "issues": [issue()]}
        await db.commit()
    created = await submit(client, record)
    assert created["model_snapshot"][0]["config_id"] == 42
    assert created["model_snapshot"][0]["reported"] is None
    absent = await record_for(actors.owner, result=None)
    missed = await submit(client, absent, kind="missed", issue_type="", suggestion="", note="")
    assert missed["model_snapshot"][0]["reported"] is None
    assert missed["model_snapshot"][0]["success"] is None


async def test_single_model_safe_identity_and_deleted_config(client, actors):
    async with async_session_factory() as db:
        config = LLMConfig(name=uuid.uuid4().hex, model="safe-model", provider="test", api_base="https://unused.invalid",
                           api_key="TEST-ONLY-SECRET-NOT-A-CREDENTIAL", remark="not for snapshot")
        db.add(config)
        await db.commit()
    record = await record_for(actors.owner, result={"config_id": config.id, "issues": [issue()],
                                                   "api_key": "TEST-ONLY-SECRET-NOT-A-CREDENTIAL", "error": "raw failure"})
    created = await submit(client, record)
    assert created["model_snapshot"] == [{"config_id": config.id, "config_name": config.name, "model": "safe-model",
                                          "success": True, "coverage_status": "unknown", "reported": True}]
    assert "SECRET" not in json.dumps(created) and "raw failure" not in json.dumps(created)
    async with async_session_factory() as db:
        await db.delete(await db.get(LLMConfig, config.id))
        await db.commit()
    new_kind = await submit(client, record, kind="bad_suggestion")
    assert new_kind["model_snapshot"][0]["config_id"] == config.id
    assert new_kind["model_snapshot"][0]["model"] == ""
    # 删除配置不会反向修改已保存的快照。
    assert (await submit(client, record)) == created


async def test_multi_model_safe_identity_saved_reports_and_unknown_negatives(client, actors):
    reports = [
        model(1, issues=[issue()]), model(2), model(3, success=False, error="raw secret traceback", issues=[issue()]),
        model(4, coverage={"status": "partial", "failed_chunks": [{"text": "private chunk"}]}),
        model(5, coverage=None), model(6), model(7, issues=[issue(start=None, end=None)]),
    ]
    saved = [model(1, issues=[issue()]), model(6, config_name="untrusted rename", model="untrusted model", issues=[issue()])]
    record = await record_for(actors.owner, result={"compare": True, "results": reports, "issues": [issue()]},
                              review_state={"issues": [issue()], "compare": {"results": saved}})
    created = await submit(client, record)
    snapshots = created["model_snapshot"]
    assert [entry["reported"] for entry in snapshots] == [True, False, None, None, None, True, None]
    assert snapshots[2]["success"] is False and snapshots[3]["coverage_status"] == "partial"
    assert snapshots[5]["config_name"] == "配置6" and snapshots[5]["model"] == "model-6"
    assert all(set(entry) == SAFE_MODEL_FIELDS for entry in snapshots)
    assert "raw secret" not in json.dumps(created) and "private chunk" not in json.dumps(created)


@pytest.mark.parametrize("saved", [False, True])
async def test_per_model_only_reports_are_valid_provenance(client, actors, saved):
    originals = [model(1, issues=[] if saved else [issue()]), model(2)]
    state = {"compare": {"results": [model(1, issues=[issue()]), model(2)]}} if saved else None
    record = await record_for(actors.owner, result={"compare": True, "results": originals}, review_state=state)
    created = await submit(client, record)
    assert created["model_snapshot"][0]["reported"] is True


async def test_failed_only_report_rejected_and_legacy_compare_unknown(client, actors):
    record = await record_for(actors.owner, result={"compare": True, "results": [model(1, success=False, issues=[issue()])]})
    assert (await client.post(POST, json=payload(record))).status_code == 422
    legacy = await record_for(actors.owner, result={"compare": True, "models": ["旧模型"], "issues": [issue()]})
    created = await submit(client, legacy)
    assert created["model_snapshot"] == [{"config_id": None, "config_name": "旧模型", "model": "", "success": None,
                                          "coverage_status": "unknown", "reported": None}]


async def test_confirm_reject_rereview_cas_and_confirmed_loader(client, actors):
    record = await record_for(actors.owner)
    first = await submit(client, record)
    second = await submit(client, record, kind="bad_suggestion")
    actors.current = actors.reviewer
    response = await client.put(f"{ADMIN}/{first['id']}", json=review())
    assert response.status_code == 200, response.text
    confirmed = response.json()
    assert confirmed["sample"] == sample() and confirmed["revision"] == 1
    assert confirmed["reviewer_id"] == actors.reviewer.id and confirmed["review_note"] == review()["review_note"]
    reviewed_at = datetime.fromisoformat(confirmed["reviewed_at"].replace("Z", "+00:00"))
    assert reviewed_at.utcoffset().total_seconds() == 0
    assert 0 <= (datetime.now(timezone.utc) - reviewed_at).total_seconds() < 5
    assert (await client.put(f"{ADMIN}/{first['id']}", json=review(status="rejected", sample=None))).status_code == 409
    assert (await client.put(f"{ADMIN}/2147483647", json=review())).status_code == 404
    assert (await client.put(f"{ADMIN}/{second['id']}", json=review(sample=sample(expectation="report")))).status_code == 200
    async with async_session_factory() as db:
        entries = await load_confirmed_samples(db, [second["id"], first["id"]])
        assert [entry["id"] for entry in entries] == [second["id"], first["id"]]
        assert entries[1] == {"id": first["id"], "revision": 1, "sample": sample()}
        assert [entry["id"] for entry in await load_confirmed_samples(db)] == sorted(entry["id"] for entry in await load_confirmed_samples(db))
    rejected = await client.put(f"{ADMIN}/{first['id']}", json=review(revision=1, status="rejected", sample=None))
    assert rejected.status_code == 200 and rejected.json()["revision"] == 2 and rejected.json()["sample"] is None
    async with async_session_factory() as db:
        with pytest.raises(HTTPException) as error:
            await load_confirmed_samples(db, [second["id"], first["id"]])
        assert error.value.status_code == 422
    reconfirmed = await client.put(f"{ADMIN}/{first['id']}", json=review(revision=2, sample=sample(issue_type=None)))
    assert reconfirmed.status_code == 200 and reconfirmed.json()["revision"] == 3
    assert reconfirmed.json()["sample"]["issue_type"] == ""
    actors.current = actors.owner
    assert await submit(client, record, note="重复不修改已审核内容") == reconfirmed.json()


@pytest.mark.parametrize("changes", [
    {"status": "pending"}, {"status": "confirmed", "sample": None}, {"status": "rejected"},
    {"revision": -1}, {"revision": True}, {"revision": 0.0}, {"revision": "0"}, {"revision": 2147483647},
    {"review_note": "注" * 1001}, {"reviewer_id": 1}, {"model_snapshot": []},
    {"sample": sample(text="")}, {"sample": sample(text="字" * 4001)},
    {"sample": sample(domain="auto")}, {"sample": sample(expectation="ignore")},
    {"sample": sample(original="")}, {"sample": sample(original="字" * 501)},
    {"sample": sample(start=True)}, {"sample": sample(end=5.0)}, {"sample": sample(start="3")},
    {"sample": sample(start=-1)}, {"sample": sample(end=3)}, {"sample": sample(end=99)},
    {"sample": sample(start=4, end=6)}, {"sample": sample(issue_type="x" * 65)},
    {"sample": sample(issue_type=7)}, {"sample": sample(whole_document=True)},
])
async def test_review_strict_schema(client, actors, changes):
    record = await record_for(actors.owner)
    item = await submit(client, record)
    actors.current = actors.reviewer
    response = await client.put(f"{ADMIN}/{item['id']}", json=review(**changes))
    assert response.status_code == 422, response.text


async def test_review_maximum_sample_bounds_and_target_only(client, actors):
    record = await record_for(actors.owner)
    item = await submit(client, record)
    actors.current = actors.reviewer
    target = sample(text="😀" * 4000, start=2000, end=2500, original="😀" * 500, issue_type="x" * 64)
    response = await client.put(f"{ADMIN}/{item['id']}", json=review(sample=target, review_note="字" * 1000))
    assert response.status_code == 200, response.text
    async with async_session_factory() as db:
        assert (await load_confirmed_samples(db, [item["id"]]))[0]["sample"] == target


async def test_database_cas_rejects_stale_session(client, actors):
    record = await record_for(actors.owner)
    item = await submit(client, record)
    async with async_session_factory() as stale, async_session_factory() as current:
        loaded = await stale.get(QualityFeedback, item["id"])
        await stale.commit()
        await review_quality_feedback(current, item["id"], actors.reviewer.id, FeedbackReviewRequest(**review()))
        await current.commit()
        assert loaded.revision == 0
        with pytest.raises(HTTPException) as error:
            await review_quality_feedback(stale, item["id"], actors.reviewer.id,
                                          FeedbackReviewRequest(**review(status="rejected", sample=None)))
        assert error.value.status_code == 409
        await stale.rollback()


@pytest.mark.parametrize("ids", [[2147483647], [True], ["1"], [1.0], [-1], [0], [2147483648]])
async def test_loader_rejects_missing_or_invalid_ids(client, ids):
    async with async_session_factory() as db:
        with pytest.raises(HTTPException) as error:
            await load_confirmed_samples(db, ids)
        assert error.value.status_code == 422
        assert await load_confirmed_samples(db, []) == []


async def test_source_deletion_retains_context_snapshot_and_reviewability(client, actors):
    record = await record_for(actors.owner)
    item = await submit(client, record)
    async with async_session_factory() as db:
        await db.execute(text("PRAGMA foreign_keys=ON"))
        await db.delete(await db.get(ProofreadRecord, record.id))
        await db.commit()
    actors.current = actors.reviewer
    response = await client.get(ADMIN, params={"status": "pending", "page_size": 50})
    retained = next(entry for entry in response.json()["items"] if entry["id"] == item["id"])
    assert retained["record_id"] is None
    for field in ("context_text", "context_start", "model_snapshot", "original", "suggestion"):
        assert retained[field] == item[field]
    assert (await client.put(f"{ADMIN}/{item['id']}", json=review())).status_code == 200
    async with async_session_factory() as db:
        assert await load_confirmed_samples(db, [item["id"]]) == [{"id": item["id"], "revision": 1, "sample": sample()}]


async def test_list_pagination_status_and_bounds(client, actors):
    record = await record_for(actors.owner)
    first = await submit(client, record)
    second = await submit(client, record, kind="other")
    actors.current = actors.reviewer
    first_page = (await client.get(ADMIN, params={"page": 1, "page_size": 1})).json()
    second_page = (await client.get(ADMIN, params={"page": 2, "page_size": 1})).json()
    assert first_page["items"][0]["id"] == second["id"] and second_page["items"][0]["id"] == first["id"]
    assert first_page["total"] == second_page["total"] and first_page["total"] >= 2
    assert (await client.put(f"{ADMIN}/{second['id']}", json=review())).status_code == 200
    filtered = (await client.get(ADMIN, params={"status": "confirmed"})).json()
    assert all(entry["status"] == "confirmed" for entry in filtered["items"])
    assert second["id"] in [entry["id"] for entry in filtered["items"]]
    for params in ({"status": "ignore"}, {"page": 0}, {"page": "true"}, {"page": "1.0"}, {"page": "1e0"},
                   {"page_size": 51}, {"page_size": 0}, {"page_size": 1.5}, {"page_size": "1.0"}):
        assert (await client.get(ADMIN, params=params)).status_code == 422, params
    for feedback_id in ("0", "-1", "1.0", "true", "2147483648"):
        assert (await client.put(f"{ADMIN}/{feedback_id}", json=review())).status_code == 422, feedback_id


async def test_unique_dedupe_constraint_and_savepoint_race_recovery(client, actors, monkeypatch):
    record = await record_for(actors.owner)
    item = await submit(client, record)
    async with async_session_factory() as db:
        existing = await db.get(QualityFeedback, item["id"])
        expected_key = hashlib.sha256(json.dumps(
            [actors.owner.id, record.id, "false_positive", 6, 8, "错词", "正词", "typo"],
            ensure_ascii=False, separators=(",", ":"),
        ).encode()).hexdigest()
        assert existing.dedupe_key == expected_key and len(existing.dedupe_key) == 64
        values = {column.name: copy.deepcopy(getattr(existing, column.name)) for column in QualityFeedback.__table__.columns
                  if column.name not in ("id", "created_at", "updated_at")}
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(QualityFeedback(**values))
                await db.flush()
        await review_quality_feedback(db, item["id"], actors.reviewer.id, FeedbackReviewRequest(**review()))
        await db.commit()
    async with async_session_factory() as db:
        # 模拟初始 lookup 与另一事务提交交错：真实唯一约束抛错，真实 SAVEPOINT 恢复。
        execute = db.execute
        skipped = False

        async def concurrent_lookup(statement, *args, **kwargs):
            nonlocal skipped
            if not skipped and getattr(statement, "column_descriptions", [{}])[0].get("entity") is QualityFeedback:
                skipped = True
                return Mock(scalar_one_or_none=lambda: None)
            return await execute(statement, *args, **kwargs)

        monkeypatch.setattr(db, "execute", concurrent_lookup)
        outer_event = IssueFeedback(user_id=actors.owner.id, record_id=record.id, original="外层事务", action="ignore")
        db.add(outer_event)
        duplicate = await create_quality_feedback(db, actors.owner.id, QualityFeedbackCreate(**payload(record, note="不覆盖")))
        assert skipped and duplicate.id == item["id"] and duplicate.revision == 1 and duplicate.sample == sample()
        assert duplicate.note == item["note"] and db.is_active
        await db.commit()
    async with async_session_factory() as db:
        assert await db.get(IssueFeedback, outer_event.id) is not None
        assert await db.scalar(select(func.count()).select_from(QualityFeedback).where(QualityFeedback.record_id == record.id)) == 1


async def test_old_accept_ignore_events_and_suggestions_unchanged(client, actors):
    record = await record_for(actors.owner)
    legacy = {"record_id": record.id, "items": [{"original": "错词", "suggestion": "正词", "issue_type": "typo", "action": "ignore"}]}
    assert (await client.post("/api/v1/proofread/feedback", json=legacy)).json() == {"saved": 1}
    async with async_session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(QualityFeedback).where(QualityFeedback.record_id == record.id)) == 0
    actors.current = actors.reviewer
    before = (await client.get("/api/v1/admin/global-dict/suggestions", params={"min_count": 1})).json()
    actors.current = actors.owner
    item = await submit(client, record, kind="preference")
    actors.current = actors.reviewer
    assert (await client.put(f"{ADMIN}/{item['id']}", json=review())).status_code == 200
    after = (await client.get("/api/v1/admin/global-dict/suggestions", params={"min_count": 1})).json()
    assert before == after
    actors.current = actors.owner
    legacy["items"][0]["action"] = "accept"
    assert (await client.post("/api/v1/proofread/feedback", json=legacy)).json() == {"saved": 1}
    async with async_session_factory() as db:
        events = (await db.execute(select(IssueFeedback).where(IssueFeedback.record_id == record.id).order_by(IssueFeedback.id))).scalars().all()
        assert [event.action for event in events] == ["ignore", "accept"]
        assert (await db.get(QualityFeedback, item["id"])).revision == 1


async def test_reject_pending_then_confirm_and_loader_refresh(client, actors):
    record = await record_for(actors.owner)
    item = await submit(client, record, kind="missed")
    actors.current = actors.reviewer
    rejected = await client.put(f"{ADMIN}/{item['id']}", json=review(status="rejected", sample=None, review_note="非模型问题"))
    assert rejected.status_code == 200 and rejected.json()["revision"] == 1
    filtered = (await client.get(ADMIN, params={"status": "rejected"})).json()
    assert item["id"] in [entry["id"] for entry in filtered["items"]]
    async with async_session_factory() as stale:
        row = await stale.get(QualityFeedback, item["id"])
        await stale.commit()
        confirmed = await client.put(f"{ADMIN}/{item['id']}", json=review(revision=1, sample=sample(expectation="report")))
        assert confirmed.status_code == 200
        assert row.status == "rejected"
        loaded = await load_confirmed_samples(stale, [item["id"], item["id"]])
        assert loaded == [{"id": item["id"], "revision": 2, "sample": sample(expectation="report")}] * 2


@pytest.mark.parametrize("field", ["accepted_suggestions", "rejected_suggestions"])
@pytest.mark.parametrize("value", [None, "正词", [None], [1], [True], ["相同", "相同"], ["", ""],
                                 [str(i) for i in range(11)], ["\U00020000" * 501]])
async def test_suggestion_constraints_strict_in_review_and_evaluation(client, actors, field, value):
    from pydantic import ValidationError

    from app.schemas.quality_evaluation import EvaluationSample

    target = sample(expectation="report", **{field: value})
    with pytest.raises(ValidationError):
        EvaluationSample.model_validate(target)
    record = await record_for(actors.owner)
    item = await submit(client, record, kind="bad_suggestion")
    actors.current = actors.reviewer
    response = await client.put(f"{ADMIN}/{item['id']}", json=review(sample=target))
    assert response.status_code == 422, response.text
    async with async_session_factory() as db:
        assert (await db.get(QualityFeedback, item["id"])).revision == 0


@pytest.mark.parametrize("changes", [
    {"expectation": "report", "accepted_suggestions": ["相同"], "rejected_suggestions": ["相同"]},
    {"expectation": "report", "accepted_suggestions": [""], "rejected_suggestions": [""]},
    {"accepted_suggestions": ["正词"]}, {"rejected_suggestions": ["坏词"]},
    {"accepted_suggestions": [""]},
])
def test_suggestion_constraints_mutually_exclusive_and_report_only(changes):
    from pydantic import ValidationError

    from app.schemas.quality_evaluation import EvaluationSample
    from app.schemas.quality_feedback import FeedbackSample

    for schema in (FeedbackSample, EvaluationSample):
        with pytest.raises(ValidationError):
            schema.model_validate(sample(**changes))


async def test_suggestion_golden_roundtrip_json_list_loader_and_rereview(client, actors):
    record = await record_for(actors.owner)
    item = await submit(client, record, kind="bad_suggestion")
    actors.current = actors.reviewer
    target = sample(expectation="report", accepted_suggestions=["", " 正词\n", "\U00020000" * 500] + [str(i) for i in range(7)],
                    rejected_suggestions=["坏词" + str(i) for i in range(10)])
    response = await client.put(f"{ADMIN}/{item['id']}", json=review(sample=target))
    assert response.status_code == 200, response.text
    assert response.json()["sample"] == target
    listing = (await client.get(ADMIN, params={"status": "confirmed", "page_size": 50})).json()
    assert next(row for row in listing["items"] if row["id"] == item["id"])["sample"] == target
    async with async_session_factory() as db:
        assert (await db.get(QualityFeedback, item["id"])).sample == target
        assert await load_confirmed_samples(db, [item["id"]]) == [{"id": item["id"], "revision": 1, "sample": target}]
    # 撤回和重新审核不得残留旧 golden。
    assert (await client.put(f"{ADMIN}/{item['id']}", json=review(revision=1, status="rejected", sample=None))).status_code == 200
    changed = sample(expectation="report", accepted_suggestions=["新词"], rejected_suggestions=["旧词"])
    assert (await client.put(f"{ADMIN}/{item['id']}", json=review(revision=2, sample=changed))).status_code == 200
    async with async_session_factory() as db:
        assert await load_confirmed_samples(db, [item["id"]]) == [{"id": item["id"], "revision": 3, "sample": changed}]


async def test_legacy_sample_defaults_and_loader_rejects_invalid_saved_golden(client, actors):
    from app.schemas.quality_feedback import FeedbackSample

    old = {key: value for key, value in sample().items() if not key.endswith("_suggestions")}
    record = await record_for(actors.owner)
    item = await submit(client, record)
    actors.current = actors.reviewer
    response = await client.put(f"{ADMIN}/{item['id']}", json=review(sample=old))
    assert response.status_code == 200 and response.json()["sample"] == sample()
    async with async_session_factory() as db:
        row = await db.get(QualityFeedback, item["id"])
        row.sample = old  # 模拟新字段加入前已经落库的 JSON。
        await db.commit()
        assert (await load_confirmed_samples(db, [item["id"]]))[0]["sample"] == sample()
        row.sample = {**old, "accepted_suggestions": ["正词"]}  # no_report 不允许带 golden。
        await db.commit()
        with pytest.raises(HTTPException) as error:
            await load_confirmed_samples(db, [item["id"]])
        assert error.value.status_code == 422
    first, second = FeedbackSample.model_validate(old), FeedbackSample.model_validate(old)
    first.accepted_suggestions.append("不共享默认数组")
    assert second.accepted_suggestions == []
