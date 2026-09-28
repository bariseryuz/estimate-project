import unittest

from domain.direct_shades_workbooks import parse_workbook_xlsx, merge_workbook_analyses
from domain.workbook_takeoff import (
    apply_workbook_precision,
    build_takeoff_from_workbook,
    workbook_has_structured_takeoff,
)
from tests.helpers_workbook import build_window_matrix_xlsx


class TestWorkbookTakeoff(unittest.TestCase):
    def setUp(self):
        data = build_window_matrix_xlsx()
        parsed = parse_workbook_xlsx("301 Window Matrix.xlsx", data)
        self.workbook = merge_workbook_analyses([parsed])

    def test_structured_takeoff_gate(self):
        self.assertTrue(workbook_has_structured_takeoff(self.workbook))

    def test_build_takeoff_from_workbook(self):
        result = build_takeoff_from_workbook(self.workbook)
        self.assertEqual(result["totalShadeCount"], 5)
        self.assertEqual(result["workbookAuthority"], "WINDOW MATRIX TOTAL row")
        tags = {(i.get("windowTag") or "").upper() for i in result["takeoffItems"]}
        self.assertIn("A-101", tags)

    def test_apply_workbook_precision_overrides_ai_qty(self):
        wb = self.workbook
        ai_items = [{"windowTag": "A-101", "quantity": 99, "dataSource": "ai_takeoff"}]
        merged = apply_workbook_precision(wb, ai_items)
        a101 = next(i for i in merged if i["windowTag"] == "A-101")
        self.assertEqual(a101["quantity"], 1)
        self.assertEqual(a101["dataSource"], "workbook_excel")


if __name__ == "__main__":
    unittest.main()
