"""
审校评测：默认沿用固定集；--feedback-only 仅运行人工确认的反馈样例。

用法（容器内）：
    python -m eval.eval
    EVAL_CONFIG_ID=13 python -m eval.eval
    python -m eval.eval --feedback-only --config-ids 13,14 --feedback-ids 1,2

反馈模式输出 FeedbackEvaluation JSON，会产生正常模型费用，不扣用户配额。
LLM 输出有随机性：结论看趋势（多次跑分对比），不看单次绝对值。
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.dataset import SAMPLES  # noqa: E402
from app.services.proofread import proofread_text  # noqa: E402
from app.services.quality_evaluation import (  # noqa: E402
    EVALUATION_ERROR,
    REQUEST_TIMEOUT_SECONDS,
    has_complete_result,
    prepare_evaluation,
    run_evaluation,
)


async def run_sample(sample: dict, config_id: Optional[int]) -> dict:
    error = {
        "id": sample["id"], "dim": sample["dim"], "error": EVALUATION_ERROR,
        "status": "ERROR", "hit": [], "false_issues": [], "degraded": 0,
        "anchors_total": len(sample.get("expect", [])), "anchors_hit": 0,
        "missed": list(sample.get("expect", [])), "issue_count": 0,
    }
    try:
        result = await proofread_text(
            text=sample["text"], domain=sample.get("domain", "general"), config_id=config_id,
        )
        if not has_complete_result(result):
            return error
        issues = result["issues"]
        hit_anchors = []
        for anchor in sample.get("expect", []):
            if any(anchor in (i.get("original") or "") for i in issues):
                hit_anchors.append(anchor)
        # 固定集维持既有锚点口径；反馈模式使用独立、精确的目标坐标口径。
        false_issues = [i.get("original", "")[:20] for i in issues] if sample["dim"] == "零误报" else []
        degraded = sum(1 for i in issues if "原文定位失败" in (i.get("explanation") or ""))
        return {
            "id": sample["id"], "dim": sample["dim"],
            "status": "PASS" if len(hit_anchors) == len(sample.get("expect", [])) and not false_issues else "FAIL",
            "anchors_total": len(sample.get("expect", [])), "anchors_hit": len(hit_anchors),
            "missed": [a for a in sample.get("expect", []) if a not in hit_anchors],
            "false_issues": false_issues, "issue_count": len(issues), "degraded": degraded,
        }
    except Exception:
        return error


def _ids(values, maximum, parser, name):
    try:
        ids = [int(value) for group in values for value in group.split(",")]
        if not 1 <= len(ids) <= maximum or len(ids) != len(set(ids)) or any(id_ <= 0 for id_ in ids):
            raise ValueError
        return ids
    except (ValueError, TypeError):
        parser.error(f"{name} 必须为 1 至 {maximum} 个不同的正整数")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feedback-only", action="store_true", help="只评测人工确认样例，输出 JSON（产生模型费用）")
    parser.add_argument("--config-ids", nargs="+", help="1..4 个已启用模型 ID，可用逗号或空格分隔")
    parser.add_argument("--feedback-ids", nargs="+", help="1..10 个已确认样例 ID；省略则加载全部 confirmed（最多 10 条）")
    args = parser.parse_args(argv)
    config_env = os.environ.get("EVAL_CONFIG_ID")
    if args.feedback_only:
        values = args.config_ids or ([config_env] if config_env else None)
        if not values:
            parser.error("反馈评测须指定 --config-ids 或 EVAL_CONFIG_ID，不自动选择活跃模型")
        args.config_ids = _ids(values, 4, parser, "模型 ID")
        args.feedback_ids = _ids(args.feedback_ids, 10, parser, "样例 ID") if args.feedback_ids is not None else None
    else:
        if args.config_ids is not None or args.feedback_ids is not None:
            parser.error("--config-ids/--feedback-ids 仅用于 --feedback-only")
        args.config_id = _ids([config_env], 1, parser, "EVAL_CONFIG_ID")[0] if config_env else None
    return args


async def run_feedback_cli(args):
    # 只注册 ORM，不运行 lifespan、建表或迁移。
    import app.main  # noqa: F401
    from app.core.database import async_session_factory

    deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
    async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
        async with async_session_factory() as db:
            snapshots, models = await prepare_evaluation(db, args.feedback_ids, args.config_ids)
    # context 已关闭，LLM 调用前释放事务/连接；评测超时仍由 runner 输出各 case error。
    return await run_evaluation(snapshots, models, deadline=deadline)


async def main(argv=None):
    args = parse_args(argv)
    if args.feedback_only:
        from loguru import logger

        # 底层 Provider 日志可能包含异常原文；反馈 CLI 只输出安全报告/固定提示。
        logger.disable("app")
        try:
            report = await run_feedback_cli(args)
        except Exception:
            print("反馈评测未完成，请确认样例已确认、模型已启用及服务可用。", file=sys.stderr)
            return 1
        print(report.model_dump_json())
        return 0

    import app.main  # noqa: F401

    config_id = args.config_id
    print("=" * 70)
    print("TextMirror 审校评测（固定集跑分）")
    if config_id:
        print(f"指定模型配置: id={config_id}")
    print("=" * 70)
    results = []
    for sample in SAMPLES:
        result = await run_sample(sample, config_id)
        results.append(result)
        detail = ""
        if result.get("error"):
            detail += f" 错误={result['error']}"
        if result.get("missed"):
            detail += f" 漏={result['missed']}"
        if result.get("false_issues"):
            detail += f" 误报={result['false_issues']}"
        if result.get("degraded"):
            detail += f" 幻觉降级={result['degraded']}"
        print(f"[{result['status']}] {result['id']}({result['dim']}): {result.get('issue_count', 0)}条{detail}")

    anchor_total = sum(result.get("anchors_total", 0) for result in results)
    anchor_hit = sum(result.get("anchors_hit", 0) for result in results)
    false_total = sum(len(result.get("false_issues", [])) for result in results)
    degraded_total = sum(result.get("degraded", 0) for result in results)
    clean_samples = [result for result in results if result["dim"] == "零误报"]
    clean_pass = sum(result["status"] == "PASS" for result in clean_samples)
    print("=" * 70)
    print(f"锚点召回率: {anchor_hit}/{anchor_total} = {anchor_hit / max(anchor_total, 1) * 100:.0f}%")
    print(f"零误报通过: {clean_pass}/{len(clean_samples)}")
    print(f"误报总数: {false_total}")
    print(f"幻觉降级: {degraded_total}")
    print(f"评测错误: {sum(result['status'] == 'ERROR' for result in results)}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
