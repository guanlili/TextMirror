"""多模型对比公共逻辑的交叉统计。"""
from app.services.model_compare import cross_model_stats


def _item(config_id, issues, success=True):
    return {
        "config_id": config_id,
        "success": success,
        "issues": [{"original": o} for o in issues],
    }


def test_consensus_and_only_in():
    items = [
        _item(1, ["共识问题", "只有1发现"]),
        _item(2, ["共识问题"]),
    ]
    consensus, only_in = cross_model_stats(items)
    assert consensus == ["共识问题"]
    assert only_in == {1: ["只有1发现"]}


def test_failed_model_excluded():
    items = [
        _item(1, ["A"]),
        _item(2, [], success=False),
    ]
    consensus, only_in = cross_model_stats(items)
    assert consensus == [] and only_in == {}


def test_partial_overlap_not_consensus():
    items = [
        _item(1, ["X", "Y"]),
        _item(2, ["X", "Y"]),
        _item(3, ["X"]),
    ]
    consensus, only_in = cross_model_stats(items)
    assert consensus == ["X"]
    # Y 出现在 2 个模型但不是全部，也不是单一模型独有
    assert only_in == {}
