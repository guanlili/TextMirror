"""Local, code-point-based article scoring; candidate gold is always provisional."""
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from math import ceil, isfinite

_DOMAINS = {"general", "official", "legal", "auto"}
_CATEGORIES = {"typo", "grammar", "logic", "format", "fact"}
_TYPES = {"typo", "grammar", "logic", "style", "sensitive", "punctuation"}
_SUBSTANTIVE = {"typo", "grammar", "logic"}
_METRICS = ("report", "substantive", "format", "fact", "no_report")
_COUNTS = (
    "issue_count", "true_positives", "false_positives", "no_report_false_positives",
    "unmatched_false_positives", "duplicates", "unjudged", "invalid", "classification_mismatches",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _choice(value: object, choices: set[str]) -> bool:
    return isinstance(value, str) and value in choices


def _span(text: str, item: dict, *, fallback: bool = False) -> tuple[int, int]:
    original = item.get("original")
    _require(isinstance(original, str) and bool(original), "original must be a nonempty string")
    if fallback and "start" not in item and "end" not in item:
        start = text.find(original)
        _require(start >= 0 and text.find(original, start + 1) < 0, "original is absent or ambiguous")
        return start, start + len(original)
    start, end = item.get("start"), item.get("end")
    _require(type(start) is int and type(end) is int, "coordinates must be integers, not null/bool")
    _require(0 <= start < end <= len(text) and text[start:end] == original, "coordinates do not match text")
    return start, end


def validate_samples(raw: list) -> list[dict]:
    """Validate and copy samples. Overlapping gold is ambiguous; annotate each place once."""
    _require(isinstance(raw, list), "samples must be a list")
    samples, ids = [], set()
    for sample in raw:
        _require(isinstance(sample, dict), "sample must be an object")
        _require(_nonempty(sample.get("id")), "id must be a nonempty string")
        _require(sample["id"] not in ids, "sample ids must be unique")
        ids.add(sample["id"])
        _require(_nonempty(sample.get("text")), "text must be a nonempty string")
        _require(_choice(sample.get("domain"), _DOMAINS), "invalid domain")
        status = sample.get("gold_status", "candidate")
        _require(_choice(status, {"candidate", "confirmed"}), "invalid gold_status")
        complete = sample.get("complete_gold", False)
        _require(type(complete) is bool, "complete_gold must be boolean")
        _require(isinstance(sample.get("targets"), list), "targets must be a list")
        targets = []
        for target in sample["targets"]:
            _require(isinstance(target, dict), "target must be an object")
            start, end = _span(sample["text"], target)
            _require(_choice(target.get("category"), _CATEGORIES), "invalid category")
            _require(_choice(target.get("expectation"), {"report", "no_report"}), "invalid expectation")
            accepted = target.get("accepted_suggestions", [])
            _require(isinstance(accepted, list) and all(isinstance(s, str) for s in accepted),
                     "accepted_suggestions must be a list of strings")
            _require(len(set(accepted)) == len(accepted), "duplicate accepted suggestions")
            _require(target["expectation"] != "no_report" or not accepted,
                     "no_report cannot have accepted suggestions")
            targets.append({"start": start, "end": end, "original": target["original"],
                            "category": target["category"], "expectation": target["expectation"],
                            "accepted_suggestions": list(accepted)})
        ordered = sorted(targets, key=lambda t: (t["start"], t["end"]))
        _require(all(a["end"] <= b["start"] for a, b in zip(ordered, ordered[1:])),
                 "duplicate, overlapping or conflicting gold targets")
        samples.append({"id": sample["id"], "text": sample["text"], "domain": sample["domain"],
                        "gold_status": status, "complete_gold": complete, "targets": targets})
    return samples


def _complete(result: object) -> bool:
    if not isinstance(result, dict):
        return False
    coverage = result.get("coverage")
    if not (result.get("success", True) is True and not result.get("error")
            and result.get("complete", True) is True and isinstance(result.get("issues"), list)
            and isinstance(coverage, dict) and coverage.get("status") == "complete"
            and coverage.get("failed_chunks") == []):
        return False
    if "total_chunks" in coverage or "completed_chunks" in coverage:
        total, completed = coverage.get("total_chunks"), coverage.get("completed_chunks")
        return type(total) is int and type(completed) is int and total >= 0 and total == completed
    return True


def _compatible(issue: dict, target: dict) -> bool:
    return ((issue["start"] <= target["start"] and target["end"] <= issue["end"])
            or (target["start"] <= issue["start"] and issue["end"] <= target["end"]))


@dataclass
class _Edge:
    to: int
    reverse: int
    capacity: int
    cost: int


def _match(groups: list[dict], targets: list[dict]) -> dict[int, int]:
    """Min-cost maximum flow: maximum cardinality, then maximum exact-span matches."""
    ordered = sorted(range(len(targets)), key=lambda i: (targets[i]["start"], targets[i]["end"]))
    target_base, sink = 1 + len(groups), 1 + len(groups) + len(targets)
    graph: list[list[_Edge]] = [[] for _ in range(sink + 1)]

    def add(left: int, right: int, cost: int = 0) -> _Edge:
        forward = _Edge(right, len(graph[right]), 1, cost)
        backward = _Edge(left, len(graph[left]), 0, -cost)
        graph[left].append(forward)
        graph[right].append(backward)
        return forward

    links = []
    for i, group in enumerate(groups):
        add(0, i + 1)
        for j, target_index in enumerate(ordered):
            target = targets[target_index]
            if _compatible(group, target):
                exact = (group["start"], group["end"]) == (target["start"], target["end"])
                edge = add(i + 1, target_base + j, int(not exact))
                links.append((i, target_index, edge))
    for j in range(len(targets)):
        add(target_base + j, sink)

    while True:
        distances = [float("inf")] * len(graph)
        distances[0] = 0
        previous: list[tuple[int, int] | None] = [None] * len(graph)
        for _ in range(len(graph) - 1):
            changed = False
            for node, edges in enumerate(graph):
                for index, edge in enumerate(edges):
                    if edge.capacity and distances[node] + edge.cost < distances[edge.to]:
                        distances[edge.to] = distances[node] + edge.cost
                        previous[edge.to] = (node, index)
                        changed = True
            if not changed:
                break
        if previous[sink] is None:
            break
        node = sink
        while node:
            parent, index = previous[node]
            edge = graph[parent][index]
            edge.capacity -= 1
            graph[node][edge.reverse].capacity += 1
            node = parent
    return {target: group for group, target, edge in links if edge.capacity == 0}


def _suggestion(text: str, target: dict, issue: dict) -> tuple[str, str]:
    accepted = target["accepted_suggestions"]
    if not accepted:
        return "not_evaluated", "no_accepted_gold"
    replacement = issue.get("suggestion")
    if replacement is None:
        return "not_evaluated", "missing_suggestion"
    if replacement == "":
        reason = "requires_deletion_patch" if issue["type"] == "sensitive" else "unavailable_suggestion"
        return "not_evaluated", reason
    start, end = issue["start"], issue["end"]
    if start <= target["start"] and target["end"] <= end:
        before, after = text[start:target["start"]], text[target["end"]:end]
        if (len(replacement) < len(before) + len(after)
                or not replacement.startswith(before) or not replacement.endswith(after)):
            return "not_evaluated", "incomparable_context"
    actual = text[:start] + replacement + text[end:]
    expected = {text[:target["start"]] + value + text[target["end"]:] for value in accepted}
    return ("pass", "accepted") if actual in expected else ("fail", "not_accepted")


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _totals(targets: list[dict]) -> dict[str, int]:
    reports = [t for t in targets if t["expectation"] == "report"]
    return {"report": len(reports), "substantive": sum(t["category"] in _SUBSTANTIVE for t in reports),
            "format": sum(t["category"] == "format" for t in reports),
            "fact": sum(t["category"] == "fact" for t in reports),
            "no_report": len(targets) - len(reports)}


def _precision(scores: list[dict], gold_status: str) -> dict:
    eligible = [s for s in scores if s["status"] == "complete" and s["complete_gold"]
                and s["unjudged"] == 0 and s["invalid"] == 0]
    tp = sum(s["true_positives"] for s in eligible)
    fp = sum(s["false_positives"] for s in eligible)
    value = _ratio(tp, tp + fp)
    return {"precision": value if gold_status == "confirmed" else None,
            "provisional_precision": value if gold_status == "candidate" else None,
            "precision_basis": ("confirmed_complete_gold" if gold_status == "confirmed"
                                else "provisional_candidate_complete_gold") if eligible else "not_evaluated",
            "precision_subset": {"runs": len(eligible), "true_positives": tp, "false_positives": fp}}


def score_article(sample: dict, result: object) -> dict:
    """Score one response. Incomplete results have null quality counts, never a clean pass."""
    sample = validate_samples([sample])[0]
    complete = _complete(result)
    issues = deepcopy(result.get("issues")) if isinstance(result, dict) else None
    totals = _totals(sample["targets"])
    score = {"sample_id": sample["id"], "domain": sample["domain"], "gold_status": sample["gold_status"],
             "provisional": sample["gold_status"] == "candidate", "complete_gold": sample["complete_gold"],
             "status": "complete" if complete else "error", "error": None if complete else "incomplete_result",
             "raw_issues": issues, "issue_details": [], "targets": [],
             "suggestions": {"pass": 0, "fail": 0, "not_evaluated": totals["report"]},
             "outputs_by_type": {kind: 0 for kind in sorted(_TYPES)}}
    for name, total in totals.items():
        score[name] = {"total": total, "evaluated": total if complete else 0,
                       "hit": 0 if complete else None, "missed": total if complete else None,
                       "recall": 0.0 if complete and total and name != "no_report" else None}
    score.update({key: 0 if complete else None for key in _COUNTS})
    for index, target in enumerate(sample["targets"]):
        score["targets"].append({**deepcopy(target), "target_index": index, "detected": False if complete else None,
                                 "issue_indices": [], "classification_mismatch": None,
                                 "detection_status": ("missed" if target["expectation"] == "report" else "clear")
                                 if complete else "not_evaluated", "suggestion_status": "not_evaluated",
                                 "suggestion_reason": "not_detected" if complete else "incomplete_result"})
    if not complete:
        score.update(_precision([], sample["gold_status"]))
        return score

    score["issue_count"] = len(issues)
    # Detection is place-level: labels and suggestions do not create additional groups.
    grouped: dict[tuple[int, int], dict] = {}
    for index, issue in enumerate(issues):
        detail = {"issue_index": index, "status": "invalid", "reason": None, "target_index": None,
                  "duplicate_of": None, "suggestion_status": "not_evaluated", "suggestion_reason": "unmatched"}
        score["issue_details"].append(detail)
        try:
            _require(isinstance(issue, dict), "issue must be an object")
            start, end = _span(sample["text"], issue, fallback=True)
            _require(_choice(issue.get("type"), _TYPES), "invalid issue type")
            _require(issue.get("suggestion") is None or isinstance(issue["suggestion"], str),
                     "suggestion must be a string or null")
        except ValueError as exc:
            score["invalid"] += 1
            detail["reason"] = str(exc)
            continue
        key = (start, end)
        group = grouped.setdefault(key, {"start": start, "end": end, "types": set(), "indices": []})
        group["types"].add(issue["type"])
        group["indices"].append(index)
        detail.update(start=start, end=end, type=issue["type"], status="unmatched")
    groups = [grouped[key] for key in sorted(grouped)]
    matches = _match(groups, sample["targets"])
    group_targets = {group: target for target, group in matches.items()}
    for group_index, group in enumerate(groups):
        indices = group["indices"]
        for kind in group["types"]:
            score["outputs_by_type"][kind] += 1
        score["duplicates"] += len(indices) - 1
        target_index = group_targets.get(group_index)
        if target_index is None:
            # Explicit negatives remain known false positives after their first hit.
            target_index = min((i for i, t in enumerate(sample["targets"])
                                if t["expectation"] == "no_report" and _compatible(group, t)),
                               key=lambda i: ((group["start"], group["end"]) !=
                                              (sample["targets"][i]["start"], sample["targets"][i]["end"]), i),
                               default=None)
        if target_index is None:
            outcome = "false_positive" if sample["complete_gold"] else "unjudged"
            score["unmatched_false_positives" if sample["complete_gold"] else "unjudged"] += 1
        else:
            target = score["targets"][target_index]
            report = target["expectation"] == "report"
            outcome = "true_positive" if report else "false_positive"
            expected_types = {"format": {"punctuation", "style"}, "fact": {"logic"}}.get(
                target["category"], {target["category"]})
            mismatch = bool(group["types"] - expected_types)
            target["issue_indices"].extend(indices)
            target.update(detected=True, classification_mismatch=mismatch or bool(target["classification_mismatch"]),
                          detection_status="hit" if report else "false_positive")
            score["classification_mismatches"] += int(mismatch)
            if not report:
                score["no_report_false_positives"] += 1
            outcomes = []
            for index in indices:
                status, reason = (_suggestion(sample["text"], target, {**issues[index], **group})
                                  if report else ("not_evaluated", "no_report"))
                score["issue_details"][index].update(suggestion_status=status, suggestion_reason=reason)
                outcomes.append((status, reason))
            # All duplicate proposals are inspected; a good alternative cannot hide a bad one.
            status, reason = min(outcomes, key=lambda item: ({"fail": 0, "not_evaluated": 1, "pass": 2}[item[0]], item[1]))
            target.update(suggestion_status=status, suggestion_reason=reason)
        for offset, index in enumerate(indices):
            score["issue_details"][index].update(status="duplicate" if offset else outcome,
                                                  target_index=target_index,
                                                  duplicate_of=indices[0] if offset else None)

    hits = _totals([t for t in score["targets"] if t["detected"]])
    for name in _METRICS:
        score[name].update(hit=hits[name], missed=totals[name] - hits[name],
                           recall=_ratio(hits[name], totals[name]) if name != "no_report" else None)
    score["true_positives"] = hits["report"]
    score["false_positives"] = score["no_report_false_positives"] + score["unmatched_false_positives"]
    score["suggestions"] = dict.fromkeys(("pass", "fail", "not_evaluated"), 0)
    for target in score["targets"]:
        if target["expectation"] == "report":
            score["suggestions"][target["suggestion_status"]] += 1
    score.update(_precision([score], sample["gold_status"]))
    return score


def _latency(values: list[float]) -> dict:
    ordered = sorted(values)
    return {"count": len(ordered), "p50_seconds": ordered[ceil(len(ordered) * .50) - 1] if ordered else None,
            "p95_seconds": ordered[ceil(len(ordered) * .95) - 1] if ordered else None,
            "method": "nearest-rank", "scope": "all_attempts", "small_sample": len(ordered) < 20,
            "small_sample_threshold": 20}


def summarize_runs(runs: list[dict]) -> dict:
    """Micro-average successful scores by depth/gold status, tracking failed attempts and unknown gold separately."""
    _require(isinstance(runs, list), "runs must be a list")
    templates, seen = {}, set()
    for run in runs:
        _require(isinstance(run, dict), "run must be an object")
        _require(_nonempty(run.get("sample_id")) and _nonempty(run.get("depth")), "invalid sample_id/depth")
        _require(type(run.get("round")) is int and run["round"] >= 0, "round must be a nonnegative integer")
        _require(_choice(run.get("status"), {"complete", "timeout", "error"}), "invalid run status")
        elapsed = run.get("elapsed_seconds")
        _require(type(elapsed) in (int, float) and isfinite(elapsed) and elapsed >= 0, "invalid elapsed_seconds")
        key = (run["sample_id"], run["depth"], run["round"])
        _require(key not in seen, "duplicate run")
        seen.add(key)
        _require("score" in run and (run["score"] is None or isinstance(run["score"], dict)), "invalid score")
        score = run["score"]
        if score is not None:
            _require(score.get("sample_id") == run["sample_id"], "score sample_id mismatch")
            _require(_choice(score.get("gold_status"), {"candidate", "confirmed"}), "invalid score gold_status")
            _require(_choice(score.get("status"), {"complete", "error"}), "invalid score status")
            _require(all(name in score for name in (*_METRICS, *_COUNTS, "targets", "complete_gold", "suggestions")),
                     "score must come from score_article")
            signature = (score["gold_status"], score["complete_gold"],
                         [(t["start"], t["end"], t["original"], t["category"], t["expectation"],
                           t["accepted_suggestions"]) for t in score["targets"]])
            previous = templates.get(run["sample_id"])
            _require(previous is None or previous[0] == signature, "inconsistent gold for sample_id")
            templates[run["sample_id"]] = (signature, score)

    buckets = defaultdict(list)
    for run in runs:
        template = templates.get(run["sample_id"])
        gold_status = template[1]["gold_status"] if template else "unknown"
        buckets[(run["depth"], gold_status)].append(run)
    groups = []
    for (depth, gold_status), entries in sorted(buckets.items()):
        statuses, successful = Counter(), []
        totals = dict.fromkeys(_METRICS, 0)
        unknown = 0
        for run in entries:
            score = run["score"]
            status = run["status"]
            if status == "complete" and (score is None or score["status"] != "complete"):
                status = "error"
            statuses[status] += 1
            if status == "complete":
                successful.append(score)
            template = templates.get(run["sample_id"])
            if template:
                for name in _METRICS:
                    totals[name] += template[1][name]["total"]
            else:
                unknown += 1
        group = {"depth": depth, "gold_status": gold_status,
                 "provisional": gold_status == "candidate" if gold_status != "unknown" else None,
                 "runs": len(entries), **{status: statuses[status] for status in ("complete", "timeout", "error")},
                 "completion_rate": _ratio(statuses["complete"], len(entries)), "unknown_gold_runs": unknown,
                 "latency": _latency([run["elapsed_seconds"] for run in entries]),
                 "quality_scope": "successful_runs", "recall_denominator": "evaluated_targets"}
        for name in _METRICS:
            evaluated = sum(s[name]["evaluated"] for s in successful)
            hit = sum(s[name]["hit"] for s in successful)
            group[name] = {"total": totals[name], "evaluated": evaluated, "hit": hit,
                           "missed": evaluated - hit, "unevaluated": totals[name] - evaluated,
                           "recall": _ratio(hit, evaluated) if name != "no_report" else None,
                           "attempt_recall": _ratio(hit, totals[name]) if not unknown and name != "no_report" else None}
        group.update({key: sum(s[key] for s in successful) if successful else None for key in _COUNTS})
        group["suggestions"] = {key: sum(s["suggestions"][key] for s in successful)
                                for key in ("pass", "fail", "not_evaluated")}
        group["suggestions"]["unevaluated_failed_runs"] = totals["report"] - group["report"]["evaluated"]
        group["outputs_by_type"] = {kind: sum(s["outputs_by_type"][kind] for s in successful)
                                    for kind in sorted(_TYPES)}
        group.update(_precision(successful, gold_status))
        groups.append(group)
    return {"runs": len(runs), "groups": groups}
