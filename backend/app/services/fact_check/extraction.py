import re

from app.schemas.fact_check import FactSearchRound

from .constants import MAX_EXTRACTED
from .errors import FactCheckError
from .fetching import _normalize


def _source_segments(text: str) -> list[dict]:
    boundaries = [match.end() for match in re.finditer(r'[。！？!?]["‘’」』）)]*|\.(?=\s|$)|\r\n|[\r\n]', text)]
    segments, start = [], 0
    for end in [*boundaries, len(text)]:
        if text[start:end].strip():
            segments.append({"id": f"s{len(segments) + 1}", "text": text[start:end], "start": start, "end": end})
        elif segments:
            segments[-1]["text"] += text[start:end]
            segments[-1]["end"] = end
        else:
            continue
        start = end
    return segments


def _locate(segments: dict[str, dict], item: dict) -> tuple[int, int] | None:
    segment_id = item.get("segment_id")
    if not isinstance(segment_id, str) or segment_id not in segments or "start" in item or "end" in item:
        return None
    segment = segments[segment_id]
    original = item["original"]
    before, after = item.get("context_before", ""), item.get("context_after", "")
    if not isinstance(before, str) or not isinstance(after, str):
        return None
    needle = before + original + after
    index = segment["text"].find(needle)
    if index < 0 or segment["text"].find(needle, index + 1) >= 0:
        return None
    start = segment["start"] + index + len(before)
    return start, start + len(original)


def _claims_from_model(data: dict, text: str) -> tuple[list[dict], list[str]]:
    raw = data.get("claims")
    if set(data) != {"claims"} or not isinstance(raw, list):
        raise FactCheckError("MODEL_FORMAT_ERROR", "事实提取结果必须为仅包含 claims 数组的对象。")
    segments = {segment["id"]: segment for segment in _source_segments(text)}
    claims, issues, seen = [], [], set()
    # Validate every item's shape, even beyond the extraction cap: malformed is not no-facts.
    for item in raw:
        if (not isinstance(item, dict) or not isinstance(item.get("original"), str)
                or not item["original"].strip() or not isinstance(item.get("statement"), str)
                or not item["statement"].strip()):
            raise FactCheckError("MODEL_FORMAT_ERROR", "事实提取条目格式错误。")
        location = _locate(segments, item)
        if location is None:
            issues.append("部分提取陈述的句段引用无效或原文存在歧义，已跳过。")
            continue
        identity = (location, _normalize(item["statement"]))
        if identity in seen:
            continue
        seen.add(identity)
        if len(claims) >= MAX_EXTRACTED:
            issues.append("已达到30条提取上限，其他陈述未纳入。")
            continue
        start, end = location
        query = _normalize(item["statement"])[:350]
        rounds = [FactSearchRound(kind=kind, query=value, status="pending", sources=[]).model_dump()
                  for kind, value in (("initial", query), ("counter", query + " 反证 反驳 更正 纠错 官方原始来源"))]
        claims.append({"id": f"c{len(claims) + 1}", "original": item["original"], "start": start, "end": end,
                       "statement": item["statement"], "verdict": "insufficient", "reason": "尚未核查。",
                       "suggestion": None, "evidence": [], "checked": False, "search_rounds": rounds})
    if len(claims) == MAX_EXTRACTED:
        issues.append("已达到30条提取上限，无法保证已穷尽全文事实。")
    return claims, list(dict.fromkeys(issues))
