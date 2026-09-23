from pydantic import ValidationError

from app.schemas.fact_check import FactEvidenceChecks, FactJudgmentEvidence

from .constants import MAX_PAGE_TEXT
from .errors import FactCheckError
from .fetching import _normalize, _Page


def _same_material(left: _Page, right: _Page) -> bool:
    a, b = left.text[:MAX_PAGE_TEXT], right.text[:MAX_PAGE_TEXT]
    if a == b:
        return True
    # Overlapping character shingles also work for Chinese and shifted reprint headers.
    # Only compare the bounded model-visible text; never imply independent corroboration.
    if min(len(a), len(b)) < 100:
        return False

    def shingles(text):
        return {text[i:i + 40] for i in range(len(text) - 39)}

    x, y = shingles(a), shingles(b)
    return len(x & y) / max(1, min(len(x), len(y))) >= 0.85


def _judge_result(data: dict, pages: list[_Page]) -> tuple[dict, bool]:
    verdict, reason, suggestion, refs = (data.get(key) for key in ("verdict", "reason", "suggestion", "evidence"))
    if (not isinstance(verdict, str) or verdict not in {"supported", "refuted", "insufficient", "conflicting"}
            or not isinstance(reason, str) or not reason.strip()
            or (suggestion is not None and not isinstance(suggestion, str)) or not isinstance(refs, list)):
        raise FactCheckError("MODEL_FORMAT_ERROR", "证据判定结果格式错误。")
    invalid = set(data) != {"verdict", "reason", "suggestion", "evidence"}
    by_id = {page.id: page for page in pages}
    evidence, seen = [], set()
    for ref in refs:
        if (not isinstance(ref, dict) or not set(ref) <= {"id", "quote", "stance", "checks"}
                or not isinstance(ref.get("id"), str) or ref["id"] not in by_id
                or not isinstance(ref.get("quote"), str) or not isinstance(ref.get("stance"), str)
                or ref["stance"] not in {"supports", "refutes", "context"}):
            invalid = True
            continue
        quote = _normalize(ref["quote"])
        page = by_id[ref["id"]]
        if not quote or len(quote) > 4000 or quote not in page.text[:MAX_PAGE_TEXT] or page.id in seen:
            invalid = True
            continue
        seen.add(page.id)
        stance = ref["stance"]
        try:
            checks = FactJudgmentEvidence.model_validate(ref).checks
        except ValidationError:
            # Keep only the verified quotation as context; missing/invalid checks never inherit a match.
            checks = FactEvidenceChecks.model_validate({
                key: {"status": "unknown", "reason": "模型未提供有效的结构化口径检查，程序未验证语义。"}
                for key in ("subject", "event_time", "scope_unit")
            })
            invalid = True
            stance = "context"
        if stance != "context" and not checks.comparable:
            stance = "context"
            invalid = True
        evidence.append(page.evidence(quote, stance, checks))
    supports = [ref for ref in evidence if ref["stance"] == "supports"]
    refutes = [ref for ref in evidence if ref["stance"] == "refutes"]
    if verdict == "supported" and (not supports or refutes):
        invalid = True
    if verdict == "refuted" and (not refutes or supports):
        invalid = True
    if verdict == "conflicting" and not any(
        a["id"] != b["id"] and not _same_material(by_id[a["id"]], by_id[b["id"]])
        for a in supports for b in refutes
    ):
        invalid = True
    if invalid:
        return {"verdict": "insufficient", "reason": "引用或结构化口径检查未通过，或缺少与结论对应的正文证据，无法作出可靠判定。",
                "suggestion": None, "evidence": evidence}, True
    return {"verdict": verdict, "reason": reason, "suggestion": suggestion if verdict == "refuted" else None,
            "evidence": evidence}, False
