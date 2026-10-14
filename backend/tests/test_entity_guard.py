"""entity_guard 专名改写保护单测。

幻觉案例来自 2026-10-13 生产实测：「日本相高市苗」（脱字，应为
「日本首相高市早苗」）被 LLM 建议改成「相原市」——编造原文中
不存在的新专名。守卫须将其降级为人工核实，且不误伤正常修正。
"""

from app.services.proofread.entity_guard import protect_entity_suggestions

TEXT = "记者：据报道，日本相高市苗近日在临时国会发表施政演说称，日周边安保环境复杂严峻，将于年底前修订安保文件。"


def _issue(original, suggestion, **kw):
    base = {"original": original, "suggestion": suggestion, "type": "typo",
            "severity": "error", "explanation": "", "source": "llm"}
    base.update(kw)
    return base


class TestEntityGuard:
    def test_hallucinated_new_name_is_downgraded(self):
        issues = [_issue("相高市苗", "相原市")]
        [result] = protect_entity_suggestions(TEXT, issues)
        assert result["suggestion"] == ""
        assert result["severity"] == "warning"
        assert result["source"] == "entity_guard"
        assert "相原市" in result["explanation"]
        assert "人工核实" in result["explanation"]

    def test_same_char_reorder_passes(self):
        # 帐号→账号：未引入新字符，正常错字修正不受影响
        issues = [_issue("帐号", "账号")]
        [result] = protect_entity_suggestions("他的帐号丢失了。", issues)
        assert result["suggestion"] == "账号"
        assert result["severity"] == "error"

    def test_completion_insertion_passes(self):
        # 高市苗→高市早苗：增字型修正（互为子序列）放行
        issues = [_issue("高市苗", "高市早苗")]
        [result] = protect_entity_suggestions(TEXT, issues)
        assert result["suggestion"] == "高市早苗"

    def test_deletion_passes(self):
        # 高市早苗→高市苗：删字型（衍字）修正放行
        issues = [_issue("高市早苗", "高市苗")]
        [result] = protect_entity_suggestions(TEXT, issues)
        assert result["suggestion"] == "高市苗"

    def test_new_char_present_elsewhere_in_text_passes(self):
        # 新字符在全文他处出现过：属合理改写，不拦截
        text = "宁波是港口城市。他去了西部。"
        issues = [_issue("西部", "西宁")]
        [result] = protect_entity_suggestions(text, issues)
        assert result["suggestion"] == "西宁"

    def test_longer_span_out_of_scope(self):
        # 超过 4 字的片段不适用专名规则，原样保留
        issues = [_issue("临时国会演说", "临时国会发言")]
        [result] = protect_entity_suggestions(TEXT, issues)
        assert result["suggestion"] == "临时国会发言"

    def test_non_cjk_out_of_scope(self):
        issues = [_issue("OK绷", "创口贴")]
        [result] = protect_entity_suggestions("他贴了个OK绷。", issues)
        assert result["suggestion"] == "创口贴"

    def test_deterministic_sources_skipped(self):
        # 词库等确定性来源的建议天然来自配置，不拦截
        issues = [_issue("相高市苗", "相原市", source="dict_scan")]
        [result] = protect_entity_suggestions(TEXT, issues)
        assert result["suggestion"] == "相原市"

    def test_empty_suggestion_untouched(self):
        issues = [_issue("相高市苗", "")]
        [result] = protect_entity_suggestions(TEXT, issues)
        assert result["suggestion"] == ""

    def test_swap_with_existing_chars_passes(self):
        # 权利→权力：建议字符全部来自原文，放行
        issues = [_issue("权利", "权力")]
        [result] = protect_entity_suggestions("权力机关依法行使权利。", issues)
        assert result["suggestion"] == "权力"

    def test_single_char_name_typo_passes(self):
        # 梁士俊→粱士俊：1 换 1 的形近字修正属经典错字形，放行
        issues = [_issue("梁士俊", "粱士俊")]
        [result] = protect_entity_suggestions("梁士俊志愿服务5年。", issues)
        assert result["suggestion"] == "粱士俊"

    def test_multi_char_replacement_is_downgraded(self):
        # 相高市苗→相原县城：4 字中 2 字被替换且新字全文未出现，拦截
        issues = [_issue("相高市苗", "相原县城")]
        [result] = protect_entity_suggestions(TEXT, issues)
        assert result["suggestion"] == ""
        assert result["source"] == "entity_guard"
