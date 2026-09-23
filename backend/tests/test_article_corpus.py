"""Offline integrity and scoring checks for candidate excerpts, not model-quality evidence."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

import pytest

from eval import articles
from eval.article_metrics import score_article, summarize_runs, validate_samples

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
CORPUS_PATH = EVAL_DIR / "article_corpus.json"
RAW = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
SAMPLE_FIELDS = {"id", "text", "domain", "gold_status", "complete_gold", "targets"}
TARGET_FIELDS = {"start", "end", "original", "category", "expectation", "accepted_suggestions"}
GROUPS = defaultdict(dict)
for _sample in RAW:
    GROUPS[_sample["source_group"]][_sample["variant"]] = _sample

# Frozen excerpt hashes and ranges refer to source.extraction, not live website content.
SOURCE_MANIFEST = {
    "general-public-01": ("c5af8f7b51ef27f174893b2c7f9bd0d25fe67ce98705ed1646df2e63928e401b", 411, 1693, 11, 16),
    "general-public-02": ("cd9d7391c7c6416f5f93a24eb5fc11acd509dcabb6965a6fc8be334d8037a6ee", 2853, 3973, 29, 37),
    "general-public-03": ("ec25e5a23aa140d7a603d6e3c9e2e075b4e2bad53ea0b2e5ceeb642c939f5628", 3974, 4700, 37, 43),
    "general-public-04": ("8e30bdda5b938828a797863a879b5883498283e9a8f132216c8e462e1addfac5", 4701, 5422, 43, 49),
    "general-public-05": ("de1acda4361c8c916e76f81217c047780ce71cc1f77d10a8f94885f9d43bbdb1", 7083, 7780, 66, 72),
    "official-public-01": ("c518d8fe1b85cbe432f97500709b46b1f6fed2bee239354344e656720ad40800", 0, 984, 0, 23),
    "official-public-02": ("3f68bc9f552991f082ba3335727773757ae2a0fb201d66dfd707bf5aa09d44d6", 0, 856, 0, 19),
    "official-public-03": ("37f1092771998f6fbc4404d0c429fc0d9b0f717297501e29d7b970cd4555df9b", 0, 792, 0, 19),
    "official-public-04": ("a3d0e097bf200fc38af7f0e12797f3c16331cf217e3967e765e2153df19d3d80", 0, 960, 0, 17),
    "official-public-05": ("3efec4777ca4eca7dec959530fdf409bb8f317f5c21a905fd4dd366a88904bf3", 0, 875, 0, 16),
    "legal-public-01": ("818620d0411806f0dab4b85884c614af4383a581a7ef68ca06b41a9fee93542d", 38831, 39614, 1009, 1033),
    "legal-public-02": ("635864108cfb67bcd08500a953719658892fe57955966dd63ca2fd1039cc653d", 39615, 40364, 1033, 1056),
    "legal-public-03": ("c12403273a6d420db5ed6ba2b89bf52a7e133caefa8644dd7528f95b0c284281", 40365, 41584, 1056, 1075),
    "legal-public-04": ("eff59a189e23d4bc5a7c64f4f80de756f712393f48a9f815d39762fc2cc642db", 41585, 42352, 1075, 1090),
    "legal-public-05": ("43312718cbe35ada6dba2e62038b6a331017b552c2b5e5ac0393b4bb72484786", 42353, 43516, 1090, 1114),
}
GENERAL_URL = "https://www.mee.gov.cn/zcwj/gwywj/202106/t20210625_841836.shtml"
LEGAL_URL = ("https://www.moj.gov.cn/pub/sfbgw/zwgkztzl/2025nianzhuanti/2025mfdxcy/"
             "2025mfdxcy_mfdql/202505/t20250507_518708.html")


def replay(text, mutations):
    """Apply original-coordinate edits right-to-left; never search/replace globally."""
    for mutation in sorted(mutations, key=lambda m: m["start"], reverse=True):
        start, end = mutation["start"], mutation["end"]
        assert text[start:end] == mutation["original"]
        text = text[:start] + mutation["replacement"] + text[end:]
    return text


def complete_result(issues):
    return {"success": True, "complete": True, "issues": issues,
            "coverage": {"status": "complete", "failed_chunks": [],
                         "total_chunks": 1, "completed_chunks": 1}}


def oracle_issues(sample):
    return [{"start": t["start"], "end": t["end"], "original": t["original"],
             "type": t["category"], "suggestion": t["accepted_suggestions"][0]}
            for t in sample["targets"] if t["expectation"] == "report"]


def oracle_run(sample):
    result = complete_result(oracle_issues(sample))
    return {"sample_id": sample["id"], "depth": "standard", "round": 1,
            "status": "complete", "elapsed_seconds": 0.0, "result": result,
            "score": score_article(sample, result)}


def assert_provisional(value):
    assert value["gold_status"] == "candidate"
    assert value["provisional"] is True
    assert value["precision"] is None
    # Incomplete annotations do not support even a provisional article precision.
    assert value["provisional_precision"] is None
    assert value["precision_basis"] == "not_evaluated"
    assert value["precision_subset"]["runs"] == 0


def test_schema_domain_counts_and_candidate_scope():
    before = deepcopy(RAW)
    normalized = validate_samples(RAW)
    assert RAW == before
    assert len(normalized) == len({s["id"] for s in RAW}) == 30
    assert len({s["text"] for s in RAW}) == 30
    assert Counter(s["domain"] for s in RAW) == {"general": 10, "official": 10, "legal": 10}
    assert Counter((s["domain"], s["variant"]) for s in RAW) == {
        (domain, variant): 5 for domain in ("general", "official", "legal")
        for variant in ("original", "controlled_error")
    }
    assert set(GROUPS) == set(SOURCE_MANIFEST)
    assert len(GROUPS) == 15
    assert Counter(t["category"] for s in RAW for t in s["targets"]
                   if t["expectation"] == "report") == {"typo": 15, "grammar": 15}
    for raw, sample in zip(RAW, normalized):
        assert set(sample) == SAMPLE_FIELDS  # CLI's existing metadata-stripping contract.
        assert sample == {key: raw[key] for key in SAMPLE_FIELDS}
        assert raw["gold_status"] == "candidate"
        assert raw["complete_gold"] is False
        assert "15段" in raw["corpus_note"] and "30个派生测试样本" in raw["corpus_note"]
        assert "不是30篇独立业务稿" in raw["corpus_note"]
        assert "不是人工金标" in raw["corpus_note"] and "provisional" in raw["corpus_note"]
        if raw["domain"] == "general":
            assert "一般语体代理" in raw["domain_note"] and "非真实业务稿" in raw["domain_note"]
        assert 600 <= len(raw["text"]) <= 1400
        assert 600 <= len(re.findall(r"[\u4e00-\u9fff]", raw["text"])) <= 1400
        assert all(set(t) == TARGET_FIELDS for t in raw["targets"])
    normalized[0]["targets"][0]["original"] = "detached copy"
    assert RAW == before


def test_fifteen_frozen_sources_are_nonoverlapping_complete_excerpts():
    by_url, long_paragraphs, digests = defaultdict(list), set(), set()
    for group, pair in GROUPS.items():
        assert set(pair) == {"original", "controlled_error"}
        original, changed = pair["original"], pair["controlled_error"]
        assert original["domain"] == changed["domain"]
        assert original["source"] == changed["source"]
        source = original["source"]
        assert {"title", "url", "section", "original_text", "sha256", "retrieved_on"} <= source.keys()
        assert original["text"] == source["original_text"]
        assert source["title"] and source["section"]
        assert date.fromisoformat(source["retrieved_on"]) == date(2026, 9, 23)
        expected_url = {"general": GENERAL_URL, "legal": LEGAL_URL}.get(original["domain"])
        if expected_url is None:
            page = int(group.rsplit("-", 1)[1]) + 2
            expected_url = f"https://cpc.people.com.cn/n/2013/0222/c64387-20571365-{page}.html"
        assert source["url"] == expected_url
        assert urlparse(source["url"]).scheme == "https"
        digest = hashlib.sha256(source["original_text"].encode("utf-8")).hexdigest()
        assert digest == source["sha256"]
        digests.add(digest)
        extraction = source["extraction"]
        span, paragraph_span = extraction["document_span"], extraction["paragraph_span"]
        assert (digest, span["start"], span["end"], paragraph_span["start"],
                paragraph_span["end"]) == SOURCE_MANIFEST[group]
        assert 0 <= span["start"] < span["end"] <= extraction["document_length"]
        assert span["end"] - span["start"] == len(source["original_text"])
        assert re.fullmatch(r"[0-9a-f]{64}", extraction["document_sha256"])
        paragraphs = source["original_text"].split("\n")
        assert len(paragraphs) == paragraph_span["end"] - paragraph_span["start"]
        assert all(p and p == p.strip() for p in paragraphs)
        assert paragraphs[-1].endswith("。")  # No mid-sentence clipping.
        for paragraph in paragraphs:
            if len(paragraph) >= 40:
                assert paragraph not in long_paragraphs  # No repeated paragraph padding.
                long_paragraphs.add(paragraph)
        by_url[source["url"]].append(extraction)
    assert len(digests) == 15
    for excerpts in by_url.values():
        assert len({e["document_sha256"] for e in excerpts}) == 1
        for key in ("document_span", "paragraph_span"):
            spans = sorted((e[key]["start"], e[key]["end"]) for e in excerpts)
            assert all(left[1] <= right[0] for left, right in zip(spans, spans[1:]))


@pytest.mark.parametrize("group", sorted(GROUPS))
def test_mutations_targets_and_suggestions_replay_in_codepoints(group):
    original, changed = GROUPS[group]["original"], GROUPS[group]["controlled_error"]
    text, mutations = original["text"], changed["mutations"]
    assert original["mutations"] == []
    assert 1 <= len(mutations) <= 2
    assert mutations == sorted(mutations, key=lambda m: m["start"])
    assert all(a["end"] <= b["start"] for a, b in zip(mutations, mutations[1:]))
    assert changed["text"] == replay(text, mutations)
    assert all(t["expectation"] == "no_report" and not t["accepted_suggestions"]
               for t in original["targets"])
    # Neither Arabic numbers nor legal article numbers were manufactured as errors.
    for pattern in (r"\d+(?:\.\d+)?", r"第[一二三四五六七八九十百千万零]+条"):
        assert re.findall(pattern, text) == re.findall(pattern, changed["text"])
    report_targets = {t["start"]: t for t in changed["targets"] if t["expectation"] == "report"}
    assert len(report_targets) == len(mutations)
    shift = 0
    for index, mutation in enumerate(mutations):
        start, end = mutation["start"], mutation["end"]
        assert type(start) is type(end) is int
        assert 0 <= start < end <= len(text)
        assert text[start:end] == mutation["original"] != mutation["replacement"]
        assert mutation["category"] in {"typo", "grammar"}
        target = report_targets[start + shift]
        assert target["end"] == start + shift + len(mutation["replacement"])
        assert changed["text"][target["start"]:target["end"]] == target["original"] == mutation["replacement"]
        assert target["category"] == mutation["category"]
        assert target["accepted_suggestions"] == [mutation["original"]]
        assert any((t["start"], t["end"], t["original"], t["category"]) ==
                   (start, end, mutation["original"], mutation["category"]) for t in original["targets"])
        # Applying this suggestion removes exactly this mutation, leaving the other.
        repaired = (changed["text"][:target["start"]] + target["accepted_suggestions"][0]
                    + changed["text"][target["end"]:])
        assert repaired == replay(text, mutations[:index] + mutations[index + 1:])
        shift += len(mutation["replacement"]) - (end - start)
    restored = changed["text"]
    for target in sorted(report_targets.values(), key=lambda t: t["start"], reverse=True):
        restored = restored[:target["start"]] + target["accepted_suggestions"][0] + restored[target["end"]:]
    assert restored == changed["source"]["original_text"] == text
    anchors = [t for t in changed["targets"] if t["expectation"] == "no_report"]
    assert anchors
    for anchor in anchors:
        before = next(t for t in original["targets"] if t["original"] == anchor["original"])
        assert all(m["end"] <= before["start"] or before["end"] <= m["start"] for m in mutations)
        delta = sum(len(m["replacement"]) - len(m["original"]) for m in mutations if m["end"] <= before["start"])
        assert (anchor["start"], anchor["end"]) == (before["start"] + delta, before["end"] + delta)
        assert changed["text"][anchor["start"]:anchor["end"]] == anchor["original"]
        assert anchor["accepted_suggestions"] == []


@pytest.mark.parametrize("sample", RAW, ids=lambda sample: sample["id"])
def test_complete_empty_result_misses_only_reports_without_false_positives(sample):
    score = score_article(sample, complete_result([]))
    reports = sum(t["expectation"] == "report" for t in sample["targets"])
    assert score["status"] == "complete"
    assert score["report"]["evaluated"] == score["report"]["missed"] == reports
    assert score["report"]["hit"] == score["true_positives"] == 0
    for key in ("false_positives", "no_report_false_positives", "unmatched_false_positives",
                "invalid", "unjudged", "duplicates", "classification_mismatches"):
        assert score[key] == 0
    assert score["suggestions"] == {"pass": 0, "fail": 0, "not_evaluated": reports}
    for target in score["targets"]:
        assert target["detection_status"] == ("missed" if target["expectation"] == "report" else "clear")
    # no_report.missed in this API counts clear anchors, not missed errors.
    assert score["no_report"]["hit"] == 0
    assert_provisional(score)


@pytest.mark.parametrize("sample", RAW, ids=lambda sample: sample["id"])
def test_oracle_detects_every_mutation_and_passes_exact_suggestions(sample):
    score = score_article(sample, complete_result(oracle_issues(sample)))
    count = len(sample["mutations"])
    assert score["status"] == "complete"
    assert score["true_positives"] == score["report"]["hit"] == count
    assert score["report"]["missed"] == 0
    assert score["substantive"]["hit"] == count
    assert score["format"]["total"] == score["fact"]["total"] == 0
    assert score["suggestions"] == {"pass": count, "fail": 0, "not_evaluated": 0}
    for key in ("false_positives", "no_report_false_positives", "invalid", "unjudged",
                "duplicates", "classification_mismatches"):
        assert score[key] == 0
    assert all(t["detection_status"] == "clear" for t in score["targets"] if t["expectation"] == "no_report")
    assert_provisional(score)


def test_incomplete_coverage_is_not_scored_as_empty_success():
    for sample in RAW:
        for result in ({"issues": []}, {"issues": [], "coverage": {"status": "partial", "failed_chunks": [0]}}):
            score = score_article(sample, result)
            assert score["status"] == "error"
            assert score["report"]["evaluated"] == 0
            assert score["report"]["missed"] is None
            assert score["false_positives"] is None
            assert_provisional(score)


def test_no_report_false_positive_and_unannotated_unjudged_are_distinct():
    for sample in RAW:
        anchor = next(t for t in sample["targets"] if t["expectation"] == "no_report")
        index = next(i for i, c in enumerate(sample["text"]) if not c.isspace()
                     and not any(t["start"] <= i < t["end"] for t in sample["targets"]))
        issues = [{"start": anchor["start"], "end": anchor["end"], "original": anchor["original"],
                   "type": anchor["category"], "suggestion": "不应改写的锚点"},
                  {"start": index, "end": index + 1, "original": sample["text"][index], "type": "typo"}]
        score = score_article(sample, complete_result(issues))
        assert score["no_report_false_positives"] == score["false_positives"] == 1
        assert score["unjudged"] == 1
        assert score["unmatched_false_positives"] == score["invalid"] == 0
        assert_provisional(score)


def test_aggregate_oracle_is_provisional_not_confirmed_full_article_quality():
    summary = summarize_runs([oracle_run(sample) for sample in RAW])
    assert summary["runs"] == 30
    assert len(summary["groups"]) == 1
    group = summary["groups"][0]
    assert group["complete"] == 30
    assert group["report"]["total"] == group["report"]["hit"] == 30
    assert group["report"]["recall"] == 1.0
    assert group["no_report"]["total"] == 60
    assert group["no_report"]["hit"] == group["false_positives"] == 0
    assert group["suggestions"] == {"pass": 30, "fail": 0, "not_evaluated": 0, "unevaluated_failed_runs": 0}
    assert_provisional(group)


def test_cli_offline_rescore_accepts_raw_metadata_without_changing_input(monkeypatch):
    before = CORPUS_PATH.read_bytes()
    normalized = validate_samples(RAW)
    report = {"schema_version": 1, "capture_input_sha256": articles.fingerprint(normalized),
              "sample_fingerprints": {s["id"]: articles.sample_fingerprint(s) for s in normalized},
              "planned_runs": [{"sample_id": s["id"], "depth": "standard", "round": 1} for s in normalized],
              "runs": [oracle_run(s) for s in normalized]}
    monkeypatch.setattr(articles, "capture_runs", lambda *args: pytest.fail("No model/API calls allowed"))
    with TemporaryDirectory(prefix="textmirror-corpus-rescore-") as directory:
        previous, output = Path(directory) / "previous.json", Path(directory) / "rescored.json"
        previous.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        assert articles.main(["--input", str(CORPUS_PATH), "--output", str(output),
                              "--rescore", str(previous)]) == 0
        rescored = json.loads(output.read_text(encoding="utf-8"))
    assert rescored["annotation_sha256"] == articles.fingerprint(sorted(normalized, key=lambda s: s["id"]))
    assert rescored["summary"]["planned_completion_rate"] == 1.0
    assert_provisional(rescored["summary"]["groups"][0])
    assert CORPUS_PATH.read_bytes() == before
