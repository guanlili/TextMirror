from typing import Any, Dict, List, Optional


def scan_words_deterministic(text: str,
                             global_words: Dict[str, List[Dict]],
                             user_words: Optional[Dict[str, List[Dict]]] = None) -> List[Dict[str, Any]]:
    """
    词库确定性扫描：敏感词/禁词/纠错词直接字符串匹配生成 issue。
    召回率 100%、零 LLM 成本；LLM 不再承担词库类检查（prompt 已移除注入）。
    同一 (原文, 类型) 只产出一条；纠错词排除「正确词本身出现在文本中」的场景
    （如词条"电度表→电能表"，文本出现"电能表"不该命中）。
    """
    issues: List[Dict[str, Any]] = []
    seen = set()
    user_words = user_words or {}

    def _add(word: str, issue_type: str, suggestion: str, explanation: str, severity: str):
        key = (word, issue_type)
        if key in seen or not word:
            return
        seen.add(key)
        issues.append({
            "original": word,
            "type": issue_type,
            "suggestion": suggestion,
            "explanation": explanation,
            "severity": severity,
            "chunk_index": 0,
            "source": "dict_scan",
        })

    # 敏感词/禁词：命中即报（禁词更严重）。说明文案带〔词库〕来源标识，
    # 用户在结果页能直接看到"这是词库在起作用"
    sensitive_words = {w["word"] for w in global_words.get("sensitive", [])}
    banned_words = {w["word"] for w in global_words.get("banned", [])}
    # suggestion="" 前端渲染为「删除」操作（replace(词, "")）——违禁词
    # 的自动修复就是删除；解释文字放 explanation 不进 suggestion
    for word in banned_words:
        if word and word in text:
            _add(word, "sensitive", "", "〔词库〕命中违禁词", "error")
    for word in sensitive_words:
        if word and word in text and word not in banned_words:
            _add(word, "sensitive", "", "〔词库〕命中敏感词", "warning")

    # 纠错词：全局 + 用户（用户词与全局词冲突时用户优先——显式维护的规则更具体）
    corrections: Dict[str, str] = {}
    correction_sources: Dict[str, str] = {}
    for w in global_words.get("correction", []):
        if w.get("word") and w.get("replacement"):
            corrections[w["word"]] = w["replacement"]
            correction_sources[w["word"]] = "全局词库"
    for w in user_words.get("correction", []):
        if w.get("word") and w.get("replacement"):
            corrections[w["word"]] = w["replacement"]
            correction_sources[w["word"]] = "我的词库"

    for wrong, correct in corrections.items():
        if wrong and wrong in text:
            src = correction_sources.get(wrong, "词库")
            _add(wrong, "typo", correct, f"〔{src}〕{wrong}→{correct}", "error")

    return issues


def merge_issues(llm_issues: List[Dict[str, Any]],
                 scanned_issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    合并确定性扫描结果与 LLM 结果，同位置/类型/建议优先保留 LLM 版本。
    主流程必须先定位，避免按 original 包含关系吞掉其他位置的问题。
    同类型扫描项被 LLM span 严格包含且其原文已被建议修掉时，也优先保留 LLM。
    仅为旧的无分片、无坐标调用保留文本包含去重兼容。
    """
    merged = list(llm_issues)
    position_fields = ("start", "end", "type", "suggestion")
    positioned = {tuple(i.get(k) for k in position_fields) for i in llm_issues
                  if i.get("start") is not None}
    legacy_llm = [i for i in llm_issues if i.get("start") is None and "chunk_index" not in i]
    for s in scanned_issues:
        if s.get("start") is not None:
            duplicate = tuple(s.get(k) for k in position_fields) in positioned
            if not duplicate and s.get("end") is not None:
                duplicate = any(
                    i.get("start") is not None and i.get("end") is not None
                    and i.get("type") == s["type"]
                    and i["start"] <= s["start"] < s["end"] <= i["end"]
                    and (i["start"] < s["start"] or s["end"] < i["end"])
                    and (i.get("suggestion") or i.get("type") == "sensitive")
                    and s["original"] not in (i.get("suggestion") or "")
                    for i in llm_issues
                )
        else:
            duplicate = "chunk_index" not in s and any(
                s["original"] and s["original"] in (i.get("original") or "") and i.get("type") == s["type"]
                for i in legacy_llm
            )
        if not duplicate:
            merged.append(s)
    # 严重度排序：error → warning → info
    order = {"error": 0, "warning": 1, "info": 2}
    merged.sort(key=lambda i: order.get(i.get("severity", "warning"), 1))
    return merged
