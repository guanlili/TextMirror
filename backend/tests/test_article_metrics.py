"""Synthetic-only tests; runnable with unittest without application/DB fixtures."""
import importlib.util
import itertools
import json
import random
import sys
import unittest
from copy import deepcopy
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "eval" / "article_metrics.py"
_SPEC = importlib.util.spec_from_file_location("article_metrics_under_test", _MODULE_PATH)
metrics = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = metrics
_SPEC.loader.exec_module(metrics)
validate_samples = metrics.validate_samples
score_article = metrics.score_article
summarize_runs = metrics.summarize_runs

TEXT = "首\U00020000错词，后错词。终"


def target(start=2, end=4, *, text=TEXT, **changes):
    value = {"start": start, "end": end, "original": text[start:end],
             "category": "typo", "expectation": "report"}
    value.update(changes)
    return value


def sample(*, text=TEXT, targets=None, **changes):
    value = {"id": "synthetic", "text": text, "domain": "general",
             "targets": [target(text=text)] if targets is None else targets}
    value.update(changes)
    return value


def issue(start=2, end=4, *, text=TEXT, **changes):
    value = {"start": start, "end": end, "original": text[start:end], "type": "typo", "suggestion": "正词"}
    value.update(changes)
    return value


def result(issues=None, **changes):
    value = {"issues": [] if issues is None else issues,
             "coverage": {"status": "complete", "failed_chunks": [], "total_chunks": 1, "completed_chunks": 1}}
    value.update(changes)
    return value


def run(score=None, **changes):
    value = {"sample_id": score["sample_id"] if score else "synthetic", "depth": "standard",
             "round": 0, "status": "complete", "elapsed_seconds": 1.0, "score": score}
    value.update(changes)
    return value


