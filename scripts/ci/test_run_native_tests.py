"""Unit checks for reporting gates; these do not claim any platform coverage."""
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from run_native_tests import REQUIRED_INTEGRATION_TESTS, summarize_junit


class JUnitGateTests(unittest.TestCase):
    def report(self, *, omitted=None, outcome=None, core=True):
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite")
        for name in sorted(REQUIRED_INTEGRATION_TESTS - {omitted}):
            case = ET.SubElement(suite, "testcase", {
                "classname": "tests.test_hermes_integration", "name": name})
            if outcome:
                ET.SubElement(case, outcome)
        if core:
            ET.SubElement(suite, "testcase", {"classname": "tests.test_ledger", "name": "core"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "junit.xml"
            ET.ElementTree(root).write(path, encoding="utf-8")
            return summarize_junit(path)

    def test_complete_report_passes_with_separate_counts(self):
        counts, problems = self.report()
        self.assertEqual(problems, [])
        self.assertEqual(counts["hermes_integration"], len(REQUIRED_INTEGRATION_TESTS))
        self.assertEqual(counts["core"], 1)
        self.assertEqual(counts["passed"], counts["total"])

    def test_missing_integration_cannot_pass(self):
        _, problems = self.report(omitted="test_actual_plugin_doctor")
        self.assertTrue(any("Missing real Hermes tests" in problem for problem in problems))

    def test_skipped_failed_and_errored_integration_cannot_pass(self):
        for outcome, count in (("skipped", "skipped"), ("failure", "failures"), ("error", "errors")):
            with self.subTest(outcome=outcome):
                counts, problems = self.report(outcome=outcome)
                self.assertEqual(counts[count], len(REQUIRED_INTEGRATION_TESTS))
                self.assertTrue(problems)

    def test_zero_core_cannot_pass(self):
        _, problems = self.report(core=False)
        self.assertIn("No core tests ran", problems)


if __name__ == "__main__":
    unittest.main()
