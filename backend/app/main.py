"""
TextMirror 智能文档审校平台 - FastAPI 应用入口
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.core.config import settings
from app.core.database import init_db, close_db
from app.core.redis import init_redis, close_redis
from app.api.router import api_router

# 导入所有模型确保表元数据注册（勿删除）
import app.models.user  # noqa
import app.models.role  # noqa
import app.models.proofread  # noqa
import app.models.dictionary  # noqa
import app.models.global_word  # noqa
import app.models.llm_config  # noqa
import app.models.audit_log  # noqa
import app.models.api_key  # noqa


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动和关闭时执行"""
    # ---- 启动阶段 ----
    logger.info(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} 正在启动...")

    # 初始化数据库
    await init_db()
    logger.info("✅ 数据库连接初始化完成")

    # 初始化 Redis
    await init_redis()
    logger.info("✅ Redis 连接初始化完成")

    # 确保上传目录存在
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    logger.info(f"✅ 上传目录已就绪: {settings.UPLOAD_DIR}")

    logger.info(f"🎉 {settings.APP_NAME} 启动成功！")

    yield

    # ---- 关闭阶段 ----
    logger.info(f"🛑 {settings.APP_NAME} 正在关闭...")
    await close_db()
    await close_redis()
    logger.info(f"👋 {settings.APP_NAME} 已安全关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="智能文档审校平台 API",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    # ---- 中间件配置 ----
    # CORS 跨域
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- 路由注册 ----
    app.include_router(api_router, prefix=settings.API_PREFIX)

    # ---- 开放 API 子应用 ----
    # 独立命名空间 /api/v1/open/*，只含对外稳定契约端点；
    # 文档页常开（主应用 /docs 仅 DEBUG 开启，内部端点不对外暴露）
    from app.api.v1.open import router as open_router
    from app.api.v1.open import validation_exception_handler
    from fastapi.exceptions import RequestValidationError

    open_api_app = FastAPI(
        title=f"{settings.APP_NAME} Open API",
        version=settings.APP_VERSION,
        description=(
            "TextMirror 对外审校 API，提供三种能力：\n\n"
            "- **文本审校**（同步）：`POST /proofread`\n"
            "- **多模型对比**（同步）：`POST /proofread/compare`\n"
            "- **文档审校**（异步）：`POST /documents` + `GET /jobs/{job_id}`\n\n"
            "**快速开始**：\n"
            "1. 网页端登录 → 「API 密钥」页创建密钥（`tm_` 开头，仅展示一次）\n"
            "2. 请求头携带 `Authorization: Bearer tm_你的密钥`\n"
            "3. 调用下方接口，最简请求只需 `{\"text\": \"...\"}`\n\n"
            "**错误契约**：非 2xx 响应 `detail` 为 `{'code': ..., 'message': ...}`，"
            "各端点的错误码示例见接口文档。\n\n"
            "**限流**：单密钥每分钟 12 次请求（429 RATE_LIMITED）；"
            "每日配额随归属账号（429 QUOTA_EXCEEDED），多模型对比按模型数计额度"
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )
    open_api_app.include_router(open_router)
    # 422 参数错误统一为 code+message 契约（其余错误已由端点自行保证）
    open_api_app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.mount(f"{settings.API_PREFIX}/open", open_api_app)

    # 上传文件不再通过 StaticFiles 直接暴露（无鉴权），
    # 统一走 /api/v1/document/download/{file_id}/{filename} 签名下载

    return app


# 创建应用实例
app = create_app()
