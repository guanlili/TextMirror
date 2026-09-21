"""
TextMirror 统一异常体系
所有业务异常继承 AppException，保证错误响应格式一致：
  - 内部 API：{"detail": {"code": "...", "message": "..."}}
  - 开放 API：同上（已有契约，本模块主要统一内部 API）

用法：
  raise NotFoundError("RECORD_NOT_FOUND", "校对记录不存在")
  raise QuotaExceededError("QUOTA_EXCEEDED", "已达今日使用配额")
"""
from fastapi import HTTPException


class AppException(HTTPException):
    """业务异常基类：detail 始终为 {code, message} 字典"""

    def __init__(self, status_code: int, code: str, message: str, headers: dict | None = None):
        self.code = code
        super().__init__(status_code=status_code, detail={"code": code, "message": message}, headers=headers)


# ---- 4xx 客户端错误 ----

class BadRequestError(AppException):
    """400 通用业务错误"""

    def __init__(self, code: str = "BAD_REQUEST", message: str = "请求参数错误"):
        super().__init__(400, code, message)


class UnauthorizedError(AppException):
    """401 认证失败"""

    def __init__(self, code: str = "UNAUTHORIZED", message: str = "未提供认证凭证", headers: dict | None = None):
        super().__init__(401, code, message, headers=headers or {"WWW-Authenticate": "Bearer"})


class ForbiddenError(AppException):
    """403 权限不足"""

    def __init__(self, code: str = "FORBIDDEN", message: str = "无权执行此操作"):
        super().__init__(403, code, message)


class NotFoundError(AppException):
    """404 资源不存在"""

    def __init__(self, code: str = "NOT_FOUND", message: str = "资源不存在"):
        super().__init__(404, code, message)


class ConflictError(AppException):
    """409 资源冲突（幂等键重复、并发冲突等）"""

    def __init__(self, code: str = "CONFLICT", message: str = "请求冲突"):
        super().__init__(409, code, message)


class QuotaExceededError(AppException):
    """429 配额/限流"""

    def __init__(self, code: str = "QUOTA_EXCEEDED", message: str = "配额已用尽"):
        super().__init__(429, code, message)


class ValidationError(AppException):
    """422 业务校验失败（区别于 FastAPI 自动参数校验）"""

    def __init__(self, code: str = "VALIDATION_ERROR", message: str = "参数校验失败"):
        super().__init__(422, code, message)


class ServiceUnavailableError(AppException):
    """503 服务暂不可用"""

    def __init__(self, code: str = "SERVICE_UNAVAILABLE", message: str = "服务暂时不可用，请稍后重试"):
        super().__init__(503, code, message)
