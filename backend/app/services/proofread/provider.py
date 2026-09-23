from typing import Optional

from loguru import logger
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.secret_crypto import decrypt_secret
from app.models.llm_config import LLMConfig
from app.services.llm.base import BaseLLMProvider
from app.services.llm.openai_compat import OpenAICompatProvider

from .errors import InvalidModelConfigError


async def get_llm_provider(config_id: Optional[int] = None) -> BaseLLMProvider:
    """
    获取大模型 Provider 实例
    指定 config_id 时使用该配置（用户在校对页手动选模型），
    否则使用数据库活跃配置。无可用配置时直接报错，引导管理员去后台配置。
    """
    try:
        async with async_session_factory() as session:
            if config_id is not None:
                # 用户指定的模型配置（须启用）
                result = await session.execute(
                    select(LLMConfig).where(
                        LLMConfig.id == config_id,
                        LLMConfig.is_enabled.is_(True),
                    )
                )
                config = result.scalar_one_or_none()
                if not config:
                    raise InvalidModelConfigError(f"指定的模型配置不存在或已停用 (id={config_id})")
            else:
                result = await session.execute(
                    select(LLMConfig).where(
                        LLMConfig.is_active.is_(True),
                        LLMConfig.is_enabled.is_(True),
                    )
                )
                config = result.scalar_one_or_none()
                if not config:
                    raise RuntimeError("尚未配置可用的大模型，请联系管理员在后台「大模型配置」中添加并设为当前使用")
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"从数据库加载 LLM 配置失败: {e}")
        raise RuntimeError("大模型配置加载失败，请稍后重试或联系管理员")

    logger.info(f"使用大模型: {config.name} ({config.provider}/{config.model})")
    provider = OpenAICompatProvider(
        api_key=decrypt_secret(config.api_key),
        api_base=config.api_base,
        model=config.model,
        timeout=config.timeout,
        max_retries=config.max_retries,
        provider_name=config.name,
        provider_slug=config.provider,
    )
    # 尊重后台配置的温度值（校对场景默认 0.2，取两者较低保证确定性）
    provider.default_temperature = min(config.temperature, 0.2)
    provider.config_id = config.id
    provider.usage_business = "proofread"
    # 仅人工点击评测的上下文限制重试/实际模型并发，不改变普通审校行为。
    from app.services.quality_evaluation import configure_evaluation_provider
    configure_evaluation_provider(provider)
    return provider
