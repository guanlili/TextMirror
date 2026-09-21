"""CSS 值白名单：DOCX 属性值进入 HTML style 属性时，需防止恶意值逃逸注入事件处理器。"""
import re
from html import escape as html_escape

_FONT_NAME_ALLOWED = re.compile(r"^[\w\u4e00-\u9fff \-\.]+$")
_HEX_COLOR = re.compile(r"^[0-9A-Fa-f]{6}$")


def _safe_css_font_family(name) -> str:
    """字体名 → 安全的 font-family 值；不合白名单则丢弃该声明"""
    if not name:
        return ""
    text = str(name).strip()
    if not text or len(text) > 64 or not _FONT_NAME_ALLOWED.match(text):
        return ""
    return f"font-family:'{html_escape(text, quote=True)}'"


def _safe_css_color(rgb) -> str:
    """字体颜色 → 安全的 color 值；仅接受 6 位十六进制"""
    if rgb is None:
        return ""
    text = str(rgb).strip()
    if not _HEX_COLOR.match(text):
        return ""
    return f"color:#{text}"


def _safe_css_length(prop: str, value, max_pt: float = 2000.0) -> str:
    """长度类样式 → 安全的 pt 值；仅接受有限范围内的数值"""
    try:
        pt = float(value)
    except (TypeError, ValueError):
        return ""
    if pt != pt or pt <= 0 or pt > max_pt:  # NaN 或超范围
        return ""
    return f"{prop}:{pt:.1f}pt"
