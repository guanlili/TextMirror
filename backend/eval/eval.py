"""
审校评测：默认沿用固定集；--feedback-only 仅运行人工确认的反馈样例。

用法（容器内）：
    python -m eval.eval
    EVAL_CONFIG_ID=13 python -m eval.eval
    python -m eval.eval --rounds 3 --output eval-report.json
    python -m eval.eval --feedback-only --config-ids 13,14 --feedback-ids 1,2

反馈模式输出 FeedbackEvaluation JSON，会产生正常模型费用，不扣用户配额。
固定集并发 4 跑样本；--rounds 连跑多轮，--output 把各轮明细与聚合指标写成 JSON。
固定集退出码：0 表示所有轮次自动判分通过（人工项另行验收），1 表示任一轮有失败或执行错误。
LLM 输出有随机性：结论看多轮趋势（均值/极差），不看单次绝对值。
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
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

EVAL_CONCURRENCY = 4  # 与反馈评测 MAX_CONCURRENCY 对齐，避免两条评测链路同时打满供应商限流。
MAX_ROUNDS = 10


def _format_sample_line(result: dict) -> str:
    detail = ""
    if result.get("error"):
        detail += f" 错误={result['error']}"
    if result.get("missed"):
        detail += f" 漏={result['missed']}"
    if result.get("false_issues"):
        detail += f" 误报={result['false_issues']}"
    if result.get("degraded"):
        detail += f" 幻觉降级={result['degraded']}"
    if result["expectation"] == "manual":
        if result["status"] == "REVIEW":
            detail += " 需人工确认检出内容，不计自动通过"
        elif result["status"] == "FAIL":
            detail += " 未检出应报问题"
    return f"[{result['status']}] {result['id']}({result['dim']}): {result.get('issue_count', 0)}条{detail}"


async def run_fixed_round(samples: list[dict], config_id: Optional[int]) -> list[dict]:
    slots = asyncio.Semaphore(EVAL_CONCURRENCY)

    async def limited(sample: dict) -> dict:
        async with slots:
            result = await run_sample(sample, config_id)
        print(_format_sample_line(result))
        return result

    return list(await asyncio.gather(*(limited(sample) for sample in samples)))


def round_metrics(results: list[dict]) -> dict:
    clean_total = sum(item["expectation"] == "no_report" for item in results)
    return {
        "anchor_hit": sum(item["anchors_hit"] for item in results),
        "anchor_total": sum(item["anchors_total"] for item in results),
        "clean_pass": sum(item["status"] == "PASS" for item in results if item["expectation"] == "no_report"),
        "clean_total": clean_total,
        "false_total": sum(len(item["false_issues"]) for item in results),
        "degraded_total": sum(item["degraded"] for item in results),
        "review": sum(item["status"] == "REVIEW" for item in results),
        "fail": sum(item["status"] == "FAIL" for item in results),
        "error": sum(item["status"] == "ERROR" for item in results),
    }


def dim_rows(results: list[dict]) -> list[dict]:
    dims: dict[str, dict] = {}
    for item in results:
        row = dims.setdefault(item["dim"], {"dim": item["dim"], "anchor_hit": 0, "anchor_total": 0,
                                            "clean_pass": 0, "clean_total": 0, "review": 0, "fail": 0, "error": 0})
        if item["expectation"] == "no_report":
            row["clean_total"] += 1
            row["clean_pass"] += item["status"] == "PASS"
        else:
            row["anchor_total"] += item["anchors_total"]
            row["anchor_hit"] += item["anchors_hit"]
            row["review"] += item["status"] == "REVIEW"
        row["fail"] += item["status"] == "FAIL"
        row["error"] += item["status"] == "ERROR"
    return list(dims.values())


def print_round_summary(metrics: dict, dims: list[dict]) -> None:
    recall = metrics["anchor_hit"] / max(metrics["anchor_total"], 1) * 100
    print("=" * 70)
    print(f"锚点召回率: {metrics['anchor_hit']}/{metrics['anchor_total']} = {recall:.0f}%")
    print(f"零误报通过: {metrics['clean_pass']}/{metrics['clean_total']}")
    print(f"误报总数: {metrics['false_total']}")
    print(f"幻觉降级: {metrics['degraded_total']}")
    print(f"人工待验收: {metrics['review']}")
    print(f"评测失败: {metrics['fail']}")
    print(f"评测错误: {metrics['error']}")
    print("按维度:")
    for row in dims:
        parts = []
        if row["anchor_total"]:
            parts.append(f"锚点 {row['anchor_hit']}/{row['anchor_total']}")
        if row["clean_total"]:
            parts.append(f"零误报 {row['clean_pass']}/{row['clean_total']}")
        if row["review"]:
            parts.append(f"REVIEW {row['review']}")
        parts += [f"FAIL {row['fail']}", f"ERROR {row['error']}"]
        print(f"  {row['dim']}: " + " | ".join(parts))
    print("=" * 70)


def aggregate_metrics(metrics_list: list[dict]) -> dict:
    def stats(values):
        return {"mean": sum(values) / len(values), "min": min(values), "max": max(values)}

    return {
        "anchor_recall": stats([round(item["anchor_hit"] / max(item["anchor_total"], 1) * 100, 1)
                                for item in metrics_list]),
        "clean_pass": stats([item["clean_pass"] for item in metrics_list]),
        "false_total": stats([item["false_total"] for item in metrics_list]),
        "degraded_total": stats([item["degraded_total"] for item in metrics_list]),
        "review": stats([item["review"] for item in metrics_list]),
        "fail": stats([item["fail"] for item in metrics_list]),
        "error": stats([item["error"] for item in metrics_list]),
    }


async def run_sample(sample: dict, config_id: Optional[int]) -> dict:
    expectation = sample.get("expectation", "report")
    anchors = sample["expect"]
    error = {
        "id": sample["id"], "dim": sample["dim"], "error": EVALUATION_ERROR,
        "expectation": expectation,
        "status": "ERROR", "hit": [], "false_issues": [], "degraded": 0,
        "anchors_total": len(anchors), "anchors_hit": 0,
        "missed": list(anchors), "issue_count": 0,
    }
    try:
        result = await proofread_text(
            text=sample["text"], domain=sample.get("domain", "general"), config_id=config_id,
        )
        if not has_complete_result(result):
            return error
        issues = result["issues"]
        hit_anchors = []
        for anchor in anchors:
            if any(anchor in (i.get("original") or "") for i in issues):
                hit_anchors.append(anchor)
        # 固定集维持既有锚点口径；反馈模式使用独立、精确的目标坐标口径。
        false_issues = [(i.get("original") or "")[:20] for i in issues] if expectation == "no_report" else []
        if expectation == "no_report":
            status = "FAIL" if issues else "PASS"
        elif expectation == "manual":
            status = "REVIEW" if issues else "FAIL"
        else:
            status = "PASS" if len(hit_anchors) == len(anchors) else "FAIL"
        degraded = sum(1 for i in issues if "原文定位失败" in (i.get("explanation") or ""))
        return {
            "id": sample["id"], "dim": sample["dim"], "expectation": expectation,
            "status": status,
            "anchors_total": len(anchors), "anchors_hit": len(hit_anchors),
            "missed": [a for a in anchors if a not in hit_anchors],
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
    parser.add_argument("--rounds", type=int, default=1, help=f"固定集连跑 1..{MAX_ROUNDS} 轮并输出聚合趋势")
    parser.add_argument("--output", help="把各轮明细与聚合指标写入 JSON 文件")
    args = parser.parse_args(argv)
    config_env = os.environ.get("EVAL_CONFIG_ID")
    if args.feedback_only:
        if args.rounds != 1 or args.output is not None:
            parser.error("--rounds/--output 仅用于固定集")
        values = args.config_ids or ([config_env] if config_env else None)
        if not values:
            parser.error("反馈评测须指定 --config-ids 或 EVAL_CONFIG_ID，不自动选择活跃模型")
        args.config_ids = _ids(values, 4, parser, "模型 ID")
        args.feedback_ids = _ids(args.feedback_ids, 10, parser, "样例 ID") if args.feedback_ids is not None else None
    else:
        if args.config_ids is not None or args.feedback_ids is not None:
            parser.error("--config-ids/--feedback-ids 仅用于 --feedback-only")
        if not 1 <= args.rounds <= MAX_ROUNDS:
            parser.error(f"--rounds 必须为 1 至 {MAX_ROUNDS} 的整数")
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
    if args.rounds > 1:
        print(f"连跑轮数: {args.rounds}")
    print("=" * 70)
    rounds_data = []
    for index in range(args.rounds):
        if args.rounds > 1:
            print(f"--- 第 {index + 1}/{args.rounds} 轮 ---")
        results = await run_fixed_round(SAMPLES, config_id)
        metrics = round_metrics(results)
        dims = dim_rows(results)
        print_round_summary(metrics, dims)
        rounds_data.append({"metrics": metrics, "dims": dims, "samples": results})
    aggregate = aggregate_metrics([entry["metrics"] for entry in rounds_data])
    if args.rounds > 1:
        clean_total = rounds_data[0]["metrics"]["clean_total"]
        print(f"多轮汇总({args.rounds}轮): "
              f"锚点召回率 均值{aggregate['anchor_recall']['mean']:.1f}%"
              f"（{aggregate['anchor_recall']['min']:.1f}~{aggregate['anchor_recall']['max']:.1f}）"
              f" | 零误报 均值{aggregate['clean_pass']['mean']:.1f}/{clean_total}"
              f" | 误报 均值{aggregate['false_total']['mean']:.1f}"
              f" | 失败 均值{aggregate['fail']['mean']:.1f}"
              f" | 错误 均值{aggregate['error']['mean']:.1f}")
    if args.output:
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "config_id": config_id,
            "rounds": rounds_data,
            "aggregate": aggregate,
        }
        try:
            Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            print(f"评测报告写入失败: {args.output}", file=sys.stderr)
            return 1
        print(f"评测报告已写入: {args.output}")
    return int(any(entry["metrics"]["fail"] or entry["metrics"]["error"] for entry in rounds_data))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
