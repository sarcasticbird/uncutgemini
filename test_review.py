import unittest
from review import (
    parse_diff_ranges,
    validate_findings,
    format_comment_body,
    build_review_payload,
    build_failure_payload,
    get_sign_off,
    CLEAN_GIFS,
    FINDINGS_QUOTES,
    FAILED_QUOTES,
)

SAMPLE_DIFF = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -10,6 +10,8 @@ def main():
     x = 1
     y = 2
+    password = "admin"
+    db.connect(password)
     return x + y

@@ -30,3 +32,4 @@ def other():
     pass
+    logging.info("done")
"""


class TestParseDiffRanges(unittest.TestCase):
    def test_basic_hunks(self):
        ranges = parse_diff_ranges(SAMPLE_DIFF)
        self.assertIn("src/app.py", ranges)
        self.assertEqual(len(ranges["src/app.py"]), 2)
        self.assertEqual(ranges["src/app.py"][0], (10, 17))
        self.assertEqual(ranges["src/app.py"][1], (32, 35))

    def test_single_line_hunk(self):
        diff = "diff --git a/f.py b/f.py\n@@ -1 +1 @@\n-old\n+new\n"
        ranges = parse_diff_ranges(diff)
        self.assertEqual(ranges["f.py"], [(1, 1)])

    def test_multiple_files(self):
        diff = (
            "diff --git a/a.py b/a.py\n@@ -1,3 +1,4 @@\n x\n+y\n"
            "diff --git a/b.py b/b.py\n@@ -5,2 +5,3 @@\n a\n+b\n"
        )
        ranges = parse_diff_ranges(diff)
        self.assertIn("a.py", ranges)
        self.assertIn("b.py", ranges)

    def test_empty_diff(self):
        self.assertEqual(parse_diff_ranges(""), {})


class TestValidateFindings(unittest.TestCase):
    def setUp(self):
        self.ranges = parse_diff_ranges(SAMPLE_DIFF)

    def test_valid_finding(self):
        findings = [{"file": "src/app.py", "line": 12, "severity": "HIGH", "description": "bad"}]
        result = validate_findings(findings, self.ranges)
        self.assertEqual(len(result), 1)

    def test_line_outside_range(self):
        findings = [{"file": "src/app.py", "line": 25, "severity": "HIGH", "description": "bad"}]
        result = validate_findings(findings, self.ranges)
        self.assertEqual(len(result), 0)

    def test_file_not_in_diff(self):
        findings = [{"file": "other.py", "line": 1, "severity": "HIGH", "description": "bad"}]
        result = validate_findings(findings, self.ranges)
        self.assertEqual(len(result), 0)

    def test_missing_line(self):
        findings = [{"file": "src/app.py", "severity": "HIGH", "description": "bad"}]
        result = validate_findings(findings, self.ranges)
        self.assertEqual(len(result), 0)

    def test_start_line_reversed(self):
        findings = [{"file": "src/app.py", "line": 12, "start_line": 15, "severity": "HIGH", "description": "bad"}]
        result = validate_findings(findings, self.ranges)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["start_line"])

    def test_start_line_outside_range(self):
        findings = [{"file": "src/app.py", "line": 12, "start_line": 5, "severity": "HIGH", "description": "bad"}]
        result = validate_findings(findings, self.ranges)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["start_line"])

    def test_valid_start_line(self):
        findings = [{"file": "src/app.py", "line": 13, "start_line": 11, "severity": "HIGH", "description": "bad"}]
        result = validate_findings(findings, self.ranges)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["start_line"], 11)

    def test_start_line_cross_hunk(self):
        findings = [{"file": "src/app.py", "line": 33, "start_line": 12, "severity": "HIGH", "description": "bad"}]
        result = validate_findings(findings, self.ranges)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["start_line"])


class TestFormatCommentBody(unittest.TestCase):
    def test_with_fix(self):
        finding = {"severity": "HIGH", "description": "SQL injection", "suggested_fix": "use $1"}
        body = format_comment_body(finding)
        self.assertIn("HIGH", body)
        self.assertIn("SQL injection", body)
        self.assertIn("```suggestion", body)
        self.assertIn("use $1", body)

    def test_without_fix(self):
        finding = {"severity": "NIT", "description": "minor thing", "suggested_fix": None}
        body = format_comment_body(finding)
        self.assertIn("NIT", body)
        self.assertNotIn("suggestion", body)

    def test_strips_fences_from_fix(self):
        finding = {"severity": "MEDIUM", "description": "test", "suggested_fix": "```python\nfixed\n```"}
        body = format_comment_body(finding)
        self.assertNotIn("```python", body)
        self.assertIn("fixed", body)


class TestBuildReviewPayload(unittest.TestCase):
    def test_clean_review(self):
        payload = build_review_payload([], "All good.", False, "", [], "diff")
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["comments"], [])
        self.assertIn("clean", payload["body"])

    def test_findings_review(self):
        findings = [
            {"file": "a.py", "line": 10, "severity": "HIGH", "description": "bad", "suggested_fix": "fix"},
        ]
        payload = build_review_payload(findings, "Issues found.", False, "", [], "diff")
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(len(payload["comments"]), 1)
        self.assertEqual(payload["comments"][0]["path"], "a.py")
        self.assertEqual(payload["comments"][0]["line"], 10)

    def test_incremental_prefix(self):
        payload = build_review_payload([], "ok", True, "abc1234567", [], "diff")
        self.assertIn("abc1234", payload["body"])

    def test_fragments_included(self):
        fragments = ["### Dependencies — clean", "### Size — 100 lines"]
        payload = build_review_payload([], "ok", False, "", fragments, "diff")
        self.assertIn("Dependencies", payload["body"])
        self.assertIn("Size", payload["body"])

    def test_multiline_comment(self):
        findings = [
            {"file": "a.py", "line": 15, "start_line": 10, "severity": "MEDIUM", "description": "x"},
        ]
        payload = build_review_payload(findings, "x", False, "", [], "diff")
        comment = payload["comments"][0]
        self.assertEqual(comment["start_line"], 10)
        self.assertEqual(comment["start_side"], "RIGHT")

    def test_clean_review_has_gif_sign_off(self):
        payload = build_review_payload([], "All good.", False, "", [], "some diff")
        self.assertIn("\n---\n", payload["body"])
        self.assertIn("![Uncut Gems]", payload["body"])

    def test_findings_review_has_quote_sign_off(self):
        findings = [
            {"file": "a.py", "line": 10, "severity": "HIGH", "description": "bad"},
        ]
        payload = build_review_payload(findings, "Issues.", False, "", [], "some diff")
        self.assertIn("\n---\n", payload["body"])
        self.assertIn("Adam Sandler", payload["body"])


class TestBuildFailurePayload(unittest.TestCase):
    def test_failure_includes_reason(self):
        payload = build_failure_payload("timed out after 120s", False, "", [], "diff")
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["comments"], [])
        self.assertIn("Code Review — failed", payload["body"])
        self.assertIn("timed out after 120s", payload["body"])

    def test_failure_includes_fragments(self):
        fragments = ["### 🛡️ Dependencies — clean", "### 📏 Size — 50 lines"]
        payload = build_failure_payload("api error", False, "", fragments, "diff")
        self.assertIn("Dependencies", payload["body"])
        self.assertIn("Size", payload["body"])
        self.assertIn("api error", payload["body"])

    def test_failure_incremental_prefix(self):
        payload = build_failure_payload("blocked", True, "abc1234567", [], "diff")
        self.assertIn("abc1234", payload["body"])
        self.assertIn("Incremental review", payload["body"])

    def test_failure_has_quote_sign_off(self):
        payload = build_failure_payload("timed out", False, "", [], "some diff")
        self.assertIn("\n---\n", payload["body"])
        self.assertIn("Adam Sandler", payload["body"])


class TestGetSignOff(unittest.TestCase):
    def test_clean_returns_gif_markdown(self):
        result = get_sign_off("clean", "some diff content")
        self.assertIn("---", result)
        self.assertIn("![", result)
        self.assertTrue(any(gif in result for gif in CLEAN_GIFS))

    def test_findings_returns_blockquote(self):
        result = get_sign_off("findings", "some diff content")
        self.assertIn("---", result)
        self.assertIn("> *\"", result)
        self.assertIn("Adam Sandler", result)
        self.assertTrue(any(q in result for q in FINDINGS_QUOTES))

    def test_failed_returns_blockquote(self):
        result = get_sign_off("failed", "some diff content")
        self.assertIn("---", result)
        self.assertIn("> *\"", result)
        self.assertIn("Adam Sandler", result)
        self.assertTrue(any(q in result for q in FAILED_QUOTES))

    def test_deterministic_for_same_diff(self):
        result1 = get_sign_off("clean", "identical diff")
        result2 = get_sign_off("clean", "identical diff")
        self.assertEqual(result1, result2)

    def test_different_diff_can_produce_different_result(self):
        results = set()
        for i in range(50):
            results.add(get_sign_off("findings", f"diff variant {i}"))
        self.assertGreater(len(results), 1)

    def test_collections_have_sufficient_variety(self):
        self.assertGreaterEqual(len(CLEAN_GIFS), 50)
        self.assertGreaterEqual(len(FINDINGS_QUOTES), 50)
        self.assertGreaterEqual(len(FAILED_QUOTES), 30)


if __name__ == "__main__":
    unittest.main()
