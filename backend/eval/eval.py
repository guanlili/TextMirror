"""
审校评测跑分脚本：对固定评测集跑完整审校链路，输出量化指标。

用法（容器内）：
    docker exec textmirror-dev-backend python -m eval.eval
    # 指定模型配置（基线跑分须与生产活跃模型一致，防 dev 活跃配置漂移）：
    docker exec -e EVAL_CONFIG_ID=13 textmirror-dev-backend python -m eval.eval

指标：
- 召回率（锚点维度）：期望锚点被 issue 覆盖的比例
- 误报数（零误报样本维度）：clean 样本上报出的 issue 数
- 幻觉率：LLM issue 中 original 不在原文（自校验降级）的比例
- 按维度分组明细

LLM 输出有随机性：结论看趋势（多次跑分对比），不看单次绝对值。
"""
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.dataset import SAMPLES  # noqa: E402
from app.services.proofread import proofread_text  # noqa: E402


async def run_sample(sample: dict, config_id: Optional[int]) -> dict:
    try:
        result = await proofread_text(
            text=sample["text"],
            domain=sample.get("domain", "general"),
            config_id=config_id,
        )
    except Exception as e:
        return {"id": sample["id"], "dim": sample["dim"], "error": str(e),
                "hit": [], "false_issues": [], "degraded": 0,
                "anchors_total": len(sample.get("expect", [])), "anchors_hit": 0,
                "missed": list(sample.get("expect", [])), "issue_count": 0}

    issues = result.get("issues", [])
    hit_anchors = []
    for anchor in sample.get("expect", []):
        if any(anchor in (i.get("original") or "") for i in issues):
            hit_anchors.append(anchor)

    # 零误报样本：全部 issue 都算误报
    false_issues = []
    if sample["dim"] == "零误报":
        false_issues = [i.get("original", "")[:20] for i in issues]

    # 幻觉降级数（自校验标记）
    degraded = sum(1 for i in issues if "原文定位失败" in (i.get("explanation") or ""))

    return {
        "id": sample["id"], "dim": sample["dim"],
        "anchors_total": len(sample.get("expect", [])),
        "anchors_hit": len(hit_anchors),
        "missed": [a for a in sample.get("expect", []) if a not in hit_anchors],
        "false_issues": false_issues,
        "issue_count": len(issues),
        "degraded": degraded,
    }


async def main():
    config_env = os.environ.get("EVAL_CONFIG_ID")
    config_id = int(config_env) if config_env else None

    print("=" * 70)
    print("TextMirror 审校评测（固定集跑分）")
    if config_id:
        print(f"指定模型配置: id={config_id}")
    print("=" * 70)

    results = []
    for s in SAMPLES:
        r = await run_sample(s, config_id)
        results.append(r)
        status = "PASS" if (r.get("anchors_hit") == r.get("anchors_total") and not r.get("false_issues")) else "FAIL"
        detail = ""
        if r.get("error"):
            detail += f" 错误={r['error']}"
        if r.get("missed"):
            detail += f" 漏={r['missed']}"
        if r.get("false_issues"):
            detail += f" 误报={r['false_issues']}"
        if r.get("degraded"):
            detail += f" 幻觉降级={r['degraded']}"
        print(f"[{status}] {r['id']}({r['dim']}): {r.get('issue_count', 0)}条{detail}")

    # 汇总
    anchor_total = sum(r.get("anchors_total", 0) for r in results)
    anchor_hit = sum(r.get("anchors_hit", 0) for r in results)
    false_total = sum(len(r.get("false_issues", [])) for r in results)
    degraded_total = sum(r.get("degraded", 0) for r in results)
    clean_samples = [r for r in results if r["dim"] == "零误报"]
    clean_fail = sum(1 for r in clean_samples if r.get("false_issues"))

    print("=" * 70)
    print(f"锚点召回率: {anchor_hit}/{anchor_total} = {anchor_hit / max(anchor_total, 1) * 100:.0f}%")
    print(f"零误报通过: {len(clean_samples) - clean_fail}/{len(clean_samples)}")
    print(f"误报总数: {false_total}")
    print(f"幻觉降级: {degraded_total}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
