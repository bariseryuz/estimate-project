import unittest

from domain.direct_shades_workbooks import WorkbookKind, parse_workbook_xlsx
from tests.helpers_workbook import build_window_matrix_xlsx


class TestWorkbookParsing(unittest.TestCase):
    def test_matrix_total_and_markings(self):
        data = build_window_matrix_xlsx(total_shades=5)
        parsed = parse_workbook_xlsx("301 Window Matrix.xlsx", data)
        self.assertEqual(parsed.kind, WorkbookKind.WINDOW_MATRIX)
        self.assertEqual(parsed.matrix_total_shades, 5)
        self.assertEqual(len(parsed.matrix_rows), 2)

    def test_blind_qty_lines_w_h_headers(self):
        data = build_window_matrix_xlsx(
            blind_header=["TAG", "QUANTITY", "WIDTH", "HEIGHT", "TYPE", "LOCATION"],
            blind_rows=[
                {"tag": "W-1", "qty": 2, "w": 50, "h": 70, "sys": "Dual", "room": "Suite"},
            ],
        )
        parsed = parse_workbook_xlsx("301 Window Matrix.xlsx", data)
        self.assertEqual(len(parsed.blind_qty_lines), 1)
        line = parsed.blind_qty_lines[0]
        self.assertEqual(line["windowTag"], "W-1")
        self.assertEqual(line["quantity"], 2)
        self.assertEqual(line["width"], "50")
        self.assertEqual(line["height"], "70")
        self.assertIn("301 Window Matrix", line["sourceLocation"])

    def test_blind_qty_sheet_name_variant(self):
        data = build_window_matrix_xlsx()
        parsed = parse_workbook_xlsx("301 Window Matrix.xlsx", data)
        self.assertTrue(any("Blind QTY" in s for s in parsed.sheets))
        self.assertGreaterEqual(len(parsed.blind_qty_sections), 1)


if __name__ == "__main__":
    unittest.main()