class ValidationTests(unittest.TestCase):
    def test_defaults_and_no_input_mutation(self):
        original = sample()
        before = deepcopy(original)
        normalized = validate_samples([original])[0]
        self.assertEqual(original, before)
        self.assertEqual(normalized["gold_status"], "candidate")
        self.assertFalse(normalized["complete_gold"])
        self.assertEqual(normalized["targets"][0]["accepted_suggestions"], [])
        normalized["targets"][0]["original"] = "modified"
        self.assertEqual(original, before)

    def test_empty_candidate_retains_retest_article(self):
        validated = validate_samples([sample(targets=[])])[0]
        self.assertEqual(validated["targets"], [])
        score = score_article(validated, result([issue()]))
        self.assertEqual(score["report"]["total"], 0)
        self.assertIsNone(score["report"]["recall"])
        self.assertEqual(score["unjudged"], 1)
        self.assertIsNone(score["precision"])

    def test_all_domains_categories_and_statuses(self):
        for domain in ("general", "official", "legal", "auto"):
            for category in ("typo", "grammar", "logic", "format", "fact"):
                for status in ("candidate", "confirmed"):
                    with self.subTest(domain=domain, category=category, status=status):
                        self.assertEqual(validate_samples([sample(domain=domain, gold_status=status,
                                                                 targets=[target(category=category)])])[0]["domain"], domain)

    def test_invalid_sample_shapes_and_types(self):
        bad_samples = [None, [], {}, sample(id=""), sample(id="  "), sample(id=1), sample(text=""),
                       sample(text=" \n"), {**sample(), "text": 42}, sample(domain="medical"), sample(domain=[]),
                       sample(gold_status=True), sample(gold_status="agent_confirmed"), sample(complete_gold=1),
                       sample(complete_gold="true"), sample(targets={}), sample(targets=[None])]
        for bad in bad_samples:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_samples([bad])
        for raw in (None, {}, "[]", (sample(),)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_samples(raw)
        self.assertEqual(validate_samples([]), [])

    def test_unique_ids(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_samples([sample(), sample()])
        self.assertEqual(len(validate_samples([sample(), sample(id="second")])), 2)

    def test_target_coordinates_are_strict_python_codepoints(self):
        self.assertEqual(validate_samples([sample(targets=[target(1, 2)])])[0]["targets"][0]["original"], "\U00020000")
        changes = [{"start": True}, {"start": 2.0}, {"start": "2"}, {"start": None}, {"end": False},
                   {"start": -1}, {"end": 99}, {"end": 2}, {"start": 3}, {"original": "错"},
                   {"original": ""}, {"original": None}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_samples([sample(targets=[{**target(), **change}])])

    def test_target_constraints_and_types(self):
        changes = [{"category": "style"}, {"category": []}, {"expectation": "ignore"}, {"expectation": None},
                   {"accepted_suggestions": None}, {"accepted_suggestions": "正词"},
                   {"accepted_suggestions": [None]}, {"accepted_suggestions": [False]},
                   {"accepted_suggestions": ["正词", "正词"]},
                   {"expectation": "no_report", "accepted_suggestions": ["正词"]}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_samples([sample(targets=[target(**change)])])
        self.assertEqual(validate_samples([sample(targets=[target(accepted_suggestions=[""])])])[0]
                         ["targets"][0]["accepted_suggestions"], [""])

    def test_duplicate_overlapping_and_conflicting_gold_rejected(self):
        pairs = [[target(), target()], [target(), target(category="grammar")],
                 [target(), target(expectation="no_report")], [target(), target(1, 5)],
                 [target(), target(3, 5)], [target(), target(1, 3, expectation="no_report")]]
        for targets in pairs:
            for order in itertools.permutations(targets):
                with self.subTest(targets=order), self.assertRaisesRegex(ValueError, "gold"):
                    validate_samples([sample(targets=list(order))])
        self.assertEqual(len(validate_samples([sample(targets=[target(2, 3), target(3, 4), target(6, 8)])])[0]
                             ["targets"]), 3)


class ArticleScoreTests(unittest.TestCase):
    def test_repeated_originals_match_by_position(self):
        score = score_article(sample(targets=[target(), target(6, 8)]), result([issue(6, 8)]))
        self.assertEqual(score["report"], {"total": 2, "evaluated": 2, "hit": 1, "missed": 1, "recall": .5})
        self.assertFalse(score["targets"][0]["detected"])
        self.assertTrue(score["targets"][1]["detected"])
        score = score_article(sample(targets=[target(), target(6, 8)]), result([issue(), issue(6, 8)]))
        self.assertEqual(score["true_positives"], 2)
        self.assertEqual(score["duplicates"], 0)

    def test_other_occurrence_does_not_hit_target(self):
        score = score_article(sample(), result([issue(6, 8)]))
        self.assertEqual(score["report"]["hit"], 0)
        self.assertEqual(score["unjudged"], 1)
        self.assertEqual(score["false_positives"], 0)

    def test_no_coordinate_fallback_requires_unique_including_overlap(self):
        cases = [(TEXT, "错词", 1), ("aaa", "aa", 1), ("测试文章", "不存在", 1),
                 (TEXT, "终", 0), (TEXT, "\U00020000", 0)]
        for text, original, invalid in cases:
            with self.subTest(text=text, original=original):
                score = score_article(sample(text=text, targets=[]), result([{"original": original, "type": "typo"}]))
                self.assertEqual(score["invalid"], invalid)
                if not invalid:
                    self.assertEqual(score["issue_details"][0]["start"], text.index(original))

    def test_invalid_outputs_are_counted_not_silently_dropped(self):
        changes = [{"start": True}, {"end": False}, {"start": 2.0}, {"start": "2"}, {"start": -1},
                   {"end": 200}, {"start": 3}, {"end": 2}, {"original": "不存在"}, {"original": ""},
                   {"original": None}, {"type": "fact"}, {"type": []}, {"type": None}, {"suggestion": []},
                   {"suggestion": 42}, {"suggestion": False}, {"start": None, "end": None}, {"end": None}]
        invalids = [None, [], "issue", {}, {"start": 9, "original": "终", "type": "typo"}]
        invalids.extend({**issue(), **change} for change in changes)
        for invalid in invalids:
            with self.subTest(invalid=invalid):
                score = score_article(sample(complete_gold=True, gold_status="confirmed"), result([issue(), invalid]))
                self.assertEqual(score["invalid"], 1)
                self.assertEqual(score["true_positives"], 1)
                self.assertEqual(score["issue_count"], 2)
                self.assertEqual(score["issue_details"][1]["status"], "invalid")
                self.assertTrue(score["issue_details"][1]["reason"])
                self.assertEqual(score["raw_issues"][1], invalid)
                self.assertIsNone(score["precision"])

    def test_explicit_wrong_unique_coordinates_do_not_fallback(self):
        value = {**issue(9, 10), "start": None}
        score = score_article(sample(), result([value]))
        self.assertEqual(score["invalid"], 1)

    def test_all_issue_types_legal_and_suggestion_optional(self):
        for kind in ("typo", "grammar", "logic", "style", "sensitive", "punctuation"):
            with self.subTest(kind=kind):
                value = issue(type=kind)
                del value["suggestion"]
                score = score_article(sample(), result([value]))
                self.assertEqual(score["invalid"], 0)
                self.assertEqual(score["report"]["hit"], 1)

    def test_nested_spans_accepted_but_crossing_is_not(self):
        cases = [(issue(1, 5), 1), (issue(2, 3), 1), (issue(3, 5), 0), (issue(4, 5), 0)]
        for value, hit in cases:
            with self.subTest(value=value):
                self.assertEqual(score_article(sample(), result([value]))["report"]["hit"], hit)

    def test_maximum_matching_not_greedy_and_order_independent(self):
        targets = [target(), target(6, 8)]
        values = [issue(0, 9), issue()]
        for target_order in itertools.permutations(targets):
            for issue_order in itertools.permutations(values):
                with self.subTest(targets=target_order, issues=issue_order):
                    score = score_article(sample(targets=list(target_order)), result(list(issue_order)))
                    self.assertEqual(score["report"]["hit"], 2)
                    for detail in score["targets"]:
                        matched = values[0] if detail["start"] == 6 else values[1]
                        self.assertEqual(list(issue_order)[detail["issue_indices"][0]], matched)

    def test_one_broad_issue_cannot_hit_multiple_gold(self):
        for order in itertools.permutations([target(), target(6, 8)]):
            score = score_article(sample(targets=list(order)), result([issue(0, 9)]))
            self.assertEqual(score["report"]["hit"], 1)
            self.assertEqual(score["report"]["missed"], 1)
            self.assertEqual(sum(t["detected"] for t in score["targets"]), 1)

    def test_mixed_report_no_report_matching_is_one_to_one_and_order_independent(self):
        targets = [target(), target(6, 8, expectation="no_report")]
        for target_order in itertools.permutations(targets):
            for values in itertools.permutations([issue(0, 9), issue(6, 8)]):
                score = score_article(sample(targets=list(target_order)), result(list(values)))
                self.assertEqual(score["true_positives"], 1)
                self.assertEqual(score["no_report_false_positives"], 1)
                self.assertEqual(score["unjudged"], 0)
                self.assertEqual(len({t["issue_indices"][0] for t in score["targets"]}), 2)

    def test_exact_span_wins_even_when_broad_is_correct_suggestion(self):
        values = [issue(0, 5, suggestion="首\U00020000正词，"), issue(suggestion="坏词")]
        for order in itertools.permutations(values):
            score = score_article(sample(targets=[target(accepted_suggestions=["正词"])]), result(list(order)))
            self.assertEqual(list(order)[score["targets"][0]["issue_indices"][0]]["start"], 2)
            self.assertEqual(score["suggestions"]["fail"], 1)
            self.assertEqual(score["unjudged"], 1)

    def test_exact_priority_is_global_not_dependent_on_augmenting_order(self):
        targets = [target(), target(6, 8)]
        values = [issue(), issue(6, 8), issue(0, 9), issue(1, 10)]
        for order in itertools.permutations(values):
            score = score_article(sample(targets=targets), result(list(order)))
            self.assertEqual(score["true_positives"], 2)
            for detail in score["targets"]:
                value = order[detail["issue_indices"][0]]
                self.assertEqual((value["start"], value["end"]), (detail["start"], detail["end"]))

    def test_duplicates_never_multiply_hits_and_all_suggestions_checked(self):
        targets = [target(accepted_suggestions=["正词"]), target(6, 8)]
        for order in itertools.permutations([issue(suggestion="正词"), issue(suggestion="坏词"), issue(suggestion=None)]):
            score = score_article(sample(targets=targets), result(list(order)))
            self.assertEqual(score["duplicates"], 2)
            self.assertEqual(score["true_positives"], 1)
            self.assertEqual(score["report"]["missed"], 1)
            self.assertEqual(score["targets"][0]["suggestion_status"], "fail")
            self.assertEqual(score["suggestions"], {"pass": 0, "fail": 1, "not_evaluated": 1})
            self.assertEqual(sum(d["status"] == "duplicate" for d in score["issue_details"]), 2)
        broad = score_article(sample(targets=targets), result([issue(0, 9), issue(0, 9, suggestion="不同建议")]))
        self.assertEqual((broad["duplicates"], broad["true_positives"]), (1, 1))

    def test_different_types_at_same_position_are_one_place(self):
        for order in itertools.permutations([issue(), issue(type="grammar")]):
            score = score_article(sample(), result(list(order)))
            self.assertEqual((score["true_positives"], score["duplicates"], score["unjudged"]), (1, 1, 0))
            self.assertEqual(score["targets"][0]["issue_indices"], [0, 1])
            self.assertEqual(score["classification_mismatches"], 1)
            self.assertTrue(score["targets"][0]["classification_mismatch"])
            self.assertEqual(score["outputs_by_type"]["typo"], 1)
            self.assertEqual(score["outputs_by_type"]["grammar"], 1)
            self.assertEqual([d["target_index"] for d in score["issue_details"]], [0, 0])
            self.assertEqual(score["issue_details"][1]["duplicate_of"], 0)

    def test_cross_type_whole_span_duplicate_cannot_hit_separate_targets(self):
        text = "abc def"
        targets = [target(0, 3, text=text, accepted_suggestions=["ABC"]),
                   target(4, 7, text=text, category="grammar", accepted_suggestions=["DEF"])]
        values = [issue(0, 7, text=text, type=kind, suggestion="ABC def") for kind in ("grammar", "typo")]
        for target_order in itertools.permutations(targets):
            for issue_order in itertools.permutations(values):
                score = score_article(sample(text=text, targets=list(target_order)), result(list(issue_order)))
                self.assertEqual((score["true_positives"], score["duplicates"]), (1, 1))
                self.assertEqual(score["report"]["missed"], 1)
                self.assertEqual(score["unjudged"], 0)
                detected = next(t for t in score["targets"] if t["detected"])
                self.assertEqual(detected["start"], 0)
                self.assertEqual(detected["issue_indices"], [0, 1])
                self.assertTrue(detected["classification_mismatch"])
                self.assertEqual(score["classification_mismatches"], 1)
                self.assertEqual(score["suggestions"], {"pass": 1, "fail": 0, "not_evaluated": 1})
                self.assertEqual([d["suggestion_status"] for d in score["issue_details"]], ["pass", "pass"])

    def test_cross_type_duplicates_inspect_all_suggestions_and_distinct_types(self):
        values = [issue(), issue(type="grammar", suggestion="坏词"), issue(suggestion=None)]
        for order in itertools.permutations(values):
            score = score_article(sample(targets=[target(accepted_suggestions=["正词"])]), result(list(order)))
            self.assertEqual((score["true_positives"], score["duplicates"]), (1, 2))
            self.assertEqual(score["targets"][0]["issue_indices"], [0, 1, 2])
            self.assertEqual(score["classification_mismatches"], 1)
            self.assertEqual(score["outputs_by_type"]["typo"], 1)
            self.assertEqual(score["outputs_by_type"]["grammar"], 1)
            self.assertEqual(score["suggestions"], {"pass": 0, "fail": 1, "not_evaluated": 0})
            self.assertCountEqual([d["suggestion_status"] for d in score["issue_details"]],
                                  ["pass", "fail", "not_evaluated"])
        for order in itertools.permutations([issue(), issue(type="grammar", suggestion=None)]):
            score = score_article(sample(targets=[target(accepted_suggestions=["正词"])]), result(list(order)))
            self.assertEqual(score["suggestions"], {"pass": 0, "fail": 0, "not_evaluated": 1})

    def test_compatible_format_types_at_one_place_are_not_misclassified(self):
        score = score_article(sample(targets=[target(category="format")]),
                              result([issue(type="style"), issue(type="punctuation")]))
        self.assertEqual((score["true_positives"], score["duplicates"]), (1, 1))
        self.assertEqual(score["classification_mismatches"], 0)
        self.assertFalse(score["targets"][0]["classification_mismatch"])
        self.assertEqual(score["outputs_by_type"]["style"], 1)
        self.assertEqual(score["outputs_by_type"]["punctuation"], 1)

    def test_fallback_and_positioned_duplicate_are_the_same_place(self):
        score = score_article(sample(targets=[target(9, 10)]),
                              result([issue(9, 10), {"original": "终", "type": "typo"}]))
        self.assertEqual((score["true_positives"], score["duplicates"]), (1, 1))

    def test_no_report_fp_and_partial_gold_unjudged(self):
        targets = [target(), target(6, 8, expectation="no_report")]
        values = [issue(), issue(6, 8), issue(9, 10), issue(9, 10)]
        for complete_gold in (False, True):
            with self.subTest(complete_gold=complete_gold):
                score = score_article(sample(targets=targets, complete_gold=complete_gold), result(values))
                self.assertEqual(score["no_report"]["hit"], 1)
                self.assertEqual(score["no_report_false_positives"], 1)
                self.assertEqual(score["false_positives"], 2 if complete_gold else 1)
                self.assertEqual(score["unmatched_false_positives"], int(complete_gold))
                self.assertEqual(score["unjudged"], 0 if complete_gold else 1)
                self.assertEqual(score["duplicates"], 1)
                self.assertEqual(score["targets"][1]["detection_status"], "false_positive")
                self.assertEqual(score["targets"][1]["suggestion_status"], "not_evaluated")

    def test_subset_outputs_on_no_report_are_known_false_positives(self):
        values = [issue(2, 3), issue(3, 4), issue(9, 10)]
        for order in itertools.permutations(values):
            score = score_article(sample(targets=[target(expectation="no_report")]), result(list(order)))
            self.assertEqual(score["no_report"]["hit"], 1)
            self.assertEqual(score["no_report"]["missed"], 0)
            self.assertEqual(score["no_report_false_positives"], 2)
            self.assertEqual(score["false_positives"], 2)
            self.assertEqual(score["unmatched_false_positives"], 0)
            self.assertEqual(score["unjudged"], 1)
            self.assertEqual(score["duplicates"], 0)
            self.assertCountEqual(score["targets"][0]["issue_indices"],
                                  [i for i, value in enumerate(order) if value["start"] != 9])
            self.assertEqual([d["target_index"] for d in score["issue_details"]],
                             [0 if value["start"] != 9 else None for value in order])
            self.assertIsNone(score["provisional_precision"])

    def test_multiple_no_report_groups_accumulate_indices_and_misclassification(self):
        values = [issue(2, 3, type="grammar"), issue(), issue(3, 4), issue(2, 3)]
        for complete_gold in (False, True):
            for order in itertools.permutations(values):
                score = score_article(sample(targets=[target(expectation="no_report")], complete_gold=complete_gold),
                                      result(list(order)))
                self.assertEqual(score["true_positives"], 0)
                self.assertEqual(score["no_report"]["hit"], 1)
                self.assertEqual(score["no_report_false_positives"], 3)
                self.assertEqual(score["false_positives"], 3)
                self.assertEqual(score["unmatched_false_positives"], 0)
                self.assertEqual(score["unjudged"], 0)
                self.assertEqual(score["duplicates"], 1)
                self.assertCountEqual(score["targets"][0]["issue_indices"], [0, 1, 2, 3])
                self.assertTrue(score["targets"][0]["classification_mismatch"])
                self.assertEqual(score["classification_mismatches"], 1)
                self.assertEqual(score["outputs_by_type"]["typo"], 3)
                self.assertEqual(score["outputs_by_type"]["grammar"], 1)
                self.assertEqual([d["target_index"] for d in score["issue_details"]], [0] * 4)
                self.assertEqual(sum(d["status"] == "false_positive" for d in score["issue_details"]), 3)
                self.assertEqual(sum(d["status"] == "duplicate" for d in score["issue_details"]), 1)
                self.assertEqual([d["suggestion_reason"] for d in score["issue_details"]], ["no_report"] * 4)
                self.assertEqual(score["provisional_precision"], 0.0 if complete_gold else None)

    def test_unmatched_broad_no_report_groups_choose_lowest_target_index(self):
        targets = [target(expectation="no_report"), target(6, 8, expectation="no_report")]
        values = [issue(), issue(6, 8), issue(0, 9), issue(1, 10)]
        for target_order in itertools.permutations(targets):
            for issue_order in itertools.permutations(values):
                score = score_article(sample(targets=list(target_order)), result(list(issue_order)))
                self.assertEqual(score["no_report"]["hit"], 2)
                self.assertEqual(score["no_report_false_positives"], 4)
                self.assertEqual(score["false_positives"], 4)
                self.assertEqual(score["unjudged"], 0)
                self.assertEqual(score["duplicates"], 0)
                for index, value in enumerate(issue_order):
                    if value["start"] in (0, 1):
                        self.assertEqual(score["issue_details"][index]["target_index"], 0)
                self.assertEqual(len(score["targets"][0]["issue_indices"]), 3)
                self.assertEqual(len(score["targets"][1]["issue_indices"]), 1)
                self.assertCountEqual([i for t in score["targets"] for i in t["issue_indices"]], [0, 1, 2, 3])

    def test_categories_not_model_labels_define_substantive_and_format(self):
        targets = [target(category="logic"), target(6, 8, category="format"), target(9, 10, category="fact")]
        score = score_article(sample(targets=targets), result([issue(type="punctuation"), issue(6, 8, type="style"),
                                                             issue(9, 10, type="logic")]))
        self.assertEqual(score["report"]["hit"], 3)
        self.assertEqual(score["substantive"]["total"], 1)
        self.assertEqual(score["substantive"]["hit"], 1)
        self.assertEqual(score["format"]["hit"], 1)
        self.assertEqual(score["fact"]["hit"], 1)
        self.assertEqual(score["classification_mismatches"], 1)
        self.assertEqual(score["outputs_by_type"]["punctuation"], 1)
        self.assertFalse(score["targets"][1]["classification_mismatch"])
        self.assertFalse(score["targets"][2]["classification_mismatch"])

    def test_format_volume_cannot_improve_substantive_recall(self):
        targets = [target(), target(4, 5, category="format"), target(8, 9, category="format")]
        score = score_article(sample(targets=targets), result([issue(4, 5, type="punctuation"),
                                                             issue(8, 9, type="punctuation")] * 30))
        self.assertEqual(score["report"]["recall"], 2 / 3)
        self.assertEqual(score["format"]["recall"], 1)
        self.assertEqual(score["substantive"]["recall"], 0)
        self.assertEqual(score["duplicates"], 58)

    def test_suggestions_full_text_equivalence_and_context_contract(self):
        values = [(issue(), "pass", "accepted"), (issue(suggestion="坏词"), "fail", "not_accepted"),
                  (issue(suggestion="正词或错词"), "fail", "not_accepted"),
                  (issue(suggestion="错词"), "fail", "not_accepted"),
                  (issue(suggestion=" 正词 "), "fail", "not_accepted"),
                  (issue(suggestion=None), "not_evaluated", "missing_suggestion"),
                  (issue(1, 5, suggestion="\U00020000正词，"), "pass", "accepted"),
                  (issue(1, 5, suggestion="\U00020000坏词，"), "fail", "not_accepted"),
                  (issue(1, 5, suggestion="正词"), "not_evaluated", "incomparable_context"),
                  (issue(1, 5, suggestion="\U00020000正词！"), "not_evaluated", "incomparable_context"),
                  (issue(2, 3, suggestion="正"), "pass", "accepted"),
                  (issue(2, 3, suggestion="正词"), "fail", "not_accepted")]
        for value, status, reason in values:
            with self.subTest(value=value):
                score = score_article(sample(targets=[target(accepted_suggestions=["正词"])]), result([value]))
                self.assertEqual(score["targets"][0]["suggestion_status"], status)
                self.assertEqual(score["targets"][0]["suggestion_reason"], reason)
                self.assertEqual(score["suggestions"][status], 1)

    def test_deletion_alternatives_and_short_span_gold_equivalence(self):
        for value, accepted in [(issue(suggestion="好词"), ["正词", "好词"]),
                                (issue(2, 3, suggestion="正"), ["正词"]),
                                (issue(1, 5, suggestion="\U00020000，"), [""])]:
            with self.subTest(value=value, accepted=accepted):
                score = score_article(sample(targets=[target(accepted_suggestions=accepted)]), result([value]))
                self.assertEqual(score["suggestions"]["pass"], 1)

    def test_empty_suggestion_never_receives_deletion_credit(self):
        for kind in ("typo", "grammar", "logic", "punctuation", "style", "sensitive"):
            with self.subTest(kind=kind):
                score = score_article(sample(targets=[target(accepted_suggestions=[""])]),
                                      result([issue(type=kind, suggestion="")]))
                self.assertEqual(score["suggestions"], {"pass": 0, "fail": 0, "not_evaluated": 1})
                reason = "requires_deletion_patch" if kind == "sensitive" else "unavailable_suggestion"
                self.assertEqual(score["targets"][0]["suggestion_reason"], reason)

    def test_no_gold_or_no_hit_cannot_pass_suggestions(self):
        score = score_article(sample(), result([issue()]))
        self.assertEqual(score["suggestions"], {"pass": 0, "fail": 0, "not_evaluated": 1})
        self.assertEqual(score["targets"][0]["suggestion_reason"], "no_accepted_gold")
        missed = score_article(sample(targets=[target(accepted_suggestions=["正词"])]), result())
        self.assertEqual(missed["suggestions"]["not_evaluated"], 1)
        self.assertEqual(missed["targets"][0]["suggestion_reason"], "not_detected")

    def test_bad_duplicate_can_prevent_pass_without_false_tp(self):
        score = score_article(sample(targets=[target(accepted_suggestions=["正词"])]),
                              result([issue(), issue(suggestion=None)]))
        self.assertEqual(score["suggestions"]["not_evaluated"], 1)
        self.assertEqual(score["true_positives"], 1)

    def test_candidate_precision_is_only_provisional_even_when_complete_gold(self):
        for status in ("candidate", "confirmed"):
            score = score_article(sample(gold_status=status, complete_gold=True), result([issue(), issue(9, 10)]))
            self.assertEqual(score["precision"], .5 if status == "confirmed" else None)
            self.assertEqual(score["provisional_precision"], .5 if status == "candidate" else None)
            self.assertEqual(score["provisional"], status == "candidate")
            self.assertEqual(score["precision_subset"]["runs"], 1)
        partial = score_article(sample(gold_status="confirmed"), result([issue()]))
        self.assertIsNone(partial["precision"])
        self.assertEqual(partial["precision_subset"]["runs"], 0)

    def test_empty_outputs_have_no_precision_denominator(self):
        score = score_article(sample(gold_status="confirmed", complete_gold=True, targets=[]), result())
        self.assertIsNone(score["precision"])
        self.assertEqual(score["false_positives"], 0)
        self.assertEqual(score["precision_subset"]["runs"], 1)

    def test_partial_failures_or_missing_coverage_are_never_clean(self):
        values = [None, {}, {"issues": []}, result(coverage=None), result(coverage={}),
                  result(coverage={"status": "partial", "failed_chunks": []}),
                  result(coverage={"status": "complete", "failed_chunks": [2]}),
                  result(coverage={"status": "complete"}), result(coverage={"status": "complete", "failed_chunks": None}),
                  result(coverage={"status": "complete", "failed_chunks": [], "total_chunks": 2, "completed_chunks": 1}),
                  result(coverage={"status": "complete", "failed_chunks": [], "total_chunks": True, "completed_chunks": 1}),
                  result(coverage={"status": "complete", "failed_chunks": [], "completed_chunks": 1}),
                  result(success=False), result(complete=False), result(error="failed"), result(issues={})]
        for value in values:
            with self.subTest(value=value):
                score = score_article(sample(gold_status="confirmed", complete_gold=True), value)
                self.assertEqual(score["status"], "error")
                self.assertIsNone(score["false_positives"])
                self.assertIsNone(score["report"]["hit"])
                self.assertIsNone(score["report"]["missed"])
                self.assertEqual(score["report"]["evaluated"], 0)
                self.assertIsNone(score["precision"])
                self.assertIsNone(score["targets"][0]["detected"])
        minimal = score_article(sample(), result(coverage={"status": "complete", "failed_chunks": []}))
        self.assertEqual(minimal["status"], "complete")

    def test_output_is_json_serializable_and_inputs_unchanged(self):
        gold, response = sample(), result([issue()])
        before = deepcopy((gold, response))
        score = score_article(gold, response)
        json.dumps(score, ensure_ascii=False, allow_nan=False)
        self.assertEqual((gold, response), before)
        score["raw_issues"][0]["original"] = "changed"
        self.assertEqual((gold, response), before)

    def test_matching_agrees_with_exhaustive_cardinality_and_exact_objective(self):
        rng = random.Random(20260921)
        text = "甲乙丙丁戊己庚辛"
        targets = [target(i, i + 1, text=text) for i in (1, 3, 6)]
        spans = [(a, b) for a in range(len(text)) for b in range(a + 1, len(text) + 1)]
        for _ in range(70):
            selected = rng.sample(spans, rng.randint(1, 5))
            values = [issue(a, b, text=text) for a, b in selected]
            best = (0, 0)
            for assignment in itertools.product(range(-1, len(values)), repeat=len(targets)):
                used = [i for i in assignment if i >= 0]
                if len(used) != len(set(used)):
                    continue
                if any(i >= 0 and not metrics._compatible(values[i], t) for t, i in zip(targets, assignment)):
                    continue
                exact = sum(i >= 0 and (values[i]["start"], values[i]["end"]) == (t["start"], t["end"])
                            for t, i in zip(targets, assignment))
                best = max(best, (len(used), exact))
            for ordered_values in (values, list(reversed(values))):
                score = score_article(sample(text=text, targets=targets), result(ordered_values))
                exact = sum(bool(t["issue_indices"]) and (ordered_values[t["issue_indices"][0]]["start"],
                                                         ordered_values[t["issue_indices"][0]]["end"])
                            == (t["start"], t["end"]) for t in score["targets"])
                self.assertEqual((score["true_positives"], exact), best)


class SummaryTests(unittest.TestCase):
    def test_depth_and_gold_status_are_separate(self):
        candidate = score_article(sample(id="candidate", complete_gold=True), result([issue()]))
        confirmed = score_article(sample(id="confirmed", gold_status="confirmed", complete_gold=True), result([issue()]))
        summary = summarize_runs([run(candidate), run(confirmed), run(confirmed, depth="deep")])
        self.assertEqual(summary["runs"], 3)
        self.assertEqual(len(summary["groups"]), 3)
        for group in summary["groups"]:
            if group["gold_status"] == "candidate":
                self.assertIsNone(group["precision"])
                self.assertEqual(group["provisional_precision"], 1)
                self.assertTrue(group["provisional"])
            else:
                self.assertEqual(group["precision"], 1)
                self.assertIsNone(group["provisional_precision"])

    def test_timeout_denominators_and_nearest_rank_include_all_attempts(self):
        gold = sample(gold_status="confirmed", complete_gold=True, targets=[target(), target(6, 8)])
        score = score_article(gold, result([issue()]))
        failed = score_article(gold, result(coverage={"status": "partial", "failed_chunks": [0]}))
        entries = [run(score, elapsed_seconds=1), run(round=1, status="timeout", elapsed_seconds=90),
                   run(round=2, status="error", elapsed_seconds=3), run(failed, round=3, elapsed_seconds=10)]
        group = summarize_runs(entries)["groups"][0]
        self.assertEqual((group["complete"], group["timeout"], group["error"]), (1, 1, 2))
        self.assertEqual(group["completion_rate"], .25)
        self.assertEqual(group["report"], {"total": 8, "evaluated": 2, "hit": 1, "missed": 1,
                                          "unevaluated": 6, "recall": .5, "attempt_recall": .125})
        self.assertEqual(group["latency"]["p50_seconds"], 3)
        self.assertEqual(group["latency"]["p95_seconds"], 90)
        self.assertTrue(group["latency"]["small_sample"])
        self.assertEqual(group["latency"]["method"], "nearest-rank")
        self.assertEqual(group["latency"]["scope"], "all_attempts")
        self.assertEqual(group["false_positives"], 0)
        self.assertEqual(group["precision_subset"]["runs"], 1)
        self.assertEqual(group["suggestions"]["unevaluated_failed_runs"], 6)

    def test_null_only_failure_gold_is_unknown_never_assumed_candidate(self):
        group = summarize_runs([run(status="timeout", elapsed_seconds=90)])["groups"][0]
        self.assertEqual(group["gold_status"], "unknown")
        self.assertIsNone(group["provisional"])
        self.assertEqual(group["unknown_gold_runs"], 1)
        self.assertEqual(group["completion_rate"], 0)
        self.assertIsNone(group["false_positives"])
        self.assertIsNone(group["report"]["recall"])
        self.assertIsNone(group["report"]["attempt_recall"])
        self.assertIsNone(group["precision"])

    def test_null_failure_uses_sample_metadata_across_depths_and_input_order(self):
        score = score_article(sample(gold_status="confirmed"), result([issue()]))
        entries = [run(status="timeout", depth="deep"), run(score)]
        for ordered in (entries, list(reversed(entries))):
            group = next(g for g in summarize_runs(ordered)["groups"] if g["depth"] == "deep")
            self.assertEqual(group["gold_status"], "confirmed")
            self.assertEqual(group["report"]["total"], 1)
            self.assertEqual(group["report"]["unevaluated"], 1)
            self.assertIsNone(group["false_positives"])

    def test_declared_complete_null_score_is_error(self):
        group = summarize_runs([run()])["groups"][0]
        self.assertEqual(group["complete"], 0)
        self.assertEqual(group["error"], 1)
        self.assertIsNone(group["false_positives"])

    def test_failed_run_with_complete_score_cannot_leak_quality(self):
        score = score_article(sample(complete_gold=True), result([issue(), issue(9, 10)]))
        for status in ("timeout", "error"):
            group = summarize_runs([run(score, status=status)])["groups"][0]
            self.assertEqual(group[status], 1)
            self.assertEqual(group["report"]["evaluated"], 0)
            self.assertEqual(group["report"]["unevaluated"], 1)
            self.assertIsNone(group["false_positives"])
            self.assertEqual(group["precision_subset"]["runs"], 0)

    def test_precision_only_uses_eligible_successful_subset(self):
        samples = [sample(id="full", complete_gold=True, gold_status="confirmed"),
                   sample(id="partial", gold_status="confirmed"),
                   sample(id="invalid", complete_gold=True, gold_status="confirmed")]
        responses = [result([issue(), issue(9, 10)]), result([issue(), issue(6, 8)]), result([issue(), None])]
        scores = [score_article(gold, response) for gold, response in zip(samples, responses)]
        group = summarize_runs([run(score) for score in scores])["groups"][0]
        self.assertEqual(group["true_positives"], 3)
        self.assertEqual(group["unjudged"], 1)
        self.assertEqual(group["invalid"], 1)
        self.assertEqual(group["precision"], .5)
        self.assertEqual(group["precision_subset"], {"runs": 1, "true_positives": 1, "false_positives": 1})

    def test_micro_recall_not_mean_of_article_recalls(self):
        one = score_article(sample(id="one"), result([issue()]))
        two = score_article(sample(id="two", targets=[target(), target(6, 8)]), result())
        group = summarize_runs([run(one), run(two)])["groups"][0]
        self.assertEqual(group["report"]["recall"], 1 / 3)
        self.assertEqual(group["substantive"]["recall"], 1 / 3)

    def test_suggestion_counts_and_duplicates_aggregate_once_per_target(self):
        score = score_article(sample(targets=[target(accepted_suggestions=["正词"])]), result([issue(), issue()]))
        group = summarize_runs([run(score), run(score, round=1)])["groups"][0]
        self.assertEqual(group["duplicates"], 2)
        self.assertEqual(group["true_positives"], 2)
        self.assertEqual(group["suggestions"], {"pass": 2, "fail": 0, "not_evaluated": 0, "unevaluated_failed_runs": 0})

    def test_summary_keeps_format_and_fact_out_of_substantive_recall(self):
        targets = [target(), target(6, 8, category="format"), target(9, 10, category="fact")]
        score = score_article(sample(targets=targets), result([issue(6, 8, type="style"), issue(9, 10)]))
        group = summarize_runs([run(score), run(status="timeout", round=1)])["groups"][0]
        self.assertEqual(group["format"]["recall"], 1)
        self.assertEqual(group["fact"]["recall"], 1)
        self.assertEqual(group["substantive"]["recall"], 0)
        self.assertEqual(group["substantive"]["total"], 2)
        self.assertEqual(group["substantive"]["evaluated"], 1)
        self.assertEqual(group["outputs_by_type"]["style"], 1)

    def test_twenty_runs_nearest_rank_and_small_sample_boundary(self):
        score = score_article(sample(), result())
        group = summarize_runs([run(score, round=i, elapsed_seconds=i + 1) for i in range(20)])["groups"][0]
        self.assertEqual(group["latency"]["p50_seconds"], 10)
        self.assertEqual(group["latency"]["p95_seconds"], 19)
        self.assertFalse(group["latency"]["small_sample"])

    def test_empty_runs(self):
        self.assertEqual(summarize_runs([]), {"runs": 0, "groups": []})

    def test_run_validation(self):
        bad_runs = [None, {}, run(round=True), run(round=-1), run(round="1"), run(status="partial"),
                    run(status=[]), run(sample_id=""), run(depth=None), run(elapsed_seconds=True),
                    run(elapsed_seconds=-1), run(elapsed_seconds=float("nan")), run(elapsed_seconds=float("inf")),
                    {**run(), "score": "bad"}, run(score={}), run(score={"sample_id": "other"})]
        for bad in bad_runs:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                summarize_runs([bad])
        with self.assertRaises(ValueError):
            summarize_runs([run(), run()])
        with self.assertRaises(ValueError):
            summarize_runs(None)

    def test_inconsistent_gold_cannot_relabel_failed_runs(self):
        candidate = score_article(sample(), result())
        confirmed = score_article(sample(gold_status="confirmed"), result())
        with self.assertRaisesRegex(ValueError, "inconsistent gold"):
            summarize_runs([run(candidate), run(confirmed, round=1)])

    def test_summary_json_and_no_mutation(self):
        entries = [run(score_article(sample(), result([issue()])))]
        original = deepcopy(entries)
        json.dumps(summarize_runs(entries), ensure_ascii=False, allow_nan=False)
        self.assertEqual(entries, original)


if __name__ == "__main__":
    unittest.main()
