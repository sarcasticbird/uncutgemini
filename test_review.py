import unittest
from review import (
    parse_diff_ranges,
    validate_findings,
    format_comment_body,
    build_review_payload,
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
        payload = build_review_payload([], "All good.", False, "", [])
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["comments"], [])
        self.assertIn("clean", payload["body"])

    def test_findings_review(self):
        findings = [
            {"file": "a.py", "line": 10, "severity": "HIGH", "description": "bad", "suggested_fix": "fix"},
        ]
        payload = build_review_payload(findings, "Issues found.", False, "", [])
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(len(payload["comments"]), 1)
        self.assertEqual(payload["comments"][0]["path"], "a.py")
        self.assertEqual(payload["comments"][0]["line"], 10)

    def test_incremental_prefix(self):
        payload = build_review_payload([], "ok", True, "abc1234567", [])
        self.assertIn("abc1234", payload["body"])

    def test_fragments_included(self):
        fragments = ["### Dependencies — clean", "### Size — 100 lines"]
        payload = build_review_payload([], "ok", False, "", fragments)
        self.assertIn("Dependencies", payload["body"])
        self.assertIn("Size", payload["body"])

    def test_multiline_comment(self):
        findings = [
            {"file": "a.py", "line": 15, "start_line": 10, "severity": "MEDIUM", "description": "x"},
        ]
        payload = build_review_payload(findings, "x", False, "", [])
        comment = payload["comments"][0]
        self.assertEqual(comment["start_line"], 10)
        self.assertEqual(comment["start_side"], "RIGHT")


if __name__ == "__main__":
    unittest.main()
