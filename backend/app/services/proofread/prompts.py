from typing import Dict, List, Optional

from loguru import logger

from .constants import DOMAIN_MAP, DOMAIN_PROMPTS


def _build_global_words_section(user_words: Optional[Dict[str, List[Dict]]] = None) -> str:
    """
    构建 Prompt 的词库提示段。
    词库类检查（敏感词/禁词/纠错词）已改为确定性扫描（scan_words_deterministic），
    不再注入 prompt——LLM 只管它真正擅长的语法/逻辑/表达；放行词由后置过滤保证。
    仅保留用户纠错词条的提示（引导模型在纠错词上下文里也注意同类表达）。
    """
    parts = []

    # 用户个性化纠错词条：仍注入提示（强化模型对该类错误的敏感度，扫描层已 100% 兜底）
    user_corrections = (user_words or {}).get("correction", [])
    if user_corrections:
        sample = "、".join([f"{w['word']}→{w['replacement']}" for w in user_corrections[:50]])
        remain = max(0, len(user_corrections) - 50)
        suffix = f" 等共{len(user_corrections)}条" if remain else ""
        parts.append(f"用户指定纠错(必须执行):{sample}{suffix}")

    return "; ".join(parts) if parts else ""


async def _get_domain_rules(domain: str) -> str:
    """
    获取领域专业规则：管理后台配置（Redis）优先，无配置回退内置 DOMAIN_PROMPTS。
    每次审校实时读取，后台改规则即时生效。
    自定义配置加"必须执行"前缀强化指令遵循（与用户词库同一策略）。
    """
    try:
        from app.core.redis import get_redis
        redis = get_redis()
        configured = await redis.hget("system:config:domain_prompts", domain)
        if configured:
            return f"以下为管理员指定的行业校对规则（必须严格执行）：{configured}"
    except Exception as e:
        logger.debug(f"读取领域规则配置失败，使用内置默认: {e}")
    return DOMAIN_PROMPTS.get(domain, DOMAIN_PROMPTS.get("general", ""))


def build_system_prompt(domain: str,
                        global_words: Optional[Dict[str, List[Dict]]] = None,
                        user_words: Optional[Dict[str, List[Dict]]] = None,
                        domain_rules: Optional[str] = None) -> str:
    """构建系统 Prompt,包含领域专业规则、全局词库和用户个性化词库"""
    from .constants import PROOFREAD_SYSTEM_PROMPT

    domain_str = DOMAIN_MAP.get(domain, "通用")

    # 领域规则（调用方在异步阶段读取后传入；未传时回退内置）
    if not domain_rules:
        domain_rules = DOMAIN_PROMPTS.get(domain, DOMAIN_PROMPTS.get("general", ""))

    # 构建词库段落（全局 + 用户）
    global_words_section = ""
    if global_words:
        global_words_section = _build_global_words_section(user_words)

    return PROOFREAD_SYSTEM_PROMPT.format(
        domain=domain_str,
        domain_rules=domain_rules,
        global_words_section=global_words_section,
    )
