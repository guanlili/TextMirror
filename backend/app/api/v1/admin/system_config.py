"""
TextMirror 系统配置管理 API（管理后台）
包含：基本设置、飞书配置、安全设置、数据维护
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.core.redis import get_redis
from app.core.secret_crypto import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/system-config", tags=["系统配置管理"])

# Redis 键前缀
BASIC_SETTINGS_KEY = "system:config:basic"
SECURITY_SETTINGS_KEY = "system:config:security"
DOMAIN_PROMPTS_KEY = "system:config:domain_prompts"

# 可配置的审校领域（与 proofread.py 的 DOMAIN_PROMPTS 对应）
DOMAIN_CODES = ["general", "official", "legal"]


# ========== 基本设置 ==========
class BasicSettingsConfig(BaseModel):
    """系统基本设置"""
    version: str = "1.0.0"
    debug: bool = False
    allow_register: bool = False
    maintenance_mode: bool = False


@router.get("/basic", response_model=BasicSettingsConfig, summary='获取系统基本设置')
async def get_basic_settings(
    _user=Depends(require_permission("admin:settings:edit")),
):
    """获取系统基本设置"""
    redis = await get_redis()
    data = await redis.hgetall(BASIC_SETTINGS_KEY)

    if not data:
        return BasicSettingsConfig()

    return BasicSettingsConfig(
        version=data.get("version", "1.0.0"),
        debug=data.get("debug") == "1",
        allow_register=data.get("allow_register") == "1",
        maintenance_mode=data.get("maintenance_mode") == "1",
    )


@router.put("/basic", response_model=BasicSettingsConfig, summary='更新系统基本设置')
async def update_basic_settings(
    config: BasicSettingsConfig,
    _user=Depends(require_permission("admin:settings:edit")),
):
    """更新系统基本设置"""
    redis = await get_redis()
    await redis.hset(
        BASIC_SETTINGS_KEY,
        mapping={
            "version": config.version,
            "debug": "1" if config.debug else "0",
            "allow_register": "1" if config.allow_register else "0",
            "maintenance_mode": "1" if config.maintenance_mode else "0",
        }
    )
    logger.info(f"系统基本设置已更新: {config.model_dump()}")
    return config


# ========== 飞书配置 ==========
# 飞书登录运行时配置由环境变量驱动（FEISHU_ENABLED/FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_REDIRECT_URI），
# 此前后台的飞书配置表单写入 Redis 后无任何消费方（死配置），已连同端点一并移除。


# ========== 安全设置 ==========
# 掩码哨兵：GET 只回掩码，PUT 收到掩码表示「不修改」
PASSWORD_MASK = "******"


class SecuritySettingsConfig(BaseModel):
    """用户安全设置"""
    default_password: str = PASSWORD_MASK


@router.get("/security", response_model=SecuritySettingsConfig, summary='获取安全设置')
async def get_security_settings(
    _user=Depends(require_permission("admin:settings:edit")),
):
    """获取安全设置（默认密码只回掩码，不回明文）"""
    return SecuritySettingsConfig(default_password=PASSWORD_MASK)


@router.put("/security", response_model=SecuritySettingsConfig, summary='更新安全设置')
async def update_security_settings(
    config: SecuritySettingsConfig,
    _user=Depends(require_permission("admin:settings:edit")),
):
    """更新安全设置（default_password 传掩码 ****** 表示保持不变）"""
    if config.default_password == PASSWORD_MASK:
        return config

    redis = await get_redis()
    await redis.hset(
        SECURITY_SETTINGS_KEY,
        mapping={"default_password": encrypt_secret(config.default_password)}
    )

    logger.info("用户安全设置已更新")
    return SecuritySettingsConfig(default_password=PASSWORD_MASK)


async def get_current_default_password() -> str:
    """从 Redis 读取管理员配置的默认密码，回退到 settings 初始值。多 worker 安全。"""
    redis = await get_redis()
    data = await redis.hget(SECURITY_SETTINGS_KEY, "default_password")
    return decrypt_secret(data) if data else settings.DEFAULT_USER_PASSWORD


# ========== 数据维护 ==========
class MaintenanceResult(BaseModel):
    """数据维护结果"""
    success: bool
    message: str
    deleted_count: int = 0


@router.post("/maintenance/clean-logs", response_model=MaintenanceResult, summary='清理审计日志（默认保留90天）')
async def clean_logs(
    days: int = 90,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:settings:edit")),
):
    """清理审计日志（默认保留90天）"""
    from app.models.audit_log import AuditLog

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        delete(AuditLog).where(AuditLog.created_at < cutoff)
    )
    deleted = result.rowcount or 0
    await db.commit()

    logger.info(f"清理审计日志: 删除 {deleted} 条（{days}天前）")
    return MaintenanceResult(
        success=True,
        message=f"已清理 {days} 天前的审计日志",
        deleted_count=deleted,
    )


@router.post("/maintenance/clean-temp", response_model=MaintenanceResult, summary='清理临时文件')
async def clean_temp_files(
    _user=Depends(require_permission("admin:settings:edit")),
):
    """清理临时文件"""
    import os
    import shutil

    temp_dir = os.path.join(settings.UPLOAD_DIR, "temp")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)
        logger.info("临时文件已清理")
        return MaintenanceResult(success=True, message="临时文件已清理")

    return MaintenanceResult(success=True, message="无临时文件需要清理")


@router.post("/maintenance/clean-cache", response_model=MaintenanceResult, summary='清理 Redis 缓存（保留配置项）')
async def clean_cache(
    _user=Depends(require_permission("admin:settings:edit")),
):
    """清理 Redis 缓存（保留配置项）"""
    redis = await get_redis()

    # Redis 还存放配置、额度和幂等凭据，只删除可重新计算的分片缓存。
    cache_keys = [k async for k in redis.scan_iter(match="textmirror:chunk_cache:*")]

    if cache_keys:
        deleted = await redis.delete(*cache_keys)
        logger.info(f"Redis 缓存已清理: {deleted} 个键")
        return MaintenanceResult(
            success=True,
            message=f"已清理 {deleted} 个缓存项",
            deleted_count=deleted,
        )

    return MaintenanceResult(success=True, message="无缓存需要清理")


@router.post("/maintenance/clean-expired-whitelist", response_model=MaintenanceResult, summary='清理过期放行词')
async def clean_expired_whitelist(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:settings:edit")),
):
    """清理过期放行词"""
    from app.models.dictionary import WhitelistWord

    # expire_at 是 naive 列（历史 schema），用 SQL 侧 now() 比较避免 aware/naive 传参冲突
    result = await db.execute(
        delete(WhitelistWord).where(
            WhitelistWord.expire_at.isnot(None),
            WhitelistWord.expire_at < func.now(),
        )
    )
    deleted = result.rowcount or 0
    await db.commit()

    logger.info(f"清理过期放行词: 删除 {deleted} 条")
    return MaintenanceResult(
        success=True,
        message=f"已清理 {deleted} 条过期放行词",
        deleted_count=deleted,
    )


# ========== 审校领域规则 ==========
class DomainPromptsConfig(BaseModel):
    """审校领域规则配置（各领域的专业校对规则，注入审校 prompt）"""
    # 空字符串 = 使用代码内置默认规则
    general: str = ""
    official: str = ""
    legal: str = ""


@router.get("/domain-prompts", response_model=DomainPromptsConfig, summary='获取审校领域规则配置')
async def get_domain_prompts(
    _user=Depends(require_permission("admin:settings:edit")),
):
    """获取各领域审校规则（空 = 使用内置默认，effective 字段见前端提示）"""
    redis = await get_redis()
    data = await redis.hgetall(DOMAIN_PROMPTS_KEY)

    config = DomainPromptsConfig()
    if data:
        for code in DOMAIN_CODES:
            val = data.get(code) or ""
            if val:
                setattr(config, code, val)
    return config


@router.put("/domain-prompts", response_model=DomainPromptsConfig, summary='更新审校领域规则配置')
async def update_domain_prompts(
    config: DomainPromptsConfig,
    _user=Depends(require_permission("admin:settings:edit")),
):
    """更新各领域审校规则（某领域传空字符串 = 恢复使用内置默认规则）"""
    redis = await get_redis()
    mapping = {code: getattr(config, code) for code in DOMAIN_CODES}
    await redis.hset(DOMAIN_PROMPTS_KEY, mapping=mapping)
    # 删除空值 field，让读取侧回退到内置默认
    empty_fields = [code for code, val in mapping.items() if not val]
    if empty_fields:
        await redis.hdel(DOMAIN_PROMPTS_KEY, *empty_fields)
    logger.info(f"审校领域规则已更新（自定义领域: {[c for c in DOMAIN_CODES if getattr(config, c)]}）")
    return config


@router.get("/domain-prompts/defaults", summary='获取各领域内置默认规则')
async def get_domain_prompts_defaults(
    _user=Depends(require_permission("admin:settings:edit")),
):
    """返回代码内置的各领域默认规则（供后台编辑时参照/恢复）"""
    from app.services.proofread import DOMAIN_PROMPTS
    return {code: DOMAIN_PROMPTS.get(code, "") for code in DOMAIN_CODES}
