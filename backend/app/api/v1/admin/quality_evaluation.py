"""管理员手动评测入口；主路由负责挂载，不自动/后台运行。"""
import asyncio
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_permission, security_scheme
from app.core.redis import get_redis
from app.schemas.quality_evaluation import EvaluationRequest, FeedbackEvaluation
from app.services.quality_evaluation import REQUEST_TIMEOUT_SECONDS, prepare_evaluation, run_evaluation


async def _require_credentials(credentials=Depends(security_scheme)):
    # 评测不开放游客入口；保留 require_permission 的真实权限检查。
    if credentials is None:
        raise HTTPException(403, "仅有词库管理权限的管理员可运行评测。")


router = APIRouter(prefix="/global-dict/quality-feedback", tags=["质量评测"],
                   dependencies=[Depends(_require_credentials)])


async def _release_lock(redis, key, token):
    try:
        async with asyncio.timeout(2):
            # WATCH + MULTI 的 compare-delete 避免旧请求删除 TTL 后新请求的锁。
            async with redis.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                value = await pipe.get(key)
                if value in (token, token.encode()):
                    pipe.multi()
                    pipe.delete(key)
                    await pipe.execute()
    except Exception:
        # Redis 不可用时交由 TTL 回收，绝不无条件删除他人锁。
        pass


@router.post("/evaluate", response_model=FeedbackEvaluation,
             summary="评测已确认样例（会产生正常模型调用费用，不扣用户配额）")
async def evaluate_quality_feedback(
    data: EvaluationRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin:global_dict:edit")),
):
    deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
    key = f"textmirror:quality_evaluation:{user.id}"
    token = uuid.uuid4().hex
    redis = None
    acquired = False
    try:
        try:
            async with asyncio.timeout(3):
                redis = get_redis()
                acquired = bool(await redis.set(key, token, nx=True, ex=300))
        except Exception:
            raise HTTPException(503, "评测暂不可用，请稍后重试。") from None
        if not acquired:
            raise HTTPException(409, "已有评测正在运行，请勿重复点击。")
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                snapshots, models = await prepare_evaluation(db, data.feedback_ids, data.config_ids)
        finally:
            # 包含权限查询的事务也在这里释放，等待模型期间不占用数据库连接。
            await db.rollback()
            await db.close()
        return await run_evaluation(snapshots, models, deadline=deadline)
    except HTTPException:
        raise
    except TimeoutError:
        raise HTTPException(504, "评测超时，请减少样例后重试。") from None
    except Exception:
        raise HTTPException(503, "评测暂不可用，请稍后重试。") from None
    finally:
        if acquired:
            await _release_lock(redis, key, token)
