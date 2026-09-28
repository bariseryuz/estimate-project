import unittest

from domain.direct_shades_workbooks import parse_workbook_xlsx, merge_workbook_analyses
from domain.workbook_takeoff import build_takeoff_from_workbook
from pipeline.quantity_schedule import build_quantity_schedule
from tests.helpers_workbook import build_window_matrix_xlsx


class TestQuantitySchedule(unittest.TestCase):
    def test_matches_matrix_via_takeoff_total(self):
        data = build_window_matrix_xlsx()
        wb = merge_workbook_analyses([parse_workbook_xlsx("301 Window Matrix.xlsx", data)])
        takeoff = build_takeoff_from_workbook(wb)
        qs = build_quantity_schedule(takeoff, {}, wb, {})
        self.assertEqual(qs["totals"]["matrixAuthority"], 5)
        self.assertTrue(qs["totals"]["matchesMatrix"])
        self.assertGreaterEqual(len(qs["lines"]), 1)
        line = qs["lines"][0]
        self.assertTrue(line.get("countBasis") or line.get("sourceLocation"))


if __name__ == "__main__":
    unittest.main()
