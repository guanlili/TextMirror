"""主 prompt 准则区内容回归（避免误报条款的存续校验）。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.proofread.constants import PERSON_REFERENCE_RULES, PROOFREAD_SYSTEM_PROMPT
from app.services.proofread.prompts import build_system_prompt
from app.services.proofread.self_check import self_check_pass


def test_main_prompt_keeps_knowledge_correspondence_guard():
    """人名与职务/机构对应不得以模型记忆质疑改写（2026-10-14 用户实测案例）。"""
    prompt = build_system_prompt("general")
    assert "不以模型记忆核对现实对应关系" in prompt
    assert "更不得据此改动人名、职务或机构名" in prompt


def test_main_prompt_keeps_existing_no_report_clauses():
    """既有 7a/7f 条款不被编辑时误删。"""
    assert "你的专有名词知识不可靠" in PROOFREAD_SYSTEM_PROMPT
    assert "易混词对不要凭感觉报" in PROOFREAD_SYSTEM_PROMPT


def test_person_rules_disambiguate_names_without_global_exemptions():
    assert "人名与普通词同形时" in PERSON_REFERENCE_RULES
    assert "助理研究员来源对记者说" in PERSON_REFERENCE_RULES
    assert "不擅自认定缺少姓名" in PERSON_REFERENCE_RULES
    assert "姓名占位符" in PERSON_REFERENCE_RULES
    assert "不是词语白名单" in PERSON_REFERENCE_RULES
    assert "真实错字、语法错误仍正常检查" in PERSON_REFERENCE_RULES


@pytest.mark.parametrize("domain", ["general", "official", "legal"])
@pytest.mark.parametrize("domain_rules", [None, "检查语序和成分残缺"])
def test_name_protection_applies_to_all_domain_prompts(domain, domain_rules):
    prompt = build_system_prompt(domain, domain_rules=domain_rules)
    assert PERSON_REFERENCE_RULES in prompt


async def test_self_check_receives_name_protection():
    provider = SimpleNamespace(
        default_temperature=0.2,
        chat=AsyncMock(return_value=SimpleNamespace(content="[]")),
    )
    result = await self_check_pass(
        "助理研究员来源对记者说，相关数据已经完成核验。", [], provider, force=True,
    )
    assert result == []
    provider.chat.assert_awaited_once()
    messages = provider.chat.call_args.args[0]
    assert messages[0]["role"] == "system"
    assert PERSON_REFERENCE_RULES in messages[0]["content"]
