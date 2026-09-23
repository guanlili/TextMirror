from typing import Any, Dict, List, Optional

from .constants import _SHORT_FIELD_MAP, PROOFREAD_TYPES


class InvalidModelConfigError(RuntimeError):
    """用户指定的配置不存在/停用，可安全向用户展示。"""


class InvalidProofreadResponse(ValueError):
    """模型返回的 JSON 或问题结构不合法（不能视为零问题）。"""


class ModelProofreadError(RuntimeError):
    """所有模型分片失败；只向调用方暴露安全错误及可追溯元数据。"""

    def __init__(self, coverage: Dict[str, Any], config_id: Optional[int]):
        super().__init__("大模型审校失败，请稍后重试")
        self.coverage = coverage
        self.config_id = config_id


def _normalize_issue_fields(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """还原字段并校验整个数组；任一条非法则整片失败，不悄悄丢弃。"""
    normalized = []
    fields = set(_SHORT_FIELD_MAP.values())
    for item in items:
        if not isinstance(item, dict):
            raise InvalidProofreadResponse("审校问题必须为对象")
        new_item = {_SHORT_FIELD_MAP.get(k, k): v for k, v in item.items()}
        if any(not isinstance(new_item.get(field), str) for field in fields):
            raise InvalidProofreadResponse("审校问题字段缺失或类型错误")
        if (not new_item["original"].strip()
                or new_item["type"] not in PROOFREAD_TYPES
                or new_item["severity"] not in ("error", "warning", "info")):
            raise InvalidProofreadResponse("审校问题字段取值非法")
        if "review" in new_item and new_item["review"] not in ("new", "confirm", "doubt"):
            raise InvalidProofreadResponse("自检标记非法")
        # source / chunk_index / 坐标只由服务端产生，不信任模型自报。
        normalized.append({k: v for k, v in new_item.items() if k in fields or k == "review"})
    return normalized
