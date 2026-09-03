"""
TextMirror 敏感字段加密
API Key 等敏感配置使用 Fernet 对称加密存储，密钥从 SECRET_KEY 派生
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from app.core.config import settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """从 SECRET_KEY 派生 Fernet 密钥（32 字节 base64）"""
    global _fernet
    if _fernet is None:
        derived = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        _fernet = Fernet(base64.urlsafe_b64encode(derived))
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    """加密敏感字段，返回带前缀的密文（enc:...）"""
    if not plaintext:
        return plaintext
    # 已是密文（或历史明文迁移前的旧值）幂等处理：仅 enc: 前缀跳过
    if plaintext.startswith("enc:"):
        return plaintext
    return "enc:" + _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(stored: str) -> str:
    """
    解密敏感字段。
    - enc: 前缀：正常解密
    - 无前缀：视为历史明文数据，原样返回（读取时兼容，下次保存自动加密）
    """
    if not stored or not stored.startswith("enc:"):
        return stored
    try:
        return _get_fernet().decrypt(stored[4:].encode()).decode()
    except InvalidToken:
        # 密钥变更等原因解不开：返回空串，让上游走到"密钥未配置"的清晰报错
        logger.warning("密文解密失败（SECRET_KEY 可能已变更），请重新填写密钥")
        return ""
