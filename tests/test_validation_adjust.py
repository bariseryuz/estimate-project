import unittest

from domain.direct_shades_workbooks import merge_workbook_analyses, parse_workbook_xlsx
from domain.workbook_takeoff import build_takeoff_from_workbook
from pipeline.validation_adjust import adjust_validation_for_workbook
from tests.helpers_workbook import build_bid_summary_xlsx, build_window_matrix_xlsx


class TestValidationReflectsWorkbookAuthority(unittest.TestCase):
    def setUp(self):
        self.wb = merge_workbook_analyses(
            [parse_workbook_xlsx("301 Window Matrix.xlsx", build_window_matrix_xlsx())]
        )
        self.takeoff = build_takeoff_from_workbook(self.wb)

    def _checks(self, validation=None, takeoff=None, estimation=None):
        result = adjust_validation_for_workbook(
            validation or {"confidenceScore": 55, "recommendation": "Needs Review"},
            self.wb,
            takeoff or self.takeoff,
            estimation=estimation,
        )
        return result, {c["item"]: c for c in result["crossChecks"]}

    def test_matrix_cross_check_is_present_and_passing(self):
        _, checks = self._checks()
        key = "Total shade count vs WINDOW MATRIX TOTAL row"
        self.assertIn(key, checks)
        self.assertTrue(checks[key]["match"])

    def test_marking_and_blind_checks_are_computed(self):
        _, checks = self._checks()
        self.assertIn("Matrix marking rows sum vs TOTAL row", checks)
        self.assertIn("Blind QTY sheet rows vs TOTAL row", checks)
        self.assertTrue(checks["Matrix marking rows sum vs TOTAL row"]["match"])
        # 2 parsed Blind QTY rows against a 5-shade matrix total — a real gap.
        self.assertFalse(checks["Blind QTY sheet rows vs TOTAL row"]["match"])

    def test_mismatched_takeoff_fails_the_matrix_check(self):
        takeoff = {**self.takeoff, "totalShadeCount": 11}
        result, checks = self._checks(takeoff=takeoff)
        self.assertFalse(checks["Total shade count vs WINDOW MATRIX TOTAL row"]["match"])
        self.assertIn("reconcile", result["userFriendlySummary"].lower())

    def test_alignment_raises_confidence_and_writes_plain_summary(self):
        result, _ = self._checks()
        self.assertGreaterEqual(result["confidenceScore"], 82)
        self.assertEqual(result["confidenceLevel"], "High")
        self.assertIn("5", result["userFriendlySummary"])
        self.assertLessEqual(len(result["reviewChecklist"]), 4)
        self.assertTrue(
            any("WINDOW MATRIX" in s.upper() for s in result.get("strengths") or [])
        )

    def test_bid_total_cross_check_reports_variance(self):
        wb = merge_workbook_analyses(
            [
                parse_workbook_xlsx("301 Window Matrix.xlsx", build_window_matrix_xlsx()),
                parse_workbook_xlsx("301 Bid Summary.xlsx", build_bid_summary_xlsx()),
            ]
        )
        result = adjust_validation_for_workbook(
            {"confidenceScore": 70},
            wb,
            build_takeoff_from_workbook(wb),
            estimation={"totalEstimate": 3000.0},
        )
        check = next(
            c for c in result["crossChecks"]
            if c["item"] == "Project total vs Bid Summary grand total"
        )
        self.assertTrue(check["match"])
        self.assertIn("0.00", check["notes"])

    def test_price_that_disagrees_with_the_bid_blocks_sending(self):
        """The count can be perfect and the estimate still be wrong on dollars."""
        wb = merge_workbook_analyses(
            [
                parse_workbook_xlsx("301 Window Matrix.xlsx", build_window_matrix_xlsx()),
                parse_workbook_xlsx("301 Bid Summary.xlsx", build_bid_summary_xlsx()),
            ]
        )
        result = adjust_validation_for_workbook(
            {"confidenceScore": 90, "readyToSendOffer": True},
            wb,
            build_takeoff_from_workbook(wb),
            estimation={"totalEstimate": 9999.0},
        )
        check = next(
            c for c in result["crossChecks"]
            if c["item"] == "Project total vs Bid Summary grand total"
        )
        self.assertFalse(check["match"])
        self.assertFalse(result["readyToSendOffer"])
        self.assertLessEqual(result["confidenceScore"], 65)
        self.assertTrue(
            any("Bid Summary" in str(i.get("issue")) for i in result["validationIssues"])
        )

    def test_advisory_blind_qty_gap_does_not_block_sending(self):
        # The Blind QTY row check fails on this fixture by design; it is informational.
        result, checks = self._checks()
        self.assertFalse(checks["Blind QTY sheet rows vs TOTAL row"]["match"])
        self.assertTrue(checks["Blind QTY sheet rows vs TOTAL row"]["advisory"])
        self.assertEqual(result["confidenceLevel"], "High")
        self.assertTrue(result["readyToSendOffer"])

    def test_deterministic_workbook_takeoff_earns_a_higher_floor(self):
        result, _ = self._checks()
        self.assertEqual(self.takeoff["dataSource"], "workbook_excel")
        self.assertGreaterEqual(result["confidenceScore"], 88)

    def test_model_takeoff_keeps_the_lower_floor(self):
        takeoff = {**self.takeoff, "dataSource": "ai_vision"}
        result, _ = self._checks(takeoff=takeoff)
        self.assertEqual(result["confidenceScore"], 82)

    def test_model_cross_checks_are_kept_after_the_computed_ones(self):
        model_check = {"item": "Fabric spec matches submittal", "match": True}
        result, _ = self._checks(
            validation={"confidenceScore": 60, "crossChecks": [model_check]}
        )
        self.assertEqual(result["crossChecks"][-1]["item"], "Fabric spec matches submittal")
        self.assertGreater(len(result["crossChecks"]), 1)

    def test_duplicate_model_check_is_not_repeated(self):
        duplicate = {"item": "Total shade count vs WINDOW MATRIX TOTAL row", "match": False}
        result, _ = self._checks(
            validation={"confidenceScore": 60, "crossChecks": [duplicate]}
        )
        matching = [
            c for c in result["crossChecks"]
            if c["item"] == "Total shade count vs WINDOW MATRIX TOTAL row"
        ]
        self.assertEqual(len(matching), 1)
        self.assertTrue(matching[0]["match"])


if __name__ == "__main__":
    unittest.main()
