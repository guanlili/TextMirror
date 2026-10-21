"""校对记录的归属解析：Web 登录用户 / 匿名游客 / 开放 API。"""
from typing import Optional


def resolve_web_owner(current_user, guest_id: Optional[str]) -> Optional[dict]:
    if current_user is not None:
        return {"user_id": current_user.id, "owner_kind": "user", "guest_id": None}
    if guest_id:
        return {"user_id": None, "owner_kind": "guest", "guest_id": guest_id}
    return None


def resolve_task_owner(db_task) -> Optional[dict]:
    if db_task.owner_kind == "guest":
        if not db_task.owner_guest_id:
            return None
        return {"user_id": None, "owner_kind": "guest", "guest_id": db_task.owner_guest_id}
    if db_task.owner_user_id is None:
        return None
    return {
        "user_id": db_task.owner_user_id,
        "owner_kind": "api_key" if db_task.owner_kind == "api_key" else "user",
        "guest_id": None,
    }
