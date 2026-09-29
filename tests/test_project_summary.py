import unittest

from domain.direct_shades_workbooks import merge_workbook_analyses, parse_workbook_xlsx
from domain.workbook_takeoff import build_takeoff_from_workbook
from pipeline.project_summary import build_project_summary
from pipeline.quantity_schedule import build_quantity_schedule
from tests.helpers_workbook import build_bid_summary_xlsx, build_window_matrix_xlsx


def _summary(workbook, takeoff, estimation=None, validation=None, fast_path=True):
    estimation = estimation or {}
    qs = build_quantity_schedule(takeoff, estimation, workbook, {})
    return build_project_summary(
        source_meta={"fileNames": ["301 Window Matrix.xlsx"]},
        workbook=workbook,
        takeoff=takeoff,
        estimation=estimation,
        validation=validation or {},
        quantity_schedule=qs,
        workbook_fast_path=fast_path,
    )


class TestProjectSummary(unittest.TestCase):
    def setUp(self):
        self.wb = merge_workbook_analyses(
            [parse_workbook_xlsx("301 Window Matrix.xlsx", build_window_matrix_xlsx())]
        )
        self.takeoff = build_takeoff_from_workbook(self.wb)

    def test_reports_every_count_the_workbook_gives(self):
        ps = _summary(self.wb, self.takeoff)
        self.assertEqual(ps["matrixTotal"], 5)
        self.assertEqual(ps["takeoffTotal"], 5)
        self.assertEqual(ps["markingSum"], 5)
        self.assertEqual(ps["blindQtySum"], 2)
        self.assertEqual(ps["route"], "workbook_fast_path")
        self.assertEqual(ps["countAuthority"], "WINDOW MATRIX TOTAL row")

    def test_matrix_only_when_schedule_rolls_up(self):
        ps = _summary(self.wb, self.takeoff)
        # Two Blind QTY lines cover a 5-shade project, so lines will not foot to 5.
        self.assertEqual(ps["matchStatus"], "matrix_only")
        self.assertIn("WINDOW MATRIX TOTAL row", ps["matchNote"])

    def test_mismatch_when_takeoff_disagrees_with_matrix(self):
        takeoff = {**self.takeoff, "totalShadeCount": 9}
        ps = _summary(self.wb, takeoff)
        self.assertEqual(ps["matchStatus"], "mismatch")
        self.assertIn("matrix wins", ps["matchNote"])

    def test_no_matrix_status_without_a_total_row(self):
        wb = {**self.wb, "authoritativeTotalShades": None}
        ps = _summary(wb, {"totalShadeCount": 4})
        self.assertEqual(ps["matchStatus"], "no_matrix")

    def test_drawing_set_matches_when_unit_rows_equal_the_printed_total(self):
        wb = {**self.wb, "authoritativeTotalShades": None, "projectShadeCount": None}
        ps = _summary(
            wb,
            {
                "dataSource": "drawing_set",
                "totalShadeCount": 473,
                "matrixUnitsRead": 144,
                "matrixUnitsPrinted": 144,
                "primarySource": "Unit matrix and unit plans",
            },
        )
        self.assertEqual(ps["matchStatus"], "match")
        self.assertIn("144", ps["matchNote"])

    def test_price_variance_against_bid_summary(self):
        wb = merge_workbook_analyses(
            [
                parse_workbook_xlsx("301 Window Matrix.xlsx", build_window_matrix_xlsx()),
                parse_workbook_xlsx("301 Bid Summary.xlsx", build_bid_summary_xlsx()),
            ]
        )
        takeoff = build_takeoff_from_workbook(wb)
        ps = _summary(wb, takeoff, estimation={"totalEstimate": 3200.0, "currency": "USD"})
        self.assertEqual(ps["referenceGrandTotal"], 3000.0)
        self.assertEqual(ps["priceVariance"]["amount"], 200.0)
        self.assertAlmostEqual(ps["priceVariance"]["percent"], 6.67, places=2)
        self.assertEqual(ps["pricePerUnit"], 640.0)  # 3200 / 5 shades

    def test_files_carry_their_role(self):
        ps = _summary(self.wb, self.takeoff)
        matrix = next(f for f in ps["files"] if f["name"] == "301 Window Matrix.xlsx")
        self.assertEqual(matrix["kind"], "window_matrix")
        self.assertIn("count authority", matrix["role"].lower())

    def test_no_price_variance_without_a_bid_workbook(self):
        ps = _summary(self.wb, self.takeoff, estimation={"totalEstimate": 1000.0})
        self.assertIsNone(ps["priceVariance"])


if __name__ == "__main__":
    unittest.main()
