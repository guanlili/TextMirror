"""主 prompt 准则区内容回归（避免误报条款的存续校验）。"""

from app.services.proofread.constants import PROOFREAD_SYSTEM_PROMPT
from app.services.proofread.prompts import build_system_prompt


def test_main_prompt_keeps_knowledge_correspondence_guard():
    """人名与职务/机构对应不得以模型记忆质疑改写（2026-10-14 用户实测案例）。"""
    prompt = build_system_prompt("general")
    assert "不以模型记忆核对现实对应关系" in prompt
    assert "更不得据此改动人名、职务或机构名" in prompt


def test_main_prompt_keeps_existing_no_report_clauses():
    """既有 7a/7f 条款不被编辑时误删。"""
    assert "你的专有名词知识不可靠" in PROOFREAD_SYSTEM_PROMPT
    assert "易混词对不要凭感觉报" in PROOFREAD_SYSTEM_PROMPT
