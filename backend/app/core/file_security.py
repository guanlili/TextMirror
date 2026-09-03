"""
文件安全工具
上传文件名净化 + 签名下载 URL（替代无鉴权的静态目录挂载）
"""
import hashlib
import hmac
import os
import re
import time
import unicodedata

from app.core.config import settings

# 签名有效期：24 小时（校对结果会话内下载，过期后需重新校对生成）
SIGNATURE_TTL = 86400


def sanitize_filename(filename: str) -> str:
    """
    净化用户上传的文件名，防路径穿越
    只保留 basename，去掉控制字符与路径分隔符，压缩空白
    """
    # 取 basename，去掉任何路径成分（包括 Windows 风格）
    filename = os.path.basename(filename.replace("\\", "/"))
    # 去掉控制字符
    filename = "".join(ch for ch in filename if unicodedata.category(ch)[0] != "C")
    # 压缩连续空白
    filename = re.sub(r"\s+", " ", filename).strip()
    # 限制长度（保留扩展名）
    if len(filename) > 200:
        root, ext = os.path.splitext(filename)
        filename = root[: 200 - len(ext)] + ext
    return filename


def _signature(file_id: str, filename: str, expires: int) -> str:
    msg = f"{file_id}:{filename}:{expires}"
    return hmac.new(
        settings.SECRET_KEY.encode(), msg.encode(), hashlib.sha256
    ).hexdigest()


def build_download_url(file_id: str, filename: str, ttl: int = SIGNATURE_TTL) -> str:
    """生成带 HMAC 签名的下载 URL（含过期时间）"""
    expires = int(time.time()) + ttl
    sig = _signature(file_id, filename, expires)
    from urllib.parse import quote

    return f"/api/v1/document/download/{file_id}/{quote(filename)}?expires={expires}&signature={sig}"


def verify_download_signature(
    file_id: str, filename: str, expires: int, signature: str
) -> bool:
    """校验下载 URL 的签名与有效期"""
    if not signature or expires < int(time.time()):
        return False
    return hmac.compare_digest(_signature(file_id, filename, expires), signature)


def safe_upload_path(file_id: str, filename: str) -> str:
    """
    拼接上传文件的磁盘路径，并确保结果仍在 UPLOAD_DIR 之内（纵深防御）
    filename 必须先经过 sanitize_filename
    """
    upload_dir = os.path.abspath(settings.UPLOAD_DIR)
    target = os.path.abspath(os.path.join(upload_dir, file_id, filename))
    if not target.startswith(upload_dir + os.sep):
        raise ValueError("非法的文件路径")
    return target
