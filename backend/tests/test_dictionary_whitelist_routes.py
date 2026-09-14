"""Web 端词库/放行词路由测试：归属隔离、批量导入、分页、软删除语义。

这些路由 #75/#77 各改过一次（配额链路/批量去 N+1），此前零测试。
"""
import uuid as _uuid

from app.core.database import async_session_factory
from app.core.security import hash_password
from app.models.role import Role
from app.models.user import User

PASSWORD = "Passw0rd!123"


async def _create_user(employee_prefix="u"):
    async with async_session_factory() as session:
        role = Role(name="角色", code=f"r_{_uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=f"{employee_prefix}_{_uuid.uuid4().hex[:8]}",
            username=f"词库用户{_uuid.uuid4().hex[:6]}",
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
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


# ========== 词库 ==========

async def test_dictionary_crud_and_entry_isolation(client):
    user = await _create_user()
    other = await _create_user("v")
    headers = await _login(client, user)
    other_headers = await _login(client, other)

    # 建库 → 加词条 → 查列表
    resp = await client.post("/api/v1/dictionary", json={"name": "测试库"}, headers=headers)
    assert resp.status_code == 201, resp.text
    dict_id = resp.json()["id"]

    resp = await client.post(
        f"/api/v1/dictionary/{dict_id}/entries",
        json={"wrong_word": "帐号", "correct_word": "账号", "remark": "形近字"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.get(f"/api/v1/dictionary/{dict_id}/entries", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # 他人访问：404（归属隔离——词库不存在于对方命名空间）
    assert (await client.get(f"/api/v1/dictionary/{dict_id}/entries", headers=other_headers)).status_code == 404
    assert (await client.post(
        f"/api/v1/dictionary/{dict_id}/entries",
        json={"wrong_word": "x", "correct_word": "y"},
        headers=other_headers,
    )).status_code == 404


async def test_dictionary_batch_import_and_dedup_on_reload(client):
    """批量导入（上限/计数）与列表分页：page=2 只回第二页"""
    user = await _create_user()
    headers = await _login(client, user)
    dict_id = (await client.post("/api/v1/dictionary", json={"name": "批量库"}, headers=headers)).json()["id"]

    entries = [{"wrong_word": f"错{i}", "correct_word": f"对{i}"} for i in range(30)]
    resp = await client.post(
        f"/api/v1/dictionary/{dict_id}/entries/batch",
        json={"entries": entries}, headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["count"] == 30

    # page_size=20 → 第 2 页 10 条
    resp = await client.get(
        f"/api/v1/dictionary/{dict_id}/entries",
        params={"page": 2, "page_size": 20}, headers=headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 10


async def test_dictionary_batch_over_limit_rejected(client):
    user = await _create_user()
    headers = await _login(client, user)
    dict_id = (await client.post("/api/v1/dictionary", json={"name": "超限库"}, headers=headers)).json()["id"]

    entries = [{"wrong_word": f"w{i}", "correct_word": f"c{i}"} for i in range(1001)]
    resp = await client.post(
        f"/api/v1/dictionary/{dict_id}/entries/batch",
        json={"entries": entries}, headers=headers,
    )
    assert resp.status_code == 422  # schema max_length=1000


# ========== 放行词 ==========

async def test_whitelist_crud_and_search(client):
    user = await _create_user()
    headers = await _login(client, user)

    resp = await client.post(
        "/api/v1/whitelist", json={"word": "OSPF", "type": "permanent"}, headers=headers,
    )
    assert resp.status_code == 201, resp.text
    word_id = resp.json()["id"]

    # 重复添加拒绝
    assert (await client.post(
        "/api/v1/whitelist", json={"word": "OSPF"}, headers=headers,
    )).status_code == 400

    # 关键词搜索命中
    resp = await client.get("/api/v1/whitelist", params={"keyword": "OSPF"}, headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # 删除后 204，列表空
    assert (await client.delete(f"/api/v1/whitelist/{word_id}", headers=headers)).status_code == 204
    assert len((await client.get("/api/v1/whitelist", headers=headers)).json()) == 0


async def test_whitelist_batch_dedupes_within_request(client):
    """#77 行为：请求内重复词只加一条 + 已存在词跳过（一次 IN 查回）"""
    user = await _create_user()
    headers = await _login(client, user)

    resp = await client.post(
        "/api/v1/whitelist/batch",
        json=[{"word": "GIS"}, {"word": "GIS"}, {"word": "SF6"}],
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["count"] == 2  # 请求内 GIS 去重

    # 再导入含已存在词的批次：只新增新词
    resp = await client.post(
        "/api/v1/whitelist/batch",
        json=[{"word": "GIS"}, {"word": "SNMP"}],
        headers=headers,
    )
    assert resp.json()["count"] == 1


async def test_whitelist_batch_over_limit_rejected(client):
    user = await _create_user()
    headers = await _login(client, user)
    words = [{"word": f"w{i}"} for i in range(1001)]
    resp = await client.post("/api/v1/whitelist/batch", json=words, headers=headers)
    assert resp.status_code == 400
    assert "1000" in resp.json()["detail"]
