"""
多模型并发审校对比的公共逻辑
Web 端 /proofread/compare 与开放 API /open/proofread/compare 共用，
两端仅保留各自的配额口径、审计动作名与错误契约。
"""
import asyncio
import time
from typing import Dict, List, Optional, Tuple

from loguru import logger

from app.services.proofread import proofread_text


async def run_proofread_compare(
    text: str,
    domain: str,
    config_ids: List[int],
    configs: Dict[int, object],
    user_id: Optional[int],
    log_tag: str = "对比校对",
) -> Tuple[List[dict], List[str], Dict[int, List[str]]]:
    """
    并发用多个模型审校同一文本。
    :param configs: {config_id: LLMConfig}，调用方已完成启用校验
    :return: (items, consensus_originals, only_in)；items 为 dict，字段与
             ModelProofreadResult / OpenCompareModelResult 对齐，调用方直接 **item 构造
    """

    async def _run_one(config):
        t0 = time.perf_counter()
        base = {"config_id": config.id, "config_name": config.name, "model": config.model}
        try:
            r = await proofread_text(
                text=text,
                domain=domain,
                config_id=config.id,
                user_id=user_id,
            )
            return {
                **base,
                "issues": r["issues"],
                "total_issues": r["total_issues"],
                "success": True,
                "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            }
        except Exception as e:
            # 异常原文可能含密钥片段/内部路径：详情记日志，调用方只给友好提示
            logger.error(f"[{log_tag}] 模型 {config.name} 失败: {e}")
            return {
                **base,
                "success": False,
                "error": f"模型 {config.name} 调用失败，请检查该配置的密钥与模型名（详情见服务端日志）",
                "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            }

    items = await asyncio.gather(*[_run_one(c) for c in configs.values()])
    items = sorted(items, key=lambda i: config_ids.index(i["config_id"]))
    consensus, only_in = cross_model_stats(items)
    return items, consensus, only_in


def cross_model_stats(items: List[dict]) -> Tuple[List[str], Dict[int, List[str]]]:
    """
    交叉统计：original 完全一致的问题算「共识」，仅单一模型发现的算「独有」。
    成功模型 ≥2 才有意义，否则返回空。
    """
    consensus: List[str] = []
    only_in: Dict[int, List[str]] = {}
    ok_ids = [i["config_id"] for i in items if i["success"]]
    if len(ok_ids) < 2:
        return consensus, only_in

    owner_map: Dict[str, List[int]] = {}
    for i in items:
        if not i["success"]:
            continue
        for iss in i.get("issues") or []:
            orig = (iss.get("original") or "").strip()
            if orig:
                owner_map.setdefault(orig, []).append(i["config_id"])
    for orig, owners in owner_map.items():
        if len(set(owners)) == len(ok_ids):
            consensus.append(orig)
        elif len(set(owners)) == 1:
            only_in.setdefault(owners[0], []).append(orig)
    return consensus, only_in
