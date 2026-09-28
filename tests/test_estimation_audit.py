import unittest

from domain.direct_shades_workbooks import parse_workbook_xlsx, merge_workbook_analyses
from domain.workbook_takeoff import build_takeoff_from_workbook
from pipeline.estimation_audit import build_estimation_audit
from pipeline.quantity_schedule import build_quantity_schedule
from tests.helpers_workbook import build_window_matrix_xlsx


class TestEstimationAudit(unittest.TestCase):
    def test_audit_includes_matrix_and_blind_refs(self):
        data = build_window_matrix_xlsx()
        wb = merge_workbook_analyses([parse_workbook_xlsx("301 Window Matrix.xlsx", data)])
        takeoff = build_takeoff_from_workbook(wb)
        qs = build_quantity_schedule(takeoff, {"subtotal": 1000, "totalEstimate": 1200}, wb, {})
        audit = build_estimation_audit(
            source_meta={"fileNames": ["301 Window Matrix.xlsx"]},
            workbook=wb,
            takeoff=takeoff,
            estimation={"subtotal": 1000, "totalEstimate": 1200},
            context={"projectName": "Test Tower"},
            quantity_schedule=qs,
        )
        roles = {r["role"] for r in audit["documentReferences"]}
        self.assertIn("Count authority", roles)
        self.assertIn("Blind QTY take-off", roles)
        self.assertTrue(audit["countCalculation"]["steps"])
        self.assertEqual(len(audit["lineCalculations"]), len(qs["lines"]))


if __name__ == "__main__":
    unittest.main()
