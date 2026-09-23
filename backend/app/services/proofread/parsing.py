import json
import re
from typing import Any, Dict, List

from .errors import InvalidProofreadResponse, _normalize_issue_fields


def parse_proofread_result(content: str) -> List[Dict[str, Any]]:
    """严格解析 JSON 数组；仅兼容完整代码围栏，不从错误结构中抽取子数组。"""
    if not isinstance(content, str):
        raise InvalidProofreadResponse("审校响应必须为文本")
    content = content.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
    if fenced:
        content = fenced.group(1)

    def reject_constant(value):
        raise InvalidProofreadResponse("审校响应含非法 JSON 常量")

    try:
        result = json.loads(content, parse_constant=reject_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise InvalidProofreadResponse("审校响应不是合法 JSON") from exc
    if not isinstance(result, list):
        raise InvalidProofreadResponse("审校响应必须为数组")
    return _normalize_issue_fields(result)
