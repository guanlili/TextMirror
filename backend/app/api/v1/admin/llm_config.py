"""
TextMirror 大模型配置管理 API（管理后台）
支持多个大模型配置的增删改查、测试连接、切换默认模型
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from loguru import logger

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.core.secret_crypto import encrypt_secret, decrypt_secret
from app.models.llm_config import LLMConfig
from app.schemas.llm_config import (
    LLMConfigCreate, LLMConfigUpdate, LLMConfigResponse,
    LLMTestResult, LLMProviderOption, SUPPORTED_PROVIDERS,
)
from app.services.llm.openai_compat import OpenAICompatProvider

router = APIRouter(prefix="/llm-config", tags=["大模型配置管理"])


def _mask_api_key(api_key: str) -> str:
    """API 密钥脱敏"""
    if not api_key or len(api_key) <= 8:
        return "****"
    return api_key[:4] + "****" + api_key[-4:]


def _to_response(config: LLMConfig) -> LLMConfigResponse:
    """模型实例转响应（含密钥脱敏）"""
    resp = LLMConfigResponse.model_validate(config)
    resp.api_key_masked = _mask_api_key(decrypt_secret(config.api_key))
    return resp


@router.get("/providers", response_model=list[LLMProviderOption], summary='获取支持的大模型供应商列表')
async def list_providers(_user=Depends(require_permission("admin:llm:edit"))):
    """获取支持的大模型供应商列表"""
    return [LLMProviderOption(**p) for p in SUPPORTED_PROVIDERS]


@router.get("", response_model=list[LLMConfigResponse], summary='获取所有大模型配置列表')
async def list_llm_configs(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:llm:edit")),
):
    """获取所有大模型配置列表"""
    result = await db.execute(
        select(LLMConfig).order_by(LLMConfig.is_active.desc(), LLMConfig.created_at.asc())
    )
    configs = result.scalars().all()
    return [_to_response(c) for c in configs]


@router.post("", response_model=LLMConfigResponse, status_code=201, summary='创建大模型配置')
async def create_llm_config(
    data: LLMConfigCreate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:llm:edit")),
):
    """创建大模型配置"""
    # 名称唯一：撞名时自动追加序号（如 DeepSeek 2），用户无需手动改名
    name = data.name
    exists = await db.execute(select(LLMConfig).where(LLMConfig.name == name))
    if exists.scalar_one_or_none():
        suffix = 2
        while True:
            candidate = f"{data.name} {suffix}"
            dup = await db.execute(select(LLMConfig).where(LLMConfig.name == candidate))
            if not dup.scalar_one_or_none():
                name = candidate
                break
            suffix += 1

    config = LLMConfig(
        name=name,
        provider=data.provider,
        api_base=data.api_base,
        api_key=encrypt_secret(data.api_key),
        model=data.model,
        temperature=data.temperature,
        max_tokens=data.max_tokens,
        timeout=data.timeout,
        max_retries=data.max_retries,
        remark=data.remark,
        is_active=False,
        is_enabled=True,
    )
    db.add(config)
    await db.flush()
    await db.refresh(config)
    logger.info(f"大模型配置已创建: {data.name} ({data.provider})")
    return _to_response(config)


@router.put("/{config_id}", response_model=LLMConfigResponse, summary='更新大模型配置')
async def update_llm_config(
    config_id: int,
    data: LLMConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:llm:edit")),
):
    """更新大模型配置"""
    result = await db.execute(select(LLMConfig).where(LLMConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    update_data = data.model_dump(exclude_unset=True)
    for field, val in update_data.items():
        # api_key 传空串表示保持原密钥不变（前端编辑时留空）
        if val is None or (field == "api_key" and not val):
            continue
        if field == "api_key":
            val = encrypt_secret(val)
        setattr(config, field, val)

    await db.flush()
    await db.refresh(config)
    logger.info(f"大模型配置已更新: {config.name}")
    return _to_response(config)


@router.delete("/{config_id}", status_code=204, summary='删除大模型配置')
async def delete_llm_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:llm:edit")),
):
    """删除大模型配置"""
    result = await db.execute(select(LLMConfig).where(LLMConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    if config.is_active:
        raise HTTPException(status_code=400, detail="不能删除当前正在使用的配置，请先切换到其他模型")

    await db.delete(config)
    logger.info(f"大模型配置已删除: {config.name}")


@router.post("/{config_id}/activate", response_model=LLMConfigResponse, summary='设为当前使用的模型（全局仅一个活跃）')
async def activate_llm_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:llm:edit")),
):
    """设为当前使用的模型（全局仅一个活跃）"""
    result = await db.execute(select(LLMConfig).where(LLMConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    if not config.is_enabled:
        raise HTTPException(status_code=400, detail="该配置已停用，请先启用")

    # 将所有配置设为非活跃
    await db.execute(update(LLMConfig).values(is_active=False))
    # 将目标配置设为活跃
    config.is_active = True
    await db.flush()
    await db.refresh(config)
    logger.info(f"当前大模型已切换为: {config.name} ({config.provider}/{config.model})")
    return _to_response(config)


@router.post("/{config_id}/test", response_model=LLMTestResult, summary='测试大模型连接')
async def test_llm_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:llm:edit")),
):
    """测试大模型连接"""
    result = await db.execute(select(LLMConfig).where(LLMConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    provider = OpenAICompatProvider(
        api_key=decrypt_secret(config.api_key),
        api_base=config.api_base,
        model=config.model,
        timeout=min(config.timeout, 30),  # 测试用短超时
        max_retries=1,
        provider_name=config.name,
    )
    try:
        test_result = await provider.test_connection()
        return LLMTestResult(**test_result)
    finally:
        await provider.close()


class DraftTestRequest(BaseModel):
    """弹窗内测试未保存的配置（编辑时 api_key 留空 + 带 config_id 表示用已存密钥）"""
    provider: str = Field(..., max_length=50)
    api_base: str = Field(..., max_length=500)
    api_key: str = Field("", max_length=500)
    model: str = Field(..., max_length=100)
    config_id: Optional[int] = Field(None, description="编辑场景：Key 留空时从该配置取已存密钥")


@router.post("/test-draft", response_model=LLMTestResult, summary='测试未保存的模型配置（添加弹窗内测试）')
async def test_llm_draft(
    body: DraftTestRequest,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:llm:edit")),
):
    """添加/编辑弹窗内直接测试连接，无需先保存"""
    api_key = body.api_key
    # 编辑场景 Key 留空：取已保存的密钥
    if not api_key and body.config_id:
        result = await db.execute(select(LLMConfig).where(LLMConfig.id == body.config_id))
        saved = result.scalar_one_or_none()
        if saved:
            api_key = decrypt_secret(saved.api_key)

    try:
        provider = OpenAICompatProvider(
            api_key=api_key,
            api_base=body.api_base,
            model=body.model,
            timeout=30,
            max_retries=1,
            provider_name=body.provider,
        )
    except RuntimeError as e:
        # 密钥无效（空/含非 ASCII 占位符）等构造期错误
        return LLMTestResult(success=False, model=body.model, message=str(e))
    try:
        test_result = await provider.test_connection()
        return LLMTestResult(**test_result)
    finally:
        await provider.close()


@router.get("/active", response_model=LLMConfigResponse, summary='获取当前活跃的大模型配置')
async def get_active_config(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:llm:edit")),
):
    """获取当前活跃的大模型配置"""
    result = await db.execute(select(LLMConfig).where(LLMConfig.is_active == True))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="尚未配置活跃的大模型，请在管理后台配置")
    return _to_response(config)


# ======================================================================
# 配置导入 / 导出
# ======================================================================

class LLMConfigExportItem(BaseModel):
    """导出/导入的单条配置"""
    name: str
    provider: str
    api_base: str
    api_key: str = ""          # 导出脱敏时为掩码；导入为掩码/空则保留目标端已存密钥
    model: str
    temperature: float = 0.3
    max_tokens: Optional[int] = None
    timeout: int = 60
    max_retries: int = 3
    is_active: bool = False
    is_enabled: bool = True
    remark: Optional[str] = None


class LLMConfigImportRequest(BaseModel):
    """导入配置请求"""
    configs: List[LLMConfigExportItem] = Field(..., min_length=1, max_length=50)
    conflict: str = Field("skip", description="同名冲突策略: skip=跳过 / overwrite=覆盖")


@router.get("/export", summary='导出全部大模型配置')
async def export_llm_configs(
    include_keys: bool = Query(False, description="是否包含明文密钥（默认脱敏，用于备份迁移时选 true）"),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:llm:edit")),
):
    """导出全部配置为 JSON（默认密钥脱敏）"""
    from datetime import datetime

    result = await db.execute(select(LLMConfig).order_by(LLMConfig.is_active.desc(), LLMConfig.id))
    configs = result.scalars().all()

    items = []
    for c in configs:
        plain_key = decrypt_secret(c.api_key)
        items.append(LLMConfigExportItem(
            name=c.name,
            provider=c.provider,
            api_base=c.api_base,
            api_key=plain_key if include_keys else _mask_api_key(plain_key),
            model=c.model,
            temperature=c.temperature,
            max_tokens=c.max_tokens,
            timeout=c.timeout,
            max_retries=c.max_retries,
            is_active=c.is_active,
            is_enabled=c.is_enabled,
            remark=c.remark,
        ).model_dump())

    export_data = {
        "version": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "contains_keys": include_keys,
        "count": len(items),
        "configs": items,
    }

    filename = f"textmirror-llm-configs{'-with-keys' if include_keys else ''}-{datetime.now():%Y%m%d%H%M%S}.json"
    from fastapi.responses import Response
    return Response(
        content=json.dumps(export_data, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _is_masked_key(key: str) -> bool:
    """判断密钥是否为脱敏掩码（如 sk-1****abcd）或空——导入时应跳过覆盖"""
    return (not key) or ("****" in key)


@router.post("/import", summary='批量导入大模型配置')
async def import_llm_configs(
    body: LLMConfigImportRequest,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:llm:edit")),
):
    """批量导入配置。同名冲突按策略处理：skip 跳过 / overwrite 覆盖（密钥为掩码时保留已存值）"""
    if body.conflict not in ("skip", "overwrite"):
        raise HTTPException(status_code=400, detail="conflict 仅支持 skip / overwrite")

    created, updated, skipped, kept_key = 0, 0, 0, 0
    for item in body.configs:
        existing = await db.execute(select(LLMConfig).where(LLMConfig.name == item.name))
        existing = existing.scalar_one_or_none()

        if existing is not None:
            if body.conflict == "skip":
                skipped += 1
                continue
            # overwrite：掩码/空密钥不覆盖已存密钥（已存值本就是密文，直接沿用）
            new_key = item.api_key
            if _is_masked_key(new_key):
                new_key = existing.api_key
                kept_key += 1
            else:
                new_key = encrypt_secret(new_key)
            existing.provider = item.provider
            existing.api_base = item.api_base
            existing.api_key = new_key
            existing.model = item.model
            existing.temperature = item.temperature
            existing.max_tokens = item.max_tokens
            existing.timeout = item.timeout
            existing.max_retries = item.max_retries
            existing.is_enabled = item.is_enabled
            if item.remark is not None:
                existing.remark = item.remark
            # 活跃标记不随导入覆盖（避免误切换生产模型）
            updated += 1
        else:
            key = item.api_key
            key_is_masked = _is_masked_key(key)
            db.add(LLMConfig(
                name=item.name,
                provider=item.provider,
                api_base=item.api_base,
                api_key=encrypt_secret(key) if not key_is_masked else "",
                model=item.model,
                temperature=item.temperature,
                max_tokens=item.max_tokens,
                timeout=item.timeout,
                max_retries=item.max_retries,
                is_active=False,      # 导入不直接设活跃，避免误切换
                # 密钥缺失的导入配置直接停用，避免"看起来正常但一调用就报错"
                is_enabled=False if key_is_masked else item.is_enabled,
                remark=item.remark,
            ))
            created += 1

    await db.flush()
    logger.info(f"配置导入完成: 新建={created} 覆盖={updated}(保留密钥={kept_key}) 跳过={skipped}")

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "kept_key": kept_key,
    }
