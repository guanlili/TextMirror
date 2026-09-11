"""
TextMirror Webhook 投递 Celery 任务
任务完成/失败事件 POST 到密钥配置的 webhook_url，HMAC-SHA256 签名，指数退避重试。
"""
import json

import httpx
from celery import shared_task
from loguru import logger
from sqlalchemy.orm import Session

from app.core.secret_crypto import decrypt_secret
from app.services.webhook import sign_payload
from app.tasks.proofread_task import _get_sync_engine

MAX_RETRIES = 5
TIMEOUT_SECONDS = 10


@shared_task(
    bind=True,
    name="webhook.deliver",
    autoretry_for=(httpx.HTTPError,),
    retry_backoff=8,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=MAX_RETRIES,
)
def webhook_deliver(self, api_key_id: int, event: dict):
    """
    投递回调事件到 api_keys.webhook_url。

    - 2xx 视为成功；非 2xx 与网络异常都走 Celery 重试（指数退避，最多 5 次）
    - 密钥不存在/已删除/webhook 未配置：静默跳过
    - 签名密钥解不开（SECRET_KEY 变更）：跳过并告警，不重试
    """
    from app.models.api_key import ApiKey

    body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()

    with Session(_get_sync_engine()) as session:
        key = session.get(ApiKey, api_key_id)
        if key is None or not key.webhook_url:
            return {"skipped": True, "reason": "webhook not configured"}
        if key.webhook_secret:
            secret = decrypt_secret(key.webhook_secret)
            if not secret:
                logger.warning(f"[Webhook] 密钥 {api_key_id} 签名密钥解密失败，跳过投递")
                return {"skipped": True, "reason": "secret undecryptable"}
        else:
            secret = ""
        url = key.webhook_url

    signature = sign_payload(secret, body) if secret else None
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "TextMirror-Webhook/1.0",
        "X-TextMirror-Event": event.get("event", ""),
        "X-TextMirror-Delivery": str(self.request.id),
    }
    if signature:
        headers["X-TextMirror-Signature"] = signature

    try:
        resp = httpx.post(url, content=body, headers=headers, timeout=TIMEOUT_SECONDS, follow_redirects=False)
    except httpx.HTTPError as e:
        logger.warning(
            f"[Webhook] 投递失败(将重试 {self.request.retries}/{MAX_RETRIES}) "
            f"key={api_key_id} event={event.get('event')} url={url}: {type(e).__name__}: {e}"
        )
        raise

    if 200 <= resp.status_code < 300:
        logger.info(f"[Webhook] 投递成功 key={api_key_id} event={event.get('event')} status={resp.status_code}")
        return {"delivered": True, "status_code": resp.status_code}

    # 3xx 不跟随重定向（重定向可能被用于绕过地址校验）；4xx/5xx 重试（410 Gone 等明确放弃场景不区分，统一重试到上限）
    logger.warning(
        f"[Webhook] 目标返回非 2xx(将重试 {self.request.retries}/{MAX_RETRIES}) "
        f"key={api_key_id} event={event.get('event')} status={resp.status_code}"
    )
    raise httpx.HTTPStatusError(
        f"webhook target returned {resp.status_code}",
        request=resp.request,
        response=resp,
    )
